"""The renderer's drawing pieces (docs/PLAN.md, section 7): easing, colours, text,
labels and their placement, the lyric band and the caption pill.

Everything here draws onto a skia canvas in frame pixels and keeps no state between
frames, so a frame depends only on its time.
"""

from __future__ import annotations

import functools
import math
import re
from dataclasses import dataclass
from pathlib import Path

import skia

from studio.render.layouts import Rect

FONTS = Path(__file__).resolve().parent / "fonts"
INK = 0xFF0F172A  # slate-900: text that is not a structure's
MUTED = 0xFF94A3B8  # words not sung yet
HAIRLINE = 0xFFE2E8F0
MARKER = 0xFFFDE68A  # behind the word being sung


# --- time ---------------------------------------------------------------------------------


def clamp01(x: float) -> float:
    return 0.0 if x <= 0 else 1.0 if x >= 1 else x


def ease(x: float) -> float:
    """Smooth in and out, from 0 at x <= 0 to 1 at x >= 1."""
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_out(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def lerp(a: float, b: float, x: float) -> float:
    return a + (b - a) * x


# --- colour -------------------------------------------------------------------------------


def rgb(hex_color: str) -> tuple[int, int, int]:
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def argb(hex_color: str, alpha: float = 1.0) -> int:
    r, g, b = rgb(hex_color)
    return skia.ColorSetARGB(round(255 * clamp01(alpha)), r, g, b)


def with_alpha(color: int, alpha: float) -> int:
    return skia.ColorSetA(color, round(skia.ColorGetA(color) * clamp01(alpha)))


def _luminance(r: float, g: float, b: float) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


@functools.cache
def darken(hex_color: str, contrast: float = 5.0) -> str:
    """The colour darkened until it reads on white (WCAG contrast ratio `contrast`)."""
    r, g, b = rgb(hex_color)
    f = 0.85
    while f > 0.05 and 1.05 / (_luminance(r * f, g * f, b * f) + 0.05) < contrast:
        f -= 0.05
    return f"#{round(r * f):02X}{round(g * f):02X}{round(b * f):02X}"


OUTLINE_CONTRAST = 1.8  # a pill's outline against its white fill: a near-white colour (frost, snow, ice) needs darkening


def outline(hex_color: str) -> str:
    """The colour as it is when it shows on white, else darkened until it does."""
    r, g, b = rgb(hex_color)
    if 1.05 / (_luminance(r, g, b) + 0.05) >= OUTLINE_CONTRAST:
        return hex_color
    return darken(hex_color, OUTLINE_CONTRAST)


# --- text ---------------------------------------------------------------------------------


@functools.cache
def typeface(weight: str) -> skia.Typeface:
    """Inter Regular, SemiBold, Bold or ExtraBold, from studio/render/fonts/."""
    return skia.Typeface.MakeFromFile(str(FONTS / f"Inter-{weight}.ttf"))


@functools.cache
def font(weight: str, size: float) -> skia.Font:
    f = skia.Font(typeface(weight), size)
    f.setEdging(skia.Font.Edging.kAntiAlias)
    f.setSubpixel(True)
    return f


def text_width(f: skia.Font, text: str) -> float:
    return f.measureText(text)


def cap_height(f: skia.Font) -> float:
    return f.getMetrics().fCapHeight


def draw_text(canvas: skia.Canvas, text: str, x: float, y: float, f: skia.Font, color: int) -> None:
    """Text with its baseline at y."""
    canvas.drawString(text, x, y, f, skia.Paint(AntiAlias=True, Color=color))


def wrap(text: str, f: skia.Font, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and text_width(f, trial) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def rounded(rect: Rect, radius: float) -> skia.RRect:
    return skia.RRect.MakeRectXY(skia.Rect.MakeLTRB(*rect), radius, radius)


def shadow(canvas: skia.Canvas, rrect: skia.RRect, alpha: float, blur: float = 8, dy: float = 3) -> None:
    paint = skia.Paint(AntiAlias=True, Color=skia.ColorSetARGB(round(46 * alpha), 15, 23, 42),
                       MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur / 2))
    canvas.save()
    canvas.translate(0, dy)
    canvas.drawRRect(rrect, paint)
    canvas.restore()


# --- labels -------------------------------------------------------------------------------

LABEL_PAD_X, LABEL_H = 20.0, 50.0
LABEL_OUTLINE = 3.5
DOT_R = 6.0
TOUCH = 8.0  # from a structure's edge to its pill: it sits against the structure, with no line
MAX_REACH = 200.0  # a big structure's label stays this close to its dot, on top of it
GAP = 12.0  # between pills


@dataclass
class Label:
    name: str
    color: str  # the structure's #RRGGBB
    anchor: tuple[float, float]  # the dot, in frame pixels
    box: Rect  # the structure's box, in frame pixels: the pill starts just outside it
    alpha: float
    big: bool = False  # a space (the stroma, a lumen): it has no edge to sit against, so its pill sits inside it
    emphasis: float = 0.0  # 1 while spotlit: a little larger
    x: float = 0.0  # the pill's centre, set by place_labels
    y: float = 0.0

    def size(self, font_size: float) -> tuple[float, float]:
        scale = 1 + 0.08 * self.emphasis
        f = font("Bold", font_size * scale)
        return text_width(f, self.name) + 2 * LABEL_PAD_X * scale, LABEL_H * scale


AVOID_MARGIN = 10.0  # frame pixels a pill keeps from the points it avoids (along an arrow)


def _clear_home(lb: Label, size: tuple[float, float], home: tuple[float, float], avoid,
                bounds: Rect, others: list[Rect] = ()) -> tuple[float, float]:
    """Where a pill starts, clear of what it avoids: its usual place when that is clear, else the nearest of the
    spots hugging its structure (above, below, each side and each corner; a space's around its label point)
    that crosses the fewest points to avoid and stays on the frame."""
    w, h = size
    bx0, by0, bx1, by1 = lb.box
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    if lb.big:
        ax, ay = lb.anchor
        spots = [(ax + kx * w, ay + ky * h * 1.5) for kx in (0, -1, 1) for ky in (0, -1, 1)]
    else:
        up, down = by0 - TOUCH - h / 2, by1 + TOUCH + h / 2
        left, right = bx0 - TOUCH - w / 2, bx1 + TOUCH + w / 2
        # The corners put the pill wholly to one side of the structure's middle, clear of a flow straight through it.
        west, east = cx - w / 2 - 2 * TOUCH, cx + w / 2 + 2 * TOUCH
        spots = [(cx, up), (cx, down), (left, cy), (right, cy), (west, up), (east, up), (west, down), (east, down)]

    def crossed(spot) -> int:
        x, y = spot
        return sum(1 for px, py in avoid if abs(px - x) < w / 2 + AVOID_MARGIN and abs(py - y) < h / 2 + AVOID_MARGIN)

    def off_frame(spot) -> bool:
        x, y = spot
        return x - w / 2 < bounds[0] or x + w / 2 > bounds[2] or y - h / 2 < bounds[1] or y + h / 2 > bounds[3]

    def on_another(spot) -> int:  # sitting on another structure would read as its label
        x, y = spot
        return sum(1 for ox0, oy0, ox1, oy1 in others
                   if x - w / 2 < ox1 and ox0 < x + w / 2 and y - h / 2 < oy1 and oy0 < y + h / 2)

    if not crossed(home):
        return home
    return min([home] + spots, key=lambda spot: (crossed(spot) + 3 * on_another(spot), off_frame(spot),
                                                  math.dist(spot, home)))


def place_labels(labels: list[Label], bounds: Rect, centre: tuple[float, float], font_size: float,
                 avoid: list[tuple[float, float]] = ()) -> None:
    """Put each pill near its dot, away from the diagram's centre, inside `bounds` and off the others.

    A fixed number of push-apart rounds from the same start, so the same input always
    gives the same places (a frame depends only on its time).
    """
    sizes = [lb.size(font_size) for lb in labels]
    homes = []
    for lb, (w, h) in zip(labels, sizes):
        dx, dy = lb.anchor[0] - centre[0], lb.anchor[1] - centre[1]
        length = math.hypot(dx, dy)
        dx, dy = (dx / length, dy / length) if length > 1 else (0.0, -1.0)
        dy -= 0.9  # labels read best above their dot
        length = math.hypot(dx, dy)
        dx, dy = dx / length, dy / length
        # From the dot out of the structure's box, then on to the pill's centre.
        bx0, by0, bx1, by1 = lb.box
        ax, ay = lb.anchor
        exit_x = ((bx1 - ax) / dx if dx > 0 else (bx0 - ax) / dx) if abs(dx) > 1e-6 else 1e9
        exit_y = ((by1 - ay) / dy if dy > 0 else (by0 - ay) / dy) if abs(dy) > 1e-6 else 1e9
        out = min(max(min(exit_x, exit_y), 0.0), MAX_REACH)
        if lb.big:  # a space's pill sits inside it, at its label point, with no line
            homes.append(lb.anchor)
            continue
        reach = out + TOUCH + min(abs(w / 2 / dx) if abs(dx) > 1e-6 else 1e9, abs(h / 2 / dy) if abs(dy) > 1e-6 else 1e9)
        homes.append((lb.anchor[0] + dx * reach, lb.anchor[1] + dy * reach))
    x0, y0, x1, y1 = bounds
    if avoid:
        homes = [_clear_home(lb, size, home, avoid, bounds, [o.box for o in labels if o is not lb and not o.big])
                 for lb, size, home in zip(labels, sizes, homes)]
    pos = [list(h) for h in homes]

    def clamp(i: int) -> None:
        w, h = sizes[i]
        pos[i][0] = min(max(pos[i][0], x0 + w / 2), x1 - w / 2)
        pos[i][1] = min(max(pos[i][1], y0 + h / 2), y1 - h / 2)

    for i in range(len(labels)):
        clamp(i)
    dots = list(avoid)  # a label's own dot is drawn only when its pill ends up far from it, so it is not avoided
    for _ in range(40):
        moved = False
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                (wi, hi), (wj, hj) = sizes[i], sizes[j]
                ox = (wi + wj) / 2 + GAP - abs(pos[i][0] - pos[j][0])
                oy = (hi + hj) / 2 + GAP - abs(pos[i][1] - pos[j][1])
                if ox > 0 and oy > 0:
                    moved = True
                    if oy <= ox:  # push apart along the shorter overlap
                        s = 1 if pos[i][1] < pos[j][1] or (pos[i][1] == pos[j][1] and i < j) else -1
                        pos[i][1] -= s * oy / 2
                        pos[j][1] += s * oy / 2
                    else:
                        s = 1 if pos[i][0] < pos[j][0] or (pos[i][0] == pos[j][0] and i < j) else -1
                        pos[i][0] -= s * ox / 2
                        pos[j][0] += s * ox / 2
            # Keep the pill off every dot, its own included.
            w, h = sizes[i]
            for dot in dots:
                ox = w / 2 + DOT_R + 6 - abs(pos[i][0] - dot[0])
                oy = h / 2 + DOT_R + 6 - abs(pos[i][1] - dot[1])
                if ox > 0 and oy > 0:
                    moved = True
                    if oy <= ox:
                        pos[i][1] += oy if pos[i][1] >= dot[1] else -oy
                    else:
                        pos[i][0] += ox if pos[i][0] >= dot[0] else -ox
            clamp(i)
        if not moved:
            break
    for lb, (x, y) in zip(labels, pos):
        lb.x, lb.y = x, y


def draw_label(canvas: skia.Canvas, lb: Label, font_size: float) -> None:
    if lb.alpha <= 0.01:
        return
    w, h = lb.size(font_size)
    scale = 1 + 0.08 * lb.emphasis
    pop = 0.85 + 0.15 * ease_out(lb.alpha)  # pills grow into place as they appear
    w, h = w * pop, h * pop
    rect = (lb.x - w / 2, lb.y - h / 2, lb.x + w / 2, lb.y + h / 2)
    color = argb(outline(lb.color), lb.alpha)
    ink = argb(darken(lb.color), lb.alpha)  # the leader and dot sit on the structure, so they are darker
    ax, ay = lb.anchor
    # A pill against its structure, or inside its space, needs no line; one the placement pass pushed away keeps it.
    bx0, by0, bx1, by1 = lb.box
    gap = math.hypot(max(bx0 - rect[2], rect[0] - bx1, 0.0), max(by0 - rect[3], rect[1] - by1, 0.0))
    leader = not lb.big and gap > h
    # The leader runs from the dot to the nearest point on the pill.
    ex, ey = min(max(ax, rect[0] + h / 2), rect[2] - h / 2), min(max(ay, rect[1]), rect[3])
    line = skia.Paint(AntiAlias=True, Color=ink, StrokeWidth=3.0, Style=skia.Paint.kStroke_Style,
                      StrokeCap=skia.Paint.kRound_Cap)
    if leader:
        canvas.drawLine(ax, ay, ex, ey, line)
        canvas.drawCircle(ax, ay, DOT_R + 2.5, skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, lb.alpha)))
        canvas.drawCircle(ax, ay, DOT_R, skia.Paint(AntiAlias=True, Color=ink))
    rrect = rounded(rect, h / 2)
    shadow(canvas, rrect, lb.alpha)
    canvas.drawRRect(rrect, skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, lb.alpha)))
    inset = rounded((rect[0] + LABEL_OUTLINE / 2, rect[1] + LABEL_OUTLINE / 2,
                     rect[2] - LABEL_OUTLINE / 2, rect[3] - LABEL_OUTLINE / 2), h / 2 - LABEL_OUTLINE / 2)
    canvas.drawRRect(inset, skia.Paint(AntiAlias=True, Color=color, StrokeWidth=LABEL_OUTLINE,
                                       Style=skia.Paint.kStroke_Style))
    f = font("Bold", font_size * scale * pop)
    tw = text_width(f, lb.name)
    draw_text(canvas, lb.name, lb.x - tw / 2, lb.y + cap_height(f) / 2, f, argb(darken(lb.color), lb.alpha))


