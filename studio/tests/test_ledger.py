import json
import subprocess
import threading
import time

import pytest

from studio import brain, ledger
from studio import run as operator_run


def call(label, model="claude-opus-5-5", cost=0.1, seconds=1.0, role="brain", credits=None):
    return brain.Call(label, role, model, 100, 50, cost, seconds, "end_turn", credits=credits)


def write_runs(job, *runs):
    (job / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))


def test_a_call_is_tagged_with_the_step_its_thread_is_on(tmp_path):
    with ledger.at_step("bundle"):
        ledger.log(tmp_path, [call("bundle")])
        with ledger.at_step("art"):
            ledger.log(tmp_path, [call("art hook")])
        ledger.log(tmp_path, [call("bundle again")])
    ledger.log(tmp_path, [call("by hand")])
    assert [(r["label"], r["step"]) for r in ledger.read(tmp_path)] == [
        ("bundle", "bundle"), ("art hook", "art"), ("bundle again", "bundle"), ("by hand", None)]


def test_two_branches_each_tag_their_own_step(tmp_path):
    together = threading.Barrier(2, timeout=5)

    def branch(step):
        with ledger.at_step(step):
            together.wait()  # both threads are on their steps at once
            ledger.log(tmp_path, [call(step)])

    threads = [threading.Thread(target=branch, args=(s,)) for s in ("art", "audio")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted((r["label"], r["step"]) for r in ledger.read(tmp_path)) == [("art", "art"), ("audio", "audio")]


def test_logged_ask_tags_the_brains_calls_with_the_step(tmp_path, monkeypatch):
    def fake_ask(role, prompt, *args, label=None, **kwargs):
        brain._record(call(label))
        return {}

    monkeypatch.setattr(brain, "ask", fake_ask)
    with ledger.at_step("factcheck"):
        operator_run.logged_ask(tmp_path)("brain", "p", label="factcheck round 1")
    [row] = ledger.read(tmp_path)
    assert row["step"] == "factcheck" and row["label"] == "factcheck round 1" and row["cost_usd"] == 0.1


def test_the_lesson_counts_every_try_and_every_model_that_answered(tmp_path):
    write_runs(tmp_path,
               {"seconds": 70.0, "result": "FAILED", "step": "bundle",
                "steps": [{"step": "request", "seconds": 0.5}, {"step": "bundle", "seconds": 60.0}]},
               {"seconds": 200.0, "result": "DONE", "step": "post",
                "steps": [{"step": "bundle", "seconds": 120.0}, {"step": "factcheck", "seconds": 79.4},
                          {"step": "post", "seconds": 0.6}]},
               {"part": "videos", "seconds": 300.0, "steps": [{"step": "art", "seconds": 76.0}]})
    with ledger.at_step("bundle"):
        ledger.log(tmp_path, [call("bundle", cost=0.4), call("bundle", model="claude-opus-5", cost=0.5)])
    with ledger.at_step("factcheck"):
        ledger.log(tmp_path, [call("factcheck round 1", cost=0.2), call("factcheck round 2", cost=0.05)])
    with ledger.at_step("art"):
        ledger.log(tmp_path, [call("art hook", model="claude-haiku-5-5", cost=0.002)])

    report = ledger.breakdown(tmp_path, "lesson")
    assert report["seconds"] == 270.0  # the two runs as they recorded themselves; the videos are not the lesson's
    steps = {s["step"]: s for s in report["steps"]}
    assert list(steps) == ["request", "bundle", "factcheck", "post"]
    assert (steps["bundle"]["seconds"], steps["bundle"]["tries"]) == (180.0, 2)
    assert steps["bundle"]["costUsd"] == pytest.approx(0.9)
    assert steps["bundle"]["models"] == [
        {"model": "claude-opus-5-5", "calls": 1, "costUsd": 0.4, "credits": None, "task": "writes the lesson and/or song"},
        {"model": "claude-opus-5", "calls": 1, "costUsd": 0.5, "credits": None,
         "task": "writes the lesson and/or song"}]  # after a refusal fell back
    assert steps["factcheck"]["models"] == [{"model": "claude-opus-5-5", "calls": 2, "costUsd": 0.25, "credits": None,
                                             "task": "fact-checks every line"}]
    assert steps["request"]["models"] == [] and steps["request"]["costUsd"] == 0


def test_the_run_still_going_and_the_step_sending_it_count(tmp_path):
    write_runs(tmp_path, {"seconds": 5.0, "steps": [{"step": "request", "seconds": 5.0}]})
    (tmp_path / "run.current.json").write_text(json.dumps(
        {"started": "x", "step": "post", "steps": [{"step": "bundle", "seconds": 100.0, "result": "DONE"}]}))
    report = ledger.breakdown(tmp_path, "lesson", running=("post", 0.5))
    assert [(s["step"], s["seconds"]) for s in report["steps"]] == [("request", 5.0), ("bundle", 100.0), ("post", 0.5)]
    assert report["seconds"] == 105.5


def test_the_art_and_the_audio_are_timed_side_by_side(tmp_path):
    (tmp_path / "run.current.json").write_text(json.dumps({"part": "videos", "steps": [
        {"step": "art", "seconds": 76.0}, {"step": "audio", "seconds": 95.0}, {"step": "locate", "seconds": 0.1},
        {"step": "timing", "seconds": 110.0}, {"step": "timeline", "seconds": 0.3}, {"step": "arrows", "seconds": 20.0},
        {"step": "render", "seconds": 157.0}]}))
    report = ledger.breakdown(tmp_path, "videos", running=("upload", 12.0))
    assert [s["step"] for s in report["steps"]] == ["art", "locate", "audio", "timing", "timeline", "arrows", "render",
                                                    "upload"]
    assert report["seconds"] == pytest.approx(95.0 + 110.0 + 0.3 + 20.0 + 157.0 + 12.0)  # the audio branch is the longer


def test_an_interrupted_try_counts_without_a_time(tmp_path):
    write_runs(tmp_path, {"part": "videos", "result": "FAILED",
                          "steps": [{"step": "render", "seconds": 40.0}, {"step": "upload", "seconds": None}]})
    [render, upload] = ledger.breakdown(tmp_path, "videos")["steps"]
    assert (render["seconds"], upload["seconds"], upload["tries"]) == (40.0, None, 1)


def test_elevenlabs_without_a_price_keeps_its_credits(tmp_path):
    with ledger.at_step("audio"):
        ledger.log(tmp_path, [call("song", model="lyria-3.5", cost=0.08, role="audio"),
                              call("narration", model="eleven_multilingual_v2", cost=None, role="audio", credits=2900),
                              call("narration", model="eleven_multilingual_v2", cost=None, role="audio", credits=400)])
    [audio] = ledger.breakdown(tmp_path, "videos")["steps"]
    assert audio["costUsd"] == 0.08 and audio["seconds"] is None and audio["tries"] == 0
    assert audio["models"][1] == {"model": "eleven_multilingual_v2", "calls": 2, "costUsd": None, "credits": 3300,
                                  "task": "reads the narration"}
    assert ledger.cost_text(ledger.read(tmp_path)) == "$0.08 + 3300 credits"


def test_a_call_logged_before_calls_had_a_step_is_placed_by_its_label(tmp_path):
    (tmp_path / "calls.jsonl").write_text(
        json.dumps({"label": "bundle", "model": "claude-opus-5-5", "cost_usd": 0.5}) + "\n"
        + json.dumps({"label": "factcheck round 2", "model": "claude-opus-5-5", "cost_usd": 0.1}) + "\n"
        + json.dumps({"label": "something else", "model": "m", "cost_usd": 1.0}) + "\n")
    assert [s["step"] for s in ledger.breakdown(tmp_path, "lesson")["steps"]] == ["bundle", "factcheck"]
    assert ledger.as_call(ledger.read(tmp_path)[0] | {"step": "bundle", "at": "now", "input_tokens": 1,
                                                       "output_tokens": 2, "seconds": 3.0, "role": "brain",
                                                       "stop_reason": "end_turn"}).cost_usd == 0.5


def test_records_that_cannot_be_read_leave_the_breakdown_out_rather_than_failing(tmp_path):
    (tmp_path / "runs.jsonl").write_text('{"steps": [')
    assert ledger.try_breakdown(tmp_path, "lesson") is None


def test_a_breakdown_bigger_than_the_api_takes_is_left_out_rather_than_refused(tmp_path, monkeypatch):
    with ledger.at_step("bundle"):
        ledger.log(tmp_path, [call("bundle")])
    assert ledger.try_breakdown(tmp_path, "lesson")
    monkeypatch.setattr(ledger, "MAX_BYTES", 10)
    assert ledger.try_breakdown(tmp_path, "lesson") is None


def test_progress_is_the_steps_running_the_failed_ones_and_the_bill_so_far(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger.time, "monotonic", lambda: 100.0)
    write_runs(tmp_path, {"part": "videos", "seconds": 50.0, "result": "FAILED", "step": "art",
                          "steps": [{"step": "art", "seconds": 50.0, "result": "FAILED"}]})
    with ledger.at_step("audio"):
        ledger.log(tmp_path, [call("song", model="music_v1", cost=None, credits=2000)])
    report = ledger.progress(tmp_path, "videos", {"timing": 98.0, "art": 90.0}, failed=("art", "locate"))
    # In the order the steps run, with how long each has run; a failed step running again is running, not failed.
    assert report["running"] == [{"step": "art", "seconds": 10.0}, {"step": "timing", "seconds": 2.0}]
    assert report["failed"] == ["locate"]
    # The running audio's call is billed already; the step has no time of its own until it ends.
    assert [(s["step"], s["tries"]) for s in report["breakdown"]["steps"]] == [("art", 1), ("audio", 0)]
    ended = ledger.progress(tmp_path, "videos", {}, ending=("audio", 30.0))
    assert [(s["step"], s["tries"], s["seconds"]) for s in ended["breakdown"]["steps"]] == [("art", 1, 50.0), ("audio", 1, 30.0)]


def test_a_job_with_nothing_done_has_an_empty_breakdown(tmp_path):
    assert ledger.breakdown(tmp_path, "videos") == {"steps": [], "seconds": None}


def test_the_pages_checklist_has_the_studios_steps_in_its_order():
    """The page shows every step before it runs (web/src/components/DropletBreakdown.tsx): a step renamed or added
    here and not there would be an unnamed line at the end of the bill, and a branch joined wrongly there would
    hide a step still to come."""
    import re

    from studio import media, videos

    source = (videos.REPO / "web/src/components/DropletBreakdown.tsx").read_text(encoding="utf-8")

    def listed(name):
        return re.findall(r"'(\w+)'", re.search(rf"const {name}[^=]*= \[([^\]]*)\]", source).group(1))

    labels = re.search(r"const STEP_LABELS[^{]*\{([^}]*)\}", source).group(1)
    assert re.findall(r"^\s*(\w+):", labels, re.M) == [*ledger.LESSON_STEPS, *media.MEDIA_STEPS]
    assert listed("LESSON_STEPS") == [s for s in ledger.LESSON_STEPS if s != "post"]  # no bill has the post
    assert listed("VIDEO_STEPS") == list(media.MEDIA_STEPS)
    after = {step: re.findall(r"'(\w+)'", nexts)
             for step, nexts in re.findall(r"(\w+): \[([^\]]*)\]", re.search(r"const NEXT[^{]*\{([^}]*)\}", source).group(1))}
    for branch in videos.BRANCHES:
        for step, following in zip(branch, (*branch[1:], videos.TAIL[0])):
            assert following in after[step], (step, following)
    for step, following in zip(videos.TAIL, videos.TAIL[1:]):
        assert following in after[step], (step, following)
    assert {branch[0] for branch in videos.BRANCHES} <= set(after["post"]) & set(after["factcheck"])


def test_every_step_the_runners_run_has_a_place():
    from studio import media

    assert operator_run.STEPS == ledger.PARTS["lesson"] and media.MEDIA_STEPS == ledger.PARTS["videos"]
    assert set(ledger.LABEL_STEPS.values()) <= set(operator_run.STEPS) | set(media.MEDIA_STEPS)
    assert set(ledger.TOOLS) <= set(media.MEDIA_STEPS)


# --- the software each step ran, and its version on this machine -------------------------------


def test_each_media_step_lists_the_software_it_runs_at_the_version_installed_here(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "version", {"demucs": "4.1.0", "torchaudio": "2.11.0", "torch": "2.11.0+cpu",
                                            "skia-python": "144.1", "ffmpeg": "7.1.1"}.get)
    (tmp_path / "run.current.json").write_text(json.dumps({"part": "videos", "steps": [
        {"step": "timing", "seconds": 110.0}, {"step": "timeline", "seconds": 0.3}, {"step": "render", "seconds": 157.0}]}))
    with ledger.at_step("timing"):
        ledger.log(tmp_path, [call("song vocals", model="htdemucs", cost=0.0, role="local")])
    steps = {s["step"]: s for s in ledger.breakdown(tmp_path, "videos")["steps"]}
    assert steps["timing"]["models"] == [{"model": "htdemucs", "calls": 1, "costUsd": 0.0, "credits": None,
                                          "task": "separates the vocals so the words can be timed"}]
    # Each tool with what it did in the step: the bill says what Demucs is for (the owner, Nov 3).
    assert steps["timing"]["tools"] == [
        {"name": "demucs", "version": "4.1.0", "task": "runs htdemucs"},
        {"name": "torchaudio", "version": "2.11.0", "task": "runs mms-fa"},
        {"name": "torch", "version": "2.11.0+cpu", "task": "runs the models"},
        {"name": "ffmpeg", "version": "7.1.1", "task": "decodes the audio for them"}]
    assert steps["render"]["tools"] == [{"name": "skia-python", "version": "144.1", "task": "draws the frames"},
                                        {"name": "ffmpeg", "version": "7.1.1", "task": "encodes the videos"}]
    assert steps["timeline"]["tools"] == []  # the studio's own code


def test_the_lesson_video_alone_times_the_voice_with_no_song_software(tmp_path, monkeypatch):
    """STUDIO_VIDEOS=lesson with a voice that came with its timings (ElevenLabs): no Demucs or aligner runs, so the bill
    does not say they did."""
    from studio import knobs

    monkeypatch.setattr(ledger, "version", lambda name: "1.0")
    (tmp_path / "run.current.json").write_text(json.dumps({"part": "videos", "steps": [
        {"step": "audio", "seconds": 2.0}, {"step": "timing", "seconds": 0.1}, {"step": "render", "seconds": 6.0}]}))
    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})
    steps = {s["step"]: s for s in ledger.breakdown(tmp_path, "videos")["steps"]}
    assert steps["timing"]["tools"] == []
    assert [t["name"] for t in steps["audio"]["tools"]] == ["ffmpeg"]  # ffprobe still reads the voice's length
    assert [t["name"] for t in steps["render"]["tools"]] == ["skia-python", "ffmpeg"]


