"""When a media step fails the same way every time, the videos take a lesser way that still ends in a droplet.

A retry gets past an outage (studio/retry.py) but not a failure that comes back the same each time: on Nov 4 the
Calvin cycle's locate step found 2 of a scene's 5 structures in the same picture on both tries, and the videos were
given up with the lesson already posted. Each such failure has a ladder here, from the droplet as planned down to
the least it may be and still be one, and the videos take its next rung instead of failing:

    a picture whose parts SAM cannot find (fewer than half of a scene's)
        1. painted again, once a job (studio/steps/locate.py: the art step paints only that scene again)
        2. kept with the parts that were found; the others are left out, as for a structure SAM missed
    the song: Lyria or Eleven Music will not make it, it does not sing its lyrics, it cannot be timed, or its video
    or hook cut cannot be built or rendered (a failure whose StepFailed.part is "song")
        1. the song made again, once a job, when its audio or timing failed (studio/videos.py)
        2. the lesson video alone, as when an admin sets STUDIO_VIDEOS to `lesson`: the posted lesson keeps its
           lyrics, and the droplet is READY with its lesson video
    an outage of the song's provider: waited out and left to a later sweep while the API allows another attempt
    (VIDEO_MAX_ATTEMPTS), as before; on the last attempt, the lesson video alone

What a job let go of is kept in its ANNEAL file, so a retry on the same machine starts on the rung the last run
reached rather than paying again for what failed, and `summary` says what the droplet was made without. A job
rebuilt on another machine (`recover`) starts again from the top.

Nothing here touches the lesson's gates: the fact audit and the fact-check still pass every line before the lesson
is posted, and the videos only ever show what was posted.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from studio import jobs

ANNEAL = "anneal.json"
SONG_TRIES = 2  # branch runs the song gets on a machine (each with the step's own retries) before it is let go


def read(job: Path) -> dict:
    try:
        data = json.loads((job / ANNEAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _update(job: Path, **changes) -> dict:
    data = {**read(job), **changes}
    jobs.write(job / ANNEAL, data)
    return data


# --- the song --------------------------------------------------------------------------------


def songless(job: Path) -> str | None:
    """Why the job's videos went on without the song (the lesson video alone), or None while it has one."""
    dropped = read(job).get("songless")
    return str(dropped.get("why") or "the song could not be made") if isinstance(dropped, dict) else None


def song_failures(job: Path) -> int:
    """How many runs of the videos here the song failed in, with every retry of its own."""
    n = read(job).get("song_failures")
    return n if isinstance(n, int) and n > 0 else 0


def note_song_failure(job: Path, code: str) -> int:
    """Count one more run the song failed in; returns the count."""
    n = song_failures(job) + 1
    _update(job, song_failures=n, song_code=code)
    return n


def drop_song(job: Path, why: str, code: str) -> None:
    """Make the lesson video alone from here on (media.lesson_only()): the song could not be made."""
    _update(job, songless={"why": why, "code": code, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})


# --- the pictures ----------------------------------------------------------------------------


def repainted(job: Path) -> list[str]:
    """The scenes painted again because SAM found too few of their parts (locate's first rung)."""
    scenes = read(job).get("repainted")
    return [str(s) for s in scenes] if isinstance(scenes, list) else []


def mark_repainted(job: Path, scenes: list[str]) -> None:
    _update(job, repainted=sorted({*repainted(job), *scenes}))


def kept_partial(job: Path, scenes: list[str]) -> None:
    """The scenes kept with the parts that were found, after their repaint (locate's second rung)."""
    before = read(job).get("partial")
    before = [str(s) for s in before] if isinstance(before, list) else []
    _update(job, partial=sorted({*before, *scenes}))


# --- for the record ----------------------------------------------------------------------------


def said(job: Path) -> str:
    """What the videos were made without, in the studio's own words (no learner's, no provider's): for `summary`,
    which goes on Inkmory, and `show`. "" when they took no lesser way."""
    data = read(job)
    parts = []
    if isinstance(data.get("songless"), dict):
        parts.append("the lesson video alone: the song could not be made"
                     + (f" ({data['songless'].get('code')})" if data["songless"].get("code") else ""))
    if data.get("repainted"):
        parts.append(f"{len(data['repainted'])} scene(s) painted again after too few of their parts were found")
    if data.get("partial"):
        parts.append(f"{len(data['partial'])} scene(s) kept with the parts that were found")
    return "; ".join(parts)
