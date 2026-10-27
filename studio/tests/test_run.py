import json

import pytest

from studio import brain, jobs
from studio import run as operator_run
from studio.api import Api, ApiError
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

    def __init__(self, question=QUESTION):
        self.question = question
        self.calls = []
        self.errors = {}  # method name -> ApiError to raise
        self.posted = None

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.errors:
            raise self.errors[name]

    def claim(self, question_id):
        self._call("claim", question_id)
        return self.question

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
    # Claimed again before every step but the post, so the lease outlasts a slow step.
    assert api.names() == ["claim", "download", "download", "claim", "claim", "post_answer"]
    assert ask.labels == ["bundle", "factcheck round 1"]

    job = jobs_root / "q1"
    request = jobs.read(job / "request.json", Request)
    assert request.topic == "ATP synthesis for my AP Bio exam"
    assert request.images == [str((job / "attachments" / "a1.png").resolve())]
    assert (job / "attachments" / "a1.png").read_bytes() == b"\x89PNG fake"
    assert request.study_guide == "guide.txt:\nATP is made by ATP synthase."

    bundle = json.loads((job / "bundle.json").read_text())
    assert api.posted == {"content": bundle["lesson_markdown"], "bundle": bundle,
                          "factcheck": json.loads((job / "factcheck.json").read_text()), "model": "claude-opus-5-5"}
    assert json.loads((job / "posted.json").read_text())["id"] == "ans1"
    [record] = operator_run.read_jsonl(job / "runs.jsonl")
    assert (record["result"], record["step"]) == ("DONE", "post")
    assert [s["step"] for s in record["steps"]] == ["request", "bundle", "factcheck", "post"]


def test_a_photo_too_big_for_the_brain_is_left_out_rather_than_failing_every_try(jobs_root, monkeypatch):
    monkeypatch.setattr(operator_run, "IMAGE_MAX_BYTES", 4)
    assert operator_run.run("q1", api=FakeApi(), ask=FakeBrain())[0] == "DONE"
    request = jobs.read(jobs_root / "q1" / "request.json", Request)
    assert request.images == [] and request.study_guide  # the text file still came through


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
    assert operator_run.mark_failed("q1", api=api) == ("STOPPED", "the last run did not fail")
    operator_run.run("q1", api=api, ask=FakeBrain(bundle_error=brain.Refused("claude-opus-5-5", "bio")))
    result, reason = operator_run.mark_failed("q1", api=api)
    assert (result, reason) == ("DONE", "bundle failed: claude-opus-5-5 declined the request (bio)")
    assert api.calls[-1] == ("fail", "q1", reason)


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
    assert "Fact-check passed in 1 round(s), 0 correction(s). Lesson posted. 2 brain call(s), $0.70." in text
    assert "ATP synthesis" not in text and "exam" not in text
    assert len(text) <= operator_run.CYCLE_LOG_MAX


def test_a_reason_is_one_short_line():
    assert operator_run.one_line("a\n  b\tc") == "a b c"
    assert len(operator_run.one_line("x" * 1000)) == operator_run.REASON_MAX


def test_only_a_question_id_names_a_job():
    with pytest.raises(ValueError):
        operator_run.job_dir("../etc")


# --- the API client -------------------------------------------------------------


def test_the_client_sends_the_key_and_json_and_raises_the_apis_error():
    sent = []

    def send(method, url, headers, body):
        sent.append((method, url, headers, body))
        if url.endswith("/fail"):
            return 409, b'{"error":{"code":"CONFLICT","message":"Question already has an answer"}}'
        return 200, b'{"id":"q1"}'

    api = Api("http://localhost:3001", "sk_pop_x", send=send)
    assert api.claim("q1") == {"id": "q1"}
    method, url, headers, body = sent[0]
    assert (method, url, body) == ("POST", "http://localhost:3001/api/questions/q1/claim", b"{}")
    assert headers["Authorization"] == "Bearer sk_pop_x" and headers["Content-Type"] == "application/json"
    with pytest.raises(ApiError) as caught:
        api.fail("q1", "bundle failed: x")
    assert (caught.value.status, caught.value.code, caught.value.message) == (409, "CONFLICT", "Question already has an answer")
    assert json.loads(sent[1][3]) == {"reason": "bundle failed: x"}


def test_the_client_needs_both_variables(monkeypatch):
    monkeypatch.delenv("POPSMOSIS_API_KEY", raising=False)
    monkeypatch.setenv("POPSMOSIS_API_URL", "http://localhost:3001/")
    with pytest.raises(ApiError) as caught:
        Api.from_env()
    assert caught.value.code == "NOT_CONFIGURED"
    monkeypatch.setenv("POPSMOSIS_API_KEY", "sk_pop_x")
    assert Api.from_env().base_url == "http://localhost:3001"