def test_the_lesson_video_alone_lists_what_the_aligner_ran_on_when_it_timed_the_voice(tmp_path, monkeypatch):
    """STUDIO_VIDEOS=lesson with Gemini's voice, which has no timings of its own: MMS aligns it, so the bill lists what
    MMS ran on beside it. Demucs's package is still left out: there is no song to separate."""
    from studio import knobs

    monkeypatch.setattr(ledger, "version", lambda name: "1.0")
    (tmp_path / "run.current.json").write_text(json.dumps({"part": "videos", "steps": [{"step": "timing", "seconds": 9.0}]}))
    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})
    with ledger.at_step("timing"):
        ledger.log(tmp_path, [call("narration alignment", model="mms-fa", cost=0.0, role="local")])
    [timing] = ledger.breakdown(tmp_path, "videos")["steps"]
    assert [m["task"] for m in timing["models"]] == ["times the narration"]
    assert timing["tools"] == [{"name": "torchaudio", "version": "1.0", "task": "runs mms-fa"},
                               {"name": "torch", "version": "1.0", "task": "runs the models"},
                               {"name": "ffmpeg", "version": "1.0", "task": "decodes the audio for them"}]


def test_the_lessons_steps_run_no_software_of_their_own(tmp_path):
    with ledger.at_step("bundle"):
        ledger.log(tmp_path, [call("bundle")])
    assert [s["tools"] for s in ledger.breakdown(tmp_path, "lesson")["steps"]] == [[]]


