"""Stills and contact sheets from a job's timeline, for looking at the renderer's output.

    python -m studio.render still jobs/sample song 0 4.5 12      # one PNG per time
    python -m studio.render sheet jobs/sample song --every 5     # every 5 s, tiled into one PNG

Written to <job>/out/stills/ unless --out says otherwise.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import skia

from studio import jobs, media
from studio.render.primitives import font
from studio.render.renderer import Renderer
from studio.schemas import Timeline


def stills(job: Path, video: str, times: list[float], out: Path) -> list[Path]:
    renderer = Renderer(job, jobs.read(job / media.timeline_path(video), Timeline))
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in times:
        path = out / f"{video}_{t:07.2f}.png"
        renderer.image(t).save(str(path), skia.kPNG)
        paths.append(path)
    return paths


def sheet(job: Path, video: str, every: float, out: Path, columns: int = 4, width: int = 480) -> Path:
    """Stills every `every` seconds, tiled with their times, into one PNG."""
    timeline = jobs.read(job / media.timeline_path(video), Timeline)
    renderer = Renderer(job, timeline)
    times = [i * every for i in range(math.ceil(timeline.duration / every))]
    height = round(width * renderer.layout.height / renderer.layout.width)
    rows = math.ceil(len(times) / columns)
    surface = skia.Surface(columns * width, rows * (height + 28))
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorWHITE)
    f = font("SemiBold", 18)
    for i, t in enumerate(times):
        x, y = (i % columns) * width, (i // columns) * (height + 28)
        canvas.drawImageRect(renderer.image(t), skia.Rect.MakeXYWH(x, y, width, height),
                             skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear))
        canvas.drawString(f"{t:.1f} s", x + 8, y + height + 20, f, skia.Paint(Color=skia.ColorBLACK))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{video}_sheet.png"
    surface.makeImageSnapshot().save(str(path), skia.kPNG)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m studio.render", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("still", "sheet"):
        command = commands.add_parser(name)
        command.add_argument("job", type=Path)
        command.add_argument("video", choices=media.VIDEOS)
        command.add_argument("--out", type=Path)
        if name == "still":
            command.add_argument("times", type=float, nargs="+")
        else:
            command.add_argument("--every", type=float, default=5.0)
    args = parser.parse_args()
    out = args.out or args.job / "out" / "stills"
    if args.command == "still":
        for path in stills(args.job, args.video, args.times, out):
            print(path)
    else:
        print(sheet(args.job, args.video, args.every, out))


if __name__ == "__main__":
    main()
