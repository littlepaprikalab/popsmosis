"""Draw a machine from its recipe and fitted shapes: real 3D positions, projected and painted far to near.

    python -m studio.schematic3d.draw NAME OUT_DIR [--still T] [--seconds 7.2] [--fps 24] [--motion rotor=-1 ...]

A Machine draws in the art's pixels (media.ART_WIDTH x ART_HEIGHT), which are the world the
renderer's camera moves over, so the renderer draws it under its camera on every frame and
the art step draws its still and its masks from it (studio/steps/art.py). Nothing here
depends on the machine: everything about it comes from its recipe (library.py).

The camera is orthographic and looks along +y, a little down, so a capsule is drawn as a
rounded bar, a sphere as a circle and a tube as a thick curve. At time t the parts a spin
turns are turned about the axle (the z axis) in 3D, everything is projected and the shapes
are painted far to near, shaded like the book's: an outline, a darker side, a soft lit side
and a shine. A part `inside` another (the rod inside the knob) goes behind all of it. The
compartments are flat colour and the membrane, which never moves, is drawn once at each
scale it is needed and kept. A frame depends only on t.

Each structure (a group of parts the scene reveals and labels) is drawn washed toward white
by its own amount, for the renderer's ghosts and spotlight; the rest (the page, the arrows,
the particles) by one amount of its own.

The motions are the ones the fact-check supported, each with its way (+1 or -1, see
library.resolve); a motion not given is not drawn at all, arrows included.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import skia

from studio import media
from studio.render import primitives as p
from studio.schematic3d.library import Flow, Recipe, Spin, load

INK = "#4E4887"  # the book's outline: a dark lavender
LIGHT = np.array([-0.55, -0.83])  # on screen, from the upper left
OUTLINE_NM = 0.095
RED = "#D9302A"
ARROW_NM, HEAD_NM = 0.3, 0.75
TAIL_COLOR, HEAD_COLORS = "#DDB240", ("#E9EBEE", "#B9BDC3", "#8C9199", "#9A9EA6")
PARTICLE_COLORS = ("#F2F3F5", "#C9CCD1", "#9EA3AA")
LABEL_INK = "#3D4046"
WHITE = (255, 255, 255)


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
    """The points turned about the z axis (the axle) by `angle` radians, counterclockwise seen from +z."""
    c, s = math.cos(angle), math.sin(angle)
    return points @ np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])


def mix(hex_color: str, toward: tuple[int, int, int], amount: float, alpha: float = 1.0) -> tuple[float, float, float, float]:
    r, g, b = p.rgb(hex_color)
    return (alpha, *(c + (t - c) * amount for c, t in zip((r, g, b), toward)))


def argb(color: tuple[float, float, float, float], wash: float = 0.0) -> int:
    """A mixed colour as skia's, washed toward white by `wash`."""
    alpha, *rgb = color
    return skia.ColorSetARGB(round(255 * alpha), *(round(c + (255 - c) * wash) for c in rgb))


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


def body(canvas: skia.Canvas, path: skia.Path, r: float, color: str, outline: float, wash: float = 0.0) -> None:
    """A rounded shape along the path, shaded like the book's: a darker side, a soft lit side and a shine."""
    canvas.drawPath(path, stroke(2 * r + 2 * outline, argb(mix(INK, WHITE, 0), wash)))
    shape = skia.Path()
    stroke(2 * r, 0).getFillPath(path, shape)
    canvas.save()
    canvas.clipPath(shape, doAntiAlias=True)
    canvas.drawPath(path, stroke(2 * r, argb(mix(color, (40, 34, 90), 0.28), wash)))
    for offset, width, shade, blur in ((0.22, 1.55, mix(color, WHITE, 0.0), 0.32),
                                       (0.45, 0.95, mix(color, WHITE, 0.38), 0.32),
                                       (0.62, 0.32, mix(color, WHITE, 0.85, 0.75), 0.22)):
        canvas.save()
        canvas.translate(LIGHT[0] * offset * r, LIGHT[1] * offset * r)
        canvas.drawPath(path, stroke(width * r, argb(shade, wash), blur * r))
        canvas.restore()
    canvas.restore()