def test_a_packages_version_is_the_installed_one_and_none_when_it_is_not_installed():
    import importlib.metadata

    assert ledger.version("pytest") == importlib.metadata.version("pytest")
    assert ledger.version("no-such-package-popsmosis") is None


def test_a_broken_install_costs_its_version_never_the_bill(tmp_path, monkeypatch):
    import importlib.metadata

    def broken(name):  # newer Pythons raise KeyError for an install whose metadata has no Version
        raise KeyError("Version")

    monkeypatch.setattr(importlib.metadata, "version", broken)
    ledger.version.cache_clear()
    (tmp_path / "run.current.json").write_text(json.dumps({"part": "videos", "steps": [{"step": "art", "seconds": 1.0}]}))
    [art] = ledger.try_breakdown(tmp_path, "videos")["steps"]
    assert art["tools"] == [{"name": "skia-python", "version": None, "task": "renders and sizes the pictures"}]
    ledger.version.cache_clear()  # what the fake said is not this machine's


def test_ffmpegs_version_is_read_from_ffmpeg_and_none_when_it_cannot_say(monkeypatch):
    def ffmpeg_says(stdout):
        def run(command, **kwargs):
            assert command == ["ffmpeg", "-version"]
            return subprocess.CompletedProcess(command, 0, stdout=stdout)
        return run

    def missing(command, **kwargs):
        raise FileNotFoundError("ffmpeg")

    for run, expected in [
        (ffmpeg_says("ffmpeg version 7.1.1-1ubuntu1 Copyright (c) 2000-2025 the FFmpeg developers\nbuilt with gcc"), "7.1.1-1ubuntu1"),
        (ffmpeg_says("something else entirely"), None),
        (ffmpeg_says(""), None),
        (missing, None),
    ]:
        ledger.version.cache_clear()
        monkeypatch.setattr(subprocess, "run", run)
        assert ledger.version("ffmpeg") == expected
    ledger.version.cache_clear()  # what the fakes said is not this machine's ffmpeg


