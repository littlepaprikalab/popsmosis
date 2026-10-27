"""A media job for tests and for building steps 3-9 before the steps before them exist.

    python -m studio.tests.sample_media jobs/sample [--art] [--audio]

writes into jobs/sample/ a request and bundle (the light reactions, from sample.py),
locations.json, the three timings and both timelines; with --art, the art and the
masks (a flat ellipse per structure, no extra libraries); with --audio, tones the
length of each audio file (needs ffmpeg). Everything in it validates.

The timelines are written by hand for the renderer: they reveal, spotlight and move
the camera more than the sample bundle's direction asks for, so they are not what the
timeline step should make from that bundle.
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import zlib
from pathlib import Path

from studio import jobs, media
from studio.schemas import (AudioClip, Beat, Box, Bundle, FrqSegment, FrqStep, Location, Locations, Particles,
                            Point, Request, Reveal, SceneLocations, TimedLine, Timeline, TimelineCamera, Timing,
                            Word)
from studio.tests.sample import bundle_data

LINE_S = 4.3  # one sung line at 112 bpm (PLAN section 5, step 5)
SENTENCE_S = 4.0
# Each structure as an ellipse (centre x, centre y, radius x, radius y), drawn in this order.
SHAPES = {
    "hook": {"Thylakoid": (896, 512, 330, 220)},
    "overview": {"Thylakoid": (896, 560, 640, 170), "Cytochrome b6f": (900, 470, 120, 150)},
    "synthase": {"ATP synthase": (896, 520, 200, 300)},
}


def bundle() -> Bundle:
    """The sample bundle, with the first lyric and narration line over the hook, as the prompt asks."""
    data = bundle_data()
    for d in data["direction"]:
        if d["line_id"] in ("v1-1", "n1"):
            d["scene"] = "hook"
    return Bundle.model_validate(data)


# --- locations and masks --------------------------------------------------------------


def _spans(scene: str) -> dict[str, list[tuple[int, int] | None]]:
    """Per structure, per row of the art, the columns where it is visible (later shapes cover earlier ones)."""
    shapes = SHAPES[scene]
    spans: dict[str, list[tuple[int, int] | None]] = {}
    for name, (cx, cy, rx, ry) in shapes.items():
        rows = []
        for y in range(media.ART_HEIGHT):
            dy = (y + 0.5 - cy) / ry
            if abs(dy) >= 1:
                rows.append(None)
                continue
            half = rx * (1 - dy * dy) ** 0.5
            rows.append((max(0, round(cx - half)), min(media.ART_WIDTH, round(cx + half))))
        spans[name] = rows
    return spans


def _visible(scene: str) -> dict[str, list[bytearray]]:
    """Each structure's mask, row by row: 255 where it shows in the art."""
    spans, names = _spans(scene), list(SHAPES[scene])
    masks = {}
    for i, name in enumerate(names):
        rows = []
        for y in range(media.ART_HEIGHT):
            row = bytearray(media.ART_WIDTH)
            if spans[name][y]:
                x0, x1 = spans[name][y]
                row[x0:x1] = b"\xff" * (x1 - x0)
                for later in names[i + 1:]:
                    if spans[later][y]:
                        a, b = spans[later][y]
                        row[a:b] = bytes(b - a)
            rows.append(row)
        masks[name] = rows
    return masks


