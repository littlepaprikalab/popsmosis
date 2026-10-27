"""Frames to a video file (docs/PLAN.md, section 7, encoding).

The frames are split into ranges, each drawn by its own process and piped as raw BGRA
to its own ffmpeg (H.264, video only). The ranges are joined with `-c copy`, and the
audio is mixed from the timeline's clips and added once at the end, since audio
encoded per range clicks at the joins. Everything is written under a temporary
folder and moved into place last.
"""

from __future__ import annotations

import itertools
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from studio.render.layouts import LAYOUTS
from studio.schemas import Timeline

PRESET = "veryfast"
CRF = "20"


class EncodeError(Exception):
    pass


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise EncodeError("ffmpeg is not installed")
    return path


def workers() -> int:
    return max(1, int(os.environ.get("STUDIO_RENDER_WORKERS") or os.cpu_count() or 1))


@dataclass
class Rendered:
    path: Path
    frames: int
    seconds: float  # wall-clock time to draw and encode it


def _run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise EncodeError(f"{Path(args[0]).name} failed: {result.stderr.strip()[-800:]}")


def render_range(job: str, timeline_json: str, layout: str, first: int, last: int, out: str) -> int:
    """Draw frames [first, last) and encode them, video only; runs in a worker process."""
    from studio.render.renderer import (
        Renderer,  # imported here: skia loads in the worker
    )

    timeline = Timeline.model_validate_json(timeline_json)
    renderer = Renderer(Path(job), timeline, layout)
    size = LAYOUTS[layout]
    proc = subprocess.Popen(
        [ffmpeg(), "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
         "-s", f"{size.width}x{size.height}", "-r", str(timeline.fps), "-i", "-",
         "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p", "-threads", "2", out],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(first, last):
            proc.stdin.write(memoryview(renderer.frame(n / timeline.fps)).cast("B"))
        proc.stdin.close()
    except BrokenPipeError:
        pass
    error = proc.stderr.read().decode(errors="replace")
    if proc.wait():
        raise EncodeError(f"ffmpeg failed on frames {first}-{last}: {error.strip()[-800:]}")
    return last - first


def ranges(frames: int, parts: int) -> list[tuple[int, int]]:
    parts = max(1, min(parts, frames))
    cuts = [round(frames * i / parts) for i in range(parts + 1)]
    return [(a, b) for a, b in itertools.pairwise(cuts) if b > a]


def audio_filter(job: Path, timeline: Timeline, first_input: int = 1) -> tuple[list[str], str]:
    """ffmpeg inputs and a filter that mixes the clips into one track the video's length.

    The clips are inputs from `first_input` on (the video is input 0)."""
    inputs, chains = [], []
    for i, clip in enumerate(timeline.audio):
        inputs += ["-i", str(job / clip.path)]
        trim = f"atrim=start={clip.start:.3f}" + (f":end={clip.end:.3f}" if clip.end is not None else "")
        delay = round(clip.at * 1000)
        chains.append(f"[{i + first_input}:a]aresample=48000,{trim},asetpts=PTS-STARTPTS,adelay={delay}:all=1[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(timeline.audio)))
    chains.append(f"{mix}amix=inputs={len(timeline.audio)}:normalize=0:duration=longest,"
                  f"apad,atrim=end={timeline.duration:.3f}[mix]")
    return inputs, ";".join(chains)


def encode(job: Path, timeline: Timeline, out: Path, layout: str = "h", parts: int | None = None) -> Rendered:
    """Render the whole timeline to `out` (an .mp4) with its audio."""
    started = time.monotonic()
    for clip in timeline.audio:
        if not (job / clip.path).exists():
            raise EncodeError(f"no audio file {clip.path}")
    frames = max(1, round(timeline.duration * timeline.fps))
    n_workers = parts or workers()
    # More ranges than workers, so one slow range does not hold up the rest.
    chunks = ranges(frames, n_workers * 2)
    with tempfile.TemporaryDirectory(prefix="popsmosis-render-") as tmp:
        tmp_dir = Path(tmp)
        paths = [tmp_dir / f"part{i:03d}.mp4" for i in range(len(chunks))]
        payload = timeline.model_dump_json()
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=min(n_workers, len(chunks)), mp_context=context) as pool:
            futures = [pool.submit(render_range, str(job.resolve()), payload, layout, a, b, str(path))
                       for (a, b), path in zip(chunks, paths)]
            for future in futures:
                future.result()
        listing = tmp_dir / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in paths))
        video = tmp_dir / "video.mp4"
        _run([ffmpeg(), "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
              "-c", "copy", str(video)])
        final = tmp_dir / "final.mp4"
        if timeline.audio:
            inputs, mix = audio_filter(job, timeline)
            _run([ffmpeg(), "-loglevel", "error", "-y", "-i", str(video), *inputs, "-filter_complex", mix,
                  "-map", "0:v", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                  "-t", f"{timeline.duration:.3f}", "-movflags", "+faststart", str(final)])
        else:
            _run([ffmpeg(), "-loglevel", "error", "-y", "-i", str(video), "-c", "copy", "-movflags", "+faststart",
                  str(final)])
        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.with_name(f".{out.name}.partial")
        shutil.move(str(final), partial)
        os.replace(partial, out)
    return Rendered(path=out, frames=frames, seconds=time.monotonic() - started)


def write_poster(job: Path, timeline: Timeline, out: Path, layout: str = "h") -> float:
    """A still for the player before it starts: the fullest moment of the diagram. Returns its time."""
    import skia

    from studio.render.renderer import Renderer

    t = poster_time(timeline)
    image = Renderer(job, timeline, layout).image(t)
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(f".{out.name}.partial")
    data = image.encodeToData(skia.kJPEG, 88)
    partial.write_bytes(bytes(data))
    os.replace(partial, out)
    return t


def poster_time(timeline: Timeline) -> float:
    """Late in the beat with the most labels, once its reveals and camera have settled."""
    best = max(timeline.beats, key=lambda b: (len(b.labels), -b.t0))
    return max(best.t0, best.t1 - 0.5)

