import json
import shutil
import subprocess

import pytest

from studio import ass_fallback, jobs, media
from studio.__main__ import main
from studio.schemas import TimedLine, Timing, Word
from studio.tests import sample_media


def _timing():
    return Timing(audio="audio/song.mp3", duration=10.0, lines=[
        TimedLine(line_id="a", start=1.0, end=2.5, words=[Word(word="Hello", start=1.0, end=1.4),
                                                           Word(word="world", start=2.0, end=2.5)]),
        TimedLine(line_id="b", start=2.8, end=4.0, words=[Word(word="Bye", start=2.8, end=4.0)]),
    ])


def test_lines_show_a_little_before_they_are_sung_and_never_overlap():
    shows = ass_fallback.show_times(_timing())
    assert shows[0] == (0.5, 2.5)  # the next line is due at 2.8, so this one leaves as it ends
    assert shows[1] == (2.5, 5.5)
    assert all(a[1] <= b[0] for a, b in zip(shows, shows[1:]))


def test_each_word_lights_up_as_it_is_sung_and_keeps_its_punctuation():
    line = _timing().lines[0]
    assert ass_fallback.karaoke(line, "Hello, {world}!", 0.5) == r"{\k50}{\k100}Hello, {\k50}(world)!"


def test_the_subtitles_are_one_event_per_line():
    timing = _timing()
    text = ass_fallback.ass(timing, {"a": "Hello world", "b": "Bye"}, ass_fallback.show_times(timing))
    events = [row for row in text.splitlines() if row.startswith("Dialogue:")]
    assert events[0].startswith("Dialogue: 0,0:00:00.50,0:00:02.50,Lyric,")
    assert len(events) == 2 and "PlayResX: 1920" in text


def test_the_art_follows_each_lines_scene_and_falls_back_to_white(tmp_path):
    job = tmp_path / "job"
    (job / "art").mkdir(parents=True)
    (job / "art/hook.png").write_bytes(b"png")
    timing = _timing()
    listing = ass_fallback.art_list(job, tmp_path, timing, {"a": "hook", "b": "gone"}, ass_fallback.show_times(timing))
    rows = listing.splitlines()
    assert rows[1].endswith("art/hook.png'") and rows[2] == "duration 2.500"
    assert rows[3].endswith("white.png'") and rows[4] == "duration 7.500"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_fallback_video_is_made_from_the_sample_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    job = sample_media.write_job(tmp_path / "sample", art=True, audio=True)
    assert main(["fallback", "sample"]) == 0
    out = job / ass_fallback.OUT
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                       str(out)], capture_output=True, text=True, check=True).stdout)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["codec_name"]) == (1920, 1080, "h264")
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    song = jobs.read(job / media.timing_path("song"), Timing)
    assert abs(float(probe["format"]["duration"]) - song.duration) < 0.2
    assert b"moov" in out.read_bytes()[:4096]  # faststart: the index comes before the video
