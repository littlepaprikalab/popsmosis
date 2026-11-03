import json
import os
import time

import pytest

from studio import brain, jobs
from studio import run as operator_run
from studio.api import PROGRESS_TIMEOUT_S, Api, ApiError
from studio.schemas import Bundle, FactAudit, FactCheck, FactCheckReport, LessonBundle, Request
from studio.tests.sample import bundle_data, lesson_bundle_data
from studio.steps import bundle as bundle_step
from studio.steps import factcheck as factcheck_step
from studio.steps import script as script_step

NO_SCRIPT = bundle_step.NO_SCRIPT + script_step.narration_rules()  # the brief's tail with no script

QUESTION = {
    "id": "q1", "content": "ATP synthesis  for my\nAP Bio exam", "status": "PENDING", "answer": None,
    "attachments": [
        {"id": "a1", "name": "notes.png", "mimeType": "image/png", "url": "/api/attachments/a1", "expired": False},
        {"id": "a2", "name": "guide.txt", "mimeType": "text/plain", "url": "/api/attachments/a2", "expired": False},
        {"id": "a3", "name": "old.png", "mimeType": "image/png", "url": "/api/attachments/a3", "expired": True},
        {"id": "a4", "name": "slides.pdf", "mimeType": "application/pdf", "url": "/api/attachments/a4", "expired": False},
    ],
}


class FakeApi:
    """Stands in for studio.api.Api: records every call, answers from `errors` when one is set."""

    def __init__(self, question=QUESTION, studio=None):
        self.question = question
        self.studio = studio or {}  # the settings on /admin/studio, as GET /api/studio/settings gives them
        self.calls = []
        self.errors = {}  # method name -> ApiError to raise
        self.posted = None
        self.reports = []  # each progress report, as sent

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.errors:
            raise self.errors[name]

    def studio_settings(self):
        self._call("studio_settings")
        return self.studio

    def claim(self, question_id, new_run=False):
        self._call("claim", question_id)
        self.new_runs = getattr(self, "new_runs", 0) + bool(new_run)
        return {**self.question, **({"studio": self.studio} if self.studio else {})}

    def report_progress(self, question_id, progress):
        self._call("report_progress", question_id)
        self.reports.append(progress)

    def download(self, path):
        self._call("download", path)
        return {"/api/attachments/a1": b"\x89PNG fake", "/api/attachments/a2": "ATP is made by ATP synthase.".encode()}[path]

    def post_answer(self, question_id, body):
        self._call("post_answer", question_id)
        self.posted = body
        return {"id": "ans1", "questionId": question_id}

    def fail(self, question_id, reason):
        self._call("fail", question_id, reason)
        return {"id": question_id, "status": "FAILED", "failedReason": reason}

    def names(self):
        return [c[0] for c in self.calls]


class FakeBrain:
    """Stands in for brain.ask: a sample bundle, a fact audit that judges every fact as `fact` says, and a
    fact-check that supports every line (or none)."""

    def __init__(self, bundle_error=None, verdict="SUPPORTED", fact="CORRECT"):
        self.bundle_error = bundle_error
        self.verdict = verdict
        self.fact = fact
        self.labels = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.labels.append(label)
        if label == "script":  # no script: the bundle is written without one
            raise brain.BrainError("no answer")
        if label == "narration edit":  # handed back as it came
            return {"lines": [{"id": n["id"], "text": n["text"]} for n in json.loads(prompt)["narration"]]}
        if schema is Bundle:
            if self.bundle_error:
                raise self.bundle_error
            return bundle_data()
        if schema is LessonBundle:  # the lesson video alone (STUDIO_VIDEOS=lesson): no song
            return lesson_bundle_data([{"text": "Light splits water → O₂ + H⁺ + e⁻", "from_narration": "n1"}])
        if schema is FactAudit:
            return FactAudit.model_validate({"facts": [
                {"id": fact["id"], "basis": "course knowledge", "quote": "", "verdict": self.fact,
                 "problem": "" if self.fact == "CORRECT" else "wrong", "corrected": ""}
                for fact in json.loads(prompt)["facts"]]}).model_dump()
        lines = json.loads(prompt)["lines"]
        bad = self.verdict == "UNSUPPORTED"
        return FactCheck.model_validate({"checks": [
            {"id": line["id"], "verdict": self.verdict, "source": "" if bad else "f1",
             "problem": "wrong" if bad else "", "corrected": ""} for line in lines
        ]}).model_dump()


