import json
import threading

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
        {"model": "claude-opus-5-5", "calls": 1, "costUsd": 0.4, "credits": None},
        {"model": "claude-opus-5", "calls": 1, "costUsd": 0.5, "credits": None}]  # after a refusal fell back
    assert steps["factcheck"]["models"] == [{"model": "claude-opus-5-5", "calls": 2, "costUsd": 0.25, "credits": None}]
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
        {"step": "timing", "seconds": 110.0}, {"step": "timeline", "seconds": 0.3}, {"step": "render", "seconds": 157.0}]}))
    report = ledger.breakdown(tmp_path, "videos", running=("upload", 12.0))
    assert [s["step"] for s in report["steps"]] == ["art", "locate", "audio", "timing", "timeline", "render", "upload"]
    assert report["seconds"] == pytest.approx(95.0 + 110.0 + 0.3 + 157.0 + 12.0)  # the audio branch is the longer


def test_an_interrupted_try_counts_without_a_time(tmp_path):
    write_runs(tmp_path, {"part": "videos", "result": "FAILED",
                          "steps": [{"step": "render", "seconds": 40.0}, {"step": "upload", "seconds": None}]})
    [render, upload] = ledger.breakdown(tmp_path, "videos")["steps"]
    assert (render["seconds"], upload["seconds"], upload["tries"]) == (40.0, None, 1)


def test_elevenlabs_without_a_price_keeps_its_credits(tmp_path):
    with ledger.at_step("audio"):
        ledger.log(tmp_path, [call("song", model="lyria-3.5", cost=0.08, role="audio"),
                              call("narration", model="eleven_multilingual_v2", cost=None, role="audio", credits=2900),
                              call("frq", model="eleven_multilingual_v2", cost=None, role="audio", credits=400)])
    [audio] = ledger.breakdown(tmp_path, "videos")["steps"]
    assert audio["costUsd"] == 0.08 and audio["seconds"] is None and audio["tries"] == 0
    assert audio["models"][1] == {"model": "eleven_multilingual_v2", "calls": 2, "costUsd": None, "credits": 3300}
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


def test_a_job_with_nothing_done_has_an_empty_breakdown(tmp_path):
    assert ledger.breakdown(tmp_path, "videos") == {"steps": [], "seconds": None}


def test_every_step_the_runners_run_has_a_place():
    from studio import media

    assert operator_run.STEPS == ledger.PARTS["lesson"] and media.MEDIA_STEPS == ledger.PARTS["videos"]
    assert set(ledger.LABEL_STEPS.values()) <= set(operator_run.STEPS) | set(media.MEDIA_STEPS)