# --- axis names -----------------------------------------------------------------------------

AXIS_HALO = 6.0  # the stroke of background colour round an axis name, so it reads where it crosses a line
AXIS_INK_LIGHT, AXIS_INK_DARK = 0xFFF1F5F9, 0xFF334155  # on a dark background, and on a light one


def draw_axis_name(canvas: skia.Canvas, text: str, rect: Rect, font_size: float, paper: int, alpha: float) -> None:
    """A graph's axis name, as a textbook prints it: plain writing in the picture's own ink, with no pill, since the
    pills are the parts the lesson teaches; ringed with the background's colour so it reads over a line."""
    if alpha <= 0.01:
        return
    f = font("Bold", font_size)
    x = (rect[0] + rect[2]) / 2 - text_width(f, text) / 2
    y = (rect[1] + rect[3]) / 2 + cap_height(f) / 2
    light = _light(paper)
    canvas.drawString(text, x, y, f, skia.Paint(AntiAlias=True, Color=with_alpha(paper, alpha), StrokeWidth=AXIS_HALO,
                                                Style=skia.Paint.kStroke_Style, StrokeJoin=skia.Paint.kRound_Join))
    draw_text(canvas, text, x, y, f, with_alpha(AXIS_INK_DARK if light else AXIS_INK_LIGHT, alpha))