@pytest.fixture(autouse=True)
def jobs_root(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    return tmp_path


class ScriptedBrain(FakeBrain):
    """FakeBrain with a script, so the bundle step begins the fact audit beside the bundle call."""

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        if label == "script":
            self.labels.append(label)
            from studio.tests.test_script import script_data
            return script_data()
        return super().__call__(role, prompt, schema, images, system=system, label=label)


def test_an_audit_the_bundle_step_passed_is_not_billed_again(jobs_root):
    # Until Nov 2 every step after the bundle ran, the audit too, though the early audit had passed its facts.
    api, ask = FakeApi(), ScriptedBrain()
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.labels.count("fact audit") == 1 and ask.labels[-1] == "factcheck round 1"
    record = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")[-1]
    assert [s["step"] for s in record["steps"]] == ["request", "bundle", "factcheck", "post"]


def test_a_fresh_run_claims_the_topic_writes_the_lesson_and_posts_it(jobs_root):
    api, ask = FakeApi(), FakeBrain()
    (jobs_root / "q1").mkdir()
    (jobs_root / "q1" / "calls.jsonl").write_text(json.dumps({"label": "bundle", "model": "claude-opus-5-5"}) + "\n")

    assert operator_run.run("q1", api=api, ask=ask) == ("DONE", "post", "lesson posted as answer ans1")
    # Claimed again before every step but the post, so the lease outlasts a slow step; and each of those steps
    # tells the page it is running.
    assert api.names() == ["claim", "report_progress", "download", "download", "claim", "report_progress",
                           "claim", "report_progress", "claim", "report_progress", "post_answer"]
    assert ask.labels == ["script", "bundle", "narration edit", "fact audit", "factcheck round 1"]

    job = jobs_root / "q1"
    request = jobs.read(job / "request.json", Request)
    assert request.topic == "ATP synthesis for my AP Bio exam"
    assert request.images == [str((job / "attachments" / "a1.png").resolve())]
    assert (job / "attachments" / "a1.png").read_bytes() == b"\x89PNG fake"
    assert request.study_guide == "guide.txt:\nATP is made by ATP synthase."

    bundle = json.loads((job / "bundle.json").read_text())
    breakdown = api.posted.pop("breakdown")
    assert api.posted == {"content": bundle["lesson_markdown"], "bundle": bundle,
                          "factcheck": json.loads((job / "factcheck.json").read_text()), "model": "claude-opus-5-5",
                          # What the code checks found, for an admin's review (studio/review.py).
                          "checks": json.loads((job / "checks.json").read_text())}
    # The steps before the post, each timed; the call logged before this run is the bundle's.
    assert [s["step"] for s in breakdown["steps"]] == ["request", "bundle", "audit", "factcheck"]
    assert breakdown["steps"][1]["models"] == [{"model": "claude-opus-5-5", "calls": 1, "costUsd": None, "credits": None}]
    assert all(s["tries"] == 1 and s["seconds"] is not None for s in breakdown["steps"])
    assert json.loads((job / "posted.json").read_text())["id"] == "ans1"
    [record] = operator_run.read_jsonl(job / "runs.jsonl")
    assert (record["result"], record["step"]) == ("DONE", "post")
    assert [s["step"] for s in record["steps"]] == ["request", "bundle", "audit", "factcheck", "post"]


def test_a_topic_past_its_spend_ceiling_fails_and_its_retry_spends_nothing(jobs_root):
    """The ceiling is the admin's, from the claim: the lesson's calls stop once the topic has cost it, the step
    fails with the reason, and the retry fails the same way at once, so the topic is given up."""
    api, ask = FakeApi(studio={"brain": {"default": "claude", "effort": "high"}, "spend": {"topicUsd": 0.5}}), FakeBrain()
    job = jobs_root / "q1"
    job.mkdir()
    (job / "calls.jsonl").write_text(json.dumps({"label": "bundle", "model": "claude-opus-5-5", "step": "bundle",
                                                 "cost_usd": 0.55}) + "\n")  # an earlier try, already past it
    result, step, reason = operator_run.run("q1", api=api, ask=operator_run.logged_ask(job))
    assert (result, step) == ("FAILED", "bundle")
    assert reason.startswith("the topic has cost $0.55, past the $0.50 a topic may cost (/admin/studio)")
    assert not (job / "bundle.json").exists()
    assert operator_run.run("q1", api=api, ask=operator_run.logged_ask(job))[:2] == ("FAILED", "bundle")
    assert [r["result"] for r in operator_run.read_jsonl(job / "runs.jsonl")] == ["FAILED", "FAILED"]
    assert operator_run.mark_failed("q1", api=api)[0] == "DONE"


def test_a_photo_too_big_for_the_brain_is_left_out_rather_than_failing_every_try(jobs_root, monkeypatch):
    monkeypatch.setattr(operator_run, "IMAGE_MAX_BYTES", 4)
    assert operator_run.run("q1", api=FakeApi(), ask=FakeBrain())[0] == "DONE"
    request = jobs.read(jobs_root / "q1" / "request.json", Request)
    assert request.images == [] and request.study_guide  # the text file still came through


def test_a_style_line_in_the_question_is_the_songs_style(jobs_root):
    question = {**QUESTION, "attachments": [],
                "content": "ATP synthesis for AP Bio\n  style: K-pop with hip-hop verses,\t98 bpm  "}
    assert operator_run.run("q1", api=FakeApi(question), ask=FakeBrain())[0] == "DONE"
    request = jobs.read(jobs_root / "q1" / "request.json", Request)
    assert (request.topic, request.song_style) == ("ATP synthesis for AP Bio", "K-pop with hip-hop verses, 98 bpm")


@pytest.mark.parametrize("content, split", [
    ("Song style: pop-punk, 150 bpm\nmitosis", ("mitosis", "pop-punk, 150 bpm")),
    ("ATP synthesis  for my\nAP Bio exam", ("ATP synthesis for my AP Bio exam", "")),
    ("English 10\nWriting style: active and passive voice", ("English 10 Writing style: active and passive voice", "")),
    ("Style: Romanticism versus Realism", ("Style: Romanticism versus Realism", "")),  # nothing else to teach
])
def test_only_a_line_that_starts_with_style_names_the_songs_style(content, split):
    assert operator_run.split_style(content) == split


def test_each_step_tells_the_page_it_is_running_with_the_bill_so_far_and_whether_videos_follow(jobs_root, monkeypatch):
    from studio import videos

    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None, **_: None)
    api = FakeApi()
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    assert [[r["step"] for r in report["running"]] for report in api.reports] == [
        ["request"], ["bundle"], ["audit"], ["factcheck"]]
    assert all(r["running"][0]["seconds"] < 5 and r["failed"] == [] and r["videos"] is True for r in api.reports)
    # The bill so far is the steps already done, never the one just starting.
    assert "breakdown" not in api.reports[0] or api.reports[0]["breakdown"]["steps"] == []
    assert [s["step"] for s in api.reports[2]["breakdown"]["steps"]] == ["request", "bundle"]

    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None, **_: "the videos are turned off here (STUDIO_VIDEOS)")
    api = FakeApi()
    operator_run.run("q2", api=api, ask=FakeBrain())
    assert api.reports and all(r["videos"] is False for r in api.reports)  # a text-only droplet: the page need not wait


def test_a_post_that_fails_tells_the_page_rather_than_leave_the_fact_check_running(jobs_root):
    api = FakeApi()
    api.errors["post_answer"] = ApiError(503, "UNAVAILABLE", "try again")
    assert operator_run.run("q1", api=api, ask=FakeBrain())[:2] == ("FAILED", "post")
    last = api.reports[-1]
    assert last["running"] == [] and last["failed"] == ["post"]


def test_a_failed_step_tells_the_page_with_the_try_it_cost(jobs_root):
    api = FakeApi()
    assert operator_run.run("q1", api=api, ask=FakeBrain(bundle_error=brain.Truncated("hit max_tokens")))[0] == "FAILED"
    last = api.reports[-1]
    assert last["running"] == [] and last["failed"] == ["bundle"]
    assert [(s["step"], s["tries"]) for s in last["breakdown"]["steps"]] == [("request", 1), ("bundle", 1)]
    # The retry runs it again, so the page says it is running, not failed; and it counts both tries.
    api.reports.clear()
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    assert api.reports[0]["running"][0]["step"] == "bundle" and api.reports[0]["failed"] == []
    bundle = next(s for s in api.posted["breakdown"]["steps"] if s["step"] == "bundle")
    assert bundle["tries"] == 2


