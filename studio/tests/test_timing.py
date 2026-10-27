import json

import numpy as np
import pytest

from studio import jobs, media
from studio.schemas import Timing, timing_problems
from studio.steps import audio, timing
from studio.steps.timing import spoken, timing_from_chars, words_of
from studio.tests import sample_media
from studio.tests.test_audio import FakeEleven


def test_words_lose_the_punctuation_around_them_but_keep_their_own():
    assert [w for w, _, _ in words_of('"Every electron hand-off, pumps — a proton!"')] == [
        "Every", "electron", "hand-off", "pumps", "a", "proton"]
    text = "Wait... (really)?"
    assert [(w, text[a:b]) for w, a, b in words_of(text)] == [("Wait", "Wait"), ("really", "really")]


def test_words_are_spelled_the_way_they_are_sung():
    assert spoken("Protons") == "protons"
    assert spoken("ATP") == "eitipi"  # said letter by letter
    assert spoken("b6f") == "bisixef"
    assert spoken("42") == "fortytwo"
    assert spoken("don't") == "don't"
    assert spoken("café") == "cafe"
    assert spoken("+") == ""


def _alignment(text, step=0.1):
    return {"characters": list(text), "character_start_times_seconds": [i * step for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) * step for i in range(len(text))]}


def test_speech_words_come_from_the_character_timings():
    text = "Hi there. Bye now!"
    spans = [{"id": "a", "start": 0, "end": 9}, {"id": "b", "start": 10, "end": 18}]
    timed = timing_from_chars("narration", text, spans, _alignment(text), 2.0)
    assert timing_problems(timed, ["a", "b"]) == []
    first, second = timed.lines
    assert [(w.word, w.start, w.end) for w in first.words] == [("Hi", 0.0, 0.2), ("there", 0.3, 0.8)]
    assert (second.start, second.end) == (1.0, 1.7)
    assert timed.duration == 2.0 and timed.audio == "audio/narration.mp3"


def test_speech_timings_survive_characters_the_tts_dropped_or_changed():
    text = "Use 2 steps."
    heard = "Use two steps."  # a normalised reading, one character longer
    alignment = _alignment(heard)
    timed = timing_from_chars("frq", text, [{"id": "x", "start": 0, "end": len(text)}], alignment, 5.0)
    words = timed.lines[0].words
    assert [w.word for w in words] == ["Use", "2", "steps"]
    assert all(a.end <= b.start for a, b in zip(words, words[1:]))


# --- the song ---------------------------------------------------------------------------------


@pytest.fixture
def made(tmp_path, monkeypatch):
    """The sample job with fake audio: the plan and character timings a real run would leave."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(audio, "send", FakeEleven())
    monkeypatch.setattr(audio, "duration", lambda path: 30.1)
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    audio.run(job)
    return job


def even_aligner(calls, pitch=0.6, score=0.9):
    """Sings the words on a steady beat from just after the margin: where the section is planned to start."""
    def align(window, words):
        calls.append((window.shape[-1], words))
        at = timing.MARGIN_S + 0.2
        return [(at + i * pitch, at + (i + 0.8) * pitch, score) for i in range(len(words))]
    return align


def test_the_song_is_aligned_section_by_section_in_song_time(made):
    calls = []
    bundle = sample_media.bundle()
    vocals = np.zeros((1, int(30.1 * timing.SAMPLE_RATE)), dtype=np.float32)
    timed, report = timing.song_timing(made, bundle, align=even_aligner(calls), vocals=vocals)
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []
    assert len(calls) == 6  # one per section
    assert calls[0][1] == ["light", "lands", "on", "the", "thylakoid", "every", "electron", "handoff", "pumps", "a",
                           "proton"]
    assert calls[1][1] == ["protons", "flow", "and", "eitipi", "grows"]
    chorus = timed.line("c1-1")
    assert 8.6 <= chorus.start < chorus.end <= 12.9  # inside the chorus's planned place
    assert [w.word for w in chorus.words] == ["Protons", "flow", "and", "ATP", "grows"]
    assert report["drifting"] == [] and report["unsure"] == []


def test_a_chorus_sung_slower_is_flagged_and_a_mumbled_line_is_unsure(made):
    bundle = sample_media.bundle()
    vocals = np.zeros((1, int(30.1 * timing.SAMPLE_RATE)), dtype=np.float32)
    seen = []

    def align(window, words):
        seen.append(words)
        if len(seen) == 4:  # the second chorus: sung twice as fast, and unsure
            return even_aligner([], pitch=0.3, score=0.1)(window, words)
        return even_aligner([])(window, words)

    timed, report = timing.song_timing(made, bundle, align=align, vocals=vocals)
    assert report["drifting"] == ["c2-1"]
    assert report["unsure"] == ["c2-1"]
    assert "DRIFT over 0.5s: c2-1" in timing.song_notes(report)


def test_the_step_writes_all_three_timings_and_reports_drift(made, monkeypatch):
    monkeypatch.setattr(timing, "separate_vocals", lambda job: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([]))
    monkeypatch.setattr(audio, "duration", lambda path: 30.1)
    summary = timing.run(made)
    assert summary.startswith("song 7 lines in 30.1s, chorus drift at most")
    assert timing.is_done(made)
    bundle = sample_media.bundle()
    expected = {"song": [line.id for line in bundle.lyrics], "narration": [n.id for n in bundle.narration],
                "frq": [i for i, _ in media.frq_lines(bundle)]}
    for kind, ids in expected.items():
        assert timing_problems(jobs.read(made / media.timing_path(kind), Timing), ids) == []
    report = json.loads((made / timing.REPORT).read_text())
    assert {d["line_id"] for d in report["drift"]} == {"c1-1", "c2-1", "c3-1"}


def test_timing_waits_for_the_audio(tmp_path):
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    with pytest.raises(media.StepFailed, match="audio step"):
        timing.run(job)
    assert not timing.is_done(job)


def test_timings_are_not_done_once_the_audio_or_the_lines_change(made, monkeypatch):
    import os

    monkeypatch.setattr(timing, "separate_vocals", lambda job: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([]))
    timing.run(made)
    assert timing.is_done(made)

    timed = made / media.timing_path("song")
    earlier = (made / media.audio_path("song")).stat().st_mtime - 10
    os.utime(timed, (earlier, earlier))  # the song was made again after it was timed
    assert not timing.is_done(made)
    timing.run(made)
    assert timing.is_done(made)

    data = sample_media.bundle().model_dump()
    data["frq"]["model_answer_steps"].append("The flow drives the synthase.")
    jobs.write(made / "bundle.json", sample_media.bundle().model_validate(data))
    assert not timing.is_done(made)  # a line to time that has no timing yet
