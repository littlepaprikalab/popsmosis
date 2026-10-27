import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from studio import jobs, media
from studio import run as operator_run
from studio import videos
from studio.api import ApiError
from studio.tests.sample import bundle_data
from studio.tests.test_run import FakeApi, FakeBrain

FACTCHECK = {"status": "passed", "reason": "", "rounds": 1, "lines": [], "corrections": []}


class VideoApi(FakeApi):
    """FakeApi plus the video claim: attempts count up, and `taken` makes every claim a 409."""

    def __init__(self):
        super().__init__()
        self.attempt = 0
        self.taken = False
        self.queue = []
        self.given_up = None

    def claim_videos(self, answer_id, attempt=None):
        self._call("claim_videos", answer_id, attempt)
        if self.taken:
            raise ApiError(409, "CONFLICT", "Another run is making these videos")
        if attempt is None or attempt != self.attempt:
            self.attempt += 1
        return {"answerId": answer_id, "attempt": self.attempt}

    def videos_queue(self, limit=1):
        self._call("videos_queue", limit)
        handed, self.queue = self.queue[:limit], self.queue[limit:]
        return handed

    def give_up_videos(self, answer_id, reason, attempt):
        self._call("give_up_videos", answer_id, reason, attempt)
        self.given_up = (answer_id, reason, attempt)
        return {"status": "FAILED", "error": reason}


class Steps:
    """Stand-in media steps: each marks itself done with a file, and can be told to fail."""

    def __init__(self):
        self.ran = []
        self.fail = {}  # step -> times left to fail
        self.barrier = threading.Barrier(2, timeout=5)  # art and audio must be running together

    def module(self, name):
        def run(job, ask=None):
            if name in ("art", "audio"):
                self.barrier.wait()
            self.ran.append(name)
            if self.fail.get(name):
                self.fail[name] -= 1
                raise media.StepFailed(f"{name} broke")
            (job / f".{name}.done").write_text("ok")
            return f"{name} made"

        return SimpleNamespace(run=run, is_done=lambda job: (job / f".{name}.done").exists())


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    monkeypatch.setattr(videos, "unavailable", lambda: None)
    steps = Steps()
    monkeypatch.setattr(media, "step_module", steps.module)
    return steps


def posted(question="q1", answer="ans1"):
    job = jobs.ROOT / question
    job.mkdir(parents=True, exist_ok=True)
    jobs.write(job / "posted.json", {"id": answer, "questionId": question})
    return job


def test_a_fresh_run_makes_art_and_audio_at_once_then_the_rest_in_order(setup):
    api = VideoApi()
    posted()
    assert videos.videos("q1", api=api, ask=FakeBrain()) == ("DONE", "upload", "upload made")
    ran = setup.ran
    assert set(ran[:2]) == {"art", "audio"} and ran[-3:] == ["timeline", "render", "upload"]
    assert ran.index("locate") > ran.index("art") and ran.index("timing") > ran.index("audio")
    claims = [c for c in api.calls if c[0] == "claim_videos"]
    assert len(claims) == 1 + len(media.MEDIA_STEPS)  # the claim, then a heartbeat before every step
    assert claims[0][2] is None and all(c[2] == 1 for c in claims[1:])  # one attempt throughout
    record = operator_run.read_jsonl(jobs.ROOT / "q1" / "runs.jsonl")[-1]
    assert record["part"] == "videos" and record["result"] == "DONE"
    assert not (jobs.ROOT / "q1" / "run.current.json").exists()


def test_a_failed_step_lets_the_other_branch_finish_and_the_retry_resumes_there(setup):
    api = VideoApi()
    posted()
    setup.fail["locate"] = 1
    assert videos.videos("q1", api=api)[:2] == ("FAILED", "locate")
    assert "timing" in setup.ran and "timeline" not in setup.ran
    setup.ran.clear()
    setup.barrier = threading.Barrier(1)  # only one branch has anything left to do
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert setup.ran == ["locate", "timeline", "render", "upload"]