def test_a_report_the_api_refuses_never_changes_the_runs_outcome(jobs_root, capsys):
    api = FakeApi()
    api.errors["report_progress"] = ApiError(0, "UNREACHABLE", "the API did not answer")
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("DONE", "post", "lesson posted as answer ans1")
    assert "progress not reported: ApiError: the API did not answer" in capsys.readouterr().err
    # Tried once: an API that does not take them costs the run one timeout a minute, not one a step.
    assert api.names().count("report_progress") == 1


def test_running_again_resumes_at_the_step_that_failed(jobs_root):
    api = FakeApi()
    first = operator_run.run("q1", api=api, ask=FakeBrain(bundle_error=brain.Truncated("hit max_tokens")))
    assert first == ("FAILED", "bundle", "hit max_tokens")

    api.calls.clear()
    ask = FakeBrain()
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert "download" not in api.names()  # request.json was already written
    assert ask.labels == ["script", "bundle", "narration edit", "fact audit", "factcheck round 1"]
    runs = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    assert [(r["result"], r["step"]) for r in runs] == [("FAILED", "bundle"), ("DONE", "post")]

    # Done is done: a third run makes no calls, and the record does not count it.
    api.calls.clear()
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("DONE", "post", "the lesson was already posted")
    assert api.calls == []
    assert len(operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")) == 2


def test_a_lesson_the_fact_check_refused_is_written_again_on_the_retry_and_nothing_is_posted(jobs_root):
    """Until Nov 3 the retry checked the same bundle again, and a lesson refused once was refused again: the topic
    was given up (docs/operator-failures.md). The refused one is kept beside it, for a look."""
    api = FakeApi()
    result, step, reason = operator_run.run("q1", api=api, ask=FakeBrain(verdict="UNSUPPORTED"))
    assert (result, step) == ("FAILED", "factcheck")
    assert reason.endswith("is unsupported and has no correction: wrong")
    assert "post_answer" not in api.names()
    assert not (jobs_root / "q1" / "bundle.json").exists() and (jobs_root / "q1" / "bundle.rejected-1.json").exists()

    ask = FakeBrain()
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.labels == ["script", "bundle", "narration edit", "fact audit", "factcheck round 1"]


def test_a_new_bundle_runs_every_step_after_it(jobs_root):
    operator_run.run("q1", api=FakeApi(), ask=FakeBrain(verdict="UNSUPPORTED"))
    assert not (jobs_root / "q1" / "bundle.json").exists()  # set aside: the audit.json and factcheck.json are stale
    ask = FakeBrain()
    assert operator_run.run("q1", api=FakeApi(), ask=ask)[0] == "DONE"
    assert ask.labels == ["script", "bundle", "narration edit", "fact audit", "factcheck round 1"]


@pytest.mark.parametrize("error, reason", [
    (ApiError(404, "NOT_FOUND", "Question not found"), "claim: the topic was deleted"),
    (ApiError(409, "CONFLICT", "Another agent holds the claim on this question"),
     "claim: Another agent holds the claim on this question"),
    (ApiError(403, "FORBIDDEN", "Agent is disabled"), "claim: the API refused the key: 403 FORBIDDEN: Agent is disabled"),
])
def test_a_topic_that_is_gone_or_taken_stops_the_run_without_a_retry(error, reason):
    api = FakeApi()
    api.errors["claim"] = error
    ask = FakeBrain()
    assert operator_run.run("q1", api=api, ask=ask) == ("STOPPED", "request", reason)
    assert ask.labels == []


def test_a_server_error_fails_the_step_so_it_is_retried():
    api = FakeApi()
    api.errors["post_answer"] = ApiError(0, "UNREACHABLE", "the API did not answer: timed out")
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("FAILED", "post", "the API did not answer: timed out")
    del api.errors["post_answer"]
    assert operator_run.run("q1", api=api, ask=FakeBrain())[:2] == ("DONE", "post")


def test_a_lesson_someone_else_posted_stops_at_the_post():
    api = FakeApi()
    api.errors["post_answer"] = ApiError(409, "CONFLICT", "Question already has an answer")
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("STOPPED", "post", "Question already has an answer")


def test_a_bug_still_ends_the_run_with_its_one_line(jobs_root, capsys):
    api = FakeApi(question={"id": "q1", "attachments": []})  # no content
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("FAILED", "request", "KeyError: 'content'")
    assert "Traceback" in capsys.readouterr().err


def test_fail_gives_the_topic_up_with_the_last_runs_reason():
    api = FakeApi()
    assert operator_run.mark_failed("q1", api=api) == ("STOPPED", "there is no job for this topic here")
    operator_run.run("q1", api=api, ask=FakeBrain(verdict="UNSUPPORTED"))
    operator_run.run("q1", api=api, ask=FakeBrain())  # the retry passes: nothing to give up
    assert operator_run.mark_failed("q2", api=api)[0] == "STOPPED"
    operator_run.run("q2", api=api, ask=FakeBrain(bundle_error=brain.Refused("claude-opus-5-5", "bio")))
    result, reason = operator_run.mark_failed("q2", api=api)
    assert (result, reason) == ("DONE", "bundle failed: claude-opus-5-5 declined the request (bio)")
    assert api.calls[-1] == ("fail", "q2", reason)


def test_fail_never_gives_up_a_posted_lesson():
    api = FakeApi()
    operator_run.run("q1", api=api, ask=FakeBrain())
    assert operator_run.mark_failed("q1", api=api)[0] == "STOPPED"
    assert "fail" not in api.names()


def test_the_summary_names_the_topic_by_the_bundle_never_the_learners_words(jobs_root):
    api = FakeApi()
    operator_run.run("q1", api=api, ask=FakeBrain(bundle_error=brain.BrainError("overloaded")))
    (jobs_root / "q1" / "calls.jsonl").write_text(
        json.dumps({"label": "bundle", "model": "claude-opus-5-5", "cost_usd": 0.5}) + "\n"
        + json.dumps({"label": "factcheck round 1", "model": "claude-opus-5-5", "cost_usd": 0.2}) + "\n")
    operator_run.run("q1", api=api, ask=FakeBrain())
    text = operator_run.summary("q1")
    assert text.startswith("job q1: AP Biology / the light reactions. 2 run(s): run 1 FAILED at bundle "
                           "(the model call failed); run 2 DONE at post.")
    assert "Fact-check passed in 1 round(s), 0 correction(s). Lesson posted. 2 model call(s), $0.70." in text
    assert "ATP synthesis" not in text and "exam" not in text
    assert len(text) <= operator_run.CYCLE_LOG_MAX


def test_the_summary_never_quotes_a_rejected_line_or_what_a_step_found(jobs_root):
    """A failed fact-check's reason quotes the lines it rejected, which come from what the learner sent; the summary
    goes on Inkmory, so it says the step and a fixed code, and a step's line without what it quotes."""
    api = FakeApi()
    assert operator_run.run("q1", api=api, ask=FakeBrain(verdict="UNSUPPORTED"))[:2] == ("FAILED", "factcheck")
    [record] = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    assert " is unsupported" in record["reason"] and record["code"] == "the fact-check failed"
    text = operator_run.summary("q1")  # the message stays in runs.jsonl, for the operator; never in the summary
    assert "run 1 FAILED at factcheck (the fact-check failed)" in text and "is unsupported" not in text

    record["steps"].append({"step": "art", "seconds": 1.0, "result": "DONE",
                            "detail": "painted 3 scenes; 1 picture refused: hook: writing in it ('Ben Okafor', 'P3') "
                                      "and Broca's area was \"Ben's notes\""})
    (jobs_root / "q1" / "runs.jsonl").write_text(json.dumps(record) + "\n")
    inside = operator_run.summary("q1").split("\n\n")[1]
    assert "Ben" not in inside and "P3" not in inside
    assert "writing in it …" in inside and "Broca's area" in inside  # an apostrophe is not a quote
    # A quote the cut left open, and a provider's own words, go too.
    assert operator_run.unquoted("s2: writing in it ('Mrs Patel period 4…', 'Ben Okafor notes page 3 photosynthe…") == (
        "s2: writing in it …")
    assert operator_run.unquoted("hook: the label says 'Ben Oka…") == "hook: the label says …"
    assert operator_run.unquoted("1 picture refused: hook: Muse Image declined the picture: a chloroplast for Ben; "
                                 "overview: students' notes") == (
        "1 picture refused: hook: Muse Image declined the picture; overview: students' notes")


def test_every_run_that_does_not_end_done_keeps_a_fixed_code(jobs_root):
    api = FakeApi()
    api.errors["post_answer"] = ApiError(503, "UNAVAILABLE", "down")
    operator_run.run("q1", api=api, ask=FakeBrain())
    api.errors["post_answer"] = ApiError(409, "CONFLICT", "answered")
    operator_run.run("q1", api=api, ask=FakeBrain())
    assert [r.get("code") for r in operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")] == [
        "the API answered 503", "the topic was answered, given up or taken"]
    assert operator_run.failure_code(ApiError(0, "", "no answer")) == "the API did not answer"
    assert operator_run.failure_code(brain.Truncated("x")) == "the answer was cut off"
    assert operator_run.failure_code(ValueError("x")) == "a studio error"


def test_a_reason_is_one_short_line():
    assert operator_run.one_line("a\n  b\tc") == "a b c"
    assert len(operator_run.one_line("x" * 1000)) == operator_run.REASON_MAX


def test_only_a_question_id_names_a_job():
    with pytest.raises(ValueError):
        operator_run.job_dir("../etc")


# --- the seams: kills, overlaps, lost replies ---------------------------------------


def test_a_step_file_cut_short_counts_as_not_done(jobs_root):
    api = FakeApi()
    api.errors["post_answer"] = ApiError(400, "VALIDATION_ERROR", "refused")  # the lesson is made, and not posted
    operator_run.run("q1", api=api, ask=FakeBrain())
    (jobs_root / "q1" / "factcheck.json").write_text('{"status": "pass')  # written by an older, non-atomic studio
    ask = FakeBrain()
    assert operator_run.run("q1", api=FakeApi(), ask=ask)[0] == "DONE"
    assert ask.labels == ["factcheck round 1"]


def test_a_job_file_is_written_whole_or_not_at_all(jobs_root, monkeypatch):
    path = jobs_root / "x.json"
    jobs.write(path, {"a": 1})

    def crash(*args, **kwargs):
        raise OSError("killed mid-write")

    monkeypatch.setattr(operator_run.jobs.os, "replace", crash)
    with pytest.raises(OSError):
        jobs.write(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 1}


def test_a_run_killed_mid_step_is_recorded_as_that_step_failing_so_fail_can_give_it_up(jobs_root):
    api = FakeApi()
    operator_run.run("q1", api=api, ask=FakeBrain(verdict="UNSUPPORTED"))
    # What a killed run leaves: its current step, and no line in runs.jsonl.
    (jobs_root / "q1" / "run.current.json").write_text(json.dumps(
        {"started": "2026-10-27T03:00:00+0000", "step": "factcheck", "steps": []}))
    result, reason = operator_run.mark_failed("q1", api=api)
    assert result == "DONE" and reason.startswith("factcheck failed: factcheck was interrupted")
    runs = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    assert [(r["result"], r["step"]) for r in runs] == [("FAILED", "factcheck"), ("FAILED", "factcheck")]
    assert not (jobs_root / "q1" / "run.current.json").exists()


def test_show_prints_a_run_that_was_cut_off(jobs_root, capsys):
    from studio.__main__ import main

    operator_run.run("q1", api=FakeApi(), ask=FakeBrain(verdict="UNSUPPORTED"))
    (jobs_root / "q1" / "run.current.json").write_text(json.dumps({"started": "t", "step": "factcheck", "steps": []}))
    operator_run.summary("q1")  # records the interrupted run
    assert main(["show", "q1", "--brief"]) == 0
    assert "factcheck cut off" in capsys.readouterr().out


def test_show_prints_the_software_each_step_ran_with_its_version(jobs_root, capsys, monkeypatch):
    from studio import ledger
    from studio.__main__ import main

    monkeypatch.setattr(ledger, "version", {"skia-python": "144.1"}.get)
    operator_run.run("q1", api=FakeApi(), ask=FakeBrain())
    (jobs_root / "q1" / "run.current.json").write_text(json.dumps(
        {"started": "t", "part": "videos", "step": "render", "steps": [{"step": "render", "seconds": 157.0}]}))
    assert main(["show", "q1", "--brief"]) == 0
    [render] = [line for line in capsys.readouterr().out.splitlines() if line.strip().startswith("render")]
    assert render.endswith("code; with skia-python 144.1, ffmpeg (version n/a)")


def test_a_finished_run_leaves_no_current_run_behind(jobs_root):
    operator_run.run("q1", api=FakeApi(), ask=FakeBrain(bundle_error=brain.BrainError("overloaded")))
    assert not (jobs_root / "q1" / "run.current.json").exists()


def test_a_second_run_of_a_job_stops_while_the_first_still_holds_it(jobs_root):
    (jobs_root / "q1").mkdir()
    api, ask = FakeApi(), FakeBrain()
    with operator_run.only_run(jobs_root / "q1") as mine:
        assert mine
        assert operator_run.run("q1", api=api, ask=ask)[0] == "STOPPED"
        assert operator_run.mark_failed("q1", api=api) == ("STOPPED", "a run of this topic is still going")
        assert "A run is still going." in operator_run.summary("q1")
    assert api.calls == [] and ask.labels == []
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"  # the lock goes with its holder


def test_a_study_guide_past_its_limit_is_cut_so_one_topic_cannot_multiply_every_call(jobs_root, monkeypatch):
    monkeypatch.setattr(operator_run, "STUDY_GUIDE_MAX_CHARS", 20)
    assert operator_run.run("q1", api=FakeApi(), ask=FakeBrain())[0] == "DONE"
    request = jobs.read(jobs_root / "q1" / "request.json", Request)
    assert request.study_guide == "guide.txt:\nATP is made by AT"[:20]


def test_attachments_are_fetched_by_their_id_not_the_url_the_api_sent(jobs_root):
    question = {**QUESTION, "attachments": [
        {"id": "a1", "name": "notes.png", "mimeType": "image/png", "url": "@evil.example/steal", "expired": False},
    ]}
    api = FakeApi(question)
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    assert ("download", "/api/attachments/a1") in api.calls


def test_an_attachment_purged_before_the_download_is_left_out(jobs_root):
    api = FakeApi()
    real = api.download

    def download(path):
        if path == "/api/attachments/a1":
            raise ApiError(410, "DOWNLOAD_FAILED", f"GET {path} answered 410")
        return real(path)

    api.download = download
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    request = jobs.read(jobs_root / "q1" / "request.json", Request)
    assert request.images == [] and request.study_guide


def test_a_lesson_posted_whose_reply_was_lost_is_recovered_from_the_409(jobs_root):
    api = FakeApi()
    api.errors["post_answer"] = ApiError(409, "CONFLICT", "Question already has an answer", {"answerId": "ans1"})
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("DONE", "post", "lesson was already posted as answer ans1")
    assert json.loads((jobs_root / "q1" / "posted.json").read_text())["id"] == "ans1"


# --- the API client -------------------------------------------------------------


def test_the_client_sends_the_key_and_json_and_raises_the_apis_error():
    sent, timeouts = [], []

    def send(method, url, headers, body, timeout=None):
        sent.append((method, url, headers, body))
        timeouts.append(timeout)
        if url.endswith("/fail"):
            return 409, b'{"error":{"code":"CONFLICT","message":"Question already has an answer"}}'
        return 200, b'{"id":"q1"}'

    api = Api("http://localhost:3001", "sk_pop_x", send=send)
    assert api.claim("q1") == {"id": "q1"}
    api.report_progress("q1", {"running": [{"step": "bundle", "seconds": 0.0}], "videos": True})
    api.report_video_progress("ans1", 2, {"running": [], "failed": ["art"]})
    progress, video_progress = sent.pop(1), sent.pop(1)
    # The claim waits as long as any call; a report for the page gives up long before it could hold a step up.
    assert timeouts[:3] == [None, PROGRESS_TIMEOUT_S, PROGRESS_TIMEOUT_S] and PROGRESS_TIMEOUT_S <= 5
    assert progress[:2] == ("PUT", "http://localhost:3001/api/questions/q1/progress")
    assert json.loads(progress[3]) == {"running": [{"step": "bundle", "seconds": 0.0}], "videos": True}
    assert video_progress[:2] == ("PUT", "http://localhost:3001/api/answers/ans1/video/progress")
    assert json.loads(video_progress[3]) == {"running": [], "failed": ["art"], "attempt": 2}
    method, url, headers, body = sent[0]
    assert (method, url, body) == ("POST", "http://localhost:3001/api/questions/q1/claim", b"{}")
    assert headers["Authorization"] == "Bearer sk_pop_x" and headers["Content-Type"] == "application/json"
    with pytest.raises(ApiError) as caught:
        api.fail("q1", "bundle failed: x")
    assert (caught.value.status, caught.value.code, caught.value.message) == (409, "CONFLICT", "Question already has an answer")
    assert json.loads(sent[1][3]) == {"reason": "bundle failed: x"}


def test_the_client_keeps_an_errors_details_and_never_repeats_an_html_page():
    def send(method, url, headers, body):
        if url.endswith("/answer"):
            return 409, b'{"error":{"code":"CONFLICT","message":"Question already has an answer","answerId":"ans1"}}'
        return 502, b"<!DOCTYPE html><html><body>Bad gateway</body></html>"

    api = Api("http://localhost:3001", "sk_pop_x", send=send)
    with pytest.raises(ApiError) as caught:
        api.post_answer("q1", {})
    assert caught.value.details == {"answerId": "ans1"}
    with pytest.raises(ApiError) as caught:
        api.claim("q1")
    assert (caught.value.status, caught.value.message) == (502, "the API answered 502")


def test_the_client_needs_both_variables(monkeypatch):
    monkeypatch.delenv("POPSMOSIS_API_KEY", raising=False)
    monkeypatch.setenv("POPSMOSIS_API_URL", "http://localhost:3001/")
    with pytest.raises(ApiError) as caught:
        Api.from_env()
    assert caught.value.code == "NOT_CONFIGURED"
    monkeypatch.setenv("POPSMOSIS_API_KEY", "sk_pop_x")
    assert Api.from_env().base_url == "http://localhost:3001"


def test_the_default_sender_does_not_use_urllibs_blocked_user_agent(monkeypatch):
    from studio import api as api_module

    seen = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(api_module.urllib.request, "urlopen", lambda request, timeout: seen.append(request) or Response())
    api_module._urllib_send("GET", "http://x/api", {"Authorization": "Bearer k"}, None)
    assert seen[0].get_header("User-agent") == api_module.USER_AGENT


def test_a_default_brain_that_cannot_run_here_stops_the_run_before_the_topic_is_claimed(jobs_root, monkeypatch):
    """The default brain on /admin/studio needs a key this machine lacks: every topic would fail the same way, and
    the teacher gives a topic up after two failures. So the run reads the settings, stops before it claims, and
    nothing is spent."""
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    api = FakeApi(studio={"brain": {"default": "muse-spark", "effort": "high"}})
    result, step, reason = operator_run.run("q1", api=api)
    assert (result, step, reason) == ("STOPPED", "run", "the studio's settings are wrong: Muse Spark 1.3 needs MODEL_API_KEY")
    assert api.names() == ["studio_settings"] and not (jobs_root / "q1").exists()


def test_the_api_refusing_the_key_stops_the_run_before_anything_else(jobs_root):
    api = FakeApi()
    api.errors["studio_settings"] = ApiError(401, "UNAUTHORIZED", "Invalid agent API key")
    assert operator_run.run("q1", api=api)[:2] == ("STOPPED", "run")


# --- the brain: the default on /admin/studio, or a topic's own picked on /learn ----------------


class SpyBrain(FakeBrain):
    """FakeBrain that notes the provider and brain model in force at each call."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.brains = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.brains.append((brain.provider(), brain.brain_model()))
        return super().__call__(role, prompt, schema, images, system=system, label=label)


def test_a_topic_sent_to_its_own_brain_is_made_on_it_and_its_job_keeps_it(jobs_root):
    api = FakeApi({**QUESTION, "brain": "muse-spark"}, studio={"brain": {"default": "claude", "effort": "high"}})
    ask = SpyBrain()
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.brains == [("meta", "muse-spark-1.3")] * 5  # the script, the bundle, its edit, the audit, the check
    assert operator_run.job_brain(jobs_root / "q1") == "muse-spark"
    assert brain.chosen_name() is None  # nothing in force once the run ends


def test_a_retry_keeps_the_topics_brain(jobs_root):
    api = FakeApi({**QUESTION, "brain": "claude"}, studio={"brain": {"default": "muse-wonder"}})
    ask = SpyBrain(bundle_error=brain.BrainError("overloaded"))
    assert operator_run.run("q1", api=api, ask=ask)[:2] == ("FAILED", "bundle")
    ask.bundle_error = None
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert {provider for provider, _ in ask.brains} == {"claude"}


def test_a_topic_without_a_brain_of_its_own_is_made_on_the_default_at_its_effort(jobs_root):
    api = FakeApi({**QUESTION, "brain": None}, studio={"brain": {"default": "muse-wonder", "effort": "medium"}})
    ask = SpyBrain()
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.brains == [("meta", "muse-wonder-1.0")] * 5
    assert (operator_run.job_brain(jobs_root / "q1"), operator_run.job_effort(jobs_root / "q1")) == ("muse-wonder", "medium")


def test_with_no_settings_from_the_api_the_brain_is_claude_opus(jobs_root):
    api, ask = FakeApi(), SpyBrain()  # an API too old to send them
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.brains == [("claude", "claude-opus-5-5")] * 5


def test_a_topics_brain_the_studio_cannot_run_fails_the_topic_rather_than_holding_up_the_queue(jobs_root, monkeypatch):
    """The default brain is set up (Claude here), so the run starts; the topic asks for Muse Wonder, whose key is
    missing. That is this topic's problem, not every topic's: the step fails with the reason, before anything is
    downloaded or sent to a model, and `fail` gives the topic up with it, so the next topic gets its turn."""
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    api = FakeApi({**QUESTION, "brain": "muse-wonder"}, studio={"brain": {"default": "claude"}})
    result, step, reason = operator_run.run("q1", api=api)
    assert (result, step) == ("FAILED", "request")
    assert reason == "the studio cannot run this topic's brain here: Muse Wonder 1.0 needs MODEL_API_KEY"
    assert "download" not in api.names() and not (jobs_root / "q1" / "calls.jsonl").exists()
    assert operator_run.mark_failed("q1", api=api) == ("DONE", f"request failed: {reason}")


def test_a_default_changed_after_the_check_to_one_that_cannot_run_stops_rather_than_fails(jobs_root, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    api = FakeApi(studio={"brain": {"default": "claude"}})
    api.claim = lambda question_id, new_run=False: {**QUESTION, "studio": {"brain": {"default": "muse-spark"}}}  # changed meanwhile
    result, step, reason = operator_run.run("q1", api=api)
    assert (result, step, reason) == ("STOPPED", "request", "the studio's settings are wrong: Muse Spark 1.3 needs MODEL_API_KEY")


def test_the_variables_that_used_to_pick_the_brain_stop_the_run_until_removed(jobs_root, monkeypatch):
    """Ignored, they would let the brain change without anyone seeing why: the run stops, says what to do, and
    asks the API for nothing."""
    monkeypatch.setenv("BRAIN_PROVIDER", "meta")
    api = FakeApi()
    result, step, reason = operator_run.run("q1", api=api)
    assert (result, step) == ("STOPPED", "run")
    assert reason.startswith("the studio's settings are wrong: BRAIN_PROVIDER is still set, but the studio no longer reads it")
    assert api.calls == []


def test_an_api_that_cannot_be_reached_stops_the_run_rather_than_failing_the_topic(jobs_root):
    api = FakeApi()
    api.errors["studio_settings"] = ApiError(0, "UNREACHABLE", "the API did not answer: timed out")
    result, step, reason = operator_run.run("q1", api=api)
    assert (result, step) == ("STOPPED", "run") and "could not be read" in reason
    assert not (jobs_root / "q1").exists()  # nothing recorded that `fail` could give the topic up for


class DownForTheFactCheck(SpyBrain):
    """SpyBrain whose fact-check calls fail, as in an outage, while `down`."""

    down = True

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        if self.down and (label or "").startswith("factcheck"):
            self.brains.append((brain.provider(), brain.brain_model()))
            raise brain.BrainError("claude-opus-5-5: Error code: 529 - overloaded_error")
        return super().__call__(role, prompt, schema, images, system=system, label=label)


def test_a_lesson_begun_on_the_default_stays_on_it_when_the_default_changes(jobs_root):
    api = FakeApi(studio={"brain": {"default": "claude"}})
    ask = DownForTheFactCheck()  # the fact-check's calls fail, so a retry comes (a lesson the fact-check refuses
    assert operator_run.run("q1", api=api, ask=ask)[:2] == ("FAILED", "factcheck")  # is written again instead)
    api.studio = {"brain": {"default": "muse-spark"}}  # an admin changes the default meanwhile
    ask.down = False
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert {provider for provider, _ in ask.brains} == {"claude"}  # the bundle's brain checked it too
    assert operator_run.job_brain(jobs_root / "q1") == "claude"


def test_a_lesson_whose_brain_can_no_longer_run_here_goes_on_with_the_default(jobs_root, monkeypatch):
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    job = jobs_root / "q1"
    api = FakeApi(studio={"brain": {"default": "claude"}})
    operator_run.run("q1", api=api, ask=FakeBrain(verdict="UNSUPPORTED"))  # the bundle, written on Claude
    operator_run.keep_brain(job, "muse-wonder")  # as if a Muse had begun it, and this machine lacks Meta's key
    question = {**QUESTION, "studio": {"brain": {"default": "claude"}}}
    with brain.chosen(None):
        operator_run.use_brain(job, question, check=True)
        assert brain.chosen_name() == "claude"
    assert operator_run.job_brain(job) == "claude"


def test_a_brain_this_studio_does_not_have_stops_rather_than_gives_the_topic_up(jobs_root, monkeypatch):
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    api = FakeApi({**QUESTION, "brain": "muse-ultra"}, studio={"brain": {"default": "claude"}})
    result, step, reason = operator_run.run("q1", api=api)
    assert (result, step) == ("STOPPED", "request") and "update the studio" in reason


# --- the operator's note to the brain, and each step's line ---------------------------------


class Recording(FakeBrain):
    """FakeBrain, keeping each call's system prompt by its label."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.systems = {}

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.systems[label] = system
        return super().__call__(role, prompt, schema, images, system=system, label=label)


def test_the_operators_note_ends_the_bundle_prompt_and_never_reaches_the_fact_check(jobs_root):
    ask = Recording()
    note = "Pictures keep coming back\nwith text in them;   keep image prompts free of labels."
    assert operator_run.run("q1", api=FakeApi(), ask=ask, note=note)[0] == "DONE"
    clean = "Pictures keep coming back with text in them; keep image prompts free of labels."
    assert ask.systems["bundle"] == bundle_step.PROMPT + NO_SCRIPT + bundle_step.NOTE.format(note=clean)
    assert ask.systems["factcheck round 1"] == factcheck_step.PROMPT
    [record] = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    assert record["note"] == clean


def test_without_a_note_the_bundle_prompt_is_as_it_was(jobs_root):
    ask = Recording()
    assert operator_run.run("q1", api=FakeApi(), ask=ask, note="   ")[0] == "DONE"
    assert ask.systems["bundle"] == bundle_step.PROMPT + NO_SCRIPT  # the fake writes no script
    [record] = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    assert "note" not in record


def test_a_note_too_long_is_cut_never_the_reason_a_topic_fails(jobs_root, capsys):
    ask = Recording()
    assert operator_run.run("q1", api=FakeApi(), ask=ask, note="x" * 900)[0] == "DONE"
    assert "<operator_note>\n" + "x" * (operator_run.NOTE_MAX - 1) + "…\n</operator_note>" in ask.systems["bundle"]
    assert f"note: cut to {operator_run.NOTE_MAX} characters from 900" in capsys.readouterr().err


def test_the_command_line_hands_the_note_to_the_run(monkeypatch):
    from studio.__main__ import main

    got = {}
    monkeypatch.setattr(operator_run, "run", lambda question, note="", session_answers=False: got.update(
        question=question, note=note, session=session_answers) or ("DONE", "post", "lesson posted as answer ans1"))
    assert main(["run", "q1", "--note", "keep labels out of the image prompts"]) == 0
    assert got == {"question": "q1", "note": "keep labels out of the image prompts", "session": False}
    # The detached run of `run --background` is told an operator waits on it (studio/session.py).
    assert main(["run", "q1", "--session"]) == 0 and got["session"] is True


def test_each_step_keeps_the_line_it_ended_on_and_a_failed_one_its_reason(jobs_root):
    api = FakeApi()
    api.errors["post_answer"] = ApiError(503, "UNAVAILABLE", "down")
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "FAILED"
    [record] = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    assert [(s["step"], s.get("detail")) for s in record["steps"]] == [
        ("request", "1 image(s), 1 text file(s)"), ("bundle", "AP Biology / the light reactions"),
        ("audit", "3 fact(s) judged by course knowledge, in 1 call(s)"),
        ("factcheck", "passed in 1 round(s), 0 correction(s)")] + [("post", None)] * 4  # tried again 3 times
    assert record["reason"] == "503 UNAVAILABLE: down"
    assert [s.get("code") for s in record["steps"][-4:]] == ["the API answered 503"] * 4
    # No media step has run, so the summary is the one paragraph for the cycle log.
    assert "\n" not in operator_run.summary("q1")


# --- a long step, and the lesson in the background ---------------------------------------------


def slow_bundle(seconds, then=None):
    """A FakeBrain whose bundle call takes `seconds` (a bundle can take 20 minutes or more), calling `then` halfway."""
    brain_ = FakeBrain()

    def ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        if schema is Bundle:
            time.sleep(seconds / 2)
            if then:
                then()
            time.sleep(seconds / 2)
        return brain_(role, prompt, schema, images, system=system, label=label)
    return ask


def test_a_step_longer_than_the_lease_claims_its_topic_again_while_it_runs(jobs_root, monkeypatch):
    monkeypatch.setattr(operator_run, "RENEW_S", 0.05)
    api = FakeApi()
    assert operator_run.run("q1", api=api, ask=slow_bundle(0.5))[0] == "DONE"
    names = api.names()
    # The bundle's claim and report, then its renewals, then the audit's claim and report.
    bundle = [i for i, name in enumerate(names) if name == "report_progress"][1]
    audit = names.index("report_progress", bundle + 1)
    assert names[bundle + 1:audit].count("claim") >= 1 + 4
    # Only while a step runs: the renewals end with it, and none outlives the run.
    calls = len(api.calls)
    time.sleep(0.2)
    assert len(api.calls) == calls and names[-1] == "post_answer"


def test_renewals_end_once_the_api_refuses_the_claim_for_good(jobs_root, monkeypatch, capsys):
    monkeypatch.setattr(operator_run, "RENEW_S", 0.05)
    api = FakeApi()
    taken = {}

    def take():  # another agent takes the topic while the bundle is being written
        api.errors["claim"] = ApiError(409, "CONFLICT", "Another agent holds the claim on this question")
        taken["at"] = len(api.calls)

    assert operator_run.run("q1", api=api, ask=slow_bundle(0.6, take))[:2] == ("STOPPED", "audit")
    # One refused renewal, and the audit's claim, which stops the run: no more renewals after the refusal.
    assert api.names()[taken["at"]:].count("claim") == 2
    assert "claim: no longer renewed during the step: Another agent holds the claim" in capsys.readouterr().err


def test_a_renewal_that_fails_is_said_once_and_tried_again(jobs_root, monkeypatch, capsys):
    monkeypatch.setattr(operator_run, "RENEW_S", 0.05)
    api = FakeApi()

    def down_then_up():  # the API down for a few renewals while the bundle is being written, then back
        api.errors["claim"] = ApiError(503, "UNAVAILABLE", "try again")
        time.sleep(0.2)
        del api.errors["claim"]

    assert operator_run.run("q1", api=api, ask=slow_bundle(0.6, down_then_up))[0] == "DONE"
    assert capsys.readouterr().err.count("claim: a renewal during the step failed") == 1


def test_background_detaches_the_run_and_wait_gives_its_outcome_line(jobs_root, monkeypatch):
    # The run it starts is a real `python -m studio run`: with no API to read the settings from, it stops at once,
    # before it claims anything, so runs.jsonl never has its outcome: wait reads the line it printed.
    monkeypatch.setenv("STUDIO_JOBS_DIR", str(jobs_root))
    monkeypatch.delenv("POPSMOSIS_API_URL", raising=False)
    monkeypatch.delenv("POPSMOSIS_API_KEY", raising=False)
    result, step, reason = operator_run.background("q1", note="keep labels out")
    assert (result, step) == ("STARTED", "run"), reason
    assert operator_run.waits_on_lesson("q1")
    assert operator_run.background("q1")[0] in ("STOPPED", "STARTED")  # never two at once
    assert operator_run.wait("q1", minutes=0.5, poll_s=0.1) == (
        "STOPPED", "run", "the API refused the key: POPSMOSIS_API_URL and POPSMOSIS_API_KEY must both be set")


def test_background_refuses_a_topic_whose_run_is_still_going(jobs_root):
    job = jobs_root / "q1"
    job.mkdir()
    with operator_run.only_run(job):  # a run of it started by hand, in the foreground
        result, _, reason = operator_run.background("q1")
    assert result == "STOPPED" and "still going" in reason
    assert not (job / operator_run.BACKGROUND).exists()


def test_wait_says_running_while_the_lesson_is_being_made(jobs_root):
    job = jobs_root / "q1"
    job.mkdir()
    jobs.write(job / operator_run.BACKGROUND, {"pid": os.getpid(), "runs": 0})
    jobs.write(job / "run.current.json", {"started": "x", "step": "bundle", "steps": []})
    assert operator_run.wait("q1", minutes=0)[:2] == ("RUNNING", "bundle")


def test_a_background_lesson_killed_in_a_step_is_a_failed_run_of_it_and_can_be_given_up(jobs_root):
    api = FakeApi()
    job = jobs_root / "q1"
    job.mkdir()
    jobs.write(job / operator_run.BACKGROUND, {"pid": 2 ** 22 + 7, "runs": 0})  # no such process: it is gone
    (job / operator_run.OUTCOME).write_text("")  # killed before it printed its line
    jobs.write(job / "run.current.json", {"started": "x", "step": "bundle", "steps": []})
    result, step, reason = operator_run.wait("q1", minutes=0)
    assert (result, step) == ("FAILED", "bundle") and "interrupted" in reason
    (job / "run.current.json").unlink(missing_ok=True)
    jobs.write(job / operator_run.BACKGROUND, {"pid": 2 ** 22 + 7, "runs": 1})  # a second one, dead with no trace
    result, step, reason = operator_run.wait("q1", minutes=0)
    assert (result, step) == ("FAILED", "run") and "without an outcome" in reason
    assert operator_run.mark_failed("q1", api=api)[0] == "DONE"
    assert "without an outcome" in api.calls[-1][2]


def test_wait_is_for_whichever_of_the_lesson_and_the_videos_went_to_the_background_last(jobs_root):
    from studio import videos

    job = jobs_root / "q1"
    job.mkdir()
    assert not operator_run.waits_on_lesson("q1")  # nothing in the background: the videos' wait says so
    jobs.write(job / operator_run.BACKGROUND, {"pid": 1, "runs": 0})
    assert operator_run.waits_on_lesson("q1")
    jobs.write(job / videos.BACKGROUND, {"pid": 1, "runs": 0})
    os.utime(job / operator_run.BACKGROUND, ns=(1, 1))  # the lesson's, long before
    assert not operator_run.waits_on_lesson("q1")


def test_the_command_line_starts_the_lesson_in_the_background_and_waits_on_it(jobs_root, monkeypatch, capsys):
    from studio.__main__ import main

    got = {}
    monkeypatch.setattr(operator_run, "background", lambda question, note="": got.update(question=question, note=note)
                        or ("STARTED", "run", "the lesson, in the background"))
    assert main(["run", "q1", "--note", "keep labels out", "--background"]) == 0
    assert got == {"question": "q1", "note": "keep labels out"}
    monkeypatch.setattr(operator_run, "waits_on_lesson", lambda question: True)
    monkeypatch.setattr(operator_run, "wait", lambda question, minutes=None: ("RUNNING", "bundle", "still going"))
    assert main(["wait", "q1"]) == 4
    assert capsys.readouterr().out.splitlines()[-1] == "RUNNING bundle: still going"


def test_with_the_song_off_on_the_studio_page_the_lesson_is_written_checked_and_posted_with_no_song(jobs_root, capsys):
    """STUDIO_VIDEOS=lesson, as the claim carries it: the bundle is asked for without its song, the fact-check
    checks no lyric, style or song tag and never calls the songwriter, and the lesson posted has no song."""
    api, ask = FakeApi(), FakeBrain()
    api.studio = {"knobs": {"STUDIO_VIDEOS": "lesson"}}
    assert operator_run.run("q1", api=api, ask=ask) == ("DONE", "post", "lesson posted as answer ans1")
    assert "lyric rewrite round 1" not in ask.labels
    bundle = jobs.read(jobs_root / "q1" / "bundle.json", Bundle)
    assert not bundle.sung and [k.from_narration for k in bundle.key_lines] == ["n1"]
    report = jobs.read(jobs_root / "q1" / "factcheck.json", FactCheckReport)
    assert report.status == "passed"
    assert not [c for c in report.lines if c.kind in ("lyric", "style", "avoid") or c.id.startswith("song-")]
    assert ("keyline", "n1") in {(c.kind, c.id) for c in report.lines}
    assert api.posted["bundle"]["lyrics"] == [] and api.posted["bundle"]["tags"]["song"] == []
    from studio.__main__ import main

    capsys.readouterr()
    assert main(["show", "q1"]) == 0
    assert "No song: written for the lesson video alone" in capsys.readouterr().out

