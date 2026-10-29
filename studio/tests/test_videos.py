import json
import os
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from studio import jobs, knobs, media
from studio import run as operator_run
from studio import videos
from studio.api import ApiError
from studio.tests.sample import bundle_data
from studio.tests.test_run import FakeApi, FakeBrain

FACTCHECK = {"status": "passed", "reason": "", "rounds": 1, "lines": [], "corrections": []}
REAL_UNAVAILABLE = videos.unavailable  # the setup fixture stands in for it


def claims(api) -> list[str]:
    """What the run asked the API for beyond reading the studio's settings: a claim, the queue, a give-up."""
    return [name for name in api.names() if name != "studio_settings"]


class VideoApi(FakeApi):
    """FakeApi plus the video claim: attempts count up, and `taken` makes every claim a 409."""

    def __init__(self):
        super().__init__()
        self.attempt = 0
        self.taken = False
        self.queue = []
        self.given_up = None
        self.video_reports = []  # (attempt, progress) for each report, as sent
        self.lock = threading.Lock()
        self.studio = None  # the studio's settings the claim carries (api/src/lib/studio-settings.ts)

    def claim_videos(self, answer_id, attempt=None):
        self._call("claim_videos", answer_id, attempt)
        if self.taken:
            raise ApiError(409, "CONFLICT", "Another run is making these videos")
        if attempt is None or attempt != self.attempt:
            self.attempt += 1
        return {"answerId": answer_id, "attempt": self.attempt, **({"studio": self.studio} if self.studio else {})}

    def report_video_progress(self, answer_id, attempt, progress):
        with self.lock:
            self._call("report_video_progress", answer_id, attempt)
            self.video_reports.append((attempt, progress))

    def videos_queue(self, limit=1):
        self._call("videos_queue", limit)
        handed, self.queue = self.queue[:limit], self.queue[limit:]
        return handed

    def give_up_videos(self, answer_id, reason, attempt, breakdown=None):
        self._call("give_up_videos", answer_id, reason, attempt)
        self.given_up = (answer_id, reason, attempt)
        self.given_up_breakdown = breakdown
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
    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None: None)
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


def running(api):
    return [sorted(r["step"] for r in progress["running"]) for _, progress in api.video_reports]


def test_each_step_tells_the_page_when_it_starts_and_ends_with_the_bill_so_far(setup):
    api = VideoApi()
    posted()
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert all(attempt == 1 for attempt, _ in api.video_reports)
    seen = running(api)
    # The art and the audio run side by side, and the page sees both running at once.
    assert ["art", "audio"] in seen
    # Every step is reported running, and the last report before the upload's READY has it running alone.
    assert all(any(step in r for r in seen) for step in media.MEDIA_STEPS)
    assert seen[-1] == ["upload"]
    # A start and an end for each step, but none after the upload: its READY carried the last bill.
    assert len(seen) == 2 * len(media.MEDIA_STEPS) - 1
    last_bill = api.video_reports[-1][1]["breakdown"]
    assert [s["step"] for s in last_bill["steps"]] == ["art", "locate", "audio", "timing", "timeline", "render"]
    assert all(progress["failed"] == [] for _, progress in api.video_reports)


def test_a_failed_step_is_reported_failed_while_the_other_branch_goes_on(setup):
    api = VideoApi()
    posted()
    setup.fail["art"] = 1
    assert videos.videos("q1", api=api)[:2] == ("FAILED", "art")
    after = [progress for _, progress in api.video_reports if "art" in progress["failed"]]
    assert after and all(not any(r["step"] == "art" for r in p["running"]) for p in after)
    assert [s for s in after[-1]["breakdown"]["steps"] if s["step"] == "art"][0]["tries"] == 1


def test_a_report_the_api_refuses_never_fails_the_render(setup, capsys):
    api = VideoApi()
    api.errors["report_video_progress"] = ApiError(409, "CONFLICT", "Another run has taken over these videos")
    posted()
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert "progress not reported" in capsys.readouterr().err
    assert api.names().count("report_video_progress") == 1  # then quiet for a minute, the render going on


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
    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None: "the media steps are not all built yet (art)")
    assert videos.videos("q1", api=api) == ("STOPPED", "videos", "the media steps are not all built yet (art)")
    assert claims(api) == []


