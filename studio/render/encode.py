"""Frames to a video file (docs/PLAN.md, section 7, encoding).

The frames are split into ranges, each drawn by its own process and piped as raw BGRA
to its own ffmpeg (H.264, video only). The ranges are joined with `-c copy`, and the
audio is mixed from the timeline's clips and added once at the end, since audio
encoded per range clicks at the joins. Everything is written under a temporary
folder and moved into place last.

encode_all() makes several videos at once (the render step's song, lesson and hook cut)
on one set of workers. Each is split into the ranges it would get alone, so its file is
the same byte for byte; the workers take the ranges of all of them, the longest video's
first, and a video's ranges are joined and its audio added (one ffmpeg, on one core) as
soon as they are drawn, while the workers go on with the others'. Made one after
another, every video ended with most cores idle through its last ranges and its audio
(8 to 10 s on one core for a 3-minute video): on `python -m studio.bench`, the three
videos took 15% less time at once than one after another.
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from studio.media import PEAK_LIMIT
from studio.render.layouts import LAYOUTS
from studio.schemas import Timeline

PARENT_ENV = "POPSMOSIS_RENDER_PARENT"  # the render's pid, so a worker started as it died can tell
ROOT = Path(__file__).resolve().parent.parent.parent  # the folder that holds studio/, for the workers
PRESET = "veryfast"
RENDER_LIMIT_BASE_S, RENDER_LIMIT_PER_S = 300.0, 3.0  # a 3-minute video may take 14 minutes
CRF = "20"


class EncodeError(Exception):
    """A video could not be made; `index` is its place in encode_all()'s list."""

    def __init__(self, message: str, index: int = 0):
        super().__init__(message)
        self.index = index


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


def time_limit(timeline: Timeline) -> float:
    """How long a render may take before it counts as hung: generous, for slower machines."""
    return RENDER_LIMIT_BASE_S + RENDER_LIMIT_PER_S * timeline.duration


def _run(args: list[str], timeout: float) -> None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise EncodeError(f"{Path(args[0]).name} took over {timeout:.0f} s") from e
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


@dataclass
class _Video:
    """One video of encode_all(): what it is made from, its ranges, and how many are still to draw."""

    index: int
    timeline: Timeline
    out: Path
    layout: str
    folder: Path  # its own temporary folder: the timeline the workers read, its ranges, the joined video
    chunks: list[tuple[int, int]]
    left: int
    started: float | None = None  # when its first range started, by time.monotonic(): its time limit runs from then

    def part(self, k: int) -> Path:
        return self.folder / f"part{k:03d}.mp4"


def _finish(job: Path, video: _Video) -> float:
    """Join a video's ranges, add its audio, and move it into place; return when it was, by time.monotonic().
    A failure is an EncodeError naming the video by its index."""
    try:
        return _join(job, video)
    except (EncodeError, OSError) as e:
        raise EncodeError(str(e), video.index) from e


def _join(job: Path, video: _Video) -> float:
    timeline, tmp_dir = video.timeline, video.folder
    listing = tmp_dir / "parts.txt"
    listing.write_text("".join(f"file '{video.part(k).name}'\n" for k in range(len(video.chunks))))
    joined = tmp_dir / "video.mp4"
    _run([ffmpeg(), "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
          "-c", "copy", str(joined)], timeout=time_limit(timeline))
    final = tmp_dir / "final.mp4"
    if timeline.audio:
        inputs, mix = audio_filter(job, timeline)
        _run([ffmpeg(), "-loglevel", "error", "-y", "-i", str(joined), *inputs, "-filter_complex", mix,
              "-map", "0:v", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
              "-t", f"{timeline.duration:.3f}", "-movflags", "+faststart", str(final)], timeout=time_limit(timeline))
    else:
        _run([ffmpeg(), "-loglevel", "error", "-y", "-i", str(joined), "-c", "copy", "-movflags", "+faststart",
              str(final)], timeout=time_limit(timeline))
    video.out.parent.mkdir(parents=True, exist_ok=True)
    partial = video.out.with_name(f".{video.out.name}.partial")
    shutil.move(str(final), partial)
    os.replace(partial, video.out)
    shutil.rmtree(tmp_dir, ignore_errors=True)  # its ranges, now in the video: the others' still need the disk
    return time.monotonic()


