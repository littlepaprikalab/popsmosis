import json

import numpy as np
import pytest

from studio import jobs, knobs, media
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
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    jobs.keep_studio_settings(job, {"song": {"provider": "elevenlabs"}})  # FakeEleven makes it
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
    assert calls[0][1] == ["light", "lands", "on", "the", "thylakoid", "each", "electron", "handoff", "pumps", "a",
                           "proton"]
    assert calls[1][1] == ["protons", "flow", "and", "eitipi", "grows"]
    chorus = timed.line("c1-1")
    assert 8.6 <= chorus.start < chorus.end <= 12.9  # inside the chorus's planned place
    assert [w.word for w in chorus.words] == ["Protons", "flow", "and", "ATP", "grows"]
    assert report["drifting"] == [] and report["unsure"] == []


def _sung(seconds, quiet=(), gain=0.3):
    """Vocals singing throughout at `gain`, but for the (start, end, gain) stretches in `quiet`."""
    rng = np.random.default_rng(0)
    vocals = (rng.standard_normal((1, int(seconds * timing.SAMPLE_RATE))) * gain).astype(np.float32)
    for start, end, level in quiet:
        vocals[:, int(start * timing.SAMPLE_RATE):int(end * timing.SAMPLE_RATE)] *= level / gain
    return vocals


def _chunks(job, length=30.1):
    """Each planned section's lines, start and end in song time: the plan stretched to the song's length."""
    sections = json.loads((job / audio.plan_path("song")).read_text())["sections"]
    scale = length * 1000 / sum(s["duration_ms"] for s in sections) / 1000
    return [(s["line_ids"], s["start_ms"] * scale, (s["start_ms"] + s["duration_ms"]) * scale) for s in sections]


def test_a_section_with_no_singing_in_its_chunk_is_not_sung(made):
    """Eleven Music left the first chunk instrumental (the Holocaust song's intro, Oct 30): the aligner put its
    lines on the next section's first bar and squashed that line into 0.75 s."""
    (ids, _, end), (next_ids, _, _), *_ = _chunks(made)
    calls = []
    bundle = sample_media.bundle()
    texts = {line.id: line.text for line in bundle.lyrics}
    vocals = _sung(30.1, quiet=[(0.0, end, 0.0003)])  # Demucs' bleed, 60 dB down
    timed, report = timing.song_timing(made, bundle, align=even_aligner(calls), vocals=vocals)
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []
    assert len(calls) == 5  # not asked for the chunk that sings nothing
    for line_id in ids:
        line = timed.line(line_id)
        assert not line.sung and line.start == line.end == round(end, 3)
        assert [w.word for w in line.words] == [w for w, _, _ in words_of(texts[line_id])]
        assert all(w.start == w.end == line.start for w in line.words)
    assert timed.line(next_ids[0]).start >= end and timed.line(next_ids[0]).sung
    assert report["unsung"] == ids == ["v1-1", "v1-2"]
    assert [r for r in report["lines"] if r["line_id"] == "v1-1"][0] == {
        "line_id": "v1-1", "text": texts["v1-1"], "seconds": 0.0, "score": None, "sung": False}
    assert "NOT SUNG: v1-1, v1-2" in timing.song_notes(report)
    assert report["unsure"] == []


def test_quiet_singing_is_sung_and_a_pickup_into_the_next_section_is_not(made):
    (ids, _, end), *_ = _chunks(made)
    bundle = sample_media.bundle()
    soft = _sung(30.1, quiet=[(0.0, end, 0.03)])  # 20 dB under the rest: a hushed verse
    timed, report = timing.song_timing(made, bundle, align=even_aligner([]), vocals=soft)
    assert report["unsung"] == [] and all(timed.line(i).sung for i in ids)
    pickup = _sung(30.1, quiet=[(0.0, end - 0.3, 0.0)])  # only the next section's first syllables, sung early
    timed, report = timing.song_timing(made, bundle, align=even_aligner([]), vocals=pickup)
    assert report["unsung"] == ids


def test_silent_vocals_leave_every_section_to_the_aligner(made):
    """Nothing to measure a section against: as before the check, the aligner places every line."""
    vocals = np.zeros((1, int(30.1 * timing.SAMPLE_RATE)), dtype=np.float32)
    timed, report = timing.song_timing(made, sample_media.bundle(), align=even_aligner([]), vocals=vocals)
    assert report["unsung"] == [] and all(line.sung for line in timed.lines)


