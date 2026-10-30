"""A media job for tests and for building steps 3-9 before the steps before them exist.

    python -m studio.tests.sample_media jobs/sample [--art] [--audio] [--graph] [--dark]

writes into jobs/sample/ a request and bundle (the light reactions, from sample.py),
locations.json, the two timings and both timelines; with --art, the art and the
masks (a flat ellipse per structure, no extra libraries); with --audio, tones the
length of each audio file (needs ffmpeg). Everything in it validates.

The timelines are written by hand for the renderer: they reveal, spotlight and move
the camera more than the sample bundle's direction asks for, so they are not what the
timeline step should make from that bundle.

With --graph it is a graph job instead (supply and demand: graph_bundle()), its curves straight
bands, its axes plain lines that are no structure, and its timelines made by the timeline step
itself; --dark paints it on dark slate blue, as the painted art is, with white axes.
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import zlib
from pathlib import Path

import numpy as np

from studio import jobs, media
from studio.schemas import (AudioClip, Beat, Box, Bundle, Location, Locations, Particles,
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


def _band_rows(x0: float, y0: float, x1: float, y1: float, width: float) -> list[tuple[int, int] | None]:
    """A straight band (a graph's line), row by row: the columns within width / 2 of the segment, which a convex
    shape keeps in one run a row."""
    ys, xs = np.mgrid[0:media.ART_HEIGHT, 0:media.ART_WIDTH] + 0.5
    dx, dy = x1 - x0, y1 - y0
    u = np.clip(((xs - x0) * dx + (ys - y0) * dy) / (dx * dx + dy * dy), 0, 1)
    inside = np.hypot(xs - x0 - u * dx, ys - y0 - u * dy) <= width / 2
    rows = []
    for row in inside:
        cols = np.flatnonzero(row)
        rows.append((int(cols[0]), int(cols[-1]) + 1) if len(cols) else None)
    return rows


def _spans(shapes: dict) -> dict[str, list[tuple[int, int] | None]]:
    """Per structure of a scene's shapes, per row of the art, the columns where it is visible (later shapes cover
    earlier ones)."""
    spans: dict[str, list[tuple[int, int] | None]] = {}
    for name, shape in shapes.items():
        if len(shape) == 5:  # a band: x0, y0, x1, y1, width
            spans[name] = _band_rows(*shape)
            continue
        cx, cy, rx, ry = shape
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


def _visible(shapes: dict) -> dict[str, list[bytearray]]:
    """Each structure's mask, row by row: 255 where it shows in the art."""
    spans, names = _spans(shapes), list(shapes)
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


def locations(shapes: dict = SHAPES) -> Locations:
    scenes = []
    for scene in shapes:
        visible = _visible(shapes[scene])
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


def write_art(job: Path, the_bundle: Bundle, shapes: dict = SHAPES, lines: dict | None = None,
              paper: bytes = b"\xff\xff\xff", ink: bytes = b"\x3a\x3a\x3a") -> None:
    """Each scene's art and masks: its structures in their colours on `paper`, over the lines it draws that are no
    structure (a graph's axes, by scene) in `ink`."""
    colors = {s.name: bytes.fromhex(s.color_hex[1:]) for scene in the_bundle.scenes for s in scene.structures}
    for scene in shapes:
        visible = _visible(shapes[scene])
        art = [bytearray(paper * media.ART_WIDTH) for _ in range(media.ART_HEIGHT)]
        for line in (lines or {}).get(scene, []):
            for y, run in enumerate(_band_rows(*line)):
                if run:
                    art[y][run[0] * 3:run[1] * 3] = ink * (run[1] - run[0])
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
    }


# --- timelines ----------------------------------------------------------------------------

