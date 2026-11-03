"""What the studio does about a failure an outage explains (studio/retry.py, docs/operator-failures.md)."""

import json
import threading
import time

import pytest

from studio import brain, jobs, media, retry, session
from studio import run as operator_run
from studio import videos
from studio.api import ApiError
from studio.tests.test_run import QUESTION, FakeApi, FakeBrain
from studio.tests.test_videos import VideoApi, posted


@pytest.fixture(autouse=True)
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    return tmp_path


def test_what_an_outage_explains_is_tried_again_and_left_to_a_later_sweep():
    for code in ("the API did not answer", "the API answered 503", "the API answered 429", "the model call failed",
                 "Gemini failed", "Meta's API failed", "the audio provider failed"):
        assert retry.outage(code) and retry.deferrable(code), code
    for code in ("the API answered 400", "the fact-check failed", "the fact audit failed", "spend ceiling",
                 "the model declined", "the picture was declined", "a studio error", "step failed", "", None):
        assert not retry.outage(code) and not retry.deferrable(code), code
    assert retry.deferrable("interrupted") and retry.deferrable("timed out") and not retry.outage("timed out")
    assert retry.waits("the song did not sing its lyrics") == (0.0,)  # made again at once, once


def test_the_waits_grow_and_scale(monkeypatch):
    monkeypatch.setenv("STUDIO_RETRY_SCALE", "1")
    assert retry.waits("Gemini failed") == retry.WAITS_S and retry.WAITS_S == tuple(sorted(retry.WAITS_S))
    monkeypatch.setenv("STUDIO_RETRY_SCALE", "0.5")
    assert retry.waits("Gemini failed")[0] == retry.WAITS_S[0] / 2
    assert retry.waits("the fact-check failed") == ()


class Blips(FakeBrain):
    """FakeBrain whose bundle call fails `times` times, as in a short outage."""

    def __init__(self, times, **kwargs):
        super().__init__(**kwargs)
        self.times = times

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        if label == "bundle" and self.times:
            self.times -= 1
            self.labels.append(label)
            raise brain.BrainError("claude-opus-5-5: Error code: 529 - overloaded_error")
        return super().__call__(role, prompt, schema, images, system=system, label=label)


def test_a_short_outage_is_waited_out_in_the_run(jobs_root):
    ask = Blips(2)
    assert operator_run.run("q1", api=FakeApi(), ask=ask)[0] == "DONE"
    [record] = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    tries = [(s["step"], s["result"]) for s in record["steps"] if s["step"] == "bundle"]
    assert tries == [("bundle", "FAILED"), ("bundle", "FAILED"), ("bundle", "DONE")]
    # One that outlasts the tries fails the run, as before, with the code `fail` reads.
    ask = Blips(9)
    result, step, _ = operator_run.run("q2", api=FakeApi({**QUESTION, "id": "q2"}), ask=ask)
    assert (result, step) == ("FAILED", "bundle")
    assert operator_run.read_jsonl(jobs_root / "q2" / "runs.jsonl")[-1]["code"] == "the model call failed"


def test_a_post_the_api_drops_for_a_while_is_sent_again_rather_than_thrown_away(jobs_root):
    api = FakeApi()
    real, calls = api.post_answer, []

    def flaky(question_id, body):
        calls.append(question_id)
        if len(calls) < 3:
            raise ApiError(503, "HTTP_ERROR", "the API answered 503")
        return real(question_id, body)

    api.post_answer = flaky
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("DONE", "post", "lesson posted as answer ans1")
    assert len(calls) == 3


def test_a_file_that_will_not_download_is_left_out_rather_than_failing_the_topic(jobs_root):
    api = FakeApi()
    real = api.download
    api.download = lambda path: (_ for _ in ()).throw(ApiError(502, "DOWNLOAD_FAILED", "502")) \
        if path.endswith("a1") else real(path)
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    request = jobs.read(jobs_root / "q1" / "request.json", operator_run.Request)
    assert request.images == [] and request.study_guide  # the photo left out, the text file kept


def lease(job, runs, most):
    jobs.write(job / operator_run.LEASE, {"runs": runs, "maxRuns": most})


