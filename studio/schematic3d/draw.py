"""Draw a textbook-style 3D schematic: real 3D positions, projected and painted back to front.

    python -m studio.schematic3d.draw RECIPE SHAPES OUT_DIR [--fps 24] [--seconds 7.2] [--still T]

The camera is orthographic and looks along +y, a little down, so a capsule is drawn as a rounded
bar, a sphere as a circle and a tube as a thick curve. Every frame turns the recipe's spinning
parts about the axle (the z axis) in 3D, projects everything and paints the shapes far to near,
shaded like the book's: an outline, a darker side, a soft lit side and a shine. The compartments
and the membrane never move, so they are drawn once. Labels and the lyric band are popsmosis's
(studio/render/primitives.py).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import skia

from studio.render import primitives as p
from studio.render.layouts import H as LAYOUT

INK = "#4E4887"  # the book's outline: a dark lavender
LIGHT = np.array([-0.55, -0.83])  # on screen, from the upper left
OUTLINE = 2.6  # px


def catmull(path, per: int = 5) -> np.ndarray:
    """A smooth curve through the points (Catmull-Rom), `per` points a span."""
    pts = [np.array(q, float) for q in path]
    if len(pts) < 3:
        return np.array(pts)
    pts = [2 * pts[0] - pts[1]] + pts + [2 * pts[-1] - pts[-2]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1:i + 3]
        for t in np.linspace(0, 1, per, endpoint=False):
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(pts[-2])
    return np.array(out)


def turned(points: np.ndarray, angle: float) -> np.ndarray:
    """The points turned about the z axis (the axle) by `angle` radians, counterclockwise seen from above."""
    c, s = math.cos(angle), math.sin(angle)
    return points @ np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])


def spin_angle(t: float, turn_s: float, start_s: float, ramp_s: float) -> float:
    """How far the spinning parts have turned at t: still until start_s, up to speed over ramp_s."""
    t -= start_s
    if t <= 0:
        return 0.0
    speed = 2 * math.pi / turn_s
    return speed * (t * t / (2 * ramp_s) if t < ramp_s else t - ramp_s / 2)


def mix(hex_color: str, toward: tuple[int, int, int], amount: float, alpha: float = 1.0) -> int:
    r, g, b = p.rgb(hex_color)
    return skia.ColorSetARGB(round(255 * alpha), *(round(c + (t - c) * amount) for c, t in zip((r, g, b), toward)))


def stroke(width: float, color: int, blur: float = 0.0) -> skia.Paint:
    paint = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=width,
                       StrokeCap=skia.Paint.kRound_Cap, StrokeJoin=skia.Paint.kRound_Join, Color=color)
    if blur:
        paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    return paint


def polyline(xs, ys) -> skia.Path:
    path = skia.Path()
    path.moveTo(float(xs[0]), float(ys[0]))
    for x, y in zip(xs[1:], ys[1:]):
        path.lineTo(float(x), float(y))
    return path


def body(canvas: skia.Canvas, path: skia.Path, r: float, color: str) -> None:
    """A rounded shape along the path, shaded like the book's: a darker side, a soft lit side and a shine."""
    canvas.drawPath(path, stroke(2 * r + 2 * OUTLINE, p.argb(INK)))
    shape = skia.Path()
    stroke(2 * r, 0).getFillPath(path, shape)
    canvas.save()
    canvas.clipPath(shape, doAntiAlias=True)
    canvas.drawPath(path, stroke(2 * r, mix(color, (40, 34, 90), 0.28)))
    for offset, width, paint_color, blur in ((0.22, 1.55, mix(color, (0, 0, 0), 0.0), 0.32),
                                             (0.45, 0.95, mix(color, (255, 255, 255), 0.38), 0.32),
                                             (0.62, 0.32, mix(color, (255, 255, 255), 0.85, 0.75), 0.22)):
        canvas.save()
        canvas.translate(LIGHT[0] * offset * r, LIGHT[1] * offset * r)
        canvas.drawPath(path, stroke(width * r, paint_color, blur * r))
        canvas.restore()
    canvas.restore()


def ball(canvas: skia.Canvas, x: float, y: float, r: float, light: str, base: str, dark: str, edge: str | None) -> None:
    shader = skia.GradientShader.MakeRadial(skia.Point(x + LIGHT[0] * 0.45 * r, y + LIGHT[1] * 0.45 * r), 1.45 * r,
                                            [p.argb(light), p.argb(base), p.argb(dark)], [0.0, 0.5, 1.0])
    canvas.drawCircle(x, y, r, skia.Paint(AntiAlias=True, Shader=shader))
    if edge:
        canvas.drawCircle(x, y, r, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=OUTLINE * 0.8,
                                              Color=p.argb(edge)))


