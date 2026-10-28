"""Draw a built machine in the textbook's style: real 3D positions, projected and painted back to front.

    python -m studio.schematic3d.draw SCHEMATIC.json OUT.png    a still of a built machine

A Machine draws in the art's pixels (media.ART_WIDTH x ART_HEIGHT), which are the world the
renderer's camera moves over: the renderer draws it under its camera, the art step draws its
still and its masks from it (studio/steps/art.py). It draws only what it is given
(model.Built), so nothing here depends on the machine.

The camera is orthographic and looks along +y, a little down, so a capsule is drawn as a
rounded bar and a tube as a thick curve. The shapes are painted far to near, shaded like the
book's: an outline, a darker side, a soft lit side and a shine, each structure in a soft
version of its colour and the machine's other chains in a neutral one. The compartments are
flat colour, and the membrane (rows of grey heads and yellow tails, cut away around the
machine) is drawn once at each scale it is needed and kept. The machine does not move.

Each structure is drawn washed toward white by its own amount, for the renderer's ghosts and
spotlight; the page by one amount of its own.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import skia

from studio import media
from studio.render import primitives as p
from studio.schematic3d.model import Built

INK = "#4E4887"  # the book's outline: a dark lavender
OTHER = "#C9C6DD"  # chains in no structure
SOFTEN = 0.3  # a structure's colour, this far toward white: the book's soft colours
LIGHT = np.array([-0.55, -0.83])  # on screen, from the upper left
OUTLINE_NM = 0.095
TAIL_COLOR, HEAD_COLORS = "#DDB240", ("#E9EBEE", "#B9BDC3", "#8C9199", "#9A9EA6")
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


def ball(canvas: skia.Canvas, x: float, y: float, r: float, colors: tuple[str, ...], edge: str | None, outline: float) -> None:
    light, base, dark = (argb(mix(c, WHITE, 0)) for c in colors[:3])
    shader = skia.GradientShader.MakeRadial(skia.Point(x + LIGHT[0] * 0.45 * r, y + LIGHT[1] * 0.45 * r), 1.45 * r,
                                            [light, base, dark], [0.0, 0.5, 1.0])
    canvas.drawCircle(x, y, r, skia.Paint(AntiAlias=True, Shader=shader))
    if edge:
        canvas.drawCircle(x, y, r, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=outline * 0.8,
                                              Color=argb(mix(edge, WHITE, 0))))


@dataclass
class Piece:
    points: np.ndarray  # nm: a capsule's two cap centres, or a tube's smoothed path
    r: float  # nm
    structure: str | None
    color: str


class Machine:
    """A built machine, ready to draw in the art's pixels."""

    def __init__(self, built: Built, width: int = media.ART_WIDTH, height: int = media.ART_HEIGHT):
        self.built = built
        self.W, self.H = width, height
        cam = built.camera
        elev = math.radians(cam.elevation_deg)
        self.right = np.array([1.0, 0, 0])
        self.up = np.array([0, math.sin(elev), math.cos(elev)])
        self.away = np.array([0, math.cos(elev), -math.sin(elev)])  # depth: larger is farther
        self.scale = height / ((cam.top - cam.bottom) * math.cos(elev))  # art pixels per nm
        self.target = np.array([cam.centre_x, 0, (cam.top + cam.bottom) / 2])
        self.outline = OUTLINE_NM * self.scale
        colors = {s.name: p.rgb(s.color) for s in built.structures}
        self.names = [s.name for s in built.structures]
        self.pieces = []
        for shape in built.shapes:
            owner = shape.structure if shape.structure in colors else None
            color = OTHER if owner is None else "#{:02X}{:02X}{:02X}".format(
                *(round(c + (255 - c) * SOFTEN) for c in colors[owner]))
            pts = catmull(shape.points) if shape.kind == "tube" else np.array(shape.points, float)
            self.pieces.append(Piece(points=pts, r=shape.radius, structure=owner, color=color))
        self._order = self._paint_order()
        self._membranes: dict[float, tuple[skia.Image, float, float]] = {}

    def project(self, points) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Art x, y (px) and depth (nm) of 3D points (nm)."""
        pts = np.atleast_2d(np.asarray(points, float)) - self.target
        return self.W / 2 + pts @ self.right * self.scale, self.H / 2 - pts @ self.up * self.scale, pts @ self.away

    def _paint_order(self) -> list[tuple[np.ndarray, np.ndarray, Piece]]:
        """Every piece, far to near, by its mean depth."""
        items = []
        for piece in self.pieces:
            xs, ys, depth = self.project(piece.points)
            items.append((float(depth.mean()), xs, ys, piece))
        return [(xs, ys, piece) for _, xs, ys, piece in sorted(items, key=lambda item: -item[0])]

    def split_y(self) -> float:
        """Where the line between the compartments above and below the membrane falls, in art pixels."""
        _, ys, _ = self.project([(0, 3.0, 0)])
        return float(ys[0])

    def silhouette(self, every: float = 1.6) -> list[tuple[float, float]]:
        """Points spread over the machine (art pixels), about `every` nm apart along each shape and across its
        width: what labels keep clear of, so a pill does not sit on the machine."""
        points = []
        for xs, ys, piece in self._order:
            r = piece.r * self.scale
            line = np.stack([xs, ys], axis=1)
            for a, b in zip(line, line[1:]):
                d = b - a
                n = max(int(np.linalg.norm(d) / (every * self.scale)), 1)
                normal = np.array([-d[1], d[0]]) / (np.linalg.norm(d) or 1)
                for k in range(n + 1):
                    c = a + d * k / n
                    points += [tuple(c), tuple(c + normal * 0.6 * r), tuple(c - normal * 0.6 * r)]
        return [(float(x), float(y)) for x, y in dict.fromkeys(points)]

    # --- the page: compartments and membrane ------------------------------------------------

    def draw_page(self, canvas: skia.Canvas, bounds: tuple[float, float, float, float], wash: float = 0.0,
                  resolution: float = 1.0) -> None:
        """The compartments over `bounds` (art pixels: the part of the world in view, which can reach
        past the art), and the membrane drawn at `resolution` image pixels per art pixel."""
        page = self.built.page
        x0, y0, x1, y1 = bounds
        if page.below is None:
            canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y1), skia.Paint(Color=argb(mix(page.above.color, WHITE, 0), wash)))
        else:
            split = min(max(self.split_y(), y0), y1)
            canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, split), skia.Paint(Color=argb(mix(page.above.color, WHITE, 0), wash)))
            canvas.drawRect(skia.Rect.MakeLTRB(x0, split, x1, y1), skia.Paint(Color=argb(mix(page.below.color, WHITE, 0), wash)))
        if self.built.membrane:
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
        (art pixels): heads and tails in rows, cut away around the machine. Kept per resolution."""
        if resolution in self._membranes:
            return self._membranes[resolution]
        mem = self.built.membrane
        head_z = mem.half_thickness + mem.head_radius
        reach = (mem.half_width + 1) * mem.spacing
        depth = 0.4 + mem.rows * mem.spacing * 0.87
        corners = [(x, y, z) for x in (-reach, reach) for y in (0, depth) for z in (-head_z - 1, head_z + 1)]
        xs, ys, _ = self.project(corners)
        pad = mem.head_radius * self.scale * 2
        x0, y0 = float(xs.min() - pad), float(ys.min() - pad)
        width, height = math.ceil((xs.max() + pad - x0) * resolution), math.ceil((ys.max() + pad - y0) * resolution)
        surface = skia.Surface(width, height)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.scale(resolution, resolution)
        canvas.translate(-x0, -y0)
        foot = np.array(mem.footprint, float).reshape(-1, 2)
        rng = np.random.default_rng(3)
        tail = stroke(2 * 0.085 * self.scale, argb(mix(TAIL_COLOR, WHITE, 0)))
        steps = max(int(round(mem.half_thickness / 0.36)), 1)
        for row in range(mem.rows - 1, -1, -1):  # far rows first
            y = 0.4 + row * mem.spacing * 0.87
            heads = []
            for col in range(-mem.half_width, mem.half_width + 1):
                x = col * mem.spacing + (0.5 * mem.spacing if row % 2 else 0)
                if len(foot) and np.min(np.hypot(foot[:, 0] - x, foot[:, 1] - y)) < mem.cut:
                    continue
                for side in (1, -1):
                    for dx in (-0.16, 0.16):
                        phase = rng.uniform(0, 2 * math.pi)
                        wavy = [(x + dx + 0.07 * math.sin(phase + k * 1.7), y, side * (head_z - 0.35 - 0.36 * k))
                                for k in range(steps + 1)]
                        txs, tys, _ = self.project(wavy)
                        canvas.drawPath(polyline(txs, tys), tail)
                    heads.append((x, y, side * head_z))
            if heads:
                hxs, hys, _ = self.project(heads)
                for hx, hy in zip(hxs, hys):
                    ball(canvas, hx, hy, mem.head_radius * self.scale, HEAD_COLORS[:3], HEAD_COLORS[3], self.outline)
        self._membranes[resolution] = (surface.makeImageSnapshot(), x0, y0)
        return self._membranes[resolution]

    # --- the machine ------------------------------------------------------------------------

    def draw_machine(self, canvas: skia.Canvas, washes: dict[str, float] | None = None, other_wash: float = 0.0) -> None:
        """Every piece, far to near, each structure washed by its amount and the other chains by `other_wash`."""
        washes = washes or {}
        for xs, ys, piece in self._order:
            wash = washes.get(piece.structure, 0.0) if piece.structure else other_wash
            body(canvas, polyline(xs, ys), piece.r * self.scale, piece.color, self.outline, wash)

    def draw(self, canvas: skia.Canvas, washes: dict[str, float] | None = None, other_wash: float = 0.0,
             bounds: tuple[float, float, float, float] | None = None, resolution: float = 1.0) -> None:
        self.draw_page(canvas, bounds or (0, 0, self.W, self.H), other_wash, resolution)
        self.draw_machine(canvas, washes, other_wash)

    # --- for the art step -------------------------------------------------------------------

    def still(self) -> np.ndarray:
        """The machine with no text on it: art.png's pixels, H x W x 3 RGB."""
        surface = skia.Surface(self.W, self.H)
        self.draw(surface.getCanvas())
        return surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)[:, :, :3].copy()

    def masks(self) -> dict[str, np.ndarray]:
        """Per structure, where it shows (bool, H x W): what nearer pieces cover is left out."""
        surface = skia.Surface(self.W, self.H)
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorBLACK)
        ids = {name: i + 1 for i, name in enumerate(self.names)}
        for xs, ys, piece in self._order:
            paint = skia.Paint(AntiAlias=False, Style=skia.Paint.kStroke_Style,
                               StrokeWidth=2 * (piece.r * self.scale + self.outline), StrokeCap=skia.Paint.kRound_Cap,
                               StrokeJoin=skia.Paint.kRound_Join, Color=skia.ColorSetARGB(255, ids.get(piece.structure, 0), 0, 0))
            canvas.drawPath(polyline(xs, ys), paint)
        red = surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)[:, :, 0]
        return {name: red == i for name, i in ids.items()}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("\n\n")[1])
    machine = Machine(Built.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8")))
    pixels = machine.still()
    rgba = np.dstack([pixels, np.full(pixels.shape[:2], 255, np.uint8)])
    skia.Image.fromarray(rgba, colorType=skia.kRGBA_8888_ColorType).save(sys.argv[2], skia.kPNG)
    print(f"{machine.built.machine} ({machine.built.source.pdb}) -> {sys.argv[2]}")