# --- the seams: kills, full disks, numbers that are not numbers -----------------------------------


def test_a_line_cut_short_by_a_kill_is_skipped_so_the_post_can_still_read_the_log(tmp_path):
    with ledger.at_step("bundle"):
        ledger.log(tmp_path, [call("bundle", cost=0.5)])
    with (tmp_path / "calls.jsonl").open("a") as f:
        f.write('{"at": "now", "step": "factch')  # the process died mid-line
    assert [r["label"] for r in ledger.read(tmp_path)] == ["bundle"]
    assert operator_run.lesson_model(tmp_path) == "claude-opus-5-5"
    assert ledger.try_breakdown(tmp_path, "lesson")["steps"][0]["costUsd"] == 0.5


def test_a_log_that_cannot_be_written_never_fails_the_step(tmp_path, capsys):
    (tmp_path / "calls.jsonl").mkdir()  # open() fails, as on a full disk
    ledger.log(tmp_path, [call("song", model="lyria-3.5", cost=0.08)])
    assert "calls not logged" in capsys.readouterr().err


def test_a_number_that_is_not_one_is_never_logged_or_sent(tmp_path):
    ledger.log(tmp_path, [call("narration", cost=None, credits=float("nan"))])
    assert ledger.read(tmp_path) == []  # left out, not written as a NaN
    # One written by anything else still never reaches the API, whose JSON has no NaN.
    (tmp_path / "calls.jsonl").write_text(json.dumps({"step": "audio", "label": "song", "model": "m", "cost_usd": float("nan")}) + "\n")
    assert "NaN" in (tmp_path / "calls.jsonl").read_text()
    assert ledger.try_breakdown(tmp_path, "videos") is None