def ball(canvas: skia.Canvas, x: float, y: float, r: float, colors: tuple[str, ...], edge: str | None, outline: float,
         wash: float = 0.0) -> None:
    light, base, dark = (argb(mix(c, WHITE, 0), wash) for c in colors[:3])
    shader = skia.GradientShader.MakeRadial(skia.Point(x + LIGHT[0] * 0.45 * r, y + LIGHT[1] * 0.45 * r), 1.45 * r,
                                            [light, base, dark], [0.0, 0.5, 1.0])
    canvas.drawCircle(x, y, r, skia.Paint(AntiAlias=True, Shader=shader))
    if edge:
        canvas.drawCircle(x, y, r, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=outline * 0.8,
                                              Color=argb(mix(edge, WHITE, 0), wash)))


def arc(radius: float, z: float, from_deg: float, to_deg: float, n: int = 28) -> np.ndarray:
    a = np.radians(np.linspace(from_deg, to_deg, n))
    return np.stack([radius * np.cos(a), radius * np.sin(a), np.full(n, z)], axis=1)


@dataclass
class Shape:
    points: np.ndarray  # nm: a capsule's two cap centres, or a tube's smoothed path
    r: float  # nm
    part: str
    chain: str
    structure: str | None  # the structure it belongs to, None for a part in none
    color: str
    spin: str | None  # the spin that turns it, if that spin is drawn
    inside: str | None


@dataclass
class Moving:
    """A flow as drawn: its path the way it goes (nm), and whether its particles move."""

    flow: Flow
    path: np.ndarray
    lengths: np.ndarray
    moves: bool