def test_a_run_cut_off_mid_step_counts_as_a_failure_that_fail_gives_up(setup):
    api = VideoApi()
    job = posted()
    videos.videos("q1", api=api)
    jobs.write(job / "run.current.json", {"started": "x", "part": "videos", "step": "render", "steps": []})
    result, reason = operator_run.mark_failed("q1", api=api)
    assert result == "DONE" and api.given_up == ("ans1", reason.removeprefix("videos given up: "), 1)
    assert api.given_up[1].startswith("render failed: render was interrupted")


def test_giving_up_sends_what_the_failed_tries_took_and_cost(setup):
    from studio import brain, ledger

    api = VideoApi()
    job = posted()
    setup.fail["render"] = 2
    with ledger.at_step("art"):  # what the art step would have logged
        ledger.log(job, [brain.Call("art hook", "helper", "claude-haiku-5-5", 10, 5, 0.002, 1.0, "end_turn")])
    assert videos.videos("q1", api=api)[:2] == ("FAILED", "render")
    # The retry is killed between steps: recorded at "videos", which is no step of the bill.
    jobs.write(job / "run.current.json", {"started": "x", "part": "videos", "step": "videos",
                                          "steps": [{"step": "render", "seconds": 4.0, "result": "FAILED"}]})
    assert operator_run.mark_failed("q1", api=api)[0] == "DONE"
    bill = {s["step"]: s for s in api.given_up_breakdown["steps"]}
    assert bill["render"]["tries"] == 2 and "videos" not in bill
    assert bill["art"]["models"] == [{"model": "claude-haiku-5-5", "calls": 1, "costUsd": 0.002, "credits": None}]


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
    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None: "the videos are turned off here (STUDIO_VIDEOS)")
    assert videos.recover(api=api)[0] == "NONE"
    assert api.names() == ["studio_settings"]  # read the settings; took nothing from the queue


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
    monkeypatch.setattr(videos, "unavailable", lambda job=None, studio=None: "the videos are turned off here (STUDIO_VIDEOS)")
    api = VideoApi()
    assert operator_run.run("q2", api=api, ask=FakeBrain())[0] == "DONE"
    assert "claim_videos" not in api.names()


def test_the_summary_says_what_happened_to_the_videos(setup):
    api = VideoApi()
    operator_run.run("q1", api=api, ask=FakeBrain())
    videos.videos("q1", api=api)
    assert "Lesson posted. Videos made." in operator_run.summary("q1")


def test_the_summary_says_what_the_media_steps_said_for_the_operators_journal(setup):
    api = VideoApi()
    operator_run.run("q1", api=api, ask=FakeBrain())
    setup.fail["timing"] = 1
    assert videos.videos("q1", api=api)[:2] == ("FAILED", "timing")
    videos.videos("q1", api=api)
    first, inside = operator_run.summary("q1").split("\n\n")
    assert len(first) <= operator_run.CYCLE_LOG_MAX and "Videos made." in first
    # Each step as it last passed; the failed try of the timing is the first paragraph's.
    assert inside == "Inside the steps, as each last said: art: art made. locate: locate made. timing: timing made."
    runs = [r for r in operator_run.read_jsonl(operator_run.job_dir("q1") / "runs.jsonl") if r.get("part") == "videos"]
    assert [(s["step"], s.get("detail")) for s in runs[0]["steps"] if s["step"] == "timing"] == [("timing", None)]
    assert {s["step"]: s.get("detail") for s in runs[-1]["steps"]}["render"] == "render made"


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


# --- the real art and locate steps inside the run, on Meta's models (stood in for) -----------


