"""How fast the renderer is: a 3-minute song from the sample job, timed end to end.

    python -m studio.render.bench jobs/bench [--workers N]

Builds a job like sample_media's, with 42 lyric lines (about 3 minutes at 4.3 s a
line), direction that reveals, spotlights and moves the camera, and shaded art
(gradients and detail encode slower than flat colour). Runs the timeline step on it,
then renders the song video and prints how long that took. The target is a 3-minute
video in under 5 minutes (docs/PLAN.md, section 7).
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

import skia

from studio import checks, jobs, media
from studio.render.encode import encode, workers
from studio.render.primitives import rgb
from studio.schemas import SECTION_ORDER, Bundle, Timeline
from studio.steps import timeline as timeline_step
from studio.tests import sample_media

LINES_PER_SECTION = 7
TEXTS = {  # (text, scene, spotlight and camera target)
    "verse": [("Light lands on the thylakoid", "hook", "Thylakoid"), ("The thylakoid holds the chain", "overview", ""),
              ("Electrons hop along the line", "overview", "Thylakoid"),
              ("Cytochrome b6f takes its turn", "overview", "Cytochrome b6f"),
              ("Only b6f pumps them in", "overview", "Cytochrome b6f"),
              ("Protons crowd the inner space", "overview", ""), ("Pressure builds across the wall", "overview", "")],
    "chorus": [("Protons flow and ATP grows", "overview", "")],
    "pre-chorus": [("Only b6f pumps them in", "overview", "Cytochrome b6f")],
    "bridge": [("Down through ATP synthase", "synthase", "ATP synthase"),
               ("The synthase spins as protons pass", "synthase", "ATP synthase")],
}
TEXTS["intro"] = TEXTS["verse"][:2]
TEXTS["outro"] = TEXTS["chorus"]


def bench_bundle() -> Bundle:
    data = sample_media.bundle().model_dump()
    data["scenes"][1]["structures"][1]["aliases"] = ["b6f"]
    data["scenes"][2]["structures"][0]["aliases"] = ["synthase"]
    canon = checks.canonical_names(data["scenes"])
    lyrics, direction = [], []
    for s, section in enumerate(SECTION_ORDER):
        for j in range(LINES_PER_SECTION):
            texts = TEXTS[section]
            text, scene, focus = texts[j % len(texts)]
            if section == "verse" and s > 0 and scene == "hook":
                scene = "overview"
            line_id = f"s{s}-{j}"
            lyrics.append({"id": line_id, "section": section, "text": text,
                           "names": list(checks.said(text, canon).values()), "fact_id": "filler"})
            direction.append({"line_id": line_id, "scene": scene, "camera": {"target": focus, "zoom": 1.8},
                              "spotlight": focus, "particles": [],
                              "caption": "Only b6f pumps" if j == 4 else ""})
    data["lyrics"] = lyrics
    data["direction"] = direction + [d for d in data["direction"] if d["line_id"].startswith("n")]
    return Bundle.model_validate(data)


def shade_art(job: Path, bundle: Bundle) -> None:
    """Redraw each scene's ellipses with gradients, outlines and some inner detail."""
    colors = {s.name: s.color_hex for scene in bundle.scenes for s in scene.structures}
    for scene, shapes in sample_media.SHAPES.items():
        surface = skia.Surface(media.ART_WIDTH, media.ART_HEIGHT)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorWHITE)
        for name, (cx, cy, rx, ry) in shapes.items():
            r, g, b = rgb(colors[name])
            light = skia.Color(min(255, r + 90), min(255, g + 90), min(255, b + 90))
            dark = skia.Color(r * 6 // 10, g * 6 // 10, b * 6 // 10)
            oval = skia.Rect.MakeLTRB(cx - rx, cy - ry, cx + rx, cy + ry)
            shader = skia.GradientShader.MakeRadial((cx - rx * 0.3, cy - ry * 0.4), max(rx, ry) * 1.3,
                                                    [light, skia.Color(r, g, b), dark])
            canvas.drawOval(oval, skia.Paint(AntiAlias=True, Shader=shader))
            for k in range(24):  # membrane-like detail inside each shape
                x = cx + rx * 0.8 * ((k * 37) % 23 / 11.5 - 1)
                y = cy + ry * 0.6 * ((k * 53) % 19 / 9.5 - 1)
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 < 0.7:
                    canvas.drawCircle(x, y, 10 + k % 7 * 3, skia.Paint(AntiAlias=True, Color=skia.ColorSetA(dark, 90)))
            canvas.drawOval(oval, skia.Paint(AntiAlias=True, Color=dark, Style=skia.Paint.kStroke_Style, StrokeWidth=4))
        surface.makeImageSnapshot().save(str(job / media.art_path(scene)), skia.kPNG)


def make_job(job: Path) -> Bundle:
    sample_media.write_job(job, art=True, audio=False)
    bundle = bench_bundle()
    jobs.write(job / "bundle.json", bundle)
    timed = sample_media.timings(bundle)
    for kind, timing in timed.items():
        jobs.write(job / media.timing_path(kind), timing)
        (job / timing.audio).parent.mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={timing.duration}", "-q:a", "9", str(job / timing.audio)],
                       check=True)
    shade_art(job, bundle)
    print(timeline_step.run(job))
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m studio.render.bench", description=__doc__.split("\n\n")[0])
    parser.add_argument("job", type=Path)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()
    make_job(args.job)
    timeline = jobs.read(args.job / media.timeline_path("song"), Timeline)
    started = time.monotonic()
    rendered = encode(args.job, timeline, args.job / media.video_path("song"), parts=args.workers)
    seconds = time.monotonic() - started
    print(f"song: {timeline.duration:.1f} s of video, {rendered.frames} frames, rendered in {seconds:.1f} s "
          f"({rendered.frames / seconds:.1f} frames/s, {args.workers or workers()} workers)")


if __name__ == "__main__":
    main()