VALUE_INK, VALUE_INK_DARK = 0xFFFFFFFF, 0xFF0F172A  # an amount a chart shows ("$1,210"), on a dark background and on
# a light one: a shade stronger than an axis's ink either way
VALUE_POP = 0.3  # how much bigger an amount is written as it is said, easing back over VALUE_POP_S
VALUE_POP_S = 0.5


def draw_value(canvas: skia.Canvas, text: str, rect: Rect, font_size: float, paper: int, alpha: float,
               tick: bool, pop: float = 0.0) -> None:
    """A number on a chart (Beat.values), as a textbook prints it: plain writing ringed with the background's colour,
    a tick in the axis names' ink, an amount in white (near black on a light page), `pop` (0-1) of VALUE_POP bigger
    about its middle."""
    if alpha <= 0.01:
        return
    if tick:
        draw_axis_name(canvas, text, rect, font_size, paper, alpha)
        return
    canvas.save()
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    grow = 1 + VALUE_POP * pop
    canvas.translate(cx, cy)
    canvas.scale(grow, grow)
    canvas.translate(-cx, -cy)
    f = font("ExtraBold", font_size)
    x, y = cx - text_width(f, text) / 2, cy + cap_height(f) / 2
    canvas.drawString(text, x, y, f, skia.Paint(AntiAlias=True, Color=with_alpha(paper, alpha), StrokeWidth=AXIS_HALO,
                                                Style=skia.Paint.kStroke_Style, StrokeJoin=skia.Paint.kRound_Join))
    draw_text(canvas, text, x, y, f, with_alpha(VALUE_INK_DARK if _light(paper) else VALUE_INK, alpha))
    canvas.restore()


NAME_CONTRAST = 4.5  # a name written in its structure's colour, against a light page: WCAG's for text


def draw_name(canvas: skia.Canvas, text: str, rect: Rect, font_size: float, paper: int, color: str,
              alpha: float) -> None:
    """A structure's name where a graph's drawing placed it (Value.label), as a textbook prints a curve's name beside
    it: plain writing in the structure's colour, darkened to read on a light page, ringed with the background's."""
    if alpha <= 0.01:
        return
    f = font("ExtraBold", font_size)
    x = (rect[0] + rect[2]) / 2 - text_width(f, text) / 2
    y = (rect[1] + rect[3]) / 2 + cap_height(f) / 2
    canvas.drawString(text, x, y, f, skia.Paint(AntiAlias=True, Color=with_alpha(paper, alpha), StrokeWidth=AXIS_HALO,
                                                Style=skia.Paint.kStroke_Style, StrokeJoin=skia.Paint.kRound_Join))
    draw_text(canvas, text, x, y, f, argb(darken(color, NAME_CONTRAST) if _light(paper) else color, alpha))


def _light(paper: int) -> bool:
    return _luminance(skia.ColorGetR(paper), skia.ColorGetG(paper), skia.ColorGetB(paper)) > 0.4


# --- particles ------------------------------------------------------------------------------

PARTICLE_R = 13.0  # the lead particle, in frame pixels
STREAM_R = 5.5  # the dots streaming along the path
HALO = 2.5  # a white ring, as a label's dot has, so a particle reads on any colour


MARKER_R = 18.0  # a marked particle: room for "H⁺" inside it, in frame pixels; one size wherever it is drawn
MARKER_FONT = 20.0
MARKER_PAD = 8.0  # a longer marker ("glucose") is a pill this far wider than its word, each side
# What moves carries at most a symbol ("H⁺", "e⁻", "ATP"), as a textbook draws an ion. A word ("glucose",
# "sediment", "loot") never moves: round 17's videos raced word pills across the picture, too fast to read and
# pulling the eye off everything else. A word is written, still, at the tail of its arrow (Renderer._routes).
SYMBOL_MAX = 3


def symbol(label: str) -> bool:
    """Whether what a flow carries is written as a chemical symbol ("H⁺", "O₂", "e⁻", "ATP", "Na⁺"), not a short
    word ("air", "sap", "mud"): short, and not all small letters."""
    return bool(carried(label)) and not (label.isalpha() and label.islower())


def carried(label: str) -> str:
    """What a moving particle has written on it: its label when that is a short symbol, else nothing."""
    return label if len(label) <= SYMBOL_MAX else ""


def draw_particles(canvas: skia.Canvas, color: str, stream: list[tuple[tuple[float, float], float]],
                   lead: tuple[float, float], trail: list[tuple[float, float]], alpha: float, label: str = "") -> None:
    """What travels between structures: dots streaming along the path (each with its own fade,
    0-1), and the lead particle with its trail, the newest position first, drawn as its kind is (draw_kind)."""
    halo, fill = skia.Paint(AntiAlias=True), skia.Paint(AntiAlias=True)
    for (x, y), fade in stream:
        if alpha * fade <= 0.01:
            continue
        halo.setColor(with_alpha(0xFFFFFFFF, 0.8 * alpha * fade))
        canvas.drawCircle(x, y, STREAM_R + HALO, halo)
        fill.setColor(argb(color, alpha * fade))
        canvas.drawCircle(x, y, STREAM_R, fill)
    # The trail is solid dots that shrink with age: fading them would muddy them with the colour under them.
    fill.setColor(argb(color, alpha))
    for k, (x, y) in reversed(list(enumerate(trail, 1))):
        canvas.drawCircle(x, y, 0.8 * PARTICLE_R * (1 - k / (len(trail) + 1)), fill)
    draw_kind(canvas, lead, color, label, alpha)