def _location(scene: str, name: str, rows: list[bytearray]) -> Location:
    xs, ys, total, sx, sy = [], [], 0, 0.0, 0.0
    for y, row in enumerate(rows):
        filled = [x for x in range(0, media.ART_WIDTH, 4) if row[x]]  # every 4th column is plenty for a centroid
        if filled:
            xs += [filled[0], filled[-1]]
            ys.append(y)
            total += len(filled)
            sx += sum(filled)
            sy += y * len(filled)
    centroid = Point(x=round(sx / total, 1), y=round(sy / total, 1))
    top = min(ys)
    anchor_row = top + (max(ys) - top) // 4  # a quarter of the way down, where a label from above lands
    x0, x1 = max(_runs(rows[anchor_row]), key=lambda run: run[1] - run[0])  # the widest stretch, clear of others
    return Location(name=name, mask=media.mask_path(scene, name),
                    box=Box(x0=min(xs), y0=top, x1=max(xs) + 4, y1=max(ys) + 1), centroid=centroid,
                    label_anchor=Point(x=(x0 + x1) // 2, y=anchor_row), found_by="svg")


def locations() -> Locations:
    scenes = []
    for scene in SHAPES:
        visible = _visible(scene)
        scenes.append(SceneLocations(scene=scene, art=media.art_path(scene), width=media.ART_WIDTH,
                                     height=media.ART_HEIGHT,
                                     structures=[_location(scene, name, rows) for name, rows in visible.items()]))
    return Locations(scenes=scenes)


def write_png(path: Path, width: int, height: int, rows: list[bytes], channels: int) -> None:
    """An 8-bit greyscale (1 channel) or RGB (3) PNG from its rows of bytes."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    color_type = {1: 0, 3: 2}[channels]
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def write_art(job: Path, the_bundle: Bundle) -> None:
    colors = {s.name: bytes.fromhex(s.color_hex[1:]) for scene in the_bundle.scenes for s in scene.structures}
    for scene in SHAPES:
        visible = _visible(scene)
        art = [bytearray(b"\xff" * (media.ART_WIDTH * 3)) for _ in range(media.ART_HEIGHT)]
        for name, rows in visible.items():
            write_png(job / media.mask_path(scene, name), media.ART_WIDTH, media.ART_HEIGHT, rows, 1)
            for y, row in enumerate(rows):
                for x0, x1 in _runs(row):
                    art[y][x0 * 3:x1 * 3] = colors[name] * (x1 - x0)
        write_png(job / media.art_path(scene), media.ART_WIDTH, media.ART_HEIGHT, art, 3)


def _runs(row: bytearray) -> list[tuple[int, int]]:
    runs, start = [], None
    for x, value in enumerate(row):
        if value and start is None:
            start = x
        elif not value and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, len(row)))
    return runs


# --- timings ----------------------------------------------------------------------------


def _timed(line_id: str, text: str, start: float, length: float) -> TimedLine:
    words = [w.strip(".,;:!?\"'()") for w in text.split()]
    step = length / len(words)
    return TimedLine(line_id=line_id, start=start, end=round(start + length, 3),
                     words=[Word(word=w, start=round(start + i * step, 3), end=round(start + (i + 0.85) * step, 3))
                            for i, w in enumerate(words)])


def _timing(kind: str, lines: list[tuple[str, str]], length: float) -> Timing:
    timed = [_timed(i, text, round(n * length, 3), length) for n, (i, text) in enumerate(lines)]
    return Timing(audio=media.audio_path(kind), duration=round(len(lines) * length, 3), lines=timed)


def timings(the_bundle: Bundle) -> dict[str, Timing]:
    return {
        "song": _timing("song", [(line.id, line.text) for line in the_bundle.lyrics], LINE_S),
        "narration": _timing("narration", [(n.id, n.text) for n in the_bundle.narration], SENTENCE_S),
        "frq": _timing("frq", media.frq_lines(the_bundle), SENTENCE_S),
    }


# --- timelines ----------------------------------------------------------------------------

# By line: the scene, what it reveals (the word heard), the spotlight, the camera's target and zoom.
SONG_DIRECTION = {
    "v1-1": ("hook", [("Thylakoid", "thylakoid")], None, None, 1.0),
    "v1-2": ("overview", [("Thylakoid", "Every")], None, None, 1.0),
    "c1-1": ("overview", [], None, None, 1.0),
    "v2-1": ("overview", [("Cytochrome b6f", "b6f")], "Cytochrome b6f", "Cytochrome b6f", 1.8),
    "c2-1": ("overview", [], "Cytochrome b6f", None, 1.0),
    "b-1": ("synthase", [("ATP synthase", "gradient")], "ATP synthase", "ATP synthase", 1.4),
    "c3-1": ("synthase", [], None, None, 1.0),
}
LESSON_DIRECTION = {
    "n1": ("hook", [("Thylakoid", "thylakoid")], "Thylakoid", "Thylakoid", 1.3),
    "n2": ("synthase", [("ATP synthase", "synthase")], None, None, 1.0),
}


def timeline(video: str, the_bundle: Bundle | None = None) -> Timeline:
    the_bundle = the_bundle or bundle()
    timed = timings(the_bundle)
    voice = timed["song" if video == "song" else "narration"]
    direction = SONG_DIRECTION if video == "song" else LESSON_DIRECTION
    palette = {s.name: s.color_hex for scene in the_bundle.scenes for s in scene.structures}
    texts = {line.id: line.text for line in [*the_bundle.lyrics, *the_bundle.narration]}

    beats, shown = [], {}
    for i, line in enumerate(voice.lines):
        scene, reveals, spotlight, target, zoom = direction[line.line_id]
        t1 = voice.lines[i + 1].start if i + 1 < len(voice.lines) else voice.duration
        revealed = [Reveal(name=name, t=next(w.start for w in line.words if w.word == word)) for name, word in reveals]
        labels = shown.setdefault(scene, [])
        labels += [r.name for r in revealed if r.name not in labels]
        caption = the_bundle.core_idea if line.line_id == "c1-1" else None
        particles = [Particles(path=["Thylakoid", "Cytochrome b6f"], color="#F2C230", trail=True)] \
            if line.line_id == "c2-1" else []
        beats.append(Beat(t0=line.start, t1=t1, scene=scene, camera=TimelineCamera(target=target, zoom=zoom),
                          reveal=revealed, spotlight=spotlight, labels=list(labels), particles=particles,
                          caption=caption, line_id=line.line_id, line=texts[line.line_id], words=line.words))

    # The exam question: read up to the predict question, a pause, then the steps.
    frq_timing, t0 = timed["frq"], voice.duration
    asked = frq_timing.line("frq-predict").end
    answer_at = t0 + asked + media.PREDICT_PAUSE_S
    steps = [line for line in frq_timing.lines if line.line_id.startswith("frq-step-")]
    duration = round(answer_at + frq_timing.duration - asked, 3)
    # The card sits over the overview with everything on it labelled, a review of the whole diagram.
    labels = [s.name for s in next(scene for scene in the_bundle.scenes if scene.id == "overview").structures]
    spotlights = ["Cytochrome b6f", "ATP synthase", None]
    frq = FrqSegment(
        t0=t0, t1=duration, scene="overview", labels=labels, prompt=the_bundle.frq.prompt,
        predict_question=the_bundle.frq.predict_question, predict_t=round(t0 + asked, 3),
        predict_pause_s=media.PREDICT_PAUSE_S,
        steps=[FrqStep(t=round(answer_at + line.start - asked, 3), text=the_bundle.frq.model_answer_steps[i],
                       spotlight=spotlights[min(i, 2)] if spotlights[min(i, 2)] in labels else None)
               for i, line in enumerate(steps)])
    audio = [
        AudioClip(path=voice.audio, at=0),
        AudioClip(path=frq_timing.audio, at=t0, start=0, end=asked),
        AudioClip(path=frq_timing.audio, at=answer_at, start=asked),
    ]
    return Timeline(video=video, duration=duration, fps=media.FPS, audio=audio, palette=palette, beats=beats, frq=frq)


# --- the job ------------------------------------------------------------------------------


def write_job(job: Path, art: bool = False, audio: bool = False) -> Path:
    job.mkdir(parents=True, exist_ok=True)
    the_bundle = bundle()
    jobs.write(job / "request.json", Request(topic=the_bundle.topic, course=the_bundle.course))
    jobs.write(job / "bundle.json", the_bundle)
    if art:
        write_art(job, the_bundle)
    jobs.write(job / media.LOCATIONS, locations())
    for kind, timing in timings(the_bundle).items():
        if audio:
            path = job / timing.audio
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"sine=frequency=440:duration={timing.duration}", "-q:a", "9", str(path)], check=True)
        (job / "timing").mkdir(exist_ok=True)
        jobs.write(job / media.timing_path(kind), timing)
    for video in media.VIDEOS:
        jobs.write(job / media.timeline_path(video), timeline(video, the_bundle))
    return job


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m studio.tests.sample_media", description=__doc__.split("\n\n")[1])
    parser.add_argument("job", type=Path)
    parser.add_argument("--art", action="store_true", help="also draw the art and the masks")
    parser.add_argument("--audio", action="store_true", help="also make tones for the audio files (ffmpeg)")
    args = parser.parse_args()
    print(write_job(args.job, art=args.art, audio=args.audio))