def arc(radius: float, z: float, from_deg: float, to_deg: float, n: int = 28) -> np.ndarray:
    a = np.radians(np.linspace(from_deg, to_deg, n))
    return np.stack([radius * np.cos(a), radius * np.sin(a), np.full(n, z)], axis=1)


class Scene:
    """One recipe's fitted shapes, ready to draw at any time t. A frame depends only on t."""

    def __init__(self, recipe: dict, fitted: dict, width: int = LAYOUT.width, height: int = LAYOUT.height):
        self.recipe, self.fitted = recipe, fitted
        self.W, self.H = width, height
        self.band_top = height - round(LAYOUT.band_h * height / LAYOUT.height)
        cam = recipe["camera"]
        elev = math.radians(cam["elevation_deg"])
        self.right = np.array([1.0, 0, 0])
        self.up = np.array([0, math.sin(elev), math.cos(elev)])
        self.away = np.array([0, math.cos(elev), -math.sin(elev)])  # depth: larger is farther
        span = (cam["top"] - cam["bottom"]) / 0.86  # the lyric band takes the bottom tenth
        self.scale = height / span  # pixels per nm
        self.target = np.array([cam["centre_x"], 0, cam["top"] - span / 2])
        parts = recipe["parts"]
        spinning = set(recipe.get("spin", {}).get("parts", []))
        self.shapes = []
        for s in fitted["shapes"]:
            pts = catmull(s["path"]) if s["kind"] == "tube" else np.array([s["a"], s["b"]], float)
            self.shapes.append({"points": pts, "r": s["radius"], "part": s["part"], "chain": s["chain"],
                                "color": parts[s["part"]]["color"], "turns": s["part"] in spinning,
                                "inside": parts[s["part"]].get("inside")})
        self.arrows = [catmull(recipe["flow"]["path"])] + [arc(**a) for a in recipe.get("arcs", [])]
        self.ions = np.array(recipe["ions"]["at"], float)
        self.labels = self._label_points()
        self.lines = self._lines()
        self._page: skia.Image | None = None
        self._ion_font = p.font("Bold", round(26 * height / 1080))
        self._title = p.font("SemiBold", round(34 * height / 1080))

    # --- geometry ---------------------------------------------------------------------------------

    def project(self, points) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Screen x, y (px) and depth of 3D points (nm)."""
        pts = np.atleast_2d(np.asarray(points, float)) - self.target
        return self.W / 2 + pts @ self.right * self.scale, self.H / 2 - pts @ self.up * self.scale, pts @ self.away

    def angle(self, t: float) -> float:
        spin = self.recipe.get("spin")
        if not spin:
            return 0.0
        a = spin_angle(t, spin["turn_s"], spin["start_s"], spin["ramp_s"])
        return -a if spin.get("clockwise_from_above") else a

    def _label_points(self) -> dict[str, np.ndarray]:
        out = {}
        for name, where in self.recipe.get("labels", {}).items():
            if name == "color":
                continue
            if "at" in where:
                point = np.array(where["at"], float)
            elif "chain" in where:
                shape = next(s for s in self.fitted["shapes"] if s["chain"] == where["chain"])
                if shape["kind"] == "tube":
                    path = np.array(shape["path"])
                    point = path[int(len(path) * where.get("along", 0.5))]
                else:
                    point = (np.array(shape["a"]) + np.array(shape["b"])) / 2
            else:
                point = np.mean([(np.array(s["a"]) + np.array(s["b"])) / 2 for s in self.fitted["shapes"]
                                 if s["part"] == where["part"] and s["kind"] == "capsule"], axis=0)
            out[name] = point + np.array(where.get("offset", [0, 0, 0]), float)
        return out

    def _lines(self) -> list[tuple[float, float, list[p.BandWord]]]:
        color = self.recipe.get("labels", {}).get("color", "#6C63C9")
        lines, self.reveals = [], {}
        for line in self.recipe.get("demo_lines", []):
            words = line["text"].split()
            each = (line["to"] - line["from"] - 0.35) / len(words)
            timed = [p.BandWord(text=w, start=line["from"] + 0.15 + i * each, end=line["from"] + 0.15 + (i + 1) * each)
                     for i, w in enumerate(words)]
            for bw in timed:
                if bw.text in line.get("reveals", {}):
                    self.reveals[line["reveals"][bw.text]] = bw.start
                    bw.color = color
            lines.append((line["from"], line["to"], timed))
        return lines

    # --- drawing ----------------------------------------------------------------------------------

    def page(self) -> skia.Image:
        """The part that never moves: the compartments and the membrane, cut away around the machine."""
        if self._page is not None:
            return self._page
        surface = skia.Surface(self.W, self.H)
        canvas = surface.getCanvas()
        page, mem = self.recipe["page"], self.recipe["membrane"]
        _, split, _ = self.project([(0, 3.0, 0)])
        canvas.drawRect(skia.Rect.MakeLTRB(0, 0, self.W, float(split[0])), skia.Paint(Color=p.argb(page["above"]["color"])))
        canvas.drawRect(skia.Rect.MakeLTRB(0, float(split[0]), self.W, self.H), skia.Paint(Color=p.argb(page["below"]["color"])))
        foot = np.array(self.fitted["membrane_footprint"])
        rng = np.random.default_rng(3)
        tail = stroke(2 * 0.085 * self.scale, p.argb("#DDB240"))
        z, spacing = mem["head_z"], mem["spacing"]
        for row in range(mem["rows"] - 1, -1, -1):  # far rows first
            y = 0.4 + row * spacing * 0.87
            heads = []
            for col in range(-mem["half_width"], mem["half_width"] + 1):
                x = col * spacing + (0.5 * spacing if row % 2 else 0)
                if len(foot) and np.min(np.hypot(foot[:, 0] - x, foot[:, 1] - y)) < mem["cut"]:
                    continue
                for side in (1, -1):
                    for dx in (-0.16, 0.16):
                        phase = rng.uniform(0, 2 * math.pi)
                        wavy = [(x + dx + 0.07 * math.sin(phase + k * 1.7), y, side * (z - 0.35 - 0.36 * k)) for k in range(6)]
                        xs, ys, _ = self.project(wavy)
                        canvas.drawPath(polyline(xs, ys), tail)
                    heads.append((x, y, side * z))
            if heads:
                xs, ys, _ = self.project(heads)
                for hx, hy in zip(xs, ys):
                    ball(canvas, hx, hy, mem["head_radius"] * self.scale, "#E9EBEE", "#B9BDC3", "#8C9199", "#9A9EA6")
        self._page = surface.makeImageSnapshot()
        return self._page

    def paint_order(self, t: float) -> list[tuple[float, np.ndarray, np.ndarray, dict]]:
        """Every shape at time t, far to near. A part `inside` another goes behind all of it, as the
        book draws a rod inside a knob, rather than between its lobes."""
        angle = self.angle(t)
        items = []
        for shape in self.shapes:
            pts = turned(shape["points"], angle) if shape["turns"] else shape["points"]
            xs, ys, depth = self.project(pts)
            items.append([float(depth.mean()), xs, ys, shape])
        farthest = {}
        for depth, _, _, shape in items:
            farthest[shape["part"]] = max(farthest.get(shape["part"], -1e9), depth)
        for item in items:
            inside = item[3]["inside"]
            if inside in farthest:
                item[0] = max(item[0], farthest[inside] + 0.01)
        return [tuple(item) for item in sorted(items, key=lambda item: -item[0])]

    def flowing(self, t: float) -> np.ndarray:
        """Where the ions flowing down the path are at t: one sets off every `every_s` once the parts turn."""
        flow = self.recipe["flow"]
        start = self.recipe.get("spin", {}).get("start_s", 0.0)
        path = self.arrows[0]
        lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
        out = []
        for k in range(4):
            u = (t - start) / flow["every_s"] - k
            if t > start and 0 <= u <= 1:
                at = np.interp(u * lengths[-1], lengths, np.arange(len(path)))
                i0 = min(int(at), len(path) - 2)
                out.append(path[i0] + (path[i0 + 1] - path[i0]) * (at - i0) + np.array(flow.get("front", [0, 0, 0])))
        return np.array(out).reshape(-1, 3)

    def draw(self, canvas: skia.Canvas, t: float, text: bool = True) -> None:
        canvas.drawImage(self.page(), 0, 0)
        for _, xs, ys, shape in self.paint_order(t):
            body(canvas, polyline(xs, ys), shape["r"] * self.scale, shape["color"])
        red = p.argb("#D9302A")
        for arrow in self.arrows:
            xs, ys, _ = self.project(arrow)
            canvas.drawPath(polyline(xs, ys), stroke(0.3 * self.scale, red))
            dx, dy = xs[-1] - xs[-2], ys[-1] - ys[-2]
            n = math.hypot(dx, dy) or 1
            dx, dy = dx / n, dy / n
            tip = (xs[-1] + dx * 0.55 * self.scale, ys[-1] + dy * 0.55 * self.scale)
            size = 0.75 * self.scale
            head = skia.Path()
            head.moveTo(*tip)
            head.lineTo(tip[0] - dx * size - dy * size * 0.55, tip[1] - dy * size + dx * size * 0.55)
            head.lineTo(tip[0] - dx * size + dy * size * 0.55, tip[1] - dy * size - dx * size * 0.55)
            head.close()
            canvas.drawPath(head, skia.Paint(AntiAlias=True, Color=red))
        ion = self.recipe["ions"]
        xs, ys, _ = self.project(np.vstack([self.ions, self.flowing(t)]))
        for x, y in zip(xs, ys):
            ball(canvas, x, y, ion["radius"] * self.scale, "#F2F3F5", "#C9CCD1", "#9EA3AA", INK)
            tw = p.text_width(self._ion_font, ion["label"])
            p.draw_text(canvas, ion["label"], x - tw / 2, y + p.cap_height(self._ion_font) / 2, self._ion_font,
                        p.argb("#3D4046"))
        if text:
            self.draw_text(canvas, t)

    def draw_text(self, canvas: skia.Canvas, t: float) -> None:
        page = self.recipe["page"]
        p.draw_text(canvas, page["above"]["name"], 40, 40 + p.cap_height(self._title), self._title, p.argb("#3B2A17", 0.85))
        p.draw_text(canvas, page["below"]["name"], 40, self.band_top - 32, self._title, p.argb("#3B3317", 0.85))
        color = self.recipe.get("labels", {}).get("color", "#6C63C9")
        labels = []
        for name, at in self.reveals.items():
            if t >= at and name in self.labels:
                xs, ys, _ = self.project([self.labels[name]])
                x, y = float(xs[0]), float(ys[0])
                labels.append(p.Label(name=name, color=color, anchor=(x, y), box=(x - 30, y - 30, x + 30, y + 30),
                                      alpha=p.ease((t - at) / 0.35)))
        _, membrane, _ = self.project([(0, 0, 0)])
        ions = list(zip(*self.project(self.ions)[:2]))
        p.place_labels(labels, (LAYOUT.margin, LAYOUT.margin + 60, self.W - LAYOUT.margin, self.band_top - LAYOUT.margin),
                       (self.W / 2, float(membrane[0]) + 120), LAYOUT.label_font, avoid=ions)
        for lb in labels:
            p.draw_label(canvas, lb, LAYOUT.label_font)
        if self.lines:
            line = next((ws for t0, t1, ws in self.lines if t0 <= t < t1), self.lines[-1][2])
            p.draw_band(canvas, (0, self.band_top, self.W, self.H), line, t, LAYOUT.band_font)

    def image(self, t: float, text: bool = True) -> skia.Image:
        surface = skia.Surface(self.W, self.H)
        self.draw(surface.getCanvas(), t, text)
        return surface.makeImageSnapshot()


def render_video(scene: Scene, out: Path, fps: int, seconds: float) -> float:
    """Encode the scene from 0 to `seconds`; returns the average drawing time per frame, in seconds."""
    ffmpeg = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                               "-s", f"{scene.W}x{scene.H}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
                               "-pix_fmt", "yuv420p", "-crf", "18", str(out)], stdin=subprocess.PIPE)
    surface = skia.Surface(scene.W, scene.H)
    frames = int(seconds * fps)
    drawing = 0.0
    for i in range(frames):
        t0 = time.time()
        scene.draw(surface.getCanvas(), i / fps)
        pixels = surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)
        drawing += time.time() - t0
        ffmpeg.stdin.write(pixels.tobytes())
    ffmpeg.stdin.close()
    if ffmpeg.wait():
        raise RuntimeError(f"ffmpeg could not encode {out}")
    return drawing / max(frames, 1)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) < 3:
        sys.exit(__doc__.split("\n\n")[1])
    recipe, fitted, out = json.loads(Path(argv[0]).read_text()), json.loads(Path(argv[1]).read_text()), Path(argv[2])
    opt = {argv[i]: argv[i + 1] for i in range(3, len(argv) - 1) if argv[i].startswith("--")}
    out.mkdir(parents=True, exist_ok=True)
    scene = Scene(recipe, fitted)
    started = time.time()
    scene.page()
    if "--still" in opt:
        t = float(opt["--still"])
        scene.image(t).save(str(out / f"{recipe['name']}_{t:.2f}s.png"), skia.kPNG)
        print(f"still at {t}s -> {out}")
    else:
        fps, seconds = int(opt.get("--fps", 24)), float(opt.get("--seconds", 7.2))
        video = out / f"{recipe['name']}_{fps}fps.mp4"
        per_frame = render_video(scene, video, fps, seconds)
        print(f"{int(seconds * fps)} frames, {per_frame * 1000:.0f} ms a frame, {time.time() - started:.1f}s in all -> {video}")