def test_a_step_that_runs_again_makes_everything_after_it_run_again(setup):
    api = VideoApi()
    job = posted()
    videos.videos("q1", api=api)
    (job / ".audio.done").unlink()
    setup.ran.clear()
    setup.barrier = threading.Barrier(1)
    videos.videos("q1", api=api)
    assert setup.ran == ["audio", "timing", "timeline", "render", "upload"]
    setup.ran.clear()
    assert videos.videos("q1", api=api) == ("DONE", "upload", "the videos were already made")
    assert setup.ran == []


def test_a_render_taken_over_by_another_run_stops_without_a_retry(setup):
    api = VideoApi()
    posted()
    setup.fail["art"] = 1
    api.taken = True
    assert videos.videos("q1", api=api)[0] == "STOPPED"
    api.taken = False
    original = api.claim_videos

    def taken_after_first(answer_id, attempt=None):
        if attempt is not None:
            api.taken = True
        return original(answer_id, attempt)

    api.claim_videos = taken_after_first
    setup.fail["art"] = 1
    result, step, reason = videos.videos("q1", api=api)
    assert result == "STOPPED" and "Another run" in reason  # beats the art failure: nothing to retry


def test_nothing_to_make_without_a_posted_lesson_or_the_steps(setup, monkeypatch):
    api = VideoApi()
    assert videos.videos("q1", api=api)[0] == "STOPPED"
    posted()
    monkeypatch.setattr(videos, "unavailable", lambda: "the media steps are not all built yet (art)")
    assert videos.videos("q1", api=api) == ("STOPPED", "videos", "the media steps are not all built yet (art)")
    assert api.calls == []


def test_a_run_cut_off_mid_step_counts_as_a_failure_that_fail_gives_up(setup):
    api = VideoApi()
    job = posted()
    videos.videos("q1", api=api)
    jobs.write(job / "run.current.json", {"started": "x", "part": "videos", "step": "render", "steps": []})
    result, reason = operator_run.mark_failed("q1", api=api)
    assert result == "DONE" and api.given_up == ("ans1", reason.removeprefix("videos given up: "), 1)
    assert api.given_up[1].startswith("render failed: render was interrupted")


def test_fail_after_videos_that_were_made_gives_nothing_up(setup):
    api = VideoApi()
    posted()
    videos.videos("q1", api=api)
    assert operator_run.mark_failed("q1", api=api)[0] == "STOPPED"
    assert api.given_up is None


def test_recover_rebuilds_the_job_from_what_the_api_stored_never_the_learners_words(setup):
    api = VideoApi()
    assert videos.recover(api=api)[0] == "NONE"
    api.attempt = 2
    api.queue = [{"answerId": "ans9", "questionId": "q9", "attempt": 2, "bundle": bundle_data(), "factcheck": FACTCHECK}]
    result, question, _ = videos.recover(api=api)
    assert (result, question) == ("RECOVER", "q9")
    job = jobs.ROOT / "q9"
    assert json.loads((job / "posted.json").read_text())["id"] == "ans9"
    assert json.loads((job / "videos.json").read_text()) == {"answerId": "ans9", "attempt": 2}
    assert json.loads((job / "request.json").read_text())["topic"] == "the light reactions"
    assert videos.videos("q9", api=api)[0] == "DONE"
    assert {c[2] for c in api.calls if c[0] == "claim_videos"} == {2}  # kept the attempt it was handed


def test_recover_takes_nothing_where_the_videos_cannot_be_made(setup, monkeypatch):
    api = VideoApi()
    api.queue = [{"answerId": "ans9", "questionId": "q9", "attempt": 2, "bundle": bundle_data(), "factcheck": FACTCHECK}]
    monkeypatch.setattr(videos, "unavailable", lambda: "the videos are turned off here (STUDIO_VIDEOS)")
    assert videos.recover(api=api)[0] == "NONE"
    assert api.calls == []


def test_recover_gives_up_a_droplet_whose_stored_lesson_cannot_be_read(setup):
    api = VideoApi()
    api.queue = [{"answerId": "ans9", "questionId": "q9", "attempt": 1, "bundle": {"topic": "x"}, "factcheck": FACTCHECK}]
    assert videos.recover(api=api)[0] == "FAILED"
    assert api.given_up[0] == "ans9" and api.given_up[2] == 1