@pytest.fixture
def meta_job(setup, monkeypatch):
    """A posted sample job whose art and locate steps are the real ones, with Meta's API, the helper
    and the brain stood in for; the other media steps stay stand-ins."""
    from studio import brain, muse
    from studio.steps import art, locate
    from studio.tests import sample_media
    from studio.tests.test_art import FakeArtist
    from studio.tests.test_muse import FakeHelper, FakeMuse

    monkeypatch.setenv("MODEL_API_KEY", "meta-test-key")
    setup.barrier = threading.Barrier(1)  # the real art step does not wait for the audio
    real = {"art": art, "locate": locate}
    stand_ins = setup.module
    monkeypatch.setattr(media, "step_module", lambda name: real.get(name) or stand_ins(name))

    fake = SimpleNamespace(muse=FakeMuse().install(monkeypatch), helper=FakeHelper(), artist=FakeArtist())

    def ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        """brain.ask: answers as the helper or the SVG artist, and records the call as brain.ask does."""
        answer = (fake.helper if role == "helper" else fake.artist)(role, prompt, schema, images,
                                                                      system=system, label=label)
        model = "muse-spark-1.3" if role == "helper" else "claude-opus-5-5"
        brain._record(brain.Call(label, role, model, 1000, 100, brain.cost(model, 1000, 100), 0.1, "stop"))
        return answer

    monkeypatch.setattr(brain, "ask", ask)
    job = sample_media.write_job(jobs.ROOT / "q1")
    operator_run.keep_brain(job, "muse-wonder")  # the lesson was Muse's: Muse Image paints, by default
    (job / media.LOCATIONS).unlink()  # the sample's own, made of ellipses
    jobs.write(job / "posted.json", {"id": "ans1", "questionId": "q1"})
    fake.job = job
    return fake


def test_on_meta_the_run_paints_the_art_then_sam_locates_it_and_bills_both(meta_job):
    from studio import ledger
    from studio.schemas import Locations

    job = meta_job.job
    assert videos.videos("q1", api=VideoApi()) == ("DONE", "upload", "upload made")
    assert len(meta_job.muse.painted) == 3 and len(meta_job.muse.segmented) == 4
    assert len(jobs.read(job / media.LOCATIONS, Locations).scenes) == 3

    steps = {s["step"]: s for s in ledger.breakdown(job, "videos")["steps"]}
    art_models = {m["model"]: m for m in steps["art"]["models"]}
    assert art_models["muse-image-1.0"]["calls"] == 3 and art_models["muse-image-1.0"]["costUsd"] == pytest.approx(0.03)
    assert art_models["muse-spark-1.3"]["calls"] == 3 and art_models["muse-spark-1.3"]["costUsd"] > 0
    locate_models = {m["model"]: m for m in steps["locate"]["models"]}
    assert locate_models == {"sam-3.1": {**locate_models["sam-3.1"], "calls": 4, "costUsd": pytest.approx(0.01)}}


def test_on_meta_a_failed_locate_keeps_the_pictures_and_the_retry_only_locates(meta_job):
    job = meta_job.job
    meta_job.muse.spots = {}  # SAM finds nothing, and the helper sees nothing to box
    assert videos.videos("q1", api=VideoApi())[:2] == ("FAILED", "locate")
    assert len(meta_job.muse.painted) == 3 and (job / "art" / "hook.json").exists()

    from studio.tests.test_muse import SPOTS

    meta_job.muse.spots = SPOTS
    meta_job.muse.painted.clear()
    assert videos.videos("q1", api=VideoApi())[0] == "DONE"
    assert meta_job.muse.painted == []  # the pictures were paid for once


def test_switching_the_art_between_runs_remakes_it_and_everything_after_it(meta_job, monkeypatch):
    setup = meta_job
    job = meta_job.job
    videos.videos("q1", api=VideoApi())
    assert videos.videos("q1", api=VideoApi()) == ("DONE", "upload", "the videos were already made")

    operator_run.keep_brain(job, "claude")
    api = VideoApi()
    api.studio = {"art": {"claude": "svg", "meta": "muse"}}  # an admin has Claude draw it
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert [label for label in meta_job.artist.labels()] == ["art hook", "art overview", "art synthase"]
    assert not (job / "art" / "hook.json").exists() and (job / "art" / "hook.svg").exists()

    operator_run.keep_brain(job, "muse-wonder")
    meta_job.muse.painted.clear()
    assert videos.videos("q1", api=VideoApi())[0] == "DONE"
    assert len(meta_job.muse.painted) == 3 and not (job / "art" / "hook.svg").exists()
    del setup


