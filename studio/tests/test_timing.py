import json

import numpy as np
import pytest

from studio import jobs, knobs, media
from studio.schemas import TimedLine, Timing, Word, timing_problems
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


def _lyria(tmp_path, monkeypatch, length):
    """The sample job with a fake song Lyria sang, `length` seconds long."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setattr(audio, "send", FakeGemini())
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type, kind="song": data)
    monkeypatch.setattr(audio, "duration", lambda path: length)
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    jobs.keep_studio_settings(job, {"song": {"provider": "gemini"}})  # as an admin sets it on /admin/studio
    audio.run(job)
    return job


def test_a_gemini_song_is_aligned_across_the_whole_song(tmp_path, monkeypatch):
    """Lyria keeps to the planned sections only roughly (an intro, a longer verse), so its
    lines are looked for in the whole song at once rather than where each was planned."""
    job = _lyria(tmp_path, monkeypatch, 41.0)  # longer than the 30.1s planned
    bundle = sample_media.bundle()
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
    job = _lyria(tmp_path, monkeypatch, 30.0)
    bundle = sample_media.bundle()
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


def test_a_chorus_the_aligner_cannot_hear_is_spread_over_its_singing(tmp_path, monkeypatch):
    """Nov 1's five Lyria songs: MMS hears no letters in a stacked-voice chorus, so the aligner crammed its lines
    into a second after the verse it heard, and the singer sang on over no line until the next line it heard."""
    job = _lyria(tmp_path, monkeypatch, 30.0)
    bundle = sample_media.bundle()
    texts = {line.id: line.text for line in bundle.lyrics}

    def align(window, words):
        if len(words) < 30:  # a line heard again alone: no surer
            return [(i * 0.05, (i + 1) * 0.05, 0.05) for i in range(len(words))]
        heard = [(1.0 + i * 0.5, 1.4 + i * 0.5, 0.9) for i in range(11)]  # the verse, v1-1 and v1-2
        crammed = [(6.6 + i * 0.11, 6.7 + i * 0.11, 0.05) for i in range(10)]  # c1-1 and v2-1, in 1.1 s
        return heard + crammed + [(16.0 + i * 0.5, 16.4 + i * 0.5, 0.9) for i in range(len(words) - 21)]

    timed, report = timing.song_timing(job, bundle, align=align, vocals=_sung(30.0))
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []
    verse, chorus, verse2, after = (timed.line(i) for i in ("v1-2", "c1-1", "v2-1", "c2-1"))
    assert report["spread"] == ["c1-1", "v2-1"] and report["unsure"] == ["c1-1", "v2-1"]
    assert "spread over the singing: c1-1, v2-1" in timing.song_notes(report)
    assert (verse.end, after.start) == (6.4, 16.0)  # the lines it heard stay where they are
    # From the line it heard before them to the next, each word over its share of the letters.
    assert chorus.start == pytest.approx(verse.end, abs=timing.FRAME_S) and chorus.end == verse2.start
    assert verse2.end == pytest.approx(after.start, abs=timing.FRAME_S)
    letters = [len(spoken(w)) for i in ("c1-1", "v2-1") for w, _, _ in words_of(texts[i])]
    seconds = [w.end - w.start for line in (chorus, verse2) for w in line.words]
    assert seconds == pytest.approx([n * (after.start - verse.end) / sum(letters) for n in letters], abs=0.06)


def _lines(*spans):
    """Lines a, b, c... over these (start, end) spans, each four words on an even beat."""
    lines = []
    for k, (start, end) in enumerate(spans):
        step = (end - start) / 4
        lines.append(TimedLine(line_id="abcdefgh"[k], start=start, end=end, words=[
            Word(word=w, start=round(start + i * step, 3), end=round(start + (i + 1) * step, 3))
            for i, w in enumerate(["Sodium", "out", "potassium", "in"])]))
    return lines


def _levels(seconds, silent=()):
    """Vocal levels (dB) singing throughout, but for the silent (start, end) stretches."""
    levels = np.zeros(round(seconds / timing.FRAME_S))
    for start, end in silent:
        levels[round(start / timing.FRAME_S):round(end / timing.FRAME_S)] = -60.0
    return levels.tolist()


def test_unheard_lines_at_a_pace_that_sings_stay_and_a_pause_ends_a_spread():
    # Singing with no line after lines it did not hear but placed at a singing pace: an echo of the hook, kept.
    lines = _lines((0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (10.0, 12.0))
    assert timing.by_the_singing(lines, {"a": 0.9, "b": 0.1, "c": 0.1, "d": 0.9}, _levels(14.0)) == []
    assert [(line.start, line.end) for line in lines] == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (10.0, 12.0)]
    # Crammed lines are spread up to the pause after them (an instrumental break); the line after it stays.
    lines = _lines((0.0, 2.0), (2.0, 2.5), (2.5, 3.0), (12.0, 14.0), (14.0, 16.0))
    scores = {"a": 0.9, "b": 0.1, "c": 0.1, "d": 0.1, "e": 0.9}
    assert timing.by_the_singing(lines, scores, _levels(16.0, silent=[(7.0, 11.0)])) == ["b", "c"]
    assert [(line.start, line.end) for line in lines] == [(0.0, 2.0), (2.0, 4.5), (4.5, 7.0), (12.0, 14.0),
                                                           (14.0, 16.0)]
    # Silent vocals: nothing to place them by.
    lines = _lines((0.0, 2.0), (2.0, 2.5), (8.0, 10.0))
    assert timing.by_the_singing(lines, {"a": 0.9, "b": 0.1, "c": 0.9}, None) == []


def test_a_line_not_sung_among_lines_spread_stays_at_no_length_in_its_place():
    lines = _lines((0.0, 2.0), (2.0, 2.4), (2.4, 2.4), (2.4, 2.8), (10.0, 12.0))
    lines[2] = lines[2].model_copy(update={"sung": False})
    scores = {"a": 0.9, "b": 0.1, "c": None, "d": 0.1, "e": 0.9}
    assert timing.by_the_singing(lines, scores, _levels(12.0)) == ["b", "d"]
    b, c, d = lines[1:4]
    assert (b.start, b.end, d.start, d.end) == (2.0, 6.0, 6.0, 10.0)
    assert not c.sung and c.start == c.end == 6.0 and all(w.start == w.end == 6.0 for w in c.words)
    Timing(audio="audio/song.mp3", duration=12.0, lines=lines)  # in order, every word inside its line


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


def test_the_lesson_alone_read_by_gemini_bills_the_aligner_and_what_it_ran_on(tmp_path, monkeypatch):
    """STUDIO_VIDEOS=lesson with Gemini's voice, which has no timings of its own: the steps run as the runner runs them
    (each at its step), and the bill shows MMS timing the voice, on torchaudio, torch and ffmpeg, with no Demucs."""
    from studio import ledger

    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setattr(audio, "send", FakeGemini())
    monkeypatch.setattr(audio, "duration", lambda path: 30.1)
    monkeypatch.setattr(audio, "as_mp3", lambda data, mime_type, kind="song": data)
    monkeypatch.setattr(timing, "_speech_audio", lambda path: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([], pitch=0.3))
    monkeypatch.setattr(timing, "separate_vocals", lambda job: pytest.fail("a song is separated for the lesson alone"))
    monkeypatch.setattr(ledger, "version", lambda name: "1.0")
    job = tmp_path / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    jobs.keep_studio_settings(job, {"song": {"provider": "gemini"}})
    with ledger.at_step("audio"):
        audio.run(job)
    with ledger.at_step("timing"):
        timing.run(job)
    knobs.use({"knobs": {"STUDIO_VIDEOS": "lesson"}})  # as the worker has it when it sends the bill

    bill = {s["step"]: s for s in ledger.breakdown(job, "videos")["steps"]}
    assert [(m["model"], m["task"]) for m in bill["audio"]["models"]] == [("gemini-3.8-flash-tts", "reads the narration")]
    assert [(m["model"], m["task"]) for m in bill["timing"]["models"]] == [("mms-fa", "times the narration")]
    assert [(t["name"], t["task"]) for t in bill["timing"]["tools"]] == [
        ("torchaudio", "runs mms-fa"), ("torch", "runs the models"), ("ffmpeg", "decodes the audio for them")]
    knobs.use({"knobs": {}})


def test_a_narration_the_aligner_cannot_hear_fails_the_step_and_is_read_again(read_by_gemini, monkeypatch):
    """Gemini cut the text short or read something else: the narration goes, and the retry reads it again."""
    monkeypatch.setattr(timing, "_speech_audio", lambda path: np.zeros((1, int(30.1 * timing.SAMPLE_RATE))))
    monkeypatch.setattr(timing, "mms_aligner", lambda: even_aligner([], pitch=0.3, score=0.1))
    with pytest.raises(media.StepFailed, match=r"the narration does not read its lines: the aligner could not hear 2 "
                                               r"of its 2 .*; the narration is deleted, so the retry reads it again"):
        timing.run(read_by_gemini)
    assert not (read_by_gemini / media.audio_path("narration")).exists()
    assert (read_by_gemini / media.audio_path("song")).exists() and not audio.is_done(read_by_gemini)


def test_a_chorus_sung_again_word_for_word_is_found_in_the_lyrics():
    texts = {"i": "Carry silk from east to west", "v": "Soldiers guard the roads", "c1": "Carry silk from east to west",
             "c2": "Faith and plague ride with the rest", "w": "Paper rolls west", "d1": "Carry silk from east to west",
             "d2": "Faith and plague ride with the rest!", "o": "Carry silk from east to west"}
    order = list(texts)
    # One line sung again is not a run; the punctuation is not sung.
    assert timing.repeated_runs(order, texts) == [[[2, 3], [5, 6]]]


def _notes(seconds, tunes):
    """Pitch classes every frame: the (start, end, pitch) stretches in `tunes` sing one note each, the rest a
    murmur of every note at once."""
    notes = np.full((round(seconds / timing.FRAME_S), 12), 1 / np.sqrt(12), dtype=np.float32)
    for start, end, pitch in tunes:
        notes[round(start / timing.FRAME_S):round(end / timing.FRAME_S)] = np.eye(12)[pitch]
    return notes


def _two_lines(start, line_id):
    """Two four-word lines sung from `start`, two seconds each."""
    return [TimedLine(line_id=f"{line_id}{n}", start=start + 2 * n, end=start + 2 * n + 2, words=[
        Word(word=w, start=start + 2 * n + 0.5 * i, end=start + 2 * n + 0.5 * (i + 1))
        for i, w in enumerate(["Sodium", "out", "potassium", "in"])]) for n in range(2)]


def test_a_chorus_the_aligner_put_a_line_late_is_found_by_its_melody():
    """Nov 3's Silk Roads song: the singer went from the line before the last chorus straight into the chorus, and
    the aligner, hearing none of it, put the line it skipped where the chorus began and the chorus a line late."""
    tune = [(0.0, 2.0, 0), (2.0, 4.0, 7)]  # the chorus's two lines, each to its own note
    lines = [*_two_lines(0.0, "c"), TimedLine(line_id="v", start=8.0, end=9.6, words=[
        Word(word="Silk", start=8.0, end=9.6)]), TimedLine(line_id="s", start=10.0, end=12.0, words=[
            Word(word=w, start=10.0 + i, end=11.0 + i) for i, w in enumerate(["Gods", "germs"])]),
        *_two_lines(12.0, "d"), TimedLine(line_id="o", start=17.0, end=18.0, words=[
            Word(word="West", start=17.0, end=18.0)])]
    texts = {"c0": "Sodium out potassium in", "c1": "Sodium out potassium in.", "v": "Silk", "s": "Gods germs",
             "d0": "Sodium out potassium in", "d1": "Sodium out potassium in", "o": "West"}
    # Sung: the chorus at 0 s, the verse line, the chorus again from 10 s; "s" is not sung.
    notes = _notes(20.0, [*tune, (8.0, 9.6, 3), *[(10.0 + a, 10.0 + b, p) for a, b, p in tune], (17.0, 18.0, 5)])
    scores = {"c0": 0.9, "c1": 0.6, "v": 0.9, "s": 0.1, "d0": 0.1, "d1": 0.1, "o": 0.9}
    runs = timing.repeated_runs([line.line_id for line in lines], {**texts, "c1": texts["c0"]})
    assert runs == [[[0, 1], [4, 5]]]

    def alone(ids, t0, t1):
        pytest.fail("no room for the line the chorus now covers: it is not aligned again")

    moved = timing.by_the_melody(lines, scores, runs, notes, alone, texts)
    assert moved == ["d0", "d1"]
    d0, d1, s = lines[4], lines[5], lines[3]
    assert (d0.start, d0.end, d1.start, d1.end) == (10.0, 12.0, 12.0, 14.0)  # the heard chorus's timing
    assert [w.start for w in d0.words] == [10.0, 10.5, 11.0, 11.5]
    assert not s.sung and s.start == s.end == 9.6 and scores["s"] is None
    Timing(audio="audio/song.mp3", duration=20.0, lines=lines)  # in order, every word inside its line
    # Counted as heard: nothing left to spread over the singing.
    assert timing.by_the_singing(lines, scores, _levels(20.0), placed=set(moved)) == []


def test_a_chorus_where_its_melody_is_sung_stays_and_a_chorus_nobody_heard_is_left():
    tune = [(0.0, 2.0, 0), (2.0, 4.0, 7)]
    lines = [*_two_lines(0.0, "c"), *_two_lines(8.0, "d")]
    texts = {line.line_id: "Sodium out potassium in" for line in lines}
    notes = _notes(14.0, [*tune, *[(8.0 + a, 8.0 + b, p) for a, b, p in tune]])
    runs = timing.repeated_runs([line.line_id for line in lines], texts)
    before = [line.model_copy() for line in lines]
    scores = {"c0": 0.9, "c1": 0.9, "d0": 0.1, "d1": 0.1}
    assert timing.by_the_melody(lines, scores, runs, notes, None, texts) == []  # already where its melody is
    assert lines == before
    scores = {line.line_id: 0.1 for line in lines}  # no repeat it heard to go by
    late = [*lines[:2], *_two_lines(9.0, "d")]
    assert timing.by_the_melody(late, scores, runs, notes, None, texts) == []
    assert timing.by_the_melody(late, {**scores, "c0": 0.9, "c1": 0.9}, runs, None, None, texts) == []  # no vocals


def test_a_line_a_chorus_found_by_its_melody_covers_is_aligned_again_where_there_is_room():
    tune = [(0.0, 2.0, 0), (2.0, 4.0, 7)]
    lines = [*_two_lines(0.0, "c"), TimedLine(line_id="s", start=11.0, end=13.0, words=[
        Word(word=w, start=11.0 + i, end=12.0 + i) for i, w in enumerate(["Gods", "germs"])]), *_two_lines(13.0, "d")]
    texts = {"c0": "Sodium out potassium in", "c1": "Sodium out potassium in", "s": "Gods germs",
             "d0": "Sodium out potassium in", "d1": "Sodium out potassium in"}
    notes = _notes(18.0, [*tune, (8.0, 10.0, 4), *[(12.0 + a, 12.0 + b, p) for a, b, p in tune]])
    scores = {"c0": 0.9, "c1": 0.9, "s": 0.1, "d0": 0.1, "d1": 0.1}
    calls = []

    def alone(ids, t0, t1):
        calls.append((ids, t0, t1))
        return [(TimedLine(line_id="s", start=8.0, end=10.0, words=[Word(word="Gods", start=8.0, end=9.0),
                                                                  Word(word="germs", start=9.0, end=10.0)]), 0.7)]

    runs = timing.repeated_runs([line.line_id for line in lines], texts)
    assert timing.by_the_melody(lines, scores, runs, notes, alone, texts) == ["d0", "d1"]
    assert calls == [(["s"], 4.0, 12.0)] and scores["s"] == 0.7
    assert (lines[2].start, lines[2].end, lines[3].start) == (8.0, 10.0, 12.0)
    Timing(audio="audio/song.mp3", duration=18.0, lines=lines)


def test_the_notes_name_the_lines_placed_by_the_chorus_melody():
    report = {"drift": [], "drifting": [], "unsure": [], "by_melody": ["c3-1", "c3-2"]}
    assert "placed by the chorus melody: c3-1, c3-2" in timing.song_notes(report)


def test_the_chroma_of_a_sung_note_is_its_pitch_class():
    t = np.arange(int(2.0 * timing.SAMPLE_RATE)) / timing.SAMPLE_RATE
    notes = timing.chroma(np.sin(2 * np.pi * 440.0 * t)[None, :])  # an A
    assert notes.shape[1] == 12 and notes[5:-5].argmax(axis=1).tolist() == [0] * (len(notes) - 10)
    assert np.allclose(np.linalg.norm(notes, axis=1), 1.0, atol=1e-4)


def test_a_heard_chorus_at_the_end_of_the_song_is_heard_as_far_as_the_chroma_reaches():
    """chroma() hears whole windows only, so it stops a little short of the song's end: the chorus heard last, sung to
    the end, is matched over the frames it has rather than failing the step."""
    tune = [(2.0, 4.0, 0), (4.0, 6.0, 7)]
    lines = [*_two_lines(1.0, "c"), *_two_lines(6.0, "d")]  # c, unheard and a second early; d heard, to the end
    texts = {line.line_id: "Sodium out potassium in" for line in lines}
    notes = _notes(10.0, [*tune, *[(a + 4.0, b + 4.0, p) for a, b, p in tune]])[:-5]  # 0.25 s short
    scores = {"c0": 0.1, "c1": 0.1, "d0": 0.9, "d1": 0.9}
    runs = timing.repeated_runs([line.line_id for line in lines], texts)
    assert timing.by_the_melody(lines, scores, runs, notes, None, texts, length=10.0) == ["c0", "c1"]
    assert (lines[0].start, lines[1].end) == (2.0, 6.0)


def test_a_last_line_a_chorus_now_covers_is_aligned_again_up_to_the_end_of_the_song():
    tune = [(0.0, 2.0, 0), (2.0, 4.0, 7)]
    lines = [*_two_lines(0.0, "c"), *_two_lines(8.0, "d"), TimedLine(line_id="o", start=12.0, end=14.0, words=[
        Word(word=w, start=12.0 + i, end=13.0 + i) for i, w in enumerate(["Gods", "germs"])])]
    texts = {"c0": "Sodium out potassium in", "c1": "Sodium out potassium in", "d0": "Sodium out potassium in",
             "d1": "Sodium out potassium in", "o": "Gods germs"}
    notes = _notes(20.0, [*tune, *[(10.0 + a, 10.0 + b, p) for a, b, p in tune], (15.0, 17.0, 4)])
    scores = {"c0": 0.9, "c1": 0.9, "d0": 0.1, "d1": 0.1, "o": 0.1}
    calls = []

    def alone(ids, t0, t1):
        calls.append((ids, t0, t1))
        return [(TimedLine(line_id="o", start=15.0, end=17.0, words=[Word(word="Gods", start=15.0, end=16.0),
                                                                  Word(word="germs", start=16.0, end=17.0)]), 0.7)]

    runs = timing.repeated_runs([line.line_id for line in lines], texts)
    assert timing.by_the_melody(lines, scores, runs, notes, alone, texts, length=20.0) == ["d0", "d1"]
    assert calls == [(["o"], 14.0, 20.0)] and lines[4].sung and lines[4].start == 15.0
    Timing(audio="audio/song.mp3", duration=20.0, lines=lines)