class Machine:
    """One recipe's fitted shapes, ready to draw at any time t, in the art's pixels."""

    def __init__(self, recipe: Recipe, fitted: dict, motions: dict[str, int] | None = None,
                 width: int = media.ART_WIDTH, height: int = media.ART_HEIGHT):
        self.recipe, self.fitted = recipe, fitted
        self.W, self.H = width, height
        cam = recipe.camera
        elev = math.radians(cam.elevation_deg)
        self.right = np.array([1.0, 0, 0])
        self.up = np.array([0, math.sin(elev), math.cos(elev)])
        self.away = np.array([0, math.cos(elev), -math.sin(elev)])  # depth: larger is farther
        self.scale = height / ((cam.top - cam.bottom) * math.cos(elev))  # art pixels per nm
        self.target = np.array([cam.centre_x, 0, (cam.top + cam.bottom) / 2])
        self.outline = OUTLINE_NM * self.scale
        self.motions = {m: s for m, s in (motions or {}).items() if recipe.motion(m) is not None}
        self.spins: dict[str, tuple[Spin, int]] = {
            m.id: (m, self.motions[m.id]) for m in recipe.motions if m.kind == "spin" and m.id in self.motions}
        turning = {part: spin_id for spin_id, (spin, _) in self.spins.items() for part in spin.parts}
        self.names = [s.name for s in recipe.structures]
        self.shapes: list[Shape] = []
        for s in fitted["shapes"]:
            part = recipe.parts[s["part"]]
            owner = recipe.structure_of(s["part"])
            pts = catmull(s["path"]) if s["kind"] == "tube" else np.array([s["a"], s["b"]], float)
            self.shapes.append(Shape(points=pts, r=s["radius"], part=s["part"], chain=s["chain"],
                                     structure=owner.name if owner else None,
                                     color=owner.color if owner else part.color, spin=turning.get(s["part"]),
                                     inside=part.inside))
        self.flows: list[Moving] = []
        self.arrows: list[np.ndarray] = []  # 3D polylines, each ending where its head points
        for motion in recipe.motions:
            sense = self.motions.get(motion.id)
            if sense is None:
                continue
            if motion.kind == "spin":
                for a in motion.arcs:
                    half = sense * a.sweep_deg / 2
                    self.arrows.append(arc(a.radius, a.z, a.around_deg - half, a.around_deg + half))
            else:
                path = catmull(motion.path)
                path = path if sense > 0 else path[::-1]
                lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
                moves = motion.moves_with is None or motion.moves_with in self.spins
                self.flows.append(Moving(motion, path, lengths, moves))
                self.arrows.append(path)
        self.moving = bool(self.spins) or any(f.moves for f in self.flows)
        self._labels = self._label_points()
        self._membranes: dict[float, tuple[skia.Image, float, float]] = {}
        self._fonts = {id(f.flow): p.font("Bold", 1.15 * f.flow.particle.radius * self.scale) for f in self.flows}

    # --- geometry ---------------------------------------------------------------------------

    def project(self, points) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Art x, y (px) and depth (nm) of 3D points (nm)."""
        pts = np.atleast_2d(np.asarray(points, float)) - self.target
        return self.W / 2 + pts @ self.right * self.scale, self.H / 2 - pts @ self.up * self.scale, pts @ self.away

    def angle(self, spin_id: str | None, t: float) -> float:
        """How far a spin has turned at t: it turns from the first frame, at full speed, its way."""
        if spin_id is None or spin_id not in self.spins:
            return 0.0
        spin, sense = self.spins[spin_id]
        return sense * 2 * math.pi * t / spin.turn_s

    def placed(self, shape: Shape, t: float) -> np.ndarray:
        return turned(shape.points, self.angle(shape.spin, t)) if shape.spin else shape.points

    def _label_points(self) -> dict[str, tuple[np.ndarray, str | None]]:
        """Each structure's label point (nm, at t = 0) and the spin it turns with, if any."""
        out = {}
        for s in self.recipe.structures:
            where, spin = s.label, None
            if where.at is not None:
                point = np.array(where.at, float)
            elif where.chain is not None:
                shape = next(sh for sh in self.shapes if sh.chain == where.chain and sh.part in s.parts)
                path = shape.points
                if len(path) == 2:
                    point = path[0] + (path[1] - path[0]) * where.along
                else:
                    point = path[min(int(len(path) * where.along), len(path) - 1)]
                spin = shape.spin
            else:
                shapes = [sh for sh in self.shapes if sh.part == where.part]
                point = np.mean([sh.points.mean(axis=0) for sh in shapes], axis=0)
                spin = shapes[0].spin
            out[s.name] = (point + np.array(where.offset, float), spin)
        return out

    def label_point(self, name: str, t: float) -> tuple[float, float]:
        """Where the structure's label points at t, in art pixels: it rides along with a turning part."""
        point, spin = self._labels[name]
        if spin:
            point = turned(point[None, :], self.angle(spin, t))[0]
        xs, ys, _ = self.project(point)
        return float(xs[0]), float(ys[0])

    def silhouette(self, every: float = 1.6) -> list[tuple[float, float]]:
        """Points spread over the machine as drawn at t = 0 (art pixels), about `every` nm apart along each
        shape and across its width: what labels keep clear of, so a pill does not sit on the machine."""
        points = []
        for _, xs, ys, shape in self.paint_order(0.0):
            r = shape.r * self.scale
            line = np.stack([xs, ys], axis=1)
            for a, b in zip(line, line[1:]):
                d = b - a
                n = max(int(np.linalg.norm(d) / (every * self.scale)), 1)
                normal = np.array([-d[1], d[0]]) / (np.linalg.norm(d) or 1)
                for k in range(n + 1):
                    c = a + d * k / n
                    points += [tuple(c), tuple(c + normal * 0.6 * r), tuple(c - normal * 0.6 * r)]
        return [(float(x), float(y)) for x, y in dict.fromkeys(points)]

    def split_y(self) -> float:
        """Where the line between the compartments above and below the membrane falls, in art pixels."""
        _, ys, _ = self.project([(0, self.recipe.page.split_depth, 0)])
        return float(ys[0])

    def paint_order(self, t: float) -> list[tuple[float, np.ndarray, np.ndarray, Shape]]:
        """Every shape at time t, far to near. A part `inside` another goes behind all of it, as the
        book draws a rod inside a knob, rather than between its lobes."""
        items = []
        for shape in self.shapes:
            xs, ys, depth = self.project(self.placed(shape, t))
            items.append([float(depth.mean()), xs, ys, shape])
        farthest: dict[str, float] = {}
        for depth, _, _, shape in items:
            farthest[shape.part] = max(farthest.get(shape.part, -1e9), depth)
        for item in items:
            inside = item[3].inside
            if inside in farthest:
                item[0] = max(item[0], farthest[inside] + 0.01)
        return [tuple(item) for item in sorted(items, key=lambda item: -item[0])]

    def particles(self, t: float) -> list[tuple[np.ndarray, Moving]]:
        """Every particle at t (nm): the crowd waiting where each flow comes from, and the ones on their way."""
        out = []
        for f in self.flows:
            crowd = f.flow.crowd.forward if self.motions[f.flow.id] > 0 else f.flow.crowd.backward
            out += [(np.array(q, float), f) for q in crowd]
            if not f.moves:
                continue
            front = np.array(f.flow.front, float)
            total = f.lengths[-1]
            first = math.floor((t - f.flow.travel_s) / f.flow.every_s) + 1
            for k in range(first, math.floor(t / f.flow.every_s) + 1):
                u = (t - k * f.flow.every_s) / f.flow.travel_s
                if 0 <= u <= 1:
                    at = np.interp(u * total, f.lengths, np.arange(len(f.path)))
                    i0 = min(int(at), len(f.path) - 2)
                    out.append((f.path[i0] + (f.path[i0 + 1] - f.path[i0]) * (at - i0) + front, f))
        return out

    # --- the page: compartments and membrane ------------------------------------------------

    def draw_page(self, canvas: skia.Canvas, bounds: tuple[float, float, float, float], wash: float = 0.0,
                  resolution: float = 1.0) -> None:
        """The compartments over `bounds` (art pixels: the part of the world in view, which can reach
        past the art), and the membrane drawn at `resolution` image pixels per art pixel."""
        page = self.recipe.page
        x0, y0, x1, y1 = bounds
        if page.below is None:
            canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y1), skia.Paint(Color=argb(mix(page.above.color, WHITE, 0), wash)))
        else:
            split = min(max(self.split_y(), y0), y1)
            canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, split), skia.Paint(Color=argb(mix(page.above.color, WHITE, 0), wash)))
            canvas.drawRect(skia.Rect.MakeLTRB(x0, split, x1, y1), skia.Paint(Color=argb(mix(page.below.color, WHITE, 0), wash)))
        if self.recipe.membrane:
            image, mx, my = self.membrane(resolution)
            canvas.save()
            canvas.translate(mx, my)
            canvas.scale(1 / resolution, 1 / resolution)
            paint = skia.Paint(AntiAlias=True)
            if wash > 0.002:  # white laid over the membrane's own pixels only, as the compartments are washed
                paint.setColorFilter(skia.ColorFilters.Blend(skia.ColorSetARGB(round(255 * wash), 255, 255, 255),
                                                             skia.BlendMode.kSrcATop))
            canvas.drawImage(image, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear), paint)
            canvas.restore()

    def membrane(self, resolution: float = 1.0) -> tuple[skia.Image, float, float]:
        """The membrane as an image at `resolution` image pixels per art pixel, and where its top left is
        (art pixels): heads and tails in rows, cut away around the parts that sit in it. Kept per resolution."""
        if resolution in self._membranes:
            return self._membranes[resolution]
        mem = self.recipe.membrane
        reach = (mem.half_width + 1) * mem.spacing
        depth = 0.4 + mem.rows * mem.spacing * 0.87
        corners = [(x, y, z) for x in (-reach, reach) for y in (0, depth) for z in (-mem.head_z - 1, mem.head_z + 1)]
        xs, ys, _ = self.project(corners)
        pad = mem.head_radius * self.scale * 2
        x0, y0 = float(xs.min() - pad), float(ys.min() - pad)
        width, height = math.ceil((xs.max() + pad - x0) * resolution), math.ceil((ys.max() + pad - y0) * resolution)
        surface = skia.Surface(width, height)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.scale(resolution, resolution)
        canvas.translate(-x0, -y0)
        foot = np.array(self.fitted.get("membrane_footprint", []))
        rng = np.random.default_rng(3)
        tail = stroke(2 * 0.085 * self.scale, argb(mix(TAIL_COLOR, WHITE, 0)))
        z, spacing = mem.head_z, mem.spacing
        for row in range(mem.rows - 1, -1, -1):  # far rows first
            y = 0.4 + row * spacing * 0.87
            heads = []
            for col in range(-mem.half_width, mem.half_width + 1):
                x = col * spacing + (0.5 * spacing if row % 2 else 0)
                if len(foot) and np.min(np.hypot(foot[:, 0] - x, foot[:, 1] - y)) < mem.cut:
                    continue
                for side in (1, -1):
                    for dx in (-0.16, 0.16):
                        phase = rng.uniform(0, 2 * math.pi)
                        wavy = [(x + dx + 0.07 * math.sin(phase + k * 1.7), y, side * (z - 0.35 - 0.36 * k)) for k in range(6)]
                        txs, tys, _ = self.project(wavy)
                        canvas.drawPath(polyline(txs, tys), tail)
                    heads.append((x, y, side * z))
            if heads:
                hxs, hys, _ = self.project(heads)
                for hx, hy in zip(hxs, hys):
                    ball(canvas, hx, hy, mem.head_radius * self.scale, HEAD_COLORS[:3], HEAD_COLORS[3], self.outline)
        self._membranes[resolution] = (surface.makeImageSnapshot(), x0, y0)
        return self._membranes[resolution]

    # --- the machine ------------------------------------------------------------------------

    def draw_machine(self, canvas: skia.Canvas, t: float, washes: dict[str, float] | None = None,
                     other_wash: float = 0.0, text: bool = True) -> None:
        """The parts at t, far to near, each structure washed by its amount; then the arrows and particles."""
        washes = washes or {}
        for _, xs, ys, shape in self.paint_order(t):
            wash = washes.get(shape.structure, 0.0) if shape.structure else other_wash
            body(canvas, polyline(xs, ys), shape.r * self.scale, shape.color, self.outline, wash)
        red = argb(mix(RED, WHITE, 0), other_wash)
        for arrow in self.arrows:
            xs, ys, _ = self.project(arrow)
            canvas.drawPath(polyline(xs, ys), stroke(ARROW_NM * self.scale, red))
            dx, dy = xs[-1] - xs[-2], ys[-1] - ys[-2]
            n = math.hypot(dx, dy) or 1
            dx, dy = dx / n, dy / n
            tip = (xs[-1] + dx * 0.55 * self.scale, ys[-1] + dy * 0.55 * self.scale)
            size = HEAD_NM * self.scale
            head = skia.Path()
            head.moveTo(*tip)
            head.lineTo(tip[0] - dx * size - dy * size * 0.55, tip[1] - dy * size + dx * size * 0.55)
            head.lineTo(tip[0] - dx * size + dy * size * 0.55, tip[1] - dy * size - dx * size * 0.55)
            head.close()
            canvas.drawPath(head, skia.Paint(AntiAlias=True, Color=red))
        for point, f in self.particles(t):
            xs, ys, _ = self.project(point)
            x, y = float(xs[0]), float(ys[0])
            ball(canvas, x, y, f.flow.particle.radius * self.scale, PARTICLE_COLORS, INK, self.outline, other_wash)
            if text and f.flow.particle.label:
                font = self._fonts[id(f.flow)]
                p.draw_text(canvas, f.flow.particle.label, x - p.text_width(font, f.flow.particle.label) / 2,
                            y + p.cap_height(font) / 2, font, argb(mix(LABEL_INK, WHITE, 0), other_wash))

    def draw(self, canvas: skia.Canvas, t: float, washes: dict[str, float] | None = None, other_wash: float = 0.0,
             text: bool = True, bounds: tuple[float, float, float, float] | None = None, resolution: float = 1.0) -> None:
        self.draw_page(canvas, bounds or (0, 0, self.W, self.H), other_wash, resolution)
        self.draw_machine(canvas, t, washes, other_wash, text)

    # --- for the art step -------------------------------------------------------------------

    def still(self) -> np.ndarray:
        """The machine at t = 0 without any text: art.png's pixels, H x W x 3 RGB."""
        surface = skia.Surface(self.W, self.H)
        self.draw(surface.getCanvas(), 0.0, text=False)
        return surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)[:, :, :3].copy()

    def masks(self) -> dict[str, np.ndarray]:
        """Per structure, where it shows at t = 0 (bool, H x W): what nearer parts cover is left out."""
        surface = skia.Surface(self.W, self.H)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorBLACK)
        ids = {name: i + 1 for i, name in enumerate(self.names)}
        for _, xs, ys, shape in self.paint_order(0.0):
            paint = skia.Paint(AntiAlias=False, Style=skia.Paint.kStroke_Style,
                               StrokeWidth=2 * (shape.r * self.scale + self.outline), StrokeCap=skia.Paint.kRound_Cap,
                               StrokeJoin=skia.Paint.kRound_Join,
                               Color=skia.ColorSetARGB(255, ids.get(shape.structure, 0), 0, 0))
            canvas.drawPath(polyline(xs, ys), paint)
        red = surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)[:, :, 0]
        return {name: red == i for name, i in ids.items()}


