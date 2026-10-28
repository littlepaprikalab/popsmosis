import json

import numpy as np
import pytest

from studio import jobs, media
from studio.schemas import Timing, timing_problems
from studio.steps import audio, timing
from studio.steps.timing import spoken, timing_from_chars, words_of
from studio.tests import sample_media
from studio.tests.test_audio import FakeEleven, FakeGemini


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
    timed = timing_from_chars("narration", text, [{"id": "x", "start": 0, "end": len(text)}], alignment, 5.0)
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
    monkeypatch.setattr(audio, "TWO_BAR_BPM", 0)  # two bars a line, room for the fake singer's steady words
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


def test_a_gemini_song_is_aligned_across_the_whole_song(tmp_path, monkeypatch):
    """Lyria keeps to the planned sections only roughly (an intro, a longer verse), so its
    lines are looked for in the whole song at once rather than where each was planned."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setattr(audio, "send", FakeGemini())
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type: data)
    monkeypatch.setattr(audio, "duration", lambda path: 41.0)  # longer than the 30.1s planned
    job = tmp_path / "sample"
    job.mkdir()
    bundle = sample_media.bundle()
    jobs.write(job / "bundle.json", bundle)
    jobs.keep_studio_settings(job, {"song": {"provider": "gemini"}})  # as an admin sets it on /admin/studio
    audio.run(job)

    calls = []
    vocals = np.zeros((1, int(41.0 * timing.SAMPLE_RATE)), dtype=np.float32)
    timed, report = timing.song_timing(job, bundle, align=even_aligner(calls), vocals=vocals)
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []
    (heard, words), = calls  # one window: the whole song
    assert heard == vocals.shape[-1]
    assert words[:5] == ["light", "lands", "on", "the", "thylakoid"] and words[-5:] == ["protons", "flow", "and",
                                                                                       "eitipi", "grows"]
    assert timed.duration == 41.0


def test_a_song_plan_from_before_the_choice_is_aligned_section_by_section():
    plan = {"sections": [{"section": "verse", "line_ids": ["a"], "start_ms": 0, "duration_ms": 4300},
                         {"section": "chorus", "line_ids": ["b"], "start_ms": 4300, "duration_ms": 4300}]}
    assert timing.windows(plan) == plan["sections"]
    assert timing.windows({**plan, "sections_pinned": False}) == [
        {"line_ids": ["a", "b"], "start_ms": 0, "duration_ms": 8600}]


def test_a_chorus_sung_slower_is_flagged_and_a_mumbled_line_is_unsure(made):
    bundle = sample_media.bundle()
    vocals = np.zeros((1, int(30.1 * timing.SAMPLE_RATE)), dtype=np.float32)
    seen = []

    def align(window, words):
        seen.append(words)
        # The second chorus: sung twice as fast, and unsure, and no surer heard alone after the six sections.
        if len(seen) == 4 or len(seen) > 6:
            return even_aligner([], pitch=0.3, score=0.1)(window, words)
        return even_aligner([])(window, words)

    timed, report = timing.song_timing(made, bundle, align=align, vocals=vocals)
    assert len(seen) == 7 and seen[-1] == seen[3]  # heard alone once more, and kept as it was
    assert report["drifting"] == ["c2-1"]
    assert report["unsure"] == ["c2-1"] and report["realigned"] == []
    assert "DRIFT over 0.5s: c2-1" in timing.song_notes(report)


def test_a_line_pulled_out_of_place_is_heard_alone_between_its_neighbours(made):
    """A repeat the lyrics do not have can pull a line's words into the last moment before the
    next line, unsure. Heard alone between its neighbours, it is placed where it is sung."""
    bundle = sample_media.bundle()
    vocals = np.zeros((1, int(30.1 * timing.SAMPLE_RATE)), dtype=np.float32)
    calls = []

    def align(window, words):
        calls.append((window.shape[-1], words))
        if len(calls) == 1:  # the first verse: its second line crammed against the chorus
            end = window.shape[-1] / timing.SAMPLE_RATE
            return even_aligner([])(window, words[:5]) + [(end - 0.6 + i * 0.1, end - 0.55 + i * 0.1, 0.05)
                                                          for i in range(6)]
        return even_aligner([])(window, words)

    timed, report = timing.song_timing(made, bundle, align=align, vocals=vocals)
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []
    first, second, chorus = timed.line("v1-1"), timed.line("v1-2"), timed.line("c1-1")
    heard, words = calls[-1]
    assert words == ["every", "electron", "handoff", "pumps", "a", "proton"]
    assert heard == pytest.approx((chorus.start - first.end) * timing.SAMPLE_RATE, abs=2)  # between its neighbours
    assert second.words[0].start == pytest.approx(first.end + timing.MARGIN_S + 0.2, abs=1e-3)
    assert [b.start - a.start for a, b in zip(second.words, second.words[1:])] == pytest.approx([0.6] * 5, abs=2e-3)
    assert first.end < second.start and second.end <= chorus.start
    assert report["realigned"] == ["v1-2"] and report["unsure"] == []
    assert "re-aligned alone: v1-2" in timing.song_notes(report)


def test_the_notes_read_a_report_from_before_lines_were_heard_alone():
    """`python -m studio show` reads the report of a job timed before the repair pass."""
    report = {"lines": [], "drift": [], "drifting": [], "unsure": ["c1-1"]}
    assert timing.song_notes(report) == "no repeated lines; aligner unsure of c1-1"


def test_a_line_with_no_room_between_its_neighbours_stays_as_it_was(made):
    bundle = sample_media.bundle()
    vocals = np.zeros((1, int(30.1 * timing.SAMPLE_RATE)), dtype=np.float32)
    calls = []

    def align(window, words):
        calls.append(words)
        if len(calls) > 6:
            raise RuntimeError("targets length is too long for CTC")  # torchaudio's forced_align, short of frames
        return even_aligner([], score=0.1 if len(calls) == 2 else 0.9)(window, words)

    timed, report = timing.song_timing(made, bundle, align=align, vocals=vocals)
    assert len(calls) == 7
    assert report["unsure"] == ["c1-1"] and report["realigned"] == []
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []


def test_the_step_writes_both_timings_and_reports_drift(made, monkeypatch):
    monkeypatch.setattr(timing, "separate_vocals", lambda job: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([]))
    monkeypatch.setattr(audio, "duration", lambda path: 30.1)
    summary = timing.run(made)
    assert summary.startswith("song 7 lines in 30.1s, chorus drift at most")
    assert timing.is_done(made)
    bundle = sample_media.bundle()
    expected = {"song": [line.id for line in bundle.lyrics], "narration": [n.id for n in bundle.narration]}
    for kind, ids in expected.items():
        assert timing_problems(jobs.read(made / media.timing_path(kind), Timing), ids) == []
    report = json.loads((made / timing.REPORT).read_text())
    assert {d["line_id"] for d in report["drift"]} == {"c1-1", "c2-1", "c3-1"}
    # Demucs and MMS ran here: logged with their time and no bill, after the audio step's paid calls.
    local = [json.loads(row) for row in (made / "calls.jsonl").read_text().splitlines()][2:]
    assert [(r["label"], r["role"], r["model"], r["cost_usd"]) for r in local] == [
        ("song vocals", "local", "htdemucs", 0.0), ("song alignment", "local", "mms-fa", 0.0)]


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
    data["narration"].append({"id": "n3", "text": "The flow drives the synthase.", "names": [], "fact_id": "f3"})
    data["direction"].append({"line_id": "n3", "scene": "synthase", "camera": {"target": "", "zoom": 1.0},
                              "spotlight": "", "particles": [], "caption": ""})
    jobs.write(made / "bundle.json", sample_media.bundle().model_validate(data))
    assert not timing.is_done(made)  # a line to time that has no timing yet


def test_a_rerun_that_fails_halfway_leaves_nothing_that_looks_done(made, monkeypatch):
    monkeypatch.setattr(timing, "separate_vocals", lambda job: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([]))
    timing.run(made)
    assert timing.is_done(made)

    def broken(job):
        raise media.StepFailed("Demucs ran out of memory")

    monkeypatch.setattr(timing, "separate_vocals", broken)
    with pytest.raises(media.StepFailed):
        timing.run(made)
    assert not timing.is_done(made)
    assert not (made / media.timing_path("song")).exists() and not (made / timing.REPORT).exists()


def test_kept_vocals_are_used_only_for_the_song_they_came_from(made):
    import os

    vocals, song = made / timing.VOCALS, made / media.audio_path("song")
    assert not timing.vocals_current(made)  # none yet
    vocals.write_bytes(b"RIFF")
    assert timing.vocals_current(made)
    later = vocals.stat().st_mtime + 10
    os.utime(song, (later, later))  # the song was made again (another provider, other lines)
    assert not timing.vocals_current(made)


# --- the aligner's front end in pieces ----------------------------------------------------------


def _tiny_mms(norm="layer_norm"):
    """A small model of MMS's make: its front end's kernels and strides (400 samples a frame, 320
    apart), a narrow transformer, the waveform normalised, log-softmax and a star appended."""
    torch = pytest.importorskip("torch")
    torchaudio = pytest.importorskip("torchaudio")
    from torchaudio.pipelines import MMS_FA
    from torchaudio.pipelines._wav2vec2 import utils

    torch.manual_seed(0)
    convs = [(16, k, s) for _, k, s in MMS_FA._params["extractor_conv_layer_config"]]
    inner = torchaudio.models.wav2vec2_model(
        extractor_mode=norm, extractor_conv_layer_config=convs, extractor_conv_bias=True, encoder_embed_dim=32,
        encoder_projection_dropout=0.0, encoder_pos_conv_kernel=16, encoder_pos_conv_groups=4,
        encoder_num_layers=2, encoder_num_heads=2, encoder_attention_dropout=0.0, encoder_ff_interm_features=64,
        encoder_ff_interm_dropout=0.0, encoder_dropout=0.0, encoder_layer_norm_first=True,
        encoder_layer_drop=0.0, aux_num_out=28)
    model = utils._extend_model(inner, normalize_waveform=True, apply_log_softmax=True, append_star=True)
    return torch, model.eval()


def test_the_aligner_hears_a_long_window_in_pieces_with_audio_either_side(monkeypatch):
    """One pass over minutes of song blurs what MMS hears, so a long window is heard a piece
    at a time, each piece a pass of its own with CONTEXT_S either side, keeping its middle."""
    torch, model = _tiny_mms()
    assert timing._front_end(model) == (400, 320)
    monkeypatch.setattr(timing, "CHUNK_S", 1)
    monkeypatch.setattr(timing, "CONTEXT_S", 0.5)
    window = torch.randn(1, int(4.37 * timing.SAMPLE_RATE)) * 0.1  # four pieces and a bit
    with torch.inference_mode():
        whole = model(window)[0]
        heard = []
        run = model.forward
        monkeypatch.setattr(model, "forward", lambda x, *rest: heard.append(x.size(1)) or run(x, *rest))
        pieces = timing._emission(model, window)
        # The second piece keeps frames 50-99, heard from frame 25 to 124: half a second either side.
        second = run(window[:, 25 * 320:124 * 320 + 400])[0][:, 25:75]
    assert pieces.shape == whole.shape and pieces.shape[-1] == 29  # one pass's frames
    assert len(heard) == 5 and max(heard) == 99 * 320 + 400  # five pieces, none longer than 2 s of frames
    assert torch.allclose(pieces[:, 50:100], second, atol=1e-6)
    assert not torch.allclose(pieces, whole, atol=1e-3)  # each piece heard only its own audio


def test_a_window_no_longer_than_a_piece_and_its_context_is_heard_in_one_pass(monkeypatch):
    torch, model = _tiny_mms()
    monkeypatch.setattr(timing, "CHUNK_S", 1)
    monkeypatch.setattr(timing, "CONTEXT_S", 0.5)
    window = torch.randn(1, 2 * timing.SAMPLE_RATE) * 0.1
    with torch.inference_mode():
        assert torch.equal(timing._emission(model, window), model(window)[0])


def test_a_front_end_normalised_across_the_window_is_heard_in_one_pass(monkeypatch):
    torch, model = _tiny_mms(norm="group_norm")
    assert timing._front_end(model) is None
    monkeypatch.setattr(timing, "CHUNK_S", 1)
    monkeypatch.setattr(timing, "CONTEXT_S", 0.5)
    window = torch.randn(1, 3 * timing.SAMPLE_RATE) * 0.1
    with torch.inference_mode():
        assert torch.equal(timing._emission(model, window), model(window)[0])


def test_jobs_on_one_machine_time_their_songs_in_turn(tmp_path):
    import threading
    import time as clock

    inside, most = [], []

    def time_a_song(name):
        (tmp_path / name).mkdir()
        with timing.cpu_turn(tmp_path / name):
            inside.append(name)
            most.append(len(inside))
            clock.sleep(0.05)
            inside.remove(name)

    threads = [threading.Thread(target=time_a_song, args=(f"job{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(most) == 1
