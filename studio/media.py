"""The files the media steps (docs/PLAN.md, steps 3-9) pass to each other, and how a step is built.

Every path is relative to the job folder, jobs/<id>/. The models are in studio/schemas.py.

    art/<scene>.png                     3     RGB, ART_WIDTH x ART_HEIGHT, white background, no text in it
    masks/<scene>/<slug>.png            3/4   8-bit greyscale, the art's size: 255 where the structure is
                                              visible in the art (what covers it is left out), 0 elsewhere
    locations.json                      3/4   Locations: each structure's mask, box, centroid, label anchor
    audio/song.mp3                      5     the song
    audio/narration.mp3                 5     the lesson video's voice-over
    audio/frq.mp3                       5     the exam question read aloud, the lines of frq_lines() in order
    timing/song_words.json              6     a Timing for each audio file
    timing/narration_words.json         6
    timing/frq_words.json               6
    timeline_song.json                  7     a Timeline for each video
    timeline_lesson.json                7
    out/song_h.mp4, out/lesson_h.mp4    8     1920x1080, 30 fps, H.264 + AAC, -movflags +faststart
    out/song_h.jpg, out/lesson_h.jpg    8     a poster frame for each
    out/song_fallback_h.mp4             -     the ffmpeg karaoke fallback (PLAN section 7)

Interim art (brain-drawn SVG, until the Meta key arrives) writes the masks and
locations.json itself, so locate has nothing left to do.

A step is a module studio/steps/<name>.py, for each name in MEDIA_STEPS, with

    run(job: Path, ask=brain.ask) -> str    do the step; return one line saying what it did;
                                            raise StepFailed (or brain.BrainError) when it does not succeed
    is_done(job: Path) -> bool              whether its files are there and read

`python -m studio step <name> <job>` runs one as soon as its module exists. A step
writes its index file (locations.json, a Timing, a Timeline) last, after the files
it points to, with jobs.write, so a run stopped halfway never looks done.
"""

from __future__ import annotations

import importlib
import re
from types import ModuleType

from studio.schemas import Bundle

MEDIA_STEPS = ("art", "locate", "audio", "timing", "timeline", "render", "upload")
VIDEOS = ("song", "lesson")
TIMED = ("song", "narration", "frq")  # the audio files, each with its Timing

ART_WIDTH, ART_HEIGHT = 1792, 1024
VIDEO_WIDTH, VIDEO_HEIGHT, FPS = 1920, 1080, 30
PREDICT_PAUSE_S = 5.0

LOCATIONS = "locations.json"


class StepFailed(Exception):
    """The step ran and did not succeed; running it again retries it."""


def slug(name: str) -> str:
    """A structure's name as a file name: "Broca's area" -> "broca-s-area"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "structure"


def art_path(scene: str) -> str:
    return f"art/{scene}.png"


def mask_path(scene: str, structure: str) -> str:
    return f"masks/{scene}/{slug(structure)}.png"


def audio_path(kind: str) -> str:
    return f"audio/{kind}.mp3"


def timing_path(kind: str) -> str:
    return f"timing/{kind}_words.json"


def timeline_path(video: str) -> str:
    return f"timeline_{video}.json"


def video_path(video: str, layout: str = "h") -> str:
    return f"out/{video}_{layout}.mp4"


def poster_path(video: str, layout: str = "h") -> str:
    return f"out/{video}_{layout}.jpg"


def frq_lines(bundle: Bundle) -> list[tuple[str, str]]:
    """The exam question as the videos read it aloud and show it: (line id, text), in order."""
    frq = bundle.frq
    lines = [("frq-prompt", frq.prompt), ("frq-predict", frq.predict_question)]
    return lines + [(f"frq-step-{i}", step) for i, step in enumerate(frq.model_answer_steps, 1)]


def step_module(name: str) -> ModuleType:
    """studio/steps/<name>.py; LookupError until that step is built."""
    if name not in MEDIA_STEPS:
        raise LookupError(f"{name!r} is not a media step")
    try:
        return importlib.import_module(f"studio.steps.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"studio.steps.{name}":
            raise  # the step exists and one of its own imports is missing
        raise LookupError(f"the {name} step is not built yet (studio/steps/{name}.py)") from e