def test_a_gemini_song_is_aligned_across_the_whole_song(tmp_path, monkeypatch):
    """Lyria keeps to the planned sections only roughly (an intro, a longer verse), so its
    lines are looked for in the whole song at once rather than where each was planned."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setattr(audio, "send", FakeGemini())
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type, kind="song": data)
    monkeypatch.setattr(audio, "duration", lambda path: 41.0)  # longer than the 30.1s planned
    job = tmp_path / "sample"
    job.mkdir()
    bundle = sample_media.bundle()
    jobs.write(job / "bundle.json", bundle)
    jobs.keep_studio_settings(job, {"song": {"provider": "gemini"}})  # as an admin sets it on /admin/studio
    audio.run(job)

    calls = []
    vocals = _sung(41.0, quiet=[(0.0, 10.0, 0.0)])  # Lyria's own 10 s intro: no chunk was planned for it
    timed, report = timing.song_timing(job, bundle, align=even_aligner(calls), vocals=vocals)
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []
    assert report["unsung"] == []  # its sections are not pinned, so none is held to a chunk
    (heard, words), = calls  # one window: the whole song
    assert heard == vocals.shape[-1]
    assert words[:5] == ["light", "lands", "on", "the", "thylakoid"] and words[-5:] == ["protons", "flow", "and",
                                                                                       "eitipi", "grows"]
    assert timed.duration == 41.0


def test_the_lines_lyria_says_it_left_out_are_found_in_its_own_text():
    texts = {"a": "Filter the blood", "b": "Pack the good back", "c": "Dump it all out", "d": "Again and again",
             "e": "Pack the good back in"}
    plan = {"sections_pinned": False, "sections": [{"line_ids": ["a", "b", "c"]}, {"line_ids": ["d", "e"]}]}
    timed = {**plan, "text": "[[A0]]\n[0.0:] Filter the blood\n[2.5:] Pack the good back\n[5.0:] Dump it out\n"
                             "[[B1]]\n[7.5:] Again and again"}
    assert timing.left_out(timed, texts) == {"e"}  # "Dump it out" is c, a word short: still sung
    bare = {**plan, "text": "[[A0]]\n[:] Filter the blood\n[:] Pack the good back\n[:] Again and again\n"
                            "[:] Pack the good back in"}
    assert timing.left_out(bare, texts) == {"c"}
    assert timing.left_out({**plan, "text": "[Verse]\nFilter the blood"}, texts) == set()  # not the song's text
    assert timing.left_out({**plan, "sections_pinned": True, "text": bare["text"]}, texts) == set()  # Eleven Music
    assert timing.left_out(plan, texts) == set()  # no text at all


def test_a_line_lyria_left_out_is_not_sung_and_the_rest_are_aligned_without_it(tmp_path, monkeypatch):
    """Round 17's kidney song stopped after its last chorus, as its own text said; the aligner, asked for the
    outro too, crammed seven lines into the song's last seven seconds, flashed in the band and never sung."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setattr(audio, "send", FakeGemini())
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type, kind="song": data)
    monkeypatch.setattr(audio, "duration", lambda path: 30.0)
    job = tmp_path / "sample"
    job.mkdir()
    bundle = sample_media.bundle()
    jobs.write(job / "bundle.json", bundle)
    jobs.keep_studio_settings(job, {"song": {"provider": "gemini"}})
    audio.run(job)
    plan_file = job / audio.plan_path("song")
    plan = json.loads(plan_file.read_text())
    *sung, last = bundle.lyrics
    plan["text"] = "\n".join(f"[:] {line.text}" for line in sung)  # all but the last chorus
    plan_file.write_text(json.dumps(plan))

    calls = []
    timed, report = timing.song_timing(job, bundle, align=even_aligner(calls), vocals=_sung(30.0))
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []
    (_, words), = calls
    assert len(words) == sum(len(words_of(line.text)) for line in sung)  # the aligner is not asked for it
    gone = timed.line(last.id)
    assert not gone.sung and gone.start == gone.end == timed.line(sung[-1].id).end
    assert report["unsung"] == [last.id] and f"NOT SUNG: {last.id}" in timing.song_notes(report)


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
    assert words == ["each", "electron", "handoff", "pumps", "a", "proton"]
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
                              "spotlight": "", "particles": [], "carries": "", "glow": "", "caption": ""})
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


def test_a_song_that_does_not_sing_its_lyrics_fails_the_step_and_is_made_again_on_the_retry(made, monkeypatch):
    """More than half the lines unsure: the singer sang something else. The step fails, the song goes (the narration
    stays), and the retry's audio step makes only the song again."""
    monkeypatch.setattr(timing, "separate_vocals", lambda job: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([], score=0.1))
    monkeypatch.setattr(audio, "duration", lambda path: 30.1)
    with pytest.raises(media.StepFailed, match=r"the song does not sing its lyrics: the aligner could not hear 7 of its 7"):
        timing.run(made)
    assert not (made / media.audio_path("song")).exists() and (made / media.audio_path("narration")).exists()
    assert not timing.is_done(made) and not audio.is_done(made)
    assert json.loads((made / timing.REPORT).read_text())["unsure"]  # what was heard is kept for `show`
    fake = FakeEleven()
    monkeypatch.setattr(audio, "send", fake)
    audio.run(made)
    assert [url for url in fake.urls() if "/v1/music" in url] and not [u for u in fake.urls() if "text-to-speech" in u]