# Kinds a reader tells apart at a glance: a short symbol ("H⁺", "H₂O", "ATP") printed on its disc; a longer word drawn
# as textbooks draw it, a sugar as its ring (a hexagon), water as a drop; anything else a dot in its colour. The legend in the corner
# names each kind moving on the scene, so nothing that moves carries a word.
SUGARS = {"glucose", "sugar", "sugars", "sucrose", "fructose", "galactose", "lactose", "maltose"}
WATER = {"water"}


def kind_of(label: str) -> str:
    """How a particle carrying `label` is drawn: "sugar", "water", "symbol" or "dot"."""
    if carried(label):  # short enough to print on the particle itself: "H⁺", "H₂O", "ATP"
        return "symbol"
    plain = label.strip().lower()
    return "sugar" if plain in SUGARS else "water" if plain in WATER else "dot"


def _kind_path(kind: str, x: float, y: float, r: float) -> skia.Path:
    path = skia.Path()
    if kind == "sugar":  # pointy-topped, a little wider than a dot so it reads as the same size
        corners = [(x + 1.15 * r * math.cos(math.radians(30 + 60 * k)), y + 1.15 * r * math.sin(math.radians(30 + 60 * k)))
                   for k in range(6)]
        path.moveTo(*corners[0])
        for corner in corners[1:]:
            path.lineTo(*corner)
        path.close()
    elif kind == "water":  # a drop: a point on top, round below
        path.moveTo(x, y - 1.3 * r)
        path.cubicTo(x + 1.05 * r, y - 0.15 * r, x + 1.05 * r, y + r, x, y + r)
        path.cubicTo(x - 1.05 * r, y + r, x - 1.05 * r, y - 0.15 * r, x, y - 1.3 * r)
        path.close()
    else:
        path.addCircle(x, y, r)
    return path


def draw_kind(canvas: skia.Canvas, at: tuple[float, float], color: str, label: str, alpha: float,
              size: float = 1.0) -> None:
    """One particle of the kind `label` names (kind_of), at `size` of a lead particle: a symbol's disc, a sugar's
    hexagon, water's drop or a dot, in its colour with a white halo and a darker ring, so it reads on any colour."""
    if alpha <= 0.01:
        return
    kind = kind_of(label)
    if kind == "symbol":
        draw_marker(canvas, at, color, label, alpha, size)
        return
    r = PARTICLE_R * size
    canvas.drawPath(_kind_path(kind, at[0], at[1], r),
                    skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2 * HALO * size,
                               Color=with_alpha(0xFFFFFFFF, alpha)))
    canvas.drawPath(_kind_path(kind, at[0], at[1], r), skia.Paint(AntiAlias=True, Color=argb(color, alpha)))
    canvas.drawPath(_kind_path(kind, at[0], at[1], r - 1.25 * size),
                    skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2.5 * size,
                               Color=argb(darken(color, 3.0), alpha)))


LEGEND_FONT = 22.0
LEGEND_ICON = 0.7  # its particles, at this share of their size on the picture
LEGEND_ROW = 38.0
LEGEND_PAD = 14.0
LEGEND_GAP = 12.0  # between a row's particle and its name
LEGEND_BG = 0xD91B2536  # a dark panel, a little see-through, that reads over any picture


def legend_rect(right: float, bottom: float, labels: list[str]) -> Rect:
    """Where the legend for these kinds sits, its bottom-right corner at (right, bottom)."""
    f = font("Bold", LEGEND_FONT)
    icon = 2 * max(MARKER_R, PARTICLE_R) * LEGEND_ICON + max((text_width(font("ExtraBold", MARKER_FONT * LEGEND_ICON), l)
                                                            for l in labels if kind_of(l) == "symbol"), default=0) * 0.4
    width = 2 * LEGEND_PAD + icon + LEGEND_GAP + max(text_width(f, label) for label in labels)
    height = 2 * LEGEND_PAD + LEGEND_ROW * len(labels) - (LEGEND_ROW - 2 * MARKER_R * LEGEND_ICON)
    return (right - width, bottom - height, right, bottom)


def draw_legend(canvas: skia.Canvas, rect: Rect, entries: list[tuple[str, str, float]],
                arrows: set[str] = frozenset()) -> None:
    """The key to what moves on the scene: a row for each (colour, kind, alpha), its particle and its name, still in
    one corner, so the particles themselves carry no words; a short arrow for a kind in `arrows`, which travels as
    hand-off arrows (draw_handoff)."""
    shown = max((alpha for _, _, alpha in entries), default=0.0)
    if shown <= 0.01:
        return
    x0, y0, x1, y1 = rect
    canvas.drawRRect(rounded(rect, 12), skia.Paint(AntiAlias=True, Color=with_alpha(LEGEND_BG, shown)))
    f = font("Bold", LEGEND_FONT)
    icon_r = max(MARKER_R, PARTICLE_R) * LEGEND_ICON
    icon_w = (x1 - x0) - 2 * LEGEND_PAD - LEGEND_GAP - max(text_width(f, label) for _, label, _ in entries)
    for k, (color, label, alpha) in enumerate(entries):
        cy = y0 + LEGEND_PAD + icon_r + k * LEGEND_ROW
        if label in arrows:
            mid, half = x0 + LEGEND_PAD + icon_w / 2, icon_r * 1.6
            draw_handoff(canvas, (mid - half, cy), (mid + half, cy), color, alpha, size=LEGEND_ICON)
        else:
            draw_kind(canvas, (x0 + LEGEND_PAD + icon_w / 2, cy), color, label, alpha, LEGEND_ICON)
        draw_text(canvas, label, x0 + LEGEND_PAD + icon_w + LEGEND_GAP, cy + cap_height(f) / 2, f,
                  with_alpha(0xFFFFFFFF, alpha))


def draw_marker(canvas: skia.Canvas, at: tuple[float, float], color: str, label: str, alpha: float,
                size: float = 1.0) -> None:
    """A particle marked with what it is: a disc for "H⁺", a pill for "glucose", in its colour
    with a white halo, the word in dark or white ink, whichever reads on it. `size` scales it. Drawn once into an
    image for each colour, word and size, then stamped: a filling space shows a couple of dozen at a time, and
    drawing each one's shadow, halo and word afresh on every frame cost about 40 ms a frame."""
    if alpha <= 0.01:
        return
    sprite = _marker_sprite(color, label, size)
    margin = MARKER_MARGIN
    canvas.drawImage(sprite, at[0] - sprite.width() / 2, at[1] - (sprite.height() - 2 * margin) / 2 - margin,
                     skia.SamplingOptions(skia.FilterMode.kLinear), skia.Paint(Alphaf=alpha))


MARKER_MARGIN = 12  # pixels round a marker's sprite for its shadow, which falls a little below it
_SPRITES: dict[tuple[str, str, float], skia.Image] = {}


