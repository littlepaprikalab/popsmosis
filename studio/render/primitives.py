"""The renderer's drawing pieces (docs/PLAN.md, section 7): easing, colours, text,
labels and their placement, the lyric band and the caption pill.

Everything here draws onto a skia canvas in frame pixels and keeps no state between
frames, so a frame depends only on its time.
"""

from __future__ import annotations

import functools
import math
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


# --- text ---------------------------------------------------------------------------------


@functools.cache
def typeface(weight: str) -> skia.Typeface:
    """Inter Bold, SemiBold or ExtraBold, from studio/render/fonts/."""
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
LEADER = 36.0  # from the structure's edge to the pill, before the placement pass
MAX_REACH = 200.0  # a big structure's label stays this close to its dot, on top of it
GAP = 12.0  # between pills


@dataclass
class Label:
    name: str
    color: str  # the structure's #RRGGBB
    anchor: tuple[float, float]  # the dot, in frame pixels
    box: Rect  # the structure's box, in frame pixels: the pill starts just outside it
    alpha: float
    emphasis: float = 0.0  # 1 while spotlit: a little larger
    x: float = 0.0  # the pill's centre, set by place_labels
    y: float = 0.0

    def size(self, font_size: float) -> tuple[float, float]:
        scale = 1 + 0.08 * self.emphasis
        f = font("Bold", font_size * scale)
        return text_width(f, self.name) + 2 * LABEL_PAD_X * scale, LABEL_H * scale


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
        reach = out + LEADER + min(abs(w / 2 / dx) if abs(dx) > 1e-6 else 1e9, abs(h / 2 / dy) if abs(dy) > 1e-6 else 1e9)
        homes.append((lb.anchor[0] + dx * reach, lb.anchor[1] + dy * reach))
    pos = [list(h) for h in homes]
    x0, y0, x1, y1 = bounds

    def clamp(i: int) -> None:
        w, h = sizes[i]
        pos[i][0] = min(max(pos[i][0], x0 + w / 2), x1 - w / 2)
        pos[i][1] = min(max(pos[i][1], y0 + h / 2), y1 - h / 2)

    for i in range(len(labels)):
        clamp(i)
    dots = [lb.anchor for lb in labels] + list(avoid)
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
    color = argb(lb.color, lb.alpha)
    ink = argb(darken(lb.color), lb.alpha)  # the leader and dot sit on the structure, so they are darker
    ax, ay = lb.anchor
    # The leader runs from the dot to the nearest point on the pill.
    ex, ey = min(max(ax, rect[0] + h / 2), rect[2] - h / 2), min(max(ay, rect[1]), rect[3])
    line = skia.Paint(AntiAlias=True, Color=ink, StrokeWidth=3.0, Style=skia.Paint.kStroke_Style,
                      StrokeCap=skia.Paint.kRound_Cap)
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


# --- particles ------------------------------------------------------------------------------

PARTICLE_R = 13.0  # the lead particle, in frame pixels
STREAM_R = 5.5  # the dots streaming along the path
HALO = 2.5  # a white ring, as a label's dot has, so a particle reads on any colour


MARKER_R = 22.0  # a marked lead particle: room for "H⁺" inside it, in frame pixels
MARKER_FONT = 24.0
MARKER_PAD = 8.0  # a longer marker ("glucose") is a pill this far wider than its word, each side


def draw_particles(canvas: skia.Canvas, color: str, stream: list[tuple[tuple[float, float], float]],
                   lead: tuple[float, float], trail: list[tuple[float, float]], alpha: float, label: str = "") -> None:
    """What travels between structures: dots streaming along the path (each with its own fade,
    0-1), and the lead particle with its trail, the newest position first; marked with what it
    is when there is a label."""
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
    if label:
        draw_marker(canvas, lead, color, label, alpha)
        return
    halo.setColor(with_alpha(0xFFFFFFFF, alpha))
    canvas.drawCircle(lead[0], lead[1], PARTICLE_R + HALO, halo)
    canvas.drawCircle(lead[0], lead[1], PARTICLE_R, fill)
    ring = skia.Paint(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=2.5,
                      Color=argb(darken(color, 3.0), alpha))
    canvas.drawCircle(lead[0], lead[1], PARTICLE_R - 1.25, ring)


def draw_marker(canvas: skia.Canvas, at: tuple[float, float], color: str, label: str, alpha: float) -> None:
    """The lead particle marked with what it is: a disc for "H⁺", a pill for "glucose", in its colour
    with a white halo, the word in dark or white ink, whichever reads on it."""
    x, y = at
    f = font("ExtraBold", MARKER_FONT)
    width = text_width(f, label)
    half = max(MARKER_R, width / 2 + MARKER_PAD)
    body = rounded((x - half, y - MARKER_R, x + half, y + MARKER_R), MARKER_R)
    shadow(canvas, body, alpha)
    canvas.drawRRect(rounded((x - half - HALO, y - MARKER_R - HALO, x + half + HALO, y + MARKER_R + HALO),
                             MARKER_R + HALO), skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, alpha)))
    canvas.drawRRect(body, skia.Paint(AntiAlias=True, Color=argb(color, alpha)))
    ink = "#1A1A1A" if _luminance(*rgb(color)) > 0.3 else "#FFFFFF"
    draw_text(canvas, label, x - width / 2, y + cap_height(f) / 2, f, argb(ink, alpha))


# --- the lyric band ------------------------------------------------------------------------


@dataclass
class BandWord:
    text: str
    start: float
    end: float
    color: str | None = None  # a structure's colour, for the words that name it


def draw_band(canvas: skia.Canvas, rect: Rect, words: list[BandWord], t: float, font_size: float,
              alpha: float = 1.0) -> None:
    """The white band at the bottom with the line centred and the word being sung marked."""
    x0, y0, x1, y1 = rect
    canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y1), skia.Paint(Color=0xFFFFFFFF))
    canvas.drawRect(skia.Rect.MakeLTRB(x0, y0, x1, y0 + 2), skia.Paint(Color=HAIRLINE))
    if not words or alpha <= 0.01:
        return
    size = font_size
    f = font("Bold", size)
    space = text_width(f, " ")
    total = sum(text_width(f, w.text) for w in words) + space * (len(words) - 1)
    room = (x1 - x0) - 160
    if total > room:
        size = max(font_size * room / total, 24)
        f = font("Bold", size)
        space = text_width(f, " ")
        total = sum(text_width(f, w.text) for w in words) + space * (len(words) - 1)
    baseline = (y0 + y1) / 2 + cap_height(f) / 2 + 1
    # The word being sung is the last one started; it stays marked until the next starts.
    current = max((i for i, w in enumerate(words) if w.start <= t), default=None)
    if current is not None and t > words[current].end + 0.6:
        current = None  # a held breath after the last word
    x = (x0 + x1) / 2 - total / 2
    for i, w in enumerate(words):
        tw = text_width(f, w.text)
        sung = w.start <= t
        if i == current:
            grow = ease_out((t - w.start) / 0.12)
            pad = size * 0.22
            box = (x - pad, baseline - cap_height(f) - pad * 1.1, x + tw + pad, baseline + pad * 0.9)
            canvas.drawRRect(rounded(box, pad * 1.2), skia.Paint(AntiAlias=True, Color=with_alpha(MARKER, alpha * grow)))
        if w.color and sung:
            color = argb(darken(w.color), alpha)
        else:
            color = with_alpha(INK if sung else MUTED, alpha)
        draw_text(canvas, w.text, x, baseline, f, color)
        x += tw + space


# --- the caption pill ----------------------------------------------------------------------


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
