"""Frames to a video file (docs/PLAN.md, section 7, encoding).

The frames are split into ranges, each drawn by its own process and piped as raw BGRA
to its own ffmpeg (H.264, video only). The ranges are joined with `-c copy`, and the
audio is mixed from the timeline's clips and added once at the end, since audio
encoded per range clicks at the joins. Everything is written under a temporary
folder and moved into place last.
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from studio.render.layouts import LAYOUTS
from studio.schemas import Timeline

PARENT_ENV = "POPSMOSIS_RENDER_PARENT"  # the render's pid, so a worker started as it died can tell
ROOT = Path(__file__).resolve().parent.parent.parent  # the folder that holds studio/, for the workers
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


def render_range(job: Path, timeline: Timeline, layout: str, first: int, last: int, out: Path) -> None:
    """Draw frames [first, last) and encode them, video only. Runs in a worker process."""
    from studio.render.renderer import Renderer  # skia loads in the worker

    renderer = Renderer(job, timeline, layout)
    size = LAYOUTS[layout]
    with open(out.with_suffix(".ffmpeg.log"), "wb") as log:
        proc = subprocess.Popen(
            [ffmpeg(), "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
             "-s", f"{size.width}x{size.height}", "-r", str(timeline.fps), "-i", "-",
             "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p", "-threads", "2", str(out)],
            stdin=subprocess.PIPE, stderr=log)
        try:
            for n in range(first, last):
                proc.stdin.write(memoryview(renderer.frame(n / timeline.fps)).cast("B"))
            proc.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg stopped early; its exit code and log say why
        except BaseException:
            proc.kill()
            raise
        finally:
            code = proc.wait()
    if code:
        error = out.with_suffix(".ffmpeg.log").read_text(errors="replace").strip()
        raise EncodeError(f"ffmpeg failed on frames {first}-{last}: {error[-800:]}")


def _die_with_parent() -> bool:
    """In a worker, first thing: end when the render that started it is killed (Linux only).

    Set in the worker rather than with preexec_fn, which can deadlock when the caller
    has threads. False if the render is already gone."""
    try:
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass  # not Linux: a killed render leaves its workers to finish on their own
    parent = os.environ.get(PARENT_ENV)
    return parent is None or os.getppid() == int(parent)


def _render_ranges(job: Path, timeline: Timeline, layout: str, chunks: list[tuple[int, int]], paths: list[Path],
                   n_workers: int, tmp_dir: Path) -> None:
    """Run render_range for each range in its own `python -m studio.render.encode` process, n at a time.

    Plain processes rather than multiprocessing, so a worker never re-runs the
    caller's script; the first failure stops the rest and is raised as EncodeError.
    """
    timeline_file = tmp_dir / "timeline.json"
    timeline_file.write_text(timeline.model_dump_json(), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])),
               **{PARENT_ENV: str(os.getpid())})
    waiting = list(zip(chunks, paths))
    running: list[tuple[subprocess.Popen, Path, tuple[int, int]]] = []
    try:
        while waiting or running:
            while waiting and len(running) < n_workers:
                (first, last), path = waiting.pop(0)
                log = path.with_suffix(".worker.log")
                with open(log, "wb") as err:
                    proc = subprocess.Popen(
                        [sys.executable, "-m", "studio.render.encode", str(job.resolve()), str(timeline_file),
                         layout, str(first), str(last), str(path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, env=env)
                running.append((proc, log, (first, last)))
            for item in list(running):
                proc, log, (first, last) = item
                code = proc.poll()
                if code is None:
                    continue
                running.remove(item)
                if code:
                    lines = log.read_text(errors="replace").strip().splitlines()
                    raise EncodeError(lines[-1] if lines else f"the worker for frames {first}-{last} exited {code}")
            time.sleep(0.05)
    finally:
        for proc, _, _ in running:
            proc.kill()
            proc.wait()


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
    frames = timeline.frame_count
    n_workers = parts or workers()
    # More ranges than workers, so one slow range does not hold up the rest.
    chunks = ranges(frames, n_workers * 2)
    with tempfile.TemporaryDirectory(prefix="popsmosis-render-") as tmp:
        tmp_dir = Path(tmp)
        paths = [tmp_dir / f"part{i:03d}.mp4" for i in range(len(chunks))]
        _render_ranges(job, timeline, layout, chunks, paths, n_workers, tmp_dir)
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


def _worker(argv: list[str]) -> int:
    """python -m studio.render.encode <job> <timeline.json> <layout> <first> <last> <out.mp4>"""
    job, timeline_file, layout, first, last, out = argv
    if not _die_with_parent():
        return 1
    timeline = Timeline.model_validate_json(Path(timeline_file).read_text(encoding="utf-8"))
    try:
        render_range(Path(job), timeline, layout, int(first), int(last), Path(out))
    except Exception as e:  # noqa: BLE001 -- any failure becomes one line for the parent to report
        print(f"{type(e).__name__}: {e}".replace("\n", " "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_worker(sys.argv[1:]))