def _render_ranges(job: Path, made: list[_Video], n_workers: int, finishing: ThreadPoolExecutor) -> dict[int, float]:
    """Draw every video's ranges, each in its own `python -m studio.render.encode` process, n at a time, and
    finish each video (_finish, on `finishing`) once its last range is drawn. Returns when each was finished,
    by time.monotonic().

    Plain processes rather than multiprocessing, so a worker never re-runs the
    caller's script; the first failure stops the rest and is raised as EncodeError.
    """
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])),
               **{PARENT_ENV: str(os.getpid())})
    for video in made:
        (video.folder / "timeline.json").write_text(video.timeline.model_dump_json(), encoding="utf-8")
    # The longest video's ranges first: the shortest's are what is left to share out at the end.
    order = sorted(made, key=lambda v: -v.timeline.frame_count)
    waiting = [(video, k) for video in order for k in range(len(video.chunks))]
    running: list[tuple[subprocess.Popen, Path, _Video, int]] = []
    # A video with no frames has no ranges to wait for (and its join fails, saying so).
    finished: dict[int, Future] = {}
    for video in made:
        if not video.left:
            video.started = time.monotonic()
            finished[video.index] = finishing.submit(_finish, job, video)
    try:
        while waiting or running or not all(f.done() for f in finished.values()):
            while waiting and len(running) < n_workers:
                video, k = waiting.pop(0)
                if video.started is None:
                    video.started = time.monotonic()
                (first, last), path = video.chunks[k], video.part(k)
                log = path.with_suffix(".worker.log")
                with open(log, "wb") as err:
                    proc = subprocess.Popen(
                        [sys.executable, "-m", "studio.render.encode", str(job.resolve()),
                         str(video.folder / "timeline.json"), video.layout, str(first), str(last), str(path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, env=env)
                running.append((proc, log, video, k))
            for item in list(running):
                proc, log, video, k = item
                code = proc.poll()
                if code is None:
                    continue
                running.remove(item)
                if code:
                    first, last = video.chunks[k]
                    lines = log.read_text(errors="replace").strip().splitlines()
                    raise EncodeError(lines[-1] if lines else f"the worker for frames {first}-{last} exited {code}",
                                      video.index)
                video.left -= 1
                if not video.left:
                    finished[video.index] = finishing.submit(_finish, job, video)
            for future in finished.values():
                if future.done() and future.exception():
                    raise future.exception()  # an EncodeError naming its video (_finish)
            # Each video's own time limit, from its first range to its file: one that hangs is caught as soon
            # as it would have been alone.
            for video in made:
                over = video.started is not None and time.monotonic() > video.started + time_limit(video.timeline)
                if over and not (video.index in finished and finished[video.index].done()):
                    raise EncodeError(f"the render took over {time_limit(video.timeline):.0f} s; its workers were "
                                      "stopped", video.index)
            time.sleep(0.05)
    finally:
        for proc, _, _, _ in running:
            proc.kill()
            proc.wait()
    return {index: future.result() for index, future in finished.items()}


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
        fade = f",afade=t=in:st=0:d={clip.fade_in:.3f}" if clip.fade_in > 0 else ""
        if clip.fade_out > 0 and clip.end is not None:
            fade += f",afade=t=out:st={max(clip.end - clip.start - clip.fade_out, 0):.3f}:d={clip.fade_out:.3f}"
        delay = round(clip.at * 1000)
        chains.append(f"[{i + first_input}:a]aresample=48000,{trim},asetpts=PTS-STARTPTS{fade},"
                      f"adelay={delay}:all=1[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(timeline.audio)))
    # The mix's peaks held under -1 dBFS before the AAC encode, as the audio step keeps a song (audio.PEAK_LIMIT): a
    # song made before Nov 1 went past full scale in its choruses and crackled.
    chains.append(f"{mix}amix=inputs={len(timeline.audio)}:normalize=0:duration=longest,{PEAK_LIMIT},"
                  f"apad,atrim=end={timeline.duration:.3f}[mix]")
    return inputs, ";".join(chains)


def encode(job: Path, timeline: Timeline, out: Path, layout: str = "h", parts: int | None = None) -> Rendered:
    """Render the whole timeline to `out` (an .mp4) with its audio."""
    return encode_all(job, [(timeline, out, layout)], parts)[0]


def encode_all(job: Path, videos: list[tuple[Timeline, Path, str]], parts: int | None = None) -> list[Rendered]:
    """Render each timeline to its .mp4 (timeline, out, layout) with its audio, all at once on one set of
    workers; the same files, byte for byte, as encode() would make one after another. A video that fails stops
    the rest, raised as EncodeError with its index in `videos`; those already finished are kept."""
    started = time.monotonic()
    for index, (timeline, _, _) in enumerate(videos):
        for clip in timeline.audio:
            if not (job / clip.path).exists():
                raise EncodeError(f"no audio file {clip.path}", index)
    n_workers = parts or workers()
    with tempfile.TemporaryDirectory(prefix="popsmosis-render-") as tmp, \
            ThreadPoolExecutor(max(1, len(videos)), thread_name_prefix="finish") as finishing:
        made = []
        for index, (timeline, out, layout) in enumerate(videos):
            folder = Path(tmp) / str(index)
            folder.mkdir()
            # More ranges than workers, so one slow range does not hold up the rest.
            chunks = ranges(timeline.frame_count, n_workers * 2)
            made.append(_Video(index, timeline, out, layout, folder, chunks, len(chunks)))
        done = _render_ranges(job, made, n_workers, finishing)
    return [Rendered(path=v.out, frames=v.timeline.frame_count, seconds=done[v.index] - started) for v in made]


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