def test_wrong_brain_settings_stop_the_videos_before_anything_is_claimed(setup, monkeypatch):
    monkeypatch.setattr(videos, "unavailable", REAL_UNAVAILABLE)
    for name in ("MODEL_API_KEY", "STUDIO_VIDEOS"):
        monkeypatch.delenv(name, raising=False)
    api = VideoApi()
    api.queue = [{"questionId": "q2", "answerId": "ans2", "attempt": 2}]
    operator_run.keep_brain(posted(), "muse-wonder")
    wrong = "the studio's settings are wrong: Muse Wonder 1.0 needs MODEL_API_KEY"
    assert videos.videos("q1", api=api) == ("STOPPED", "videos", wrong)
    assert videos.background("q1", api=api) == ("STOPPED", "videos", wrong)
    api.studio_settings = lambda: {"brain": {"default": "muse-wonder"}}  # recover checks the default brain
    assert videos.recover(api=api) == ("NONE", "recover", wrong)
    assert claims(api) == [] and setup.ran == []  # no claim, no attempt taken from the queue, no step


def test_the_studio_page_turns_the_videos_off_or_on_whatever_this_machine_says(monkeypatch):
    monkeypatch.setenv("STUDIO_VIDEOS", "off")
    assert REAL_UNAVAILABLE(studio={}) == "the videos are turned off here (STUDIO_VIDEOS)"
    knobs.use({"knobs": {"STUDIO_VIDEOS": "on"}})
    assert "turned off" not in (REAL_UNAVAILABLE(studio={}) or "")
    monkeypatch.delenv("STUDIO_VIDEOS")
    knobs.use({"knobs": {"STUDIO_VIDEOS": "off"}})
    assert REAL_UNAVAILABLE(studio={}) == "the videos are turned off here (STUDIO_VIDEOS, set on /admin/studio)"


def test_recover_takes_nothing_once_the_studio_page_turns_the_videos_off(setup, monkeypatch):
    monkeypatch.setattr(videos, "unavailable", REAL_UNAVAILABLE)
    monkeypatch.delenv("STUDIO_VIDEOS", raising=False)
    api = VideoApi()
    api.queue = [{"answerId": "ans9", "questionId": "q9", "attempt": 2, "bundle": bundle_data(), "factcheck": FACTCHECK}]
    api.studio_settings = lambda: {"knobs": {"STUDIO_VIDEOS": "off"}}
    assert videos.recover(api=api) == ("NONE", "recover", "the videos are turned off here (STUDIO_VIDEOS, set on /admin/studio)")
    assert claims(api) == []  # no attempt taken from the queue


# --- a topic's own brain, picked by an admin on /learn ----------------------------------------


def test_a_topic_sent_to_claude_is_painted_by_gemini_and_sam_finds_its_structures(meta_job, monkeypatch):
    """Claude writes and checks the lesson; Gemini 3.1 Flash Image paints the art by default, and SAM 3.1 finds the
    structures in it, while the teacher's own brain is Muse's."""
    from studio import brain
    from studio.tests.test_gemini import FakeGemini

    painter = FakeGemini().install(monkeypatch)
    operator_run.keep_brain(meta_job.job, "claude")
    assert videos.videos("q1", api=VideoApi()) == ("DONE", "upload", "upload made")
    asked = {re.sub(r" try \d+$", "", label) for label, _, _ in meta_job.muse.segmented}  # in plainer words too
    assert len(painter.painted) == 3 and meta_job.muse.painted == [] and len(asked) == 4
    assert meta_job.artist.labels() == []  # not the SVG artist either
    assert brain.chosen_name() is None


def test_a_job_from_before_the_brain_was_kept_takes_it_from_the_claim(meta_job):
    """Its lesson was written before brain.json existed: the videos run on the topic's own brain if it has one, else
    on the default the claim names, not on the studio's own fallback."""
    (meta_job.job / operator_run.BRAIN_FILE).unlink()
    api = VideoApi()
    api.studio = {"brain": {"default": "muse-wonder", "effort": "high"}, "art": {"claude": "gemini", "meta": "muse"}}
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert operator_run.job_brain(meta_job.job) == "muse-wonder"
    assert len(meta_job.muse.painted) == 3  # Muse's painter, as the default brain is a Muse


def test_the_claim_that_starts_a_run_sets_its_painter_for_the_whole_run(meta_job, monkeypatch):
    from studio.tests.test_gemini import FakeGemini

    painter = FakeGemini().install(monkeypatch)
    api = VideoApi()
    api.studio = {"art": {"claude": "gemini", "meta": "gemini"}}  # an admin has Gemini paint for Muse's brain too
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert len(painter.painted) == 3 and meta_job.muse.painted == []
    assert jobs.studio_settings(meta_job.job) == api.studio