def _marker_sprite(color: str, label: str, size: float) -> skia.Image:
    """The marker drawn on its own, centred, at full strength: what draw_marker stamps."""
    key = (color, label, size)
    if key in _SPRITES:
        return _SPRITES[key]
    r, pad = MARKER_R * size, MARKER_PAD * size
    f = font("ExtraBold", MARKER_FONT * size)
    width = text_width(f, label)
    half = max(r, width / 2 + pad)
    margin = MARKER_MARGIN
    w, h = math.ceil(2 * (half + HALO + margin)), math.ceil(2 * (r + HALO + margin))
    surface = skia.Surface(w, h)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorTRANSPARENT)
    x, y = w / 2, h / 2
    body = rounded((x - half, y - r, x + half, y + r), r)
    shadow(canvas, body, 1.0)
    canvas.drawRRect(rounded((x - half - HALO, y - r - HALO, x + half + HALO, y + r + HALO),
                             r + HALO), skia.Paint(AntiAlias=True, Color=0xFFFFFFFF))
    canvas.drawRRect(body, skia.Paint(AntiAlias=True, Color=argb(color)))
    ink = "#1A1A1A" if _luminance(*rgb(color)) > 0.3 else "#FFFFFF"
    draw_text(canvas, label, x - width / 2, y + cap_height(f) / 2, f, argb(ink))
    _SPRITES[key] = surface.makeImageSnapshot()
    return _SPRITES[key]


CONVERT_FONT = 30.0
CONVERT_PAD = 16.0


def conversion_size(text: str, font_size: float = CONVERT_FONT) -> tuple[float, float]:
    return text_width(font("Bold", font_size), text) + 2 * CONVERT_PAD, font_size * 1.7


def draw_conversion(canvas: skia.Canvas, centre: tuple[float, float], text: str, color: str, alpha: float,
                    bounds: Rect, font_size: float = CONVERT_FONT) -> None:
    """What a structure turns into what, "H₂O → O₂ + H⁺ + e⁻", on a white pill edged in its colour, centred on
    `centre` and kept inside `bounds`."""
    if alpha <= 0.01:
        return
    f = font("Bold", font_size)
    width, height = conversion_size(text, font_size)
    x = min(max(centre[0], bounds[0] + width / 2), bounds[2] - width / 2)
    y = min(max(centre[1], bounds[1] + height / 2), bounds[3] - height / 2)
    body = rounded((x - width / 2, y - height / 2, x + width / 2, y + height / 2), height / 2)
    shadow(canvas, body, alpha)
    canvas.drawRRect(body, skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, 0.96 * alpha)))
    canvas.drawRRect(body, skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=3.5,
                                      Color=argb(color, alpha)))
    draw_text(canvas, text, x - width / 2 + CONVERT_PAD, y + cap_height(f) / 2, f, argb("#1A1A1A", alpha))


ARROW_SWEEP = 280.0  # degrees of the ellipse a turning arrow covers
ARROW_STROKE = 6.0  # frame pixels, inside a dark edge ARROW_EDGE wider
ARROW_EDGE = 4.0
ARROW_HEAD = 15.0
ARROW_BEHIND = 0.35  # how strong the arrow is where it passes behind the part


def draw_turning_arrow(canvas: skia.Canvas, centre: tuple[float, float], rx: float, ry: float, turns: float,
                       alpha: float) -> None:
    """A curved arrow round an ellipse, the way a textbook shows a rotor turning about an axis in the page:
    its far half (the top) faint, as if behind the part; it goes round as the part turns."""
    cx, cy = centre
    start = 360.0 * turns
    steps = 48
    points = [(cx + rx * math.cos(math.radians(start + ARROW_SWEEP * k / steps)),
               cy + ry * math.sin(math.radians(start + ARROW_SWEEP * k / steps))) for k in range(steps + 1)]
    for width, color in ((ARROW_STROKE + ARROW_EDGE, 0xFF1A1A1A), (ARROW_STROKE, 0xFFFFFFFF)):
        paint = skia.Paint(AntiAlias=True, StrokeWidth=width, Style=skia.Paint.kStroke_Style,
                           StrokeCap=skia.Paint.kRound_Cap)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            behind = (y0 + y1) / 2 < cy
            paint.setColor(with_alpha(color, alpha * (ARROW_BEHIND if behind else 1.0) * (0.6 if color == 0xFF1A1A1A else 1)))
            canvas.drawLine(x0, y0, x1, y1, paint)
    (x0, y0), (x1, y1) = points[-2], points[-1]
    length = math.hypot(x1 - x0, y1 - y0) or 1.0
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    head = skia.Path()
    head.moveTo(x1 + ux * ARROW_HEAD, y1 + uy * ARROW_HEAD)
    head.lineTo(x1 - uy * ARROW_HEAD * 0.7, y1 + ux * ARROW_HEAD * 0.7)
    head.lineTo(x1 + uy * ARROW_HEAD * 0.7, y1 - ux * ARROW_HEAD * 0.7)
    head.close()
    behind = y1 < cy
    shade = ARROW_BEHIND if behind else 1.0
    canvas.drawPath(head, skia.Paint(AntiAlias=True, Color=with_alpha(0xFF1A1A1A, 0.6 * alpha * shade),
                                     Style=skia.Paint.kStrokeAndFill_Style, StrokeWidth=ARROW_EDGE))
    canvas.drawPath(head, skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, alpha * shade)))


HANDOFF_WIDTH = 10.0  # a hand-off arrow's shaft, in frame pixels, inside a white halo HALO wide each side
HANDOFF_HEAD = 26.0  # its head, from the tip back
HANDOFF_PULSE = 0.25  # how much thicker it is as the pulse passes


def draw_handoff(canvas: skia.Canvas, tail: tuple[float, float], tip: tuple[float, float], color: str, alpha: float,
                 grown: float = 1.0, pulse: float = 0.0, size: float = 1.0) -> None:
    """A straight arrow where a flow passes from one part into the next, as a textbook draws one: in the flow's
    colour inside a white halo, so it reads on any part; drawn `grown` (0-1) of the way from its tail, `pulse` (0-1)
    thicker as the flow passes along it; `size` scales it (the legend's)."""
    if alpha <= 0.01 or grown <= 0.01:
        return
    (x0, y0), (x1, y1) = tail, tip
    x1, y1 = x0 + (x1 - x0) * grown, y0 + (y1 - y0) * grown
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1.0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    width = HANDOFF_WIDTH * size * (1 + HANDOFF_PULSE * pulse)
    head = min(HANDOFF_HEAD * size * (1 + HANDOFF_PULSE * pulse), length)
    base = (x1 - ux * head, y1 - uy * head)
    shape = skia.Path()
    shape.moveTo(x1, y1)
    shape.lineTo(base[0] - uy * head * 0.62, base[1] + ux * head * 0.62)
    shape.lineTo(base[0] + uy * head * 0.62, base[1] - ux * head * 0.62)
    shape.close()
    for ink, extra in ((with_alpha(0xFFFFFFFF, 0.9 * alpha), 2 * HALO * size), (argb(color, alpha), 0.0)):
        stroke = skia.Paint(AntiAlias=True, Color=ink, StrokeWidth=width + extra, Style=skia.Paint.kStroke_Style,
                            StrokeCap=skia.Paint.kRound_Cap)
        canvas.drawLine(x0, y0, base[0] + ux * 2, base[1] + uy * 2, stroke)
        canvas.drawPath(shape, skia.Paint(AntiAlias=True, Color=ink, StrokeWidth=extra,
                                          Style=skia.Paint.kStrokeAndFill_Style if extra else skia.Paint.kFill_Style,
                                          StrokeJoin=skia.Paint.kRound_Join))


