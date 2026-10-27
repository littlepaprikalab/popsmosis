"""Live checks of the audio lane. Every ElevenLabs call costs credits, so these run only
when asked for, with STUDIO_LIVE_AUDIO=1 and ELEVENLABS_API_KEY set; the aligner check
also downloads the MMS weights (~1.2 GB) the first time.

    STUDIO_LIVE_AUDIO=1 python -m pytest studio/tests/test_live_audio.py -s
"""

import os

import pytest

from studio import jobs, media
from studio.schemas import timing_problems
from studio.steps import audio, timing
from studio.tests import sample_media

pytestmark = pytest.mark.skipif(
    not (os.environ.get("STUDIO_LIVE_AUDIO") == "1" and os.environ.get("ELEVENLABS_API_KEY")),
    reason="needs STUDIO_LIVE_AUDIO=1 and ELEVENLABS_API_KEY (spends credits)")


@pytest.fixture(scope="module")
def spoken_job(tmp_path_factory):
    """The sample job's exam question, read aloud once (about 60 credits)."""
    job = tmp_path_factory.mktemp("live") / "sample"
    job.mkdir()
    jobs.write(job / "bundle.json", sample_media.bundle())
    (job / "audio").mkdir()
    audio._make_speech(job, "frq", audio.speech_lines(sample_media.bundle(), "frq"))
    return job


def test_the_tts_character_timings_become_words(spoken_job):
    timed = timing.speech_timing(spoken_job, "frq")
    assert timing_problems(timed, [i for i, _ in media.frq_lines(sample_media.bundle())]) == []
    for line in timed.lines:
        print(line.line_id, line.start, line.end, " ".join(f"{w.word}@{w.start}" for w in line.words))


def test_the_mms_aligner_agrees_with_the_tts_timings(spoken_job):
    """Speech is the easy case, but it proves the aligner, its weights and its alphabet work here."""
    import torch

    from studio.steps.timing import _decode

    told = timing.speech_timing(spoken_job, "frq")
    waveform = torch.from_numpy(_decode(spoken_job / media.audio_path("frq"), timing.SAMPLE_RATE, 1))
    words = [w for line in told.lines for w in line.words]
    found = timing.mms_aligner()(waveform, [timing.spoken(w.word) for w in words])
    gaps = [abs(start - w.start) for w, (start, _, _) in zip(words, found)]
    print(f"{len(words)} words, start times within {max(gaps):.3f}s of the TTS's (mean {sum(gaps) / len(gaps):.3f}s)")
    assert sum(gaps) / len(gaps) < 0.15