def test_recover_keeps_the_topics_brain_so_the_new_machine_makes_the_art_the_same_way(setup):
    api = VideoApi()
    api.attempt = 2
    api.queue = [{"answerId": "ans9", "questionId": "q9", "attempt": 2, "bundle": bundle_data(), "factcheck": FACTCHECK,
                  "brain": "muse-spark", "studio": {"art": {"claude": "gemini", "meta": "gemini"}}}]
    assert videos.recover(api=api)[0] == "RECOVER"
    assert operator_run.job_brain(jobs.ROOT / "q9") == "muse-spark"
    assert jobs.studio_settings(jobs.ROOT / "q9") == {"art": {"claude": "gemini", "meta": "gemini"}}

    # A topic with no brain of its own is rebuilt on the default brain the settings name.
    api.queue = [{"answerId": "ans8", "questionId": "q8", "attempt": 2, "bundle": bundle_data(), "factcheck": FACTCHECK,
                  "brain": None, "studio": {"brain": {"default": "muse-wonder", "effort": "high"}}}]
    assert videos.recover(api=api)[0] == "RECOVER"
    assert operator_run.job_brain(jobs.ROOT / "q8") == "muse-wonder"


def test_the_videos_need_the_keys_of_the_painter_and_sam(setup, monkeypatch):
    monkeypatch.setattr(videos, "unavailable", REAL_UNAVAILABLE)
    for name in ("MODEL_API_KEY", "GEMINI_API_KEY",
                 "STUDIO_VIDEOS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")  # Claude's brain is set up; the art's keys are not
    api = VideoApi()
    job = posted()
    wrong = ("the studio's settings are wrong: the art needs GEMINI_API_KEY and MODEL_API_KEY: Gemini 3.1 Flash Image "
             "paints it and SAM 3.1 finds its structures (an admin can pick another painter on /admin/studio)")
    assert videos.videos("q1", api=api) == ("STOPPED", "videos", wrong)
    assert claims(api) == [] and setup.ran == []

    # The claim says an admin has Claude draw it, which needs neither key: the setting kept, the run goes on.
    jobs.keep_studio_settings(job, {"art": {"claude": "svg"}})
    assert videos.videos("q1", api=api)[0] == "DONE"

    # A claim that switches to a painter this machine has no key for stops before any step, the attempt held.
    for step in media.MEDIA_STEPS:
        (job / f".{step}.done").unlink(missing_ok=True)
    api.studio, setup.ran = {"art": {"claude": "gemini"}}, []
    result, step, reason = videos.videos("q1", api=api)
    assert (result, step) == ("STOPPED", "videos") and "GEMINI_API_KEY" in reason and setup.ran == []


def test_the_videos_check_the_settings_of_the_topics_brain(setup, monkeypatch):
    monkeypatch.setattr(videos, "unavailable", REAL_UNAVAILABLE)
    for name in ("MODEL_API_KEY", "STUDIO_VIDEOS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    api = VideoApi()
    operator_run.keep_brain(posted(), "muse-spark")
    wrong = "the studio's settings are wrong: Muse Spark 1.3 needs MODEL_API_KEY"
    assert videos.videos("q1", api=api) == ("STOPPED", "videos", wrong)
    assert videos.background("q1", api=api) == ("STOPPED", "videos", wrong)
    assert claims(api) == [] and setup.ran == []


def test_a_painter_changed_on_the_page_since_the_lesson_unblocks_the_videos(setup, monkeypatch):
    """The lesson's last claim said Gemini paints, and this machine has no Gemini key, so the videos stopped. The
    admin picks Claude's SVG on the page: the next `videos` reads the settings as they are now, before it checks
    the keys, and goes on, rather than stopping on the old ones for good."""
    monkeypatch.setattr(videos, "unavailable", REAL_UNAVAILABLE)
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    job = posted()
    operator_run.keep_brain(job, "claude")
    jobs.keep_studio_settings(job, {"art": {"claude": "gemini", "meta": "muse"}})
    api = VideoApi()
    assert videos.videos("q1", api=api)[:2] == ("STOPPED", "videos")
    api.studio_settings = lambda: {"brain": {"default": "claude"}, "art": {"claude": "svg", "meta": "muse"}}
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert jobs.studio_settings(job)["art"]["claude"] == "svg"
    api2 = VideoApi()  # and the background start reads them too
    for step in media.MEDIA_STEPS:
        (job / f".{step}.done").unlink(missing_ok=True)
    jobs.keep_studio_settings(job, {"art": {"claude": "gemini", "meta": "muse"}})
    api2.studio_settings = api.studio_settings
    monkeypatch.setattr(videos.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=999999))
    assert videos.background("q1", api=api2)[0] == "STARTED"