BLOCK_R = 17.0  # the cross on a blocker, in frame pixels


def draw_block(canvas: skia.Canvas, at: tuple[float, float], alpha: float) -> None:
    """A red disc with a white cross, where blocked particles stop."""
    if alpha <= 0.01:
        return
    x, y = at
    canvas.drawCircle(x, y, BLOCK_R + HALO, skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, alpha)))
    canvas.drawCircle(x, y, BLOCK_R, skia.Paint(AntiAlias=True, Color=argb("#E5484D", alpha)))
    arm = BLOCK_R * 0.5
    cross = skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, alpha), StrokeWidth=4.5,
                       Style=skia.Paint.kStroke_Style, StrokeCap=skia.Paint.kRound_Cap)
    canvas.drawLine(x - arm, y - arm, x + arm, y + arm, cross)
    canvas.drawLine(x - arm, y + arm, x + arm, y - arm, cross)


CROWD_R = 8.5  # a crowd's unmarked dots, in frame pixels: bigger than the stream's, smaller than the lead's


def draw_crowd_dot(canvas: skia.Canvas, at: tuple[float, float], color: str, alpha: float) -> None:
    """One of a crowd's dots: its colour in a white ring, as the particles have."""
    canvas.drawCircle(at[0], at[1], CROWD_R + HALO, skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, 0.85 * alpha)))
    canvas.drawCircle(at[0], at[1], CROWD_R, skia.Paint(AntiAlias=True, Color=argb(color, alpha)))


# --- the lyric band ------------------------------------------------------------------------


@dataclass
class BandWord:
    text: str
    start: float
    end: float
    color: str | None = None  # a structure's colour, for the words that name it


def draw_band(canvas: skia.Canvas, rect: Rect, words: list[BandWord], t: float, font_size: float,
              alpha: float = 1.0, lines: int = 1) -> None:
    """The white band at the bottom with the line centred and the word being sung marked: on one row, or, when it
    is too wide and the band allows `lines` rows, wrapped onto rows of about the same width; only a line still too
    wide shrinks."""
    x0, y0, x1, y1 = rect
    canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y1), skia.Paint(Color=0xFFFFFFFF))
    canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y0 + 2), skia.Paint(Color=HAIRLINE))
    if not words or alpha <= 0.01:
        return
    room = (x1 - x0) - 160
    size = font_size
    f = font("Bold", size)
    space = text_width(f, " ")
    widths = [text_width(f, w.text) for w in words]
    total = sum(widths) + space * (len(words) - 1)
    rows = [list(range(len(words)))]
    if total > room and lines > 1:
        # Break where the rows come out closest to equal: the fewest rows that fit, up to `lines`.
        n = min(lines, max(2, math.ceil(total / room)))
        rows, row, run = [], [], 0.0
        for i, width in enumerate(widths):
            if row and run + space + width > total / n + width / 2 and len(rows) < n - 1:
                rows.append(row)
                row, run = [], 0.0
            run += (space if row else 0) + width
            row.append(i)
        rows.append(row)
    widest = max(sum(widths[i] for i in row) + space * (len(row) - 1) for row in rows)
    if widest > room:
        size = max(font_size * room / widest, 24)
        f = font("Bold", size)
        space = text_width(f, " ")
        widths = [text_width(f, w.text) for w in words]
    step = size * 1.35
    first = (y0 + y1) / 2 - step * (len(rows) - 1) / 2 + cap_height(f) / 2 + 1
    # The word being sung is the last one started; it stays marked until the next starts.
    current = max((i for i, w in enumerate(words) if w.start <= t), default=None)
    if current is not None and t > words[current].end + 0.6:
        current = None  # a held breath after the last word
    for r, row in enumerate(rows):
        baseline = first + r * step
        x = (x0 + x1) / 2 - (sum(widths[i] for i in row) + space * (len(row) - 1)) / 2
        for i in row:
            w, tw = words[i], widths[i]
            sung = w.start <= t
            if i == current:
                grow = ease_out((t - w.start) / 0.12)
                pad = size * 0.22
                box = (x - pad, baseline - cap_height(f) - pad * 1.1, x + tw + pad, baseline + pad * 0.9)
                canvas.drawRRect(rounded(box, pad * 1.2),
                                 skia.Paint(AntiAlias=True, Color=with_alpha(MARKER, alpha * grow)))
            if w.color and sung:
                color = argb(darken(w.color), alpha)
            else:
                color = with_alpha(INK if sung else MUTED, alpha)
            draw_text(canvas, w.text, x, baseline, f, color)
            x += tw + space


def draw_title(canvas: skia.Canvas, rect: Rect, text: str, font_size: float) -> None:
    """The title band along the top of an upright frame: the topic, white on the art's dark slate, centred, on up
    to two lines."""
    x0, y0, x1, y1 = rect
    canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y1), skia.Paint(Color=argb(TITLE_BG)))
    if not text:
        return
    f = font("ExtraBold", font_size)
    rows = wrap(text, f, (x1 - x0) - 120)[:2]
    step = font_size * 1.2
    first = (y0 + y1) / 2 - step * (len(rows) - 1) / 2 + cap_height(f) / 2
    for r, row in enumerate(rows):
        draw_text(canvas, row, (x0 + x1) / 2 - text_width(f, row) / 2, first + r * step, f, 0xFFFFFFFF)


TITLE_BG = "#34435E"  # a shade under the art's dark slate blue, so the title sits on it as a band
KEY_TERM = 0xFFFCD34D  # amber-300: a key term in a key line, so the words the learner must know stand out
KICKER = 0xFFCBD5E1  # slate-300: the topic, small, above the key line in an upright title band


def key_term_spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    """Where the key terms are in a key line, longest first and never overlapping, ignoring case."""
    spans: list[tuple[int, int]] = []
    low = text.casefold()
    for term in sorted({t.strip() for t in terms if t.strip()}, key=len, reverse=True):
        start = 0
        while (at := low.find(term.casefold(), start)) >= 0:
            end = at + len(term)
            whole = (at == 0 or not low[at - 1].isalnum()) and (end == len(low) or not low[end].isalnum())
            if whole and all(end <= a or at >= b for a, b in spans):
                spans.append((at, end))
            start = end
    return sorted(spans)