def test_posting_the_lesson_claims_the_first_attempt_at_the_videos(setup, monkeypatch):
    api = VideoApi()
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    assert ("claim_videos", "ans1", None) in api.calls
    assert json.loads((jobs.ROOT / "q1" / "videos.json").read_text())["attempt"] == 1


def test_a_failed_video_claim_never_changes_the_lessons_outcome(setup, monkeypatch):
    api = VideoApi()
    api.taken = True
    assert operator_run.run("q1", api=api, ask=FakeBrain())[0] == "DONE"
    monkeypatch.setattr(videos, "unavailable", lambda: "the videos are turned off here (STUDIO_VIDEOS)")
    api = VideoApi()
    assert operator_run.run("q2", api=api, ask=FakeBrain())[0] == "DONE"
    assert "claim_videos" not in api.names()


def test_the_summary_says_what_happened_to_the_videos(setup):
    api = VideoApi()
    operator_run.run("q1", api=api, ask=FakeBrain())
    videos.videos("q1", api=api)
    assert "Lesson posted. Videos made." in operator_run.summary("q1")


def test_background_detaches_the_run_and_wait_reads_its_outcome(setup, monkeypatch, tmp_path):
    api = VideoApi()
    posted()
    # The run it starts is a real `python -m studio videos`: told the videos are off, it stops at once.
    monkeypatch.setenv("STUDIO_JOBS_DIR", str(tmp_path))
    monkeypatch.setenv("STUDIO_VIDEOS", "off")
    result, _, reason = videos.background("q1", api=api)
    assert result == "STARTED", reason
    assert videos.background("q1", api=api)[0] in ("STOPPED", "STARTED")  # never two at once
    assert videos.wait("q1", minutes=0.5, poll_s=0.1) == ("STOPPED", "videos", "the videos are turned off here (STUDIO_VIDEOS)")


def test_a_background_run_that_died_without_an_outcome_can_still_be_given_up(setup):
    api = VideoApi()
    job = posted()
    videos.claim(api, job)
    jobs.write(job / videos.BACKGROUND, {"pid": 2 ** 22 + 7, "runs": 0})  # no such process: it is gone
    result, _, reason = videos.wait("q1", minutes=0)
    assert result == "FAILED" and "without an outcome" in reason
    assert operator_run.mark_failed("q1", api=api)[0] == "DONE"
    assert api.given_up[0] == "ans1" and "without an outcome" in api.given_up[1]


def test_wait_says_running_while_the_run_is_alive(setup):
    job = posted()
    jobs.write(job / videos.BACKGROUND, {"pid": os.getpid(), "runs": 0})
    jobs.write(job / "run.current.json", {"started": "x", "part": "videos", "step": "render", "steps": []})
    result, step, _ = videos.wait("q1", minutes=0)
    assert (result, step) == ("RUNNING", "render")


def test_a_finished_run_nobody_reaped_is_not_alive():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    for _ in range(100):  # until it has exited (a zombie: nobody has waited on it)
        if not videos._alive(child.pid):
            break
        time.sleep(0.05)
    assert not videos._alive(child.pid)
    child.wait()


def test_two_branches_asking_at_once_each_log_only_their_own_calls(tmp_path, monkeypatch):
    from studio import brain

    together = threading.Barrier(2, timeout=5)

    def fake_ask(role, prompt, *args, label=None, **kwargs):
        together.wait()  # both threads are inside ask before either records its call
        brain._record(brain.Call(label, role, "m", 10, 5, 0.01, 0.1, "end_turn"))
        together.wait()  # and both have recorded before either logs
        return {}

    monkeypatch.setattr(brain, "ask", fake_ask)
    ask = operator_run.logged_ask(tmp_path)
    threads = [threading.Thread(target=ask, args=("brain", "p"), kwargs={"label": name}) for name in ("art", "audio")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    logged = sorted(json.loads(row)["label"] for row in (tmp_path / "calls.jsonl").read_text().splitlines())
    assert logged == ["art", "audio"]
