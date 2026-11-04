"""A model call the API fails, answered by the operator's own Claude session (studio/session.py)."""

import json
import os
import threading
import time

import pytest

from studio import brain, jobs, knobs, ledger, session
from studio import run as operator_run
from studio.schemas import FactCheck
from studio.tests.test_run import FakeApi, ScriptedBrain


@pytest.fixture(autouse=True)
def quick(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    monkeypatch.setattr(session, "POLL_S", 0.02)
    monkeypatch.setattr(session, "SETTLE_S", 0.1)
    monkeypatch.setattr(session, "WAIT_S", 20)  # a call nobody answers fails the test rather than hanging it
    return tmp_path


def failing(*args, **kwargs):
    raise brain.BrainError("claude-opus-5-5: Error code: 529 - overloaded_error")


CHECK = {"checks": [{"id": "n1", "verdict": "SUPPORTED", "source": "f1", "problem": "", "corrected": ""}]}


def answer_when_asked(job, answer, model="claude-opus-5-5", file="answer.json"):
    """The operator's side, on a thread: wait for the call, write the answer to its folder, save it."""
    said = []

    def go():
        for _ in range(500):
            calls = session.pending(job)
            if calls:
                (calls[0] / file).write_text(answer if isinstance(answer, str) else json.dumps(answer))
                said.extend(session.save(job, model))
                return
            time.sleep(0.01)

    thread = threading.Thread(target=go)
    thread.start()
    return thread, said


def test_a_call_the_api_fails_waits_for_the_session_and_is_billed_to_its_plan(quick, monkeypatch):
    job = quick / "q1"
    job.mkdir()
    monkeypatch.setattr(brain, "ask", failing)
    ask = operator_run.logged_ask(job, session_answers=True)
    thread, said = answer_when_asked(job, CHECK)
    with ledger.at_step("factcheck"):
        got = ask("brain", '{"lines": []}', FactCheck, system="Check each line.", label="factcheck round 1")
    thread.join()
    assert got == FactCheck.model_validate(CHECK).model_dump()
    assert said[0].startswith("SAVED factcheck-round-1-") and "by claude-opus-5-5" in said[0]
    [row] = ledger.read(job)
    assert (row["label"], row["model"], row["via"], row["cost_usd"], row["step"]) == (
        "factcheck round 1", "claude-opus-5-5", "session", 0.0, "factcheck")
    assert row["input_tokens"] > 0 and row["api_usd"] > 0  # estimated, and priced as the API would have
    assert not session.pending(job) and (job / "session" / "BRIEF.md").exists()
    # On the bill, a line of its own, at nothing: the API key paid for none of it.
    assert ledger._models(ledger.read(job)) == [
        {"model": "claude-opus-5-5", "calls": 1, "costUsd": 0.0, "credits": None, "via": "session",
         "task": "fact-checks and fixes every line"}]
    assert "1 call(s) answered in session by claude-opus-5-5" in session.summary(ledger.read(job))
    # The same call asked again on a later run, failing on the API again, takes the saved answer at once.
    with brain.collect():
        assert ask("brain", '{"lines": []}', FactCheck, system="Check each line.", label="factcheck round 1") == got


def test_without_an_operator_waiting_or_with_the_knob_off_the_call_fails_as_before(quick, monkeypatch):
    job = quick / "q1"
    job.mkdir()
    monkeypatch.setattr(brain, "ask", failing)
    with pytest.raises(brain.BrainError):
        operator_run.logged_ask(job)("brain", "{}", FactCheck, label="factcheck round 1")
    knobs.use({"knobs": {"STUDIO_SESSION_FALLBACK": "off"}})
    with pytest.raises(brain.BrainError):
        operator_run.logged_ask(job, session_answers=True)("brain", "{}", FactCheck, label="factcheck round 1")
    assert not session.pending(job)


def test_the_admin_can_send_a_claude_topic_to_the_session_before_the_api(quick, monkeypatch):
    # The owner (Nov 4): when Claude is the brain, pick whether it is answered on the key or in the teacher's session.
    job = quick / "q1"
    job.mkdir()
    asked = []
    monkeypatch.setattr(brain, "ask", lambda *a, **k: asked.append(a[0]) or CHECK)
    knobs.use({"knobs": {"STUDIO_CLAUDE_VIA": "session", "STUDIO_SESSION_FALLBACK": "off"}})
    ask = operator_run.logged_ask(job, session_answers=True)
    for role in ("brain", "checker"):  # every call of a Claude topic, never tried on the API
        thread, said = answer_when_asked(job, CHECK)
        with ledger.at_step("factcheck"):
            assert ask(role, f'{{"{role}": 1}}', FactCheck, label=f"factcheck {role}") == FactCheck.model_validate(
                CHECK).model_dump()
        thread.join()
        assert said[0].startswith("SAVED factcheck-")
    assert asked == [] and [r["via"] for r in ledger.read(job)] == ["session", "session"]
    assert "STUDIO_CLAUDE_VIA is session" in json.dumps(
        [json.loads(p.read_text()) for p in (job / "session").rglob("request.json")])
    # The brain needs no key of its own then, even with the fallback off.
    assert session.answers()
    # A Muse topic is unchanged: its calls go to the API, Claude's checks beside them too.
    with brain.chosen("muse-spark"):
        assert not session.answers() and not session.first("checker")
        assert ask("checker", "{}", FactCheck, label="art check") == CHECK and asked == ["checker"]
    # So is a run nobody waits on, and a Claude topic with the knob at api.
    assert operator_run.logged_ask(job)("brain", "{}", FactCheck, label="x") == CHECK
    knobs.use({"knobs": {"STUDIO_CLAUDE_VIA": "api"}})
    assert ask("brain", "{}", FactCheck, label="y") == CHECK and asked == ["checker", "brain", "brain"]


def test_an_answer_that_does_not_fit_is_handed_back_with_what_to_fix(quick, monkeypatch):
    job = quick / "q1"
    job.mkdir()
    monkeypatch.setattr(brain, "ask", failing)
    result = {}
    thread = threading.Thread(target=lambda: result.update(got=operator_run.logged_ask(job, session_answers=True)(
        "brain", "{}", FactCheck, label="factcheck round 1")))
    thread.start()
    while not session.pending(job):
        time.sleep(0.01)
    [call] = session.pending(job)
    (call / "answer.json").write_text('{"checks": [{"id": "n1"}]}')
    [line] = session.save(job, "claude-opus-5-5")
    assert line.startswith("NOT VALID") and "verdict" in line
    assert session.pending(job) == [call]  # still waiting
    (call / "answer.json").write_text(json.dumps(CHECK))
    assert session.save(job, "claude-opus-5-5")[0].startswith("SAVED")
    thread.join()
    assert result["got"]["checks"][0]["verdict"] == "SUPPORTED"


def test_a_text_call_takes_the_text_and_a_model_must_be_named(quick, monkeypatch):
    job = quick / "q1"
    job.mkdir()
    monkeypatch.setattr(brain, "ask", failing)
    thread, said = answer_when_asked(job, "<svg></svg>", file="answer.txt")
    assert operator_run.logged_ask(job, session_answers=True)("drawer", "Draw it.", None, label="art hook") == {
        "text": "<svg></svg>"}
    thread.join()
    with pytest.raises(ValueError):
        session.save(job, "claude opus; rm -rf")


def test_a_declined_call_fails_as_a_refusal_and_one_never_answered_as_no_answer(quick, monkeypatch):
    job = quick / "q1"
    job.mkdir()
    monkeypatch.setattr(brain, "ask", failing)
    ask = operator_run.logged_ask(job, session_answers=True)

    def decline():
        while not session.pending(job):
            time.sleep(0.01)
        assert session.decline(job, session.pending(job)[0].name, "not a lesson").startswith("DECLINED")

    thread = threading.Thread(target=decline)
    thread.start()
    with pytest.raises(brain.Refused):
        ask("brain", "{}", FactCheck, label="factcheck round 1")
    thread.join()
    monkeypatch.setattr(session, "WAIT_S", 0.2)
    with pytest.raises(session.NoAnswer) as e:
        ask("brain", "{}", FactCheck, label="factcheck round 2")
    assert operator_run.failure_code(e.value) == "the session did not answer"


def test_wait_says_ask_while_calls_wait_for_the_session(quick):
    job = quick / "q1"
    job.mkdir()
    jobs.write(job / operator_run.BACKGROUND, {"pid": os.getpid(), "runs": 0})  # "alive": this very process
    jobs.write(job / "run.current.json", {"started": "", "step": "bundle", "steps": []})
    call = job / "session" / "pending" / "bundle-0123456789abcdef"
    call.mkdir(parents=True)
    (call / "request.json").write_text(json.dumps({"label": "bundle", "role": "brain", "schema": None, "images": 0}))
    result, step, reason = operator_run.wait("q1", minutes=0.05, poll_s=0.01)
    assert (result, step) == ("ASK", "bundle") and "python -m studio session list q1" in reason
    assert session.listing(job)[0].startswith(f"CALL {call} brain 'bundle'")


def test_a_run_waited_on_goes_on_without_the_brains_key_and_its_calls_go_to_the_session(quick, monkeypatch):
    for name in brain.CLAUDE_KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    api = FakeApi(studio={"brain": {"default": "claude", "effort": "high"}})
    # Without an operator waiting, a missing key stops the run before anything is claimed, as it always has.
    assert operator_run.run("q1", api=api)[:2] == ("STOPPED", "run")
    scripted = ScriptedBrain()
    monkeypatch.setattr(brain, "_ask_claude", lambda *a, **k: (_ for _ in ()).throw(brain.BrainError("needs a key")))
    answered = []

    def operator():
        job = quick / "q1"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (job / "posted.json").exists():
            for call in session.pending(job):
                req = session.request(call)
                schema = None
                if req["schema"]:
                    import importlib
                    module, name = req["schema"].split(":")
                    schema = getattr(importlib.import_module(module), name)
                data = scripted(req["role"], (call / "prompt.md").read_text(), schema, label=req["label"])
                # Every property present, as the strict schema asks (the samples leave out fields with defaults).
                full = schema.model_validate(data if isinstance(data, dict) else data.model_dump()).model_dump(mode="json")
                (call / "answer.json").write_text(json.dumps(full))
                answered.extend(session.save(job, "claude-opus-5-5"))
            time.sleep(0.02)

    def guarded():
        try:
            operator()
        except BaseException as e:  # the run would wait out WAIT_S: say why at once
            answered.append(f"operator failed: {type(e).__name__}: {e}")
            raise

    thread = threading.Thread(target=guarded)
    thread.start()
    result = operator_run.run("q1", api=api, session_answers=True)
    thread.join()
    assert result[0] == "DONE", (result, answered)
    rows = ledger.read(quick / "q1")
    assert rows and all(r["via"] == "session" and r["cost_usd"] == 0 for r in rows if r.get("model") != "unknown")
    assert api.posted["model"] == "claude-opus-5-5"  # the session's model wrote the lesson, as its bill says
    bill = api.posted["breakdown"]["steps"]
    assert any(m.get("via") == "session" for s in bill for m in s["models"])