def draw_key_line(canvas: skia.Canvas, rect: Rect, text: str, terms: list[str], font_size: float, alpha: float,
                  rows: int = 2) -> None:
    """A key line centred in rect, white with its key terms in amber: on one row, or wrapped onto up to `rows` rows
    of about the same width; only a line still too wide shrinks."""
    words = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if not words or alpha <= 0.01:
        return
    x0, y0, x1, y1 = rect
    room = (x1 - x0) - 2 * KEY_PAD
    spans = key_term_spans(text, terms)
    marked = [any(a < end and start < b for a, b in spans) for _, start, end in words]
    size = font_size
    while True:
        f = font("ExtraBold", size)
        space = text_width(f, " ")
        widths = [text_width(f, w) for w, _, _ in words]
        total = sum(widths) + space * (len(words) - 1)
        n = min(rows, max(1, math.ceil(total / room)))
        lines, line, run = [], [], 0.0
        for i, width in enumerate(widths):
            if line and run + space + width > total / n + width / 2 and len(lines) < n - 1:
                lines.append(line)
                line, run = [], 0.0
            run += (space if line else 0) + width
            line.append(i)
        lines.append(line)
        widest = max(sum(widths[i] for i in ln) + space * (len(ln) - 1) for ln in lines)
        step = size * 1.22
        if (widest <= room and step * len(lines) <= (y1 - y0)) or size <= KEY_MIN_FONT:
            break
        size = max(KEY_MIN_FONT, size * min(room / widest, (y1 - y0) / (step * len(lines)), 0.95))
    first = (y0 + y1) / 2 - step * (len(lines) - 1) / 2 + cap_height(f) / 2
    for r, ln in enumerate(lines):
        width = sum(widths[i] for i in ln) + space * (len(ln) - 1)
        x = (x0 + x1) / 2 - width / 2
        for i in ln:
            draw_text(canvas, words[i][0], x, first + r * step, f, with_alpha(KEY_TERM if marked[i] else 0xFFFFFFFF, alpha))
            x += widths[i] + space


def draw_key_band(canvas: skia.Canvas, rect: Rect, text: str, terms: list[str], font_size: float, alpha: float) -> None:
    """The band just above the lyric band that holds the scene's key line, on the title band's slate."""
    x0, y0, x1, y1 = rect
    canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y1), skia.Paint(Color=argb(TITLE_BG)))
    draw_key_line(canvas, (x0, y0 + 6, x1, y1 - 6), text, terms, font_size, alpha)


KEY_PAD = 60  # frame px a key line keeps from each side
KEY_MIN_FONT = 26


# --- the caption pill ----------------------------------------------------------------------


NOTICE_ACCENT = 0xFFF59E0B  # amber: the side of a notice card, and its warning sign
NOTICE_SUBTLE = 0xFF475569  # slate-600
NOTICE_TITLE = "AI-generated picture"
NOTICE_LEAD = "A check found these mistakes:"
NOTICE_LINES = 6  # lines a note may take on the card


def draw_notice(canvas: skia.Canvas, x0: float, top: float, x1: float, notes: list[str], font_size: float,
                alpha: float = 1.0, more: int = 0) -> float:
    """A white card telling the viewer the picture beside it is AI-made and what a check found wrong in it, a
    bullet per note (and how many more were left out). Returns the card's bottom."""
    if alpha <= 0.01:
        return top
    pad, bar = 22.0, 7.0
    title, body = font("ExtraBold", font_size * 1.15), font("SemiBold", font_size)
    line_h = font_size * 1.32
    inner = x1 - x0 - 2 * pad - bar
    bullet = text_width(body, "•  ")
    rows: list[tuple[str, float, skia.Font, int]] = []  # text, indent, font, colour
    for note in notes:
        lines = wrap(note, body, inner - bullet)
        if len(lines) > NOTICE_LINES:  # a long note is cut, so the card stays above the lyric band
            lines = lines[:NOTICE_LINES]
            lines[-1] = lines[-1].rstrip(" .,;:") + "…"
        for k, line in enumerate(lines):
            rows.append((("•  " if k == 0 else "") + line, 0.0 if k == 0 else bullet, body, INK))
    if more:
        rows.append((f"and {more} more", 0.0, body, NOTICE_SUBTLE))
    lead = wrap(NOTICE_LEAD, body, inner)
    sign = font_size * 1.15
    height = pad + sign + 12 + len(lead) * line_h + 8 + len(rows) * line_h + pad - 6
    rect = (x0, top, x1, top + height)
    rrect = rounded(rect, 16)
    shadow(canvas, rrect, alpha, blur=12, dy=4)
    canvas.drawRRect(rrect, skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, alpha * 0.97)))
    canvas.save()
    canvas.clipRRect(rrect, doAntiAlias=True)
    canvas.drawRect(skia.Rect.MakeLTRB(x0, top, x0 + bar, top + height), skia.Paint(Color=with_alpha(NOTICE_ACCENT, alpha)))
    canvas.restore()
    x, y = x0 + bar + pad, top + pad
    warning_sign(canvas, x, y, sign, alpha)
    draw_text(canvas, NOTICE_TITLE, x + sign + 12, y + sign * 0.5 + cap_height(title) / 2, title, with_alpha(INK, alpha))
    y += sign + 12
    for line in lead:
        y += line_h
        draw_text(canvas, line, x, y - line_h * 0.3, body, with_alpha(NOTICE_SUBTLE, alpha))
    y += 8
    for text, indent, f, color in rows:
        y += line_h
        draw_text(canvas, text, x + indent, y - line_h * 0.3, f, with_alpha(color, alpha))
    return top + height


def warning_sign(canvas: skia.Canvas, x: float, y: float, size: float, alpha: float, edge: float = 0.0) -> None:
    """The warning sign, its top-left corner at (x, y): an amber triangle with a dark "!", in a dark edge this
    wide where it sits on a picture."""
    path = skia.Path()
    path.moveTo(x + size / 2, y)
    path.lineTo(x + size, y + size * 0.9)
    path.lineTo(x, y + size * 0.9)
    path.close()
    if edge:
        canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=with_alpha(INK, alpha), StrokeWidth=2 * edge,
                                         Style=skia.Paint.kStroke_Style, StrokeJoin=skia.Paint.kRound_Join))
    canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=with_alpha(NOTICE_ACCENT, alpha)))
    bang = font("ExtraBold", size * 0.62)
    draw_text(canvas, "!", x + size / 2 - text_width(bang, "!") / 2, y + size * 0.78, bang, with_alpha(INK, alpha))


FLAG_PAD = 6.0  # frame pixels between a flagged label and the ring round it
FLAG_RING = 4.0  # the ring's amber stroke, inside a dark edge FLAG_EDGE wider each side, so it reads on any picture
FLAG_EDGE = 2.0


def flag_ring(rect: Rect) -> Rect:
    """The ring round a flagged label's box (frame pixels), as draw_flag draws it."""
    x0, y0, x1, y1 = rect
    return x0 - FLAG_PAD, y0 - FLAG_PAD, x1 + FLAG_PAD, y1 + FLAG_PAD