def test_fail_leaves_an_outage_to_a_later_sweep_while_the_topic_has_runs_left(jobs_root):
    api = FakeApi()
    operator_run.run("q1", api=api, ask=Blips(9))
    job = jobs_root / "q1"
    lease(job, 1, 3)
    result, reason = operator_run.mark_failed("q1", api=api)
    assert result == "DEFERRED" and "2 run(s) left" in reason and "fail" not in api.names()
    lease(job, 3, 3)  # its last run: given up, as before
    assert operator_run.mark_failed("q1", api=api)[0] == "DONE" and "fail" in api.names()


def test_fail_gives_up_at_once_what_no_later_try_would_get_past(jobs_root):
    api = FakeApi()
    operator_run.run("q1", api=api, ask=FakeBrain(verdict="UNSUPPORTED"))
    lease(jobs_root / "q1", 1, 3)
    assert operator_run.mark_failed("q1", api=api)[0] == "DONE"


def test_every_claim_of_a_machines_run_carries_its_one_run_id_and_a_deferred_topic_a_new_one(jobs_root):
    """The API counts a run once per id, so a claim sent again (its answer lost, the step tried again, the operator's
    retry here) never counts a second run (the review of PR #116: one machine could use all 3)."""
    api = FakeApi(question={**QUESTION, "runs": 1, "maxRuns": 3})
    operator_run.run("q1", api=api, ask=Blips(9))
    operator_run.run("q1", api=api, ask=Blips(9))  # the operator's retry, on the same machine
    ids = set(api.run_ids)
    assert len(ids) == 1 and None not in ids and len(api.run_ids) > 3
    assert json.loads((jobs_root / "q1" / operator_run.LEASE).read_text()) == {"runs": 1, "maxRuns": 3}
    assert operator_run.mark_failed("q1", api=api)[0] == "DEFERRED"
    operator_run.run("q1", api=api, ask=FakeBrain())  # a later run here counts as a new one
    assert api.run_ids[-1] not in ids


def test_the_videos_try_an_outage_again_in_the_run_and_make_a_drifted_song_again(setup_videos):
    steps, api = setup_videos
    steps.fail = {"audio": 1}
    steps.codes = {"audio": "the audio provider failed"}
    posted()
    assert videos.videos("q1", api=api, ask=FakeBrain())[0] == "DONE"
    assert steps.ran.count("audio") == 2


def test_videos_given_up_for_an_outage_wait_for_a_later_attempt_while_one_is_left(setup_videos):
    steps, api = setup_videos
    steps.fail = {"audio": 99}
    steps.codes = {"audio": "the audio provider failed"}
    job = posted()
    assert videos.videos("q1", api=api, ask=FakeBrain())[:2] == ("FAILED", "audio")
    claimed = json.loads((job / videos.CLAIM).read_text())
    jobs.write(job / videos.CLAIM, {**claimed, "maxAttempts": 2})
    result, reason = operator_run.mark_failed("q1", api=api)
    assert result == "DEFERRED" and api.given_up is None
    jobs.write(job / videos.CLAIM, {**claimed, "maxAttempts": 1})  # no attempt left: given up, as before
    assert operator_run.mark_failed("q1", api=api)[0] == "DONE" and api.given_up is not None


@pytest.fixture
def setup_videos(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None, **_: None)

    class Steps:
        def __init__(self):
            self.ran, self.fail, self.codes = [], {}, {}

        def module(self, name):
            def run(job, ask=None):
                self.ran.append(name)
                if self.fail.get(name):
                    self.fail[name] -= 1
                    raise media.StepFailed(f"{name} broke", code=self.codes.get(name))
                (job / f".{name}.done").write_text("ok")
                return f"{name} made"

            return SimpleNamespace(run=run, is_done=lambda job: (job / f".{name}.done").exists())

    steps = Steps()
    monkeypatch.setattr(media, "step_module", steps.module)
    return steps, VideoApi()