def test_recover_takes_the_brain_from_the_model_that_wrote_the_lesson(setup):
    """A topic on the default brain has no brain of its own on the answer: the model that wrote its lesson says
    whose it was, so a new machine paints with that brain's painter, whatever the default is today."""
    api = VideoApi()
    api.attempt = 2
    api.queue = [{"answerId": "ans7", "questionId": "q7", "attempt": 2, "bundle": bundle_data(), "factcheck": FACTCHECK,
                  "brain": None, "model": "muse-wonder-1.0", "studio": {"brain": {"default": "claude"}}}]
    assert videos.recover(api=api)[0] == "RECOVER"
    assert operator_run.job_brain(jobs.ROOT / "q7") == "muse-wonder"


def test_a_job_from_before_the_brain_was_kept_takes_the_one_its_lesson_was_written_on(meta_job):
    (meta_job.job / operator_run.BRAIN_FILE).unlink()
    (meta_job.job / "calls.jsonl").write_text(json.dumps({"label": "bundle", "model": "muse-spark-1.3"}) + "\n")
    api = VideoApi()
    api.studio = {"brain": {"default": "claude"}, "art": {"claude": "gemini", "meta": "muse"}}
    assert videos.videos("q1", api=api)[0] == "DONE"
    assert operator_run.job_brain(meta_job.job) == "muse-spark"
    assert len(meta_job.muse.painted) == 3


def test_a_song_lyria_sings_needs_the_gemini_key_before_anything_is_claimed(setup, monkeypatch):
    monkeypatch.setattr(videos, "unavailable", REAL_UNAVAILABLE)
    monkeypatch.setenv("POPSMOSIS_ANTHROPIC_API_KEY", "k")
    job = posted()
    operator_run.keep_brain(job, "claude")
    jobs.keep_studio_settings(job, {"art": {"claude": "svg"}, "song": {"provider": "gemini"}})
    api = VideoApi()
    result, step, reason = videos.videos("q1", api=api)
    assert (result, step) == ("STOPPED", "videos")
    assert reason == ("the studio's settings are wrong: the song needs GEMINI_API_KEY: Lyria sings it "
                      "(an admin can pick ElevenLabs on /admin/studio)")
    assert claims(api) == [] and setup.ran == []
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert videos.videos("q1", api=api)[0] == "DONE"


# --- `python -m studio make`: a local job's videos, with no API --------------------------------------


def test_make_runs_a_local_jobs_videos_in_videos_order_with_no_api_and_no_upload(setup):
    job = jobs.ROOT / "local-job"
    job.mkdir()  # from `plan`: no posted lesson, no claim
    assert videos.local(job) == (videos.DONE, "render", "render made")
    assert sorted(setup.ran[:2]) == ["art", "audio"]  # side by side (the stand-ins wait for each other)
    assert setup.ran[2:] == ["locate", "timing", "timeline", "render"] or \
        setup.ran[2:] == ["timing", "locate", "timeline", "render"]
    assert "upload" not in setup.ran
    run = operator_run.read_jsonl(job / "runs.jsonl")[-1]  # kept, so `show` has each step's time
    assert run["result"] == videos.DONE and {s["step"] for s in run["steps"]} == {
        "art", "locate", "audio", "timing", "timeline", "render"}

    setup.ran.clear()
    assert videos.local(job) == (videos.DONE, "render", "the videos were already made")
    assert setup.ran == []


def test_make_resumes_at_the_step_that_failed(setup):
    job = jobs.ROOT / "local-job"
    job.mkdir()
    setup.fail["locate"] = 1
    assert videos.local(job) == (videos.FAILED, "locate", "locate broke")
    setup.ran.clear()
    assert videos.local(job)[0] == videos.DONE
    assert setup.ran == ["locate", "timeline", "render"]