def draw_flag(canvas: skia.Canvas, rect: Rect, alpha: float) -> None:
    """A label the art check found wrong in a painted picture, ringed in amber where it is; its post-it says why."""
    if alpha <= 0.01:
        return
    ring = rounded(flag_ring(rect), 10)
    canvas.drawRRect(ring, skia.Paint(AntiAlias=True, Color=with_alpha(INK, alpha * 0.8),
                                      StrokeWidth=FLAG_RING + 2 * FLAG_EDGE, Style=skia.Paint.kStroke_Style))
    canvas.drawRRect(ring, skia.Paint(AntiAlias=True, Color=with_alpha(NOTICE_ACCENT, alpha), StrokeWidth=FLAG_RING,
                                      Style=skia.Paint.kStroke_Style))


POSTIT_FILL = 0xFFFFE27A  # a sticky note's yellow
POSTIT_GLUE = 0xFFF5D25A  # the strip along its top, a shade darker, where it sticks
POSTIT_FONT = 21.0
POSTIT_PAD = 14.0
POSTIT_TEXT_W = 210.0  # the widest a line of its note runs, frame pixels
POSTIT_TILT = -2.0  # degrees: stuck on by hand
POSTIT_LINES = 4


def postit_size(note: str, font_size: float = POSTIT_FONT) -> tuple[float, float]:
    """How wide and tall a post-it with this note is, in frame pixels."""
    f = font("SemiBold", font_size)
    lines = wrap(note, f, POSTIT_TEXT_W)[:POSTIT_LINES] or [""]
    width = max(text_width(f, line) for line in lines)
    return max(width, 3 * font_size) + 2 * POSTIT_PAD, len(lines) * font_size * 1.3 + 2 * POSTIT_PAD + 6


def draw_postit(canvas: skia.Canvas, rect: Rect, note: str, alpha: float, font_size: float = POSTIT_FONT) -> None:
    """A sticky note with the note on it, filling `rect` (frame pixels), tilted a little about its middle, with a
    soft shadow and the darker strip it sticks by along its top. A note past POSTIT_LINES lines is cut short."""
    if alpha <= 0.01:
        return
    x0, y0, x1, y1 = rect
    f = font("SemiBold", font_size)
    lines = wrap(note, f, POSTIT_TEXT_W)
    if len(lines) > POSTIT_LINES:
        lines = lines[:POSTIT_LINES]
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    canvas.save()
    canvas.rotate(POSTIT_TILT, (x0 + x1) / 2, (y0 + y1) / 2)
    body = rounded(rect, 3)
    shadow(canvas, body, alpha * 0.9, blur=10, dy=5)
    canvas.drawRRect(body, skia.Paint(AntiAlias=True, Color=with_alpha(POSTIT_FILL, alpha)))
    canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y0 + 6), skia.Paint(AntiAlias=True, Color=with_alpha(POSTIT_GLUE, alpha)))
    line_h = font_size * 1.3
    for k, line in enumerate(lines):
        draw_text(canvas, line, x0 + POSTIT_PAD, y0 + 6 + POSTIT_PAD + (k + 0.78) * line_h, f, with_alpha(INK, alpha))
    canvas.restore()


def draw_postit_line(canvas: skia.Canvas, start: tuple[float, float], end: tuple[float, float], alpha: float) -> None:
    """The line from a post-it to the ring of the label it is about, where the post-it sits away from it."""
    if alpha <= 0.01:
        return
    for color, width in ((with_alpha(INK, alpha * 0.8), 4.5), (with_alpha(NOTICE_ACCENT, alpha), 2.5)):
        canvas.drawLine(*start, *end, skia.Paint(AntiAlias=True, Color=color, StrokeWidth=width,
                                                 StrokeCap=skia.Paint.kRound_Cap))


def ai_tag_rect(x0: float, bottom: float, font_size: float) -> Rect:
    """Where draw_ai_tag draws its pill."""
    h = font_size * 1.7
    return (x0, bottom - h, x0 + text_width(font("SemiBold", font_size), NOTICE_TITLE) + font_size * 1.6, bottom)


def draw_ai_tag(canvas: skia.Canvas, x0: float, bottom: float, font_size: float, alpha: float = 1.0) -> float:
    """A small dark pill reading "AI-generated picture", its bottom-left corner at (x0, bottom). Returns its top."""
    if alpha <= 0.01:
        return bottom
    f = font("SemiBold", font_size)
    _, top, right, _ = ai_tag_rect(x0, bottom, font_size)
    h = bottom - top
    rrect = rounded((x0, top, right, bottom), h / 2)
    canvas.drawRRect(rrect, skia.Paint(AntiAlias=True, Color=with_alpha(INK, alpha * 0.72)))
    draw_text(canvas, NOTICE_TITLE, x0 + font_size * 0.8, bottom - h / 2 + cap_height(f) / 2, f,
              with_alpha(0xFFFFFFFF, alpha * 0.92))
    return bottom - h


BRAND_CHIP = 0xFF0B7499  # the site's wordmark chip (web: --brand-chip)


def brand_size(font_size: float) -> tuple[float, float]:
    """How wide and tall the brand is, its text this size."""
    width = text_width(font("ExtraBold", font_size), "POP") + text_width(font("Regular", font_size), "smosis")
    return width + 2 * 0.55 * font_size, 1.6 * font_size


def draw_brand(canvas: skia.Canvas, x0: float, top: float, font_size: float) -> None:
    """The site's wordmark (web/src/components/Wordmark.tsx), its top-left corner at (x0, top): "POP" heavy and
    "smosis" lighter, white on the aqua chip."""
    w, h = brand_size(font_size)
    canvas.drawRRect(rounded((x0, top, x0 + w, top + h), 0.3 * font_size),
                     skia.Paint(AntiAlias=True, Color=BRAND_CHIP))
    heavy, light = font("ExtraBold", font_size), font("Regular", font_size)
    x, baseline = x0 + 0.55 * font_size, top + h / 2 + cap_height(heavy) / 2
    draw_text(canvas, "POP", x, baseline, heavy, 0xFFFFFFFF)
    draw_text(canvas, "smosis", x + text_width(heavy, "POP"), baseline, light, with_alpha(0xFFFFFFFF, 0.8))


def draw_caption(canvas: skia.Canvas, text: str, centre_x: float, top: float, font_size: float, alpha: float) -> None:
    if alpha <= 0.01:
        return
    f = font("ExtraBold", font_size)
    tw = text_width(f, text)
    h = font_size * 1.75
    y = top - 10 * (1 - ease_out(alpha))
    rect = (centre_x - tw / 2 - 30, y, centre_x + tw / 2 + 30, y + h)
    rrect = rounded(rect, h / 2)
    shadow(canvas, rrect, alpha, blur=12, dy=4)
    canvas.drawRRect(rrect, skia.Paint(AntiAlias=True, Color=with_alpha(INK, alpha * 0.94)))
    draw_text(canvas, text, centre_x - tw / 2, y + h / 2 + cap_height(f) / 2, f, with_alpha(0xFFFFFFFF, alpha))