def test_a_run_killed_between_steps_keeps_the_bill(tmp_path):
    (tmp_path / "run.current.json").write_text(json.dumps(
        {"started": "x", "part": "videos", "step": "videos", "steps": [{"step": "art", "seconds": 76.0, "result": "DONE"}]}))
    operator_run.record_interrupted(tmp_path)  # appends a try of "videos", which is no step
    report = ledger.breakdown(tmp_path, "videos")
    assert [s["step"] for s in report["steps"]] == ["art"] and report["seconds"] == 76.0


def test_work_run_in_parallel_is_billed_to_the_step_that_started_it(tmp_path):
    seen = []

    def work(item):
        seen.append((item, ledger.current_step(), threading.get_ident()))
        ledger.log(tmp_path, [brain.Call(f"call {item}", "brain", "claude-haiku-5-5", 1, 1, 0.0, 0.1, "end_turn")])
        return item * 2

    with ledger.at_step("art"):
        assert ledger.parallel(work, [1, 2, 3, 4], workers=4) == [2, 4, 6, 8]  # in the items' order
    assert {step for _, step, _ in seen} == {"art"} and len({ident for *_, ident in seen}) > 1
    assert {row["step"] for row in ledger.read(tmp_path)} == {"art"}


def test_parallel_work_all_ends_before_the_first_failure_is_raised():
    ended = []

    def work(item):
        if item in (2, 3):
            raise ValueError(f"item {item} broke")
        time.sleep(0.05)
        ended.append(item)

    with pytest.raises(ValueError, match="item 2 broke"):  # the first in the items' order
        ledger.parallel(work, [1, 2, 3, 4], workers=4)
    assert sorted(ended) == [1, 4]  # the others were not cut short


