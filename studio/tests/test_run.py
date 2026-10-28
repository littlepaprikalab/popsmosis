import json

import pytest

from studio import brain, jobs
from studio import run as operator_run
from studio.api import PROGRESS_TIMEOUT_S, Api, ApiError
from studio.schemas import Bundle, FactCheck, Request
from studio.tests.sample import bundle_data

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

    def claim(self, question_id):
        self._call("claim", question_id)
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
    """Stands in for brain.ask: a sample bundle, and a fact-check that supports every line (or none)."""

    def __init__(self, bundle_error=None, verdict="SUPPORTED"):
        self.bundle_error = bundle_error
        self.verdict = verdict
        self.labels = []

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.labels.append(label)
        if schema is Bundle:
            if self.bundle_error:
                raise self.bundle_error
            return bundle_data()
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


def test_a_fresh_run_claims_the_topic_writes_the_lesson_and_posts_it(jobs_root):
    api, ask = FakeApi(), FakeBrain()
    (jobs_root / "q1").mkdir()
    (jobs_root / "q1" / "calls.jsonl").write_text(json.dumps({"label": "bundle", "model": "claude-opus-5-5"}) + "\n")

    assert operator_run.run("q1", api=api, ask=ask) == ("DONE", "post", "lesson posted as answer ans1")
    # Claimed again before every step but the post, so the lease outlasts a slow step; and each of those steps
    # tells the page it is running.
    assert api.names() == ["claim", "report_progress", "download", "download", "claim", "report_progress",
                           "claim", "report_progress", "post_answer"]
    assert ask.labels == ["bundle", "factcheck round 1"]

    job = jobs_root / "q1"
    request = jobs.read(job / "request.json", Request)
    assert request.topic == "ATP synthesis for my AP Bio exam"
    assert request.images == [str((job / "attachments" / "a1.png").resolve())]
    assert (job / "attachments" / "a1.png").read_bytes() == b"\x89PNG fake"
    assert request.study_guide == "guide.txt:\nATP is made by ATP synthase."

    bundle = json.loads((job / "bundle.json").read_text())
    breakdown = api.posted.pop("breakdown")
    assert api.posted == {"content": bundle["lesson_markdown"], "bundle": bundle,
                          "factcheck": json.loads((job / "factcheck.json").read_text()), "model": "claude-opus-5-5"}
    # The steps before the post, each timed; the call logged before this run is the bundle's.
    assert [s["step"] for s in breakdown["steps"]] == ["request", "bundle", "factcheck"]
    assert breakdown["steps"][1]["models"] == [{"model": "claude-opus-5-5", "calls": 1, "costUsd": None, "credits": None}]
    assert all(s["tries"] == 1 and s["seconds"] is not None for s in breakdown["steps"])
    assert json.loads((job / "posted.json").read_text())["id"] == "ans1"
    [record] = operator_run.read_jsonl(job / "runs.jsonl")
    assert (record["result"], record["step"]) == ("DONE", "post")
    assert [s["step"] for s in record["steps"]] == ["request", "bundle", "factcheck", "post"]


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

    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None: None)
    api = FakeApi()
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    assert [[r["step"] for r in report["running"]] for report in api.reports] == [["request"], ["bundle"], ["factcheck"]]
    assert all(r["running"][0]["seconds"] < 5 and r["failed"] == [] and r["videos"] is True for r in api.reports)
    # The bill so far is the steps already done, never the one just starting.
    assert "breakdown" not in api.reports[0] or api.reports[0]["breakdown"]["steps"] == []
    assert [s["step"] for s in api.reports[2]["breakdown"]["steps"]] == ["request", "bundle"]

    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None: "the videos are turned off here (STUDIO_VIDEOS)")
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
    assert ask.labels == ["bundle", "factcheck round 1"]
    runs = operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")
    assert [(r["result"], r["step"]) for r in runs] == [("FAILED", "bundle"), ("DONE", "post")]

    # Done is done: a third run makes no calls, and the record does not count it.
    api.calls.clear()
    assert operator_run.run("q1", api=api, ask=FakeBrain()) == ("DONE", "post", "the lesson was already posted")
    assert api.calls == []
    assert len(operator_run.read_jsonl(jobs_root / "q1" / "runs.jsonl")) == 2


def test_a_failed_fact_check_is_retried_on_the_same_bundle_and_nothing_is_posted(jobs_root):
    api = FakeApi()
    result, step, reason = operator_run.run("q1", api=api, ask=FakeBrain(verdict="UNSUPPORTED"))
    assert (result, step) == ("FAILED", "factcheck")
    assert reason.endswith("is unsupported and has no correction: wrong")
    assert "post_answer" not in api.names()

    ask = FakeBrain()
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.labels == ["factcheck round 1"]  # the bundle was kept


def test_a_new_bundle_runs_every_step_after_it(jobs_root):
    operator_run.run("q1", api=FakeApi(), ask=FakeBrain(verdict="UNSUPPORTED"))
    (jobs_root / "q1" / "bundle.json").unlink()  # by hand: the factcheck.json beside it is now stale
    ask = FakeBrain()
    assert operator_run.run("q1", api=FakeApi(), ask=ask)[0] == "DONE"
    assert ask.labels == ["bundle", "factcheck round 1"]


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
    assert text.startswith("job q1: AP Biology / the light reactions. 2 run(s): run 1 FAILED at bundle (overloaded); "
                           "run 2 DONE at post.")
    assert "Fact-check passed in 1 round(s), 0 correction(s). Lesson posted. 2 model call(s), $0.70." in text
    assert "ATP synthesis" not in text and "exam" not in text
    assert len(text) <= operator_run.CYCLE_LOG_MAX


def test_a_reason_is_one_short_line():
    assert operator_run.one_line("a\n  b\tc") == "a b c"
    assert len(operator_run.one_line("x" * 1000)) == operator_run.REASON_MAX


def test_only_a_question_id_names_a_job():
    with pytest.raises(ValueError):
        operator_run.job_dir("../etc")


# --- the seams: kills, overlaps, lost replies ---------------------------------------


def test_a_step_file_cut_short_counts_as_not_done(jobs_root):
    operator_run.run("q1", api=FakeApi(), ask=FakeBrain(verdict="UNSUPPORTED"))
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
    assert ask.brains == [("meta", "muse-spark-1.3")] * 2  # the bundle and the fact-check
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
    assert ask.brains == [("meta", "muse-wonder-1.0")] * 2
    assert (operator_run.job_brain(jobs_root / "q1"), operator_run.job_effort(jobs_root / "q1")) == ("muse-wonder", "medium")


def test_with_no_settings_from_the_api_the_brain_is_claude_opus(jobs_root):
    api, ask = FakeApi(), SpyBrain()  # an API too old to send them
    assert operator_run.run("q1", api=api, ask=ask)[0] == "DONE"
    assert ask.brains == [("claude", "claude-opus-5-5")] * 2


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
    api.claim = lambda question_id: {**QUESTION, "studio": {"brain": {"default": "muse-spark"}}}  # changed meanwhile
    result, step, reason = operator_run.run("q1", api=api)
    assert (result, step, reason) == ("STOPPED", "request", "the studio's settings are wrong: Muse Spark 1.3 needs MODEL_API_KEY")