# By line: the scene, what it reveals (the word heard), the spotlight, the camera's target and zoom.
SONG_DIRECTION = {
    "v1-1": ("hook", [("Thylakoid", "thylakoid")], None, None, 1.0),
    "v1-2": ("overview", [("Thylakoid", "Each")], None, None, 1.0),
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

    return Timeline(video=video, duration=voice.duration, fps=media.FPS,
                    audio=[AudioClip(path=voice.audio, at=0)], palette=palette, beats=beats)


# --- a graph ------------------------------------------------------------------------------

# Supply and demand as an economics textbook draws it: two straight curves crossing, on axes that are plain lines.
GRAPH_SHAPES = {
    "hook": {"Market stall": (896, 540, 360, 240)},
    "overview": {"Supply curve": (380, 820, 1400, 230, 16), "Demand curve": (380, 230, 1400, 820, 16),
                 "Equilibrium": (890, 525, 30, 30)},
    # The same graph with a price ceiling: a long, thin line across it, but a structure, never an axis.
    "ceiling": {"Supply curve": (380, 820, 1400, 230, 16), "Demand curve": (380, 230, 1400, 820, 16),
                "Price ceiling": (300, 690, 1500, 690, 10)},
}
AXES = {scene: [(300, 880, 1500, 880, 6), (300, 140, 300, 880, 6)] for scene in ("overview", "ceiling")}
DARK_PAPER, WHITE_INK = b"\x28\x37\x4b", b"\xee\xf2\xf6"  # the painted art's dark slate blue, and its axes

GRAPH_LYRICS = [  # id, section, text, names, scene, camera target, zoom
    ("v1-1", "verse", "At the market every price is a tug of war", ["market"], "hook", "", 1.0),
    ("v1-2", "verse", "Buyers pull it down while sellers pull it up", [], "overview", "", 1.0),
    ("c1-1", "chorus", "Supply meets demand and the price is set", ["supply", "demand"], "overview", "", 1.0),
    ("v2-1", "verse", "Where the two lines cross is the equilibrium", ["equilibrium"], "overview", "Equilibrium", 2.0),
    ("c2-1", "chorus", "Supply meets demand and the price is set", ["supply", "demand"], "overview", "", 1.0),
    ("b-1", "bridge", "Cap it with a ceiling and the shelves run bare", ["ceiling"], "ceiling", "", 1.0),
    ("c3-1", "chorus", "Supply meets demand and the price is set", ["supply", "demand"], "ceiling", "", 1.0),
]
GRAPH_NARRATION = [
    ("n1", "At a market the price moves until buyers and sellers agree.", ["market"], "hook", "", 1.0),
    ("n2", "The demand curve slopes down and the supply curve slopes up.", ["demand curve", "supply curve"],
     "overview", "", 1.0),
    ("n3", "They cross at the equilibrium, where the price is set.", ["equilibrium"], "overview", "Equilibrium", 2.2),
    ("n4", "A price ceiling below the equilibrium leaves a shortage.", ["price ceiling", "equilibrium"], "ceiling", "",
     1.0),
]


def graph_bundle() -> Bundle:
    """A droplet on supply and demand whose graph scenes name their axes, for the axis names (Scene.axes)."""
    def structure(name: str, color: str, *aliases: str) -> dict:
        return {"name": name, "aliases": list(aliases), "sam_phrase": f"{name.lower()}", "color_hex": color}

    supply, demand = structure("Supply curve", "#F28C28", "supply"), structure("Demand curve", "#4CC9F0", "demand")
    axes = {"x": "Quantity", "y": "Price"}
    facts = [{"id": "f1", "text": "Demand slopes down and supply slopes up.", "source_quote": ""},
             {"id": "f2", "text": "The price settles where supply meets demand, the equilibrium.", "source_quote": ""},
             {"id": "f3", "text": "A price ceiling below the equilibrium makes a shortage.", "source_quote": ""}]
    lines = [*GRAPH_LYRICS, *[(i, "", t, n, sc, tg, z) for i, t, n, sc, tg, z in GRAPH_NARRATION]]
    return Bundle.model_validate({
        "course": "AP Macroeconomics", "topic": "How supply and demand set a price", "audience": "high school",
        "core_idea": "The price settles where supply meets demand.", "facts": facts,
        "misconception": "A price is not set by sellers alone.",
        "scenes": [
            {"id": "hook", "image_prompt": "A market stall.", "structures": [structure("Market stall", "#90BE6D", "market")]},
            {"id": "overview", "image_prompt": "A supply and demand graph.",
             "structures": [supply, demand, structure("Equilibrium", "#FFD166")], "axes": axes},
            {"id": "ceiling", "image_prompt": "The graph with a price ceiling.",
             "structures": [supply, demand, structure("Price ceiling", "#FF6B6B", "ceiling")], "axes": axes},
        ],
        "lesson_markdown": "## Supply and demand\n\nThe price settles where supply meets demand.\n",
        "music": {"positive_styles": ["upbeat pop, 112 bpm"], "negative_styles": [], "sections": []},
        "lyrics": [{"id": i, "section": s, "text": t, "names": n, "fact_id": "f2"} for i, s, t, n, *_ in GRAPH_LYRICS],
        "narration": [{"id": i, "text": t, "names": n, "fact_id": "f2"} for i, t, n, *_ in GRAPH_NARRATION],
        "direction": [{"line_id": i, "scene": sc, "camera": {"target": tg, "zoom": z}, "spotlight": "",
                       "particles": [], "carries": "", "glow": "", "caption": ""} for i, _, _, _, sc, tg, z in lines],
    })


def write_graph_job(job: Path, audio: bool = False, dark: bool = False) -> Path:
    """The graph job, with its art, masks and timings; its timelines (the hook cut's too) from the timeline step."""
    from studio.steps import timeline as timeline_step

    job.mkdir(parents=True, exist_ok=True)
    the_bundle = graph_bundle()
    jobs.write(job / "request.json", Request(topic=the_bundle.topic, course=the_bundle.course))
    jobs.write(job / "bundle.json", the_bundle)
    write_art(job, the_bundle, GRAPH_SHAPES, AXES, *((DARK_PAPER, WHITE_INK) if dark else ()))
    jobs.write(job / media.LOCATIONS, locations(GRAPH_SHAPES))
    _write_timings(job, timings(the_bundle), audio)
    timeline_step.run(job)
    return job


# --- the job ------------------------------------------------------------------------------


def _write_timings(job: Path, timed: dict[str, Timing], audio: bool) -> None:
    for kind, timing in timed.items():
        if audio:
            path = job / timing.audio
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"sine=frequency=440:duration={timing.duration}", "-q:a", "9", str(path)], check=True)
        (job / "timing").mkdir(exist_ok=True)
        jobs.write(job / media.timing_path(kind), timing)


def write_job(job: Path, art: bool = False, audio: bool = False) -> Path:
    job.mkdir(parents=True, exist_ok=True)
    the_bundle = bundle()
    jobs.write(job / "request.json", Request(topic=the_bundle.topic, course=the_bundle.course))
    jobs.write(job / "bundle.json", the_bundle)
    if art:
        write_art(job, the_bundle)
    jobs.write(job / media.LOCATIONS, locations())
    _write_timings(job, timings(the_bundle), audio)
    for video in media.VIDEOS:
        jobs.write(job / media.timeline_path(video), timeline(video, the_bundle))
    return job


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m studio.tests.sample_media", description=__doc__.split("\n\n")[1])
    parser.add_argument("job", type=Path)
    parser.add_argument("--art", action="store_true", help="also draw the art and the masks")
    parser.add_argument("--audio", action="store_true", help="also make tones for the audio files (ffmpeg)")
    parser.add_argument("--graph", action="store_true", help="a graph job instead, with its art and timelines")
    parser.add_argument("--dark", action="store_true", help="the graph on dark slate blue, as painted")
    args = parser.parse_args()
    print(write_graph_job(args.job, audio=args.audio, dark=args.dark) if args.graph
          else write_job(args.job, art=args.art, audio=args.audio))