def test_a_step_past_its_time_limit_ends_the_run_and_waiting_on_the_session_does_not_count(jobs_root, monkeypatch):
    monkeypatch.setattr(retry, "TICK_S", 0.02)
    monkeypatch.setenv("STUDIO_STEP_MINUTES", str(0.3 / 60))  # 0.3 seconds
    job = jobs_root / "q1"
    job.mkdir()
    ended = threading.Event()
    said = []
    start = time.monotonic()
    stop = retry.watch(job, lambda: {"bundle": start}, lambda step, why: (said.append((step, why)), ended.set()))
    assert ended.wait(5) and said[0][0] == "bundle" and "limit" in said[0][1]
    stop()
    # While a call waits for the session (studio/session.py), the clock stops.
    call = session.root(job) / "pending" / "bundle-0123"
    call.mkdir(parents=True)
    (call / "request.json").write_text(json.dumps({"pid": __import__("os").getpid()}))  # this process's call
    ended.clear()
    start = time.monotonic()
    stop = retry.watch(job, lambda: {"bundle": start}, lambda step, why: ended.set())
    assert not ended.wait(1.0)
    stop()


def test_a_run_past_its_time_limit_is_recorded_as_timed_out_and_its_process_ends(tmp_path):
    """The real end, in a process of its own (it ends the process it runs in)."""
    import subprocess
    import sys

    job = tmp_path / "q1"
    job.mkdir()
    jobs.write(job / "run.current.json", {"started": "2026-11-03T03:00:00+0000", "step": "bundle", "steps": []})
    code = ("import sys, time; from pathlib import Path; from studio import retry; "
            "retry.watch(Path(sys.argv[1]), lambda: {'bundle': time.monotonic() - 1}, "
            "lambda step, why: retry.end_process(Path(sys.argv[1]), step, why)); time.sleep(30)")
    done = subprocess.run([sys.executable, "-c", code, str(job)], capture_output=True, text=True, timeout=25,
                          env={**__import__("os").environ, "STUDIO_STEP_MINUTES": str(0.2 / 60), "STUDIO_RETRY_SCALE": "0.01"},
                          start_new_session=True)
    assert done.returncode != 0
    assert done.stdout.strip().splitlines()[-1].startswith("FAILED bundle: the bundle step ran past its")
    [record] = operator_run.read_jsonl(job / "runs.jsonl")
    assert (record["result"], record["step"], record["code"]) == ("FAILED", "bundle", "timed out")
    assert not (job / "run.current.json").exists()


def test_the_apis_own_503_is_a_setting_to_fix_not_an_outage_to_wait_out():
    """The API answers 503 UNAVAILABLE itself when its file storage is not set up: no wait or later sweep fixes that,
    so it is neither tried again in the run nor left to a later sweep (upload.py's _retrying reads it the same way).
    A proxy's 503, when the API is down, is an outage."""
    assert operator_run.failure_code(ApiError(503, "UNAVAILABLE", "File storage is not configured")) == \
        "the API is not set up for it"
    assert not retry.outage("the API is not set up for it") and not retry.deferrable("the API is not set up for it")
    assert retry.outage(operator_run.failure_code(ApiError(503, "HTTP_ERROR", "the API answered 503")))
    assert retry.outage(operator_run.failure_code(ApiError(503, "UNAVAILABLE", "busy", {"retryable": True})))


def test_the_time_limit_is_the_steps_across_its_tries(setup_videos):
    steps, api = setup_videos
    job = posted()
    record = videos.Record(job)
    record.begin("audio")
    first = record.running["audio"]
    record.end("audio", 1.0, "FAILED", "busy", "the audio provider failed")
    time.sleep(0.01)
    record.begin("audio")  # the next try
    assert record.watched() == {"audio": first}


def test_a_provider_that_took_too_long_is_left_to_a_later_sweep_not_asked_again_here():
    """The audio step, Gemini and Muse do not send a request again that timed out (it may still be made and charged
    for), so the run does not either; a later sweep may (the review of PR #116)."""
    from studio import gemini, muse
    from studio.steps import audio

    assert audio.TOOK_TOO_LONG == "the provider took too long"
    assert retry.waits("the provider took too long") == () and retry.deferrable("the provider took too long")
    assert retry.waits("the session did not answer") == () and retry.deferrable("the session did not answer")
    # A refusal of what was asked (a key refused, a model retired) is no outage either.
    assert gemini._code(403) == "Gemini refused the request" and gemini._code(503) is None
    assert muse._code(404) == "Meta's API refused the request" and muse._code(429) is None
    for code in ("Gemini refused the request", "Meta's API refused the request"):
        assert not retry.outage(code) and not retry.deferrable(code)
    assert gemini.GeminiError("x", code=gemini._code(503)).code == "Gemini failed"