# --- the ceiling ------------------------------------------------------------------------------


def claimed(job, spend="unset"):
    """A job as a claim left it: settings.json with the admin's spend ceiling, or without one (an older API)."""
    settings = {"art": {"claude": "gemini", "meta": "muse"}}
    if spend != "unset":
        settings["spend"] = spend
    (job / "settings.json").write_text(json.dumps(settings))


def test_the_ceiling_is_the_admins_setting_as_the_claim_said(tmp_path):
    assert ledger.ceiling(tmp_path) is None  # never claimed: a local job has none
    claimed(tmp_path)
    assert ledger.ceiling(tmp_path) == ledger.DEFAULT_TOPIC_USD  # an API older than the setting
    claimed(tmp_path, {"topicUsd": 2.5})
    assert ledger.ceiling(tmp_path) == 2.5
    claimed(tmp_path, {"topicUsd": 0})
    assert ledger.ceiling(tmp_path) is None  # 0: no ceiling
    for broken in ({"topicUsd": "lots"}, {"topicUsd": -1}, {"topicUsd": True}, {"topicUsd": float("nan")}, "cheap"):
        claimed(tmp_path, broken)
        assert ledger.ceiling(tmp_path) == ledger.DEFAULT_TOPIC_USD  # the safe side, never none


def test_every_priced_call_of_the_job_counts_and_the_ceiling_stops_the_next(tmp_path):
    claimed(tmp_path, {"topicUsd": 1.0})
    with ledger.at_step("bundle"):
        ledger.log(tmp_path, [call("bundle", cost=0.6)])
    with ledger.at_step("audio"):
        ledger.log(tmp_path, [call("song", model="music_v1", cost=None, credits=900)])  # no price: counts as nothing
    ledger.check_ceiling(tmp_path)  # $0.60 of $1.00
    with ledger.at_step("art"):
        ledger.log(tmp_path, [call("art hook", cost=0.25), call("art check hook", cost=0.15)])
    assert ledger.spent(tmp_path) == pytest.approx(1.0)
    with pytest.raises(ledger.OverCeiling, match=r"cost \$1\.00, past the \$1\.00 a topic may cost"):
        ledger.check_ceiling(tmp_path)
    claimed(tmp_path, {"topicUsd": 0})
    ledger.check_ceiling(tmp_path)  # an admin lifted it: the next claim carries that


def test_logged_ask_makes_no_call_once_the_topic_is_past_its_ceiling(tmp_path, monkeypatch):
    asked = []

    def fake_ask(role, prompt, *args, label=None, **kwargs):
        asked.append(label)
        brain._record(call(label, cost=0.5))
        return {}

    monkeypatch.setattr(brain, "ask", fake_ask)
    claimed(tmp_path, {"topicUsd": 0.75})
    ask = operator_run.logged_ask(tmp_path)
    ask("brain", "p", label="factcheck round 1")
    ask("brain", "p", label="factcheck round 2")  # starts at $0.50: under the ceiling, so it runs and ends past it
    with pytest.raises(ledger.OverCeiling):
        ask("brain", "p", label="factcheck round 3")
    assert asked == ["factcheck round 1", "factcheck round 2"]


