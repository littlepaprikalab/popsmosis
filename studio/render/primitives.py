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


def draw_marker(canvas: skia.Canvas, at: tuple[float, float], color: str, label: str, alpha: float,
                size: float = 1.0) -> None:
    """A particle marked with what it is: a disc for "H⁺", a pill for "glucose", in its colour
    with a white halo, the word in dark or white ink, whichever reads on it. `size` scales it (a crowd's
    marked dots are smaller than the lead particle)."""
    x, y = at
    r, pad = MARKER_R * size, MARKER_PAD * size
    f = font("ExtraBold", MARKER_FONT * size)
    width = text_width(f, label)
    half = max(r, width / 2 + pad)
    body = rounded((x - half, y - r, x + half, y + r), r)
    shadow(canvas, body, alpha)
    canvas.drawRRect(rounded((x - half - HALO, y - r - HALO, x + half + HALO, y + r + HALO),
                             r + HALO), skia.Paint(AntiAlias=True, Color=with_alpha(0xFFFFFFFF, alpha)))
    canvas.drawRRect(body, skia.Paint(AntiAlias=True, Color=argb(color, alpha)))
    ink = "#1A1A1A" if _luminance(*rgb(color)) > 0.3 else "#FFFFFF"
    draw_text(canvas, label, x - width / 2, y + cap_height(f) / 2, f, argb(ink, alpha))


CONVERT_FONT = 30.0
CONVERT_PAD = 16.0


def draw_conversion(canvas: skia.Canvas, centre: tuple[float, float], text: str, color: str, alpha: float,
                    bounds: Rect, font_size: float = CONVERT_FONT) -> None:
    """What a structure turns into what, "H₂O → O₂ + H⁺ + e⁻", on a white pill edged in its colour, centred on
    `centre` and kept inside `bounds`."""
    if alpha <= 0.01:
        return
    f = font("Bold", font_size)
    width, height = text_width(f, text) + 2 * CONVERT_PAD, font_size * 1.7
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
    # The warning sign: an amber triangle with a dark "!".
    path = skia.Path()
    path.moveTo(x + sign / 2, y)
    path.lineTo(x + sign, y + sign * 0.9)
    path.lineTo(x, y + sign * 0.9)
    path.close()
    canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=with_alpha(NOTICE_ACCENT, alpha)))
    bang = font("ExtraBold", sign * 0.62)
    draw_text(canvas, "!", x + sign / 2 - text_width(bang, "!") / 2, y + sign * 0.78, bang, with_alpha(INK, alpha))
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


def draw_ai_tag(canvas: skia.Canvas, x0: float, bottom: float, font_size: float, alpha: float = 1.0) -> float:
    """A small dark pill reading "AI-generated picture", its bottom-left corner at (x0, bottom). Returns its top."""
    if alpha <= 0.01:
        return bottom
    f = font("SemiBold", font_size)
    h = font_size * 1.7
    w = text_width(f, NOTICE_TITLE) + font_size * 1.6
    rrect = rounded((x0, bottom - h, x0 + w, bottom), h / 2)
    canvas.drawRRect(rrect, skia.Paint(AntiAlias=True, Color=with_alpha(INK, alpha * 0.72)))
    draw_text(canvas, NOTICE_TITLE, x0 + font_size * 0.8, bottom - h / 2 + cap_height(f) / 2, f,
              with_alpha(0xFFFFFFFF, alpha * 0.92))
    return bottom - h


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