def test_a_few_lines_sung_differently_still_pass():
    rows = [{"line_id": f"l{i}", "score": 0.1 if i < 3 else 0.9} for i in range(7)]
    assert timing.lyric_mismatch({"lines": rows}) is None  # 3 of 7, as a Lyria chorus can be
    rows[3]["score"] = 0.2
    assert "could not hear 4 of its 7 lines" in timing.lyric_mismatch({"lines": rows})
    assert timing.lyric_mismatch({"lines": [{"line_id": "l1", "score": None}]}) is None  # nothing scored: no verdict
    # A section left instrumental is never shown, so its lines are not counted against the song.
    unsung = [{"line_id": f"i{i}", "score": 0.0, "sung": False} for i in range(10)]
    assert timing.lyric_mismatch({"lines": rows[:3] + rows[4:] + unsung}) is None


def test_the_lesson_alone_times_only_the_narration(tmp_path, monkeypatch):
    """STUDIO_VIDEOS=lesson: there is no song, so neither Demucs nor the aligner runs."""
    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(audio, "send", FakeEleven())
    monkeypatch.setattr(audio, "duration", lambda path: 30.1)
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    jobs.keep_studio_settings(job, {"song": {"provider": "elevenlabs"}})  # FakeEleven reads it
    audio.run(job)

    def no_song(*args):
        raise AssertionError("the song is timed")

    monkeypatch.setattr(timing, "separate_vocals", no_song)
    monkeypatch.setattr(timing, "mms_aligner", no_song)
    assert timing.run(job).startswith("no song (the lesson video alone); narration ")
    assert timing.is_done(job)
    assert not (job / media.timing_path("song")).exists() and not (job / timing.REPORT).exists()
    knobs.use({"knobs": {}})
    assert not timing.is_done(job)


# --- a Gemini narration: no timings of its own, so the aligner times it -------------------------


@pytest.fixture
def read_by_gemini(tmp_path, monkeypatch):
    """The sample job with its narration read by Gemini (fake audio), which says nothing of when."""
    from studio.tests.test_audio import sing

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setattr(audio, "send", FakeGemini())
    monkeypatch.setattr(audio, "duration", lambda path: 30.1)
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type, kind="song": data)
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    sing(job, "gemini")
    audio.run(job)
    return job


def test_a_gemini_narration_is_aligned_over_the_whole_narration(read_by_gemini):
    calls = []
    timed, report = timing.speech_timing(read_by_gemini, "narration", align=even_aligner(calls, pitch=0.3),
                                         audio=np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    ids = [n.id for n in sample_media.bundle().narration]
    assert timing_problems(timed, ids) == []
    (heard, words), = calls  # one pass over the whole narration, every word in order
    assert heard == int(30.1 * timing.SAMPLE_RATE)
    assert words == [spoken(w) for n in sample_media.bundle().narration for w, _, _ in words_of(n.text)]
    assert [row["line_id"] for row in report["lines"]] == ids and report["unsure"] == []
    assert timing.speech_mismatch("narration", report) is None


def test_the_step_aligns_a_gemini_narration_and_logs_the_aligner(read_by_gemini, monkeypatch):
    monkeypatch.setattr(timing, "separate_vocals", lambda job: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "_speech_audio", lambda path: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([], pitch=0.3))
    summary = timing.run(read_by_gemini)
    assert "narration 2 lines, aligned (0 unsure)" in summary
    assert timing.is_done(read_by_gemini)
    assert json.loads((read_by_gemini / timing.speech_report("narration")).read_text())["unsure"] == []
    local = [json.loads(row) for row in (read_by_gemini / "calls.jsonl").read_text().splitlines()]
    assert ("narration alignment", "local", "mms-fa", 0.0) in [(r["label"], r["role"], r["model"], r["cost_usd"])
                                                            for r in local]


def test_a_narration_the_aligner_cannot_hear_fails_the_step_and_is_read_again(read_by_gemini, monkeypatch):
    """Gemini cut the text short or read something else: the narration goes, and the retry reads it again."""
    monkeypatch.setattr(timing, "_speech_audio", lambda path: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([], pitch=0.3, score=0.1))
    with pytest.raises(media.StepFailed, match=r"the narration does not read its lines: the aligner could not hear 2 "
                                               r"of its 2 .*; the narration is deleted, so the retry reads it again"):
        timing.run(read_by_gemini)
    assert not (read_by_gemini / media.audio_path("narration")).exists()
    assert (read_by_gemini / media.audio_path("song")).exists() and not audio.is_done(read_by_gemini)