def test_each_line_says_what_its_calls_did_and_the_checker_is_apart_from_the_painter(tmp_path):
    # The bill says what checked the pictures, apart from what painted them (the owner, Nov 3).
    with ledger.at_step("art"):
        ledger.log(tmp_path, [call("art overview", model="muse-image-1.0", cost=0.01, role="meta"),
                              call("art check overview", model="claude-sonnet-5-5", cost=0.02, role="checker"),
                              call("art rewrite overview", cost=0.02, role="brain"),
                              call("art check overview try 2", model="claude-sonnet-5-5", cost=0.02, role="checker")])
    art = ledger.breakdown(tmp_path, "videos")["steps"][0]
    assert [(m["model"], m["calls"], m.get("task")) for m in art["models"]] == [
        ("muse-image-1.0", 1, "makes the pictures"), ("claude-sonnet-5-5", 2, "checks the pictures"),
        ("claude-opus-5-5", 1, "rewrites a scene's words")]
    # The same words mean different work by step; a label it does not know leaves the line unlabelled.
    assert ledger.task_of({"label": "song", "step": "audio"}) == "sings the song"
    assert ledger.task_of({"label": "song", "step": "timing"}) == "times the song"
    assert ledger.task_of({"label": "something new", "step": "art"}) == ""


def test_every_label_the_studio_logs_says_what_its_call_did():
    """Each label as the code logs it (the step's module in the comment), and what its line on the bill says."""
    said = {
        ("bundle", "script"): "writes the script",  # steps/script.py
        ("bundle", "bundle"): "writes the lesson and/or song", ("bundle", "bundle again"): "writes the lesson and/or song",  # steps/bundle.py
        ("bundle", "fact audit"): "audits the facts", ("bundle", "fact audit again"): "audits the facts",  # audit.py
        ("bundle", "learner"): "takes the lesson as a student",  # steps/learner.py: the student, then its grader
        ("bundle", "learner grade"): "grades the student's answers",
        ("bundle", "lesson rewrite"): "rewrites the lesson",
        ("bundle", "learner again"): "takes the lesson as a student",
        ("bundle", "learner again grade"): "grades the student's answers",
        ("bundle", "narration edit"): "edits the narration",  # steps/narration.py
        ("factcheck", "factcheck round 1"): "fact-checks every line",  # steps/factcheck.py
        ("factcheck", "lyric rewrite round 1"): "rewrites the lyrics",  # steps/songwriter.py
        ("art", "art overview try 2"): "makes the pictures",  # steps/art.py
        ("art", "art check overview"): "checks the pictures",
        ("art", "art rewrite overview"): "rewrites a scene's words",
        ("locate", "locate overview pump try 2"): "finds the parts",  # steps/locate.py: SAM
        ("locate", "locate overview pump box"): "finds the parts",  # SAM again, in the helper's box
        ("locate", "locate box overview pump"): "boxes a part SAM missed",  # the helper's box
        ("locate", "label check overview"): "checks the labels",
        ("locate", "repair overview"): "finds upside-down parts",  # repair.py
        ("locate", "repair check overview"): "checks the flipped picture",
        ("audio", "song"): "sings the song", ("audio", "narration"): "reads the narration",  # steps/audio.py
        ("timing", "song vocals"): "separates the vocals so the words can be timed",  # steps/timing.py: Demucs
        ("timing", "song alignment"): "times the song",  # MMS
        ("timing", "narration alignment"): "times the narration",
        ("arrows", "arrows point overview"): "places the arrows",  # steps/arrows.py
        ("arrows", "arrows look overview ATP pump > cell"): "checks the arrows",
    }
    assert {key: ledger.task_of({"label": key[1], "step": key[0]}) for key in said} == said


def test_the_bill_names_the_aligner_as_the_timing_step_logs_it():
    from studio.steps import timing

    assert ledger.ALIGNER == timing.MMS_MODEL
    assert set(ledger.ALIGNER_TOOLS) < set(ledger.TOOLS["timing"])


def test_every_tool_on_the_bill_says_what_it_did_in_its_step():
    for step, tools in ledger.TOOLS.items():
        for tool, task in tools.items():
            assert task.strip() and len(task) <= 60, (step, tool)  # the API keeps 60 characters
