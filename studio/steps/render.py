"""Step 8: render. Both timelines to 16:9 videos and their poster frames (docs/PLAN.md, section 7).

    out/song_h.mp4, out/lesson_h.mp4    1920x1080, 30 fps, H.264 + AAC, -movflags +faststart
    out/song_h.jpg, out/lesson_h.jpg    a poster frame for each
    out/hook_v.mp4, out/hook_v.jpg      the hook cut, 1080x1920, when the song has one (timeline_hook.json)

The drawing is in studio/render/: renderer.py draws a frame at a time, encode.py
splits the frames across processes and adds the audio. The videos are made at once on
one set of workers (encode_all()), each the same file it would be made alone, and each
one's time in the step's line runs from the start of the step. No model calls.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from studio import media
from studio.jobs import read
from studio.media import StepFailed
from studio.schemas import Locations, Timeline, timeline_problems

LAYOUT = "h"  # the song and lesson videos; the hook cut is upright (media.HOOK_LAYOUT)


def videos(job: Path) -> list[tuple[str, str]]:
    """What this job renders, each video with its layout: the song and the lesson, and the hook cut when there is one."""
    made = [(video, LAYOUT) for video in media.VIDEOS]
    if (job / media.timeline_path(media.HOOK)).exists():
        made.append((media.HOOK, media.HOOK_LAYOUT))
    return made


def run(job: Path, ask=None) -> str:
    """Render the videos; `ask` is unused (no model calls)."""
    from studio.render.encode import EncodeError, encode_all, write_poster
    from studio.render.renderer import RenderError

    made = videos(job)
    # A re-render cut off halfway must not leave last time's videos looking done.
    media.restart(job, *(path(video, layout) for video, layout in made for path in (media.video_path, media.poster_path)))
    locations = read(job / media.LOCATIONS, Locations)
    timelines = []
    for video, _ in made:
        timeline = read(job / media.timeline_path(video), Timeline)
        problems = timeline_problems(timeline, locations)
        if problems:
            raise StepFailed(f"the {video} timeline: " + "; ".join(problems))
        timelines.append(timeline)

    def posters() -> None:
        for (video, layout), timeline in zip(made, timelines):
            try:
                write_poster(job, timeline, job / media.poster_path(video, layout), layout)
            except RenderError as e:
                raise StepFailed(f"{video}: {e}") from e

    # All the videos at once, on one set of workers (encode_all), and their posters drawn meanwhile.
    with ThreadPoolExecutor(1) as drawing:
        drawn = drawing.submit(posters)
        try:
            rendered = encode_all(job, [(timeline, job / media.video_path(video, layout), layout)
                                        for (video, layout), timeline in zip(made, timelines)])
        except EncodeError as e:
            raise StepFailed(f"{made[e.index][0]}: {e}") from e
        drawn.result()
    return "rendered " + ", ".join(f"{video} {timeline.duration:.0f} s in {done.seconds:.0f} s"
                                   for (video, _), timeline, done in zip(made, timelines, rendered))


def is_done(job: Path) -> bool:
    """Every video and poster is there, and newer than the timeline it was made from."""
    for video, layout in videos(job):
        timeline = job / media.timeline_path(video)
        outputs = [job / media.video_path(video, layout), job / media.poster_path(video, layout)]
        if not timeline.exists() or not all(o.exists() and o.stat().st_size for o in outputs):
            return False
        if any(o.stat().st_mtime < timeline.stat().st_mtime for o in outputs):
            return False
    return True