def render_video(machine: Machine, out: Path, fps: int, seconds: float) -> float:
    """Encode the machine from 0 to `seconds`; returns the average drawing time per frame, in seconds."""
    ffmpeg = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                               "-s", f"{machine.W}x{machine.H}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
                               "-pix_fmt", "yuv420p", "-crf", "18", str(out)], stdin=subprocess.PIPE)
    surface = skia.Surface(machine.W, machine.H)
    frames = int(seconds * fps)
    drawing = 0.0
    for i in range(frames):
        t0 = time.time()
        machine.draw(surface.getCanvas(), i / fps)
        pixels = surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)
        drawing += time.time() - t0
        ffmpeg.stdin.write(pixels.tobytes())
    ffmpeg.stdin.close()
    if ffmpeg.wait():
        raise RuntimeError(f"ffmpeg could not encode {out}")
    return drawing / max(frames, 1)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) < 2:
        sys.exit(__doc__.split("\n\n")[1])
    entry, out = load(argv[0]), Path(argv[1])
    opt: dict[str, str] = {}
    motions: dict[str, int] = {}
    i = 2
    while i < len(argv) - 1:
        if argv[i] == "--motion":
            motion, sense = argv[i + 1].split("=")
            motions[motion] = 1 if int(sense) > 0 else -1
        elif argv[i].startswith("--"):
            opt[argv[i]] = argv[i + 1]
        i += 2
    out.mkdir(parents=True, exist_ok=True)
    machine = Machine(entry.recipe, entry.shapes, motions)
    started = time.time()
    if "--still" in opt:
        t = float(opt["--still"])
        surface = skia.Surface(machine.W, machine.H)
        machine.draw(surface.getCanvas(), t)
        surface.makeImageSnapshot().save(str(out / f"{entry.recipe.name}_{t:.2f}s.png"), skia.kPNG)
        print(f"still at {t}s -> {out}")
    else:
        fps, seconds = int(opt.get("--fps", 24)), float(opt.get("--seconds", 7.2))
        video = out / f"{entry.recipe.name}_{fps}fps.mp4"
        per_frame = render_video(machine, video, fps, seconds)
        print(f"{int(seconds * fps)} frames, {per_frame * 1000:.0f} ms a frame, {time.time() - started:.1f}s in all -> {video}")