def test_a_call_nobody_answered_leaves_no_folder_and_only_this_processs_calls_pause_the_limit(jobs_root, monkeypatch):
    import os as os_

    job = jobs_root / "q1"
    job.mkdir()
    monkeypatch.setattr(session, "WAIT_S", 0.1)
    monkeypatch.setattr(session, "POLL_S", 0.02)
    with pytest.raises(session.NoAnswer):
        session.ask(job, brain.BrainError("down"), "brain", "{}", None, label="factcheck round 1")
    assert session.pending(job) == []
    left = session.root(job) / "pending" / "left-by-another"
    left.mkdir(parents=True)
    (left / "request.json").write_text(json.dumps({"label": "x", "role": "brain", "schema": None, "images": 0,
                                                   "pid": os_.getpid() + 1}))
    assert session.pending(job) == [left] and session.pending(job, mine=True) == []


def test_a_run_recorded_by_the_time_limit_is_not_recorded_again_as_interrupted(jobs_root):
    job = jobs_root / "q1"
    job.mkdir()
    current = {"started": "2026-11-03T03:00:00+0000", "part": "videos", "step": "render", "steps": []}
    with (job / "runs.jsonl").open("a") as log:
        log.write(json.dumps({**current, "result": "FAILED", "code": "timed out", "reason": "x"}) + "\n")
    jobs.write(job / "run.current.json", current)  # written again in passing, after the time limit's record
    operator_run.record_interrupted(job)
    assert len(operator_run.read_jsonl(job / "runs.jsonl")) == 1 and not (job / "run.current.json").exists()


def test_once_the_run_ends_itself_the_time_limit_leaves_it_be(jobs_root, monkeypatch):
    job = jobs_root / "q1"
    job.mkdir()
    ended = []
    monkeypatch.setattr(retry.os, "_exit", lambda code: ended.append(code))
    retry.watch(job, lambda: {}, lambda step, why: None)()  # a new run in this process
    with retry.finishing():
        pass
    retry.end_process(job, "render", "past its limit")  # too late: the run recorded itself
    assert ended == [] and not (job / "runs.jsonl").exists()


def test_waiting_for_the_machines_turn_at_its_cores_stops_the_clock(jobs_root, monkeypatch):
    import fcntl

    monkeypatch.setattr(retry, "TICK_S", 0.02)
    monkeypatch.setenv("STUDIO_STEP_MINUTES", str(0.3 / 60))
    job = jobs_root / "q1"
    job.mkdir()
    other = open(jobs_root / media.CPU_TURN, "a")  # another job's render holds the turn
    fcntl.flock(other, fcntl.LOCK_EX)
    ended = threading.Event()
    start = time.monotonic()
    stop = retry.watch(job, lambda: {"render": start}, lambda step, why: ended.set())
    waiting = threading.Thread(target=lambda: media.cpu_turn(job).__enter__(), daemon=True)
    waiting.start()
    assert not ended.wait(1.0)  # three times the limit, all of it waiting for the turn
    fcntl.flock(other, fcntl.LOCK_UN)
    other.close()
    assert ended.wait(5)  # its turn came, and the step ran on past the limit
    stop()


def test_the_videos_claim_is_renewed_while_a_step_runs(setup_videos, monkeypatch):
    steps, api = setup_videos
    job = posted()
    jobs.write(job / videos.CLAIM, {"answerId": "ans1", "attempt": 1})
    api.attempt = 1
    monkeypatch.setattr(videos, "RENEW_S", 0.05)
    with videos.renewing(api, job):
        time.sleep(0.4)
    renewals = [c for c in api.calls if c[0] == "claim_videos"]
    assert len(renewals) >= 3 and all(c[2] == 1 for c in renewals)  # the attempt held, renewed
