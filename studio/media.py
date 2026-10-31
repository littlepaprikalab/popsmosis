"""The files the media steps (docs/PLAN.md, steps 3-9) pass to each other, and how a step is built.

Every path is relative to the job folder, jobs/<id>/. The models are in studio/schemas.py.

    art/<scene>.png                     3     RGB, ART_WIDTH x ART_HEIGHT, a plain background, no text in it
    masks/<scene>/<structure>.png       3/4   8-bit greyscale, the art's size: 255 where the structure is
                                              visible in the art (what covers it is left out), 0 elsewhere
    locations.json                      3/4   Locations: each structure's mask, box, centroid, label anchor
    audio/song.mp3                      5     the song
    audio/narration.mp3                 5     the lesson video's voice-over
    timing/song_words.json              6     a Timing for each audio file
    timing/narration_words.json         6
    timeline_song.json                  7     a Timeline for each video
    timeline_lesson.json                7
    out/song_h.mp4, out/lesson_h.mp4    8     1920x1080, 30 fps, H.264 + AAC, -movflags +faststart
    out/song_h.jpg, out/lesson_h.jpg    8     a poster frame for each, beside its video: a poster's
                                              URL is its video's with .jpg for .mp4
    out/song_fallback_h.mp4             -     the ffmpeg karaoke fallback (PLAN section 7)

Scene ids and structure names are written by the model, so they never reach a path
as they are: file_name() makes each one safe for the job folder and for the API's
upload paths, and distinct ("α subunit" and "β subunit" do not share a mask).
Always build paths with the functions below.

The brain's SVG art (an admin's setting for Claude's brain) writes the masks and
locations.json itself, so locate has nothing left to do. Painted art, Gemini 3.1 Flash
Image's or Muse Image's, is pictures only: locate writes the masks and locations.json
from SAM 3.1.

The lesson alone. While STUDIO_VIDEOS is `lesson` (an admin's knob on /admin/studio,
studio/knobs.py) no song is made: audio, timing, timeline, render and upload make and
send only the narration and the lesson video, and no hook cut, which is the song's. A
bundle written then has no song to make (Bundle.sung), so its job stays the lesson's alone
whatever the knob says later. videos() and timed() say which a job makes; VIDEOS and TIMED
name them all.

A step is a module studio/steps/<name>.py, for each name in MEDIA_STEPS, with

    run(job: Path, ask=brain.ask) -> str    do the step; return one line saying what it did;
                                            raise StepFailed (or brain.BrainError) when it does not succeed
    is_done(job: Path) -> bool              whether its files are there and read

`python -m studio step <name> <job>` runs one as soon as its module exists. A step
starts by removing its index files (locations.json, a Timing, a Timeline) with
restart(), and writes them last, after the files they point to, with jobs.write: a
run stopped halfway, even on a re-run, never looks done with old and new files mixed.
A step that runs makes every later one stale, so `run` runs every step after the
first one that is not done, as it already does for the bundle.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path
from types import ModuleType

import numpy as np

from studio import knobs
from studio.schemas import SAFE_PATH  # every path built here passes SAFE_PATH

MEDIA_STEPS = ("art", "locate", "audio", "timing", "timeline", "render", "upload")
VIDEOS = ("song", "lesson")
HOOK, HOOK_LAYOUT = "hook", "v"  # the hook cut: the song's pre-chorus and first chorus, upright, to share
TIMED = ("song", "narration")  # the audio files, each with its Timing

ART_WIDTH, ART_HEIGHT = 1792, 1024
VIDEO_WIDTH, VIDEO_HEIGHT, FPS = 1920, 1080, 30

LOCATIONS = "locations.json"
LESSON_ONLY = "lesson"  # STUDIO_VIDEOS's value for the lesson video alone


def lesson_only(job: Path | None = None) -> bool:
    """Whether the videos are the lesson's alone: STUDIO_VIDEOS is `lesson`, on /admin/studio or in the environment,
    or the job's bundle has no song to make (written while it was)."""
    return knobs.env("STUDIO_VIDEOS").strip().lower() == LESSON_ONLY or (job is not None and not sung(job))


def sung(job: Path) -> bool:
    """Whether the job's bundle has a song (Bundle.sung): read as JSON, not as a Bundle, since the steps ask often.
    True when there is no bundle to read: the knob alone decides."""
    try:
        bundle = json.loads((job / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(bundle, dict):
        return True
    music = bundle.get("music") if isinstance(bundle.get("music"), dict) else {}
    return bool(bundle.get("lyrics") or music.get("positive_styles") or music.get("negative_styles"))


def videos(job: Path | None = None) -> tuple[str, ...]:
    """The videos a job makes: the song and the lesson, or the lesson alone (lesson_only())."""
    return ("lesson",) if lesson_only(job) else VIDEOS


def timed(job: Path | None = None) -> tuple[str, ...]:
    """The audio files a job makes and times: the song and the narration, or the narration alone."""
    return ("narration",) if lesson_only(job) else TIMED


class StepFailed(Exception):
    """The step ran and did not succeed; running it again retries it.

    `code` says why in fixed words of the studio's own, for what goes public (studio/run.py, failure_code): the
    message can quote a line, a picture's writing or a provider's answer, all of them made from what a learner sent."""

    code = "step failed"

    def __init__(self, message: str = "", code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


def slug(name: str) -> str:
    """Lowercase letters, digits and dashes: "Broca's area" -> "broca-s-area"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")


def file_name(name: str) -> str:
    """A scene id or structure name as a file name: itself when it is already a slug
    ("limbic-detail"), else its slug and a short hash of the name ("broca-s-area-3c2e1a"),
    so two names never share a file and none can leave its folder."""
    plain = slug(name)
    if plain and plain == name:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
    return f"{plain}-{digest}" if plain else digest


PAPER_EDGE = 6  # pixels: the ring around a picture whose colour is its background


def paper(pixels: np.ndarray) -> tuple[int, int, int]:
    """A picture's background colour: the median of a thin ring around its edge, its channels in the
    picture's own order (H x W x 3 or more). The house page for the SVG art (svg.PAGE); for Muse Image's,
    whatever it painted behind the diagram."""
    rgb = pixels[..., :3]
    e = PAPER_EDGE
    ring = np.concatenate([rgb[:e].reshape(-1, 3), rgb[-e:].reshape(-1, 3),
                           rgb[:, :e].reshape(-1, 3), rgb[:, -e:].reshape(-1, 3)])
    return tuple(int(v) for v in np.median(ring, axis=0))


def art_path(scene: str) -> str:
    return f"art/{file_name(scene)}.png"


def check_path(scene: str) -> str:
    """What a Muse Image picture of the scene was made from and what its check found (studio/steps/art.py)."""
    return f"art/{file_name(scene)}.json"


def mask_path(scene: str, structure: str) -> str:
    return f"masks/{file_name(scene)}/{file_name(structure)}.png"


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


def restart(job: Path, *index_files: str) -> None:
    """Remove a step's index files before it writes anything, so it is not done until it finishes."""
    for name in index_files:
        (job / name).unlink(missing_ok=True)


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
