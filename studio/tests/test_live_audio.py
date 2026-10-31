"""Live checks of the audio lane. Every ElevenLabs and Gemini call costs credits, so these
run only when asked for, with STUDIO_LIVE_AUDIO=1 and the service's key set
(ELEVENLABS_API_KEY for its speech, GEMINI_API_KEY for the Lyria song and Gemini's speech); the aligner
checks also download the MMS weights (~1.2 GB) the first time, and the Lyria song's Demucs.

    STUDIO_LIVE_AUDIO=1 python -m pytest studio/tests/test_live_audio.py -s
"""

import json
import os

import pytest

from studio import jobs, media
from studio.schemas import timing_problems
from studio.steps import audio, timing
from studio.tests import sample_media

LIVE = os.environ.get("STUDIO_LIVE_AUDIO") == "1"
needs_elevenlabs = pytest.mark.skipif(not (LIVE and os.environ.get("ELEVENLABS_API_KEY")),
                                      reason="needs STUDIO_LIVE_AUDIO=1 and ELEVENLABS_API_KEY (spends credits)")
needs_gemini = pytest.mark.skipif(not (LIVE and os.environ.get("GEMINI_API_KEY")),
                                  reason="needs STUDIO_LIVE_AUDIO=1 and GEMINI_API_KEY (spends credits)")


@pytest.fixture(scope="module")
def spoken_job(tmp_path_factory):
    """The sample job's narration, read aloud once (about 110 credits)."""
    job = tmp_path_factory.mktemp("live") / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    (job / "audio").mkdir()
    audio._make_speech(job, "narration", audio.speech_lines(sample_media.bundle(), "narration"))
    return job


@needs_elevenlabs
def test_the_tts_character_timings_become_words(spoken_job):
    timed, _ = timing.speech_timing(spoken_job, "narration")
    assert timing_problems(timed, [n.id for n in sample_media.bundle().narration]) == []
    for line in timed.lines:
        print(line.line_id, line.start, line.end, " ".join(f"{w.word}@{w.start}" for w in line.words))


@needs_elevenlabs
def test_the_mms_aligner_agrees_with_the_tts_timings(spoken_job):
    """Speech is the easy case, but it proves the aligner, its weights and its alphabet work here."""
    import torch

    from studio.steps.timing import _decode

    told, _ = timing.speech_timing(spoken_job, "narration")
    waveform = torch.from_numpy(_decode(spoken_job / media.audio_path("narration"), timing.SAMPLE_RATE, 1))
    words = [w for line in told.lines for w in line.words]
    found = timing.mms_aligner()(waveform, [timing.spoken(w.word) for w in words])
    gaps = [abs(start - w.start) for w, (start, _, _) in zip(words, found)]
    print(f"{len(words)} words, start times within {max(gaps):.3f}s of the TTS's (mean {sum(gaps) / len(gaps):.3f}s)")
    assert sum(gaps) / len(gaps) < 0.15


@pytest.fixture(scope="module")
def lyria_job(tmp_path_factory):
    """The sample song, sung once by Lyria on the Gemini API (one song, about $0.08)."""
    job = tmp_path_factory.mktemp("live") / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    (job / "audio").mkdir()
    audio._make_song(job, sample_media.bundle(), "gemini")
    return job


@needs_gemini
def test_lyria_sings_the_sample_song(lyria_job):
    plan = json.loads((lyria_job / audio.plan_path("song")).read_text(encoding="utf-8"))
    length = audio.duration(lyria_job / media.audio_path("song"))
    print(f"{plan['model_id']} ({plan['model_version']}) sent {plan['mime_type']}, {length:.1f}s, "
          f"finish {plan['finish_reason']}, usage {plan['usage']}\n{plan['text']}")
    assert plan["provider"] == "gemini" and length > 10


@needs_gemini
def test_the_lyria_song_aligns_across_the_whole_song(lyria_job):
    """Whether the lyrics are found in Lyria's song without its sections where they were planned."""
    bundle = sample_media.bundle()
    timed, report = timing.song_timing(lyria_job, bundle)
    for row in report["lines"]:
        line = timed.line(row["line_id"])
        print(f"{row['line_id']:<5} {line.start:6.2f}-{line.end:6.2f}  score {row['score']}  {row['text']}")
    print(timing.song_notes(report))
    assert timing_problems(timed, [line.id for line in bundle.lyrics]) == []


@pytest.fixture(scope="module")
def gemini_spoken_job(tmp_path_factory):
    """The sample job's narration, read once by Gemini's text-to-speech (well under a cent)."""
    job = tmp_path_factory.mktemp("live") / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    (job / "audio").mkdir()
    audio._make_speech(job, "narration", audio.speech_lines(sample_media.bundle(), "narration"), "gemini")
    return job


@needs_gemini
def test_gemini_reads_the_narration_and_the_aligner_times_it(gemini_spoken_job):
    """Gemini says nothing of when it read each word: the aligner finds them, and must hear every line."""
    plan = json.loads((gemini_spoken_job / audio.plan_path("narration")).read_text(encoding="utf-8"))
    length = audio.duration(gemini_spoken_job / media.audio_path("narration"))
    timed, report = timing.speech_timing(gemini_spoken_job, "narration")
    for row in report["lines"]:
        line = timed.line(row["line_id"])
        print(f"{row['line_id']:<4} {line.start:6.2f}-{line.end:6.2f}  score {row['score']}  {row['text']}")
    print(f"{plan['model_id']} ({plan['voice']}) sent {plan['mime_type']}, {length:.1f}s, usage {plan['usage']}")
    assert plan["provider"] == "gemini" and "alignment" not in plan and length > 3
    assert timing_problems(timed, [n.id for n in sample_media.bundle().narration]) == []
    assert timing.speech_mismatch("narration", report) is None
