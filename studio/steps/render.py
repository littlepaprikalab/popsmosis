"""Step 8: render. Both timelines to 16:9 videos and their poster frames (docs/PLAN.md, section 7).

    out/song_h.mp4, out/lesson_h.mp4    1920x1080, 30 fps, H.264 + AAC, -movflags +faststart
    out/song_h.jpg, out/lesson_h.jpg    a poster frame for each

The drawing is in studio/render/: renderer.py draws a frame at a time, encode.py
splits the frames across processes and adds the audio. No model calls.
"""

from __future__ import annotations

from pathlib import Path

from studio import media
from studio.jobs import read
from studio.media import StepFailed
from studio.schemas import Locations, Timeline, timeline_problems

LAYOUT = "h"  # the vertical "v" layout is a stretch goal


def run(job: Path, ask=None) -> str:
    """Render both videos; `ask` is unused (no model calls)."""
    from studio.render.encode import EncodeError, encode, write_poster
    from studio.render.renderer import RenderError

    # A re-render cut off halfway must not leave last time's videos looking done.
    media.restart(job, *(path(video, LAYOUT) for video in media.VIDEOS
                         for path in (media.video_path, media.poster_path)))
    locations = read(job / media.LOCATIONS, Locations)
    done = []
    for video in media.VIDEOS:
        timeline = read(job / media.timeline_path(video), Timeline)
        problems = timeline_problems(timeline, locations)
        if problems:
            raise StepFailed(f"the {video} timeline: " + "; ".join(problems))
        try:
            write_poster(job, timeline, job / media.poster_path(video, LAYOUT), LAYOUT)
            rendered = encode(job, timeline, job / media.video_path(video, LAYOUT), LAYOUT)
        except (RenderError, EncodeError) as e:
            raise StepFailed(f"{video}: {e}") from e
        done.append(f"{video} {timeline.duration:.0f} s in {rendered.seconds:.0f} s")
    return "rendered " + ", ".join(done)


def is_done(job: Path) -> bool:
    """Both videos and posters are there, and newer than the timelines they were made from."""
    for video in media.VIDEOS:
        timeline = job / media.timeline_path(video)
        outputs = [job / media.video_path(video, LAYOUT), job / media.poster_path(video, LAYOUT)]
        if not timeline.exists() or not all(o.exists() and o.stat().st_size for o in outputs):
            return False
        if any(o.stat().st_mtime < timeline.stat().st_mtime for o in outputs):
            return False
    return True
