"""Draws a timeline's frames (docs/PLAN.md, section 7).

Every frame depends only on its time: Renderer.frame(t) finds the beat that holds t
and draws it. Nothing carries over from the frame
before, so frame ranges render in parallel and stills cost one frame each. What does
not change between frames (each scene's art, its masks and the washes made from
them) is made once per scene and kept.

The world is the art's pixels; the camera maps it onto the frame: the diagram fills the
frame above the lyric band.
"""

from __future__ import annotations

import math
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import skia

from studio import jobs, media
from studio.render import primitives as p
from studio.render.layouts import LAYOUTS, Layout, Rect
from studio.schemas import Locations, SceneLocations, Timeline

GHOST_WASH = 0.85  # how pale an unrevealed structure is
SPOT_WASH = 0.6  # how pale everything but the spotlit structure is
EDGE_BLUR = 3.0  # sigma of a ghost's edge, in art pixels
SPOT_BLUR = 1.2  # a spotlight's edge stays crisp, so the bright structure keeps its outline
REVEAL_S = 0.4  # ghost to full
LABEL_IN_S = 0.35
SPACE_SHARE = 0.2  # a structure whose box covers this share of its picture is a space: its pill sits inside it
SPACE_REACH = 55.0  # art pixels: a path's stop in a space sits this far past its neighbour's edge
SPACE_SPAN = 0.6  # or whose box spans this share of its width (a lumen is a thin strip across the picture)
SPOT_S = 0.6
MOVE_S = 1.8  # a camera move between two stations
SETTLE_S = 2.5  # the drift after a cut, so every shot opens moving
SETTLE_ZOOM = 1.06
STAGE_FADE_S = 0.8  # one stage of a process fading into the next (Timeline.stages), in place of a cut
NOTICE_W = 440  # frame px at the right kept for a picture's notice card (Timeline.notices); the picture moves over
NOTICE_MAX = 3  # notes on the card; any more are counted
NOTICE_FONT = 22
AI_TAG_FONT = 18  # the tag on every AI-made picture (Timeline.ai_pictures) without a notice card
HOOK_ZOOM = 1.15  # frame 0 starts this far in and pulls back: motion from the first frame
FIT_PAD = 1.35  # a zoomed-in target keeps this much room around its box
WHOLE_PAD = 1.1  # the whole scene keeps this much around what is drawn: labels sit against the structures
WHOLE_MAX_ZOOM = 2.0
PORTRAIT_MAX_ZOOM = 2.6  # about two complexes of a membrane across an upright frame
PORTRAIT_PAD = 1.25  # an upright frame's whole scene leaves this much room above and below what is drawn
CAPTION_FADE_S = 0.3
KEY_FADE_S = 0.35  # a key line fades in and out this fast at the ends of its run
# In the song, the next line shows in the band this long before it is sung, unmarked and
# dimmed, as karaoke does: the eye gets to its first word as the voice does, and a singer
# who comes in early (Lyria adds a take now and then) lands on words already on screen.
LEAD_S = 1.5
BLINK_S = 0.5  # an empty band shorter than this would blink: the next line takes the whole gap
EDGE_FADE = 2 * p.DOT_R  # a label fades out as its dot nears the edge of the view, over about the dot's width
# Particles: what travels between structures (a proton, a signal), so a process moves as it is sung.
FLOW_SPEED = 260.0  # art pixels a second: slow enough to follow
FLOW_PAUSE_S = 0.5  # the lead particle holds at each stop
FLOW_MIN_LEG_S = 0.8  # a short hop still takes this long, so it reads as a move
FLOW_IN_S = 0.4
STREAM_GAP = 90.0  # art pixels between the dots streaming along the path
ROUTE_BEHIND = 0.7  # how much of a route's arrow fades where it crosses a part it goes through
ROUTE_BEND = 0.25  # a hop's curve bows out by this share of its length, at its middle
CURVE_STEPS = 24  # straight pieces a curved arrow is drawn with
ARROW_SAMPLE = 14.0  # art pixels between the points along an arrow that labels keep clear of
BADGE_APART = 70.0  # frame pixels: one marker per spot, where several routes start together
ROUTE_GAP = 10.0  # frame pixels a route's arrow stops short of where it ends in a space
ROUTE_EDGE_GAP = 4.0  # and of the edge of a part it reaches
TRAIL = 7  # the lead particle's trail: this many earlier positions...
TRAIL_DT = 0.04  # ...this many seconds apart
TRAIL_GAP = 24.0  # frame pixels: a trail dot closer than this to the one before it is left out, so the dots
# stay apart instead of smearing into a streak while the lead slows
STOP_OFFSET = 34.0  # art pixels: the lead particle holds beside a label's dot, not under it
# A space filling up (Beat.crowds): its dots, at most CROWD_MAX of them, CROWD_SPACING art pixels apart
# or more, each drifting CROWD_DRIFT pixels about its place, each written with what it is (an unmarked dot
# left the learner asking what it was), and the space tinted their colour, CROWD_TINT strong when it is full.
CROWD_MAX = 24
CROWD_SPACING = 60.0
CROWD_DRIFT = 5.0
CROWD_TINT = 0.18  # stronger, gold over a blue space turns olive
# A part that turns (Beat.turning). One that looks the same turned a quarter and a half turn (its outline
# overlaps itself so turned by SPIN_SAME or more, and its box is at most SPIN_ROUND times wider than tall, or
# taller than wide), as a wheel seen face-on does, turns in place: the disc of the picture round it. Any other
# (a rotor seen edge-on; a mushroom-shaped ATP synthase, which turned in place would stand on its head) gets a
# curved arrow going round it, ARROW_WIDTH of its width across and ARROW_TILT as deep as wide, while it turns.
# The picture decides, not the bundle: its writer never sees the picture.
SPIN_ROUND = 1.45
SPIN_SAME = 0.6
# Blocked particles (Particles.blocked) queue up behind their last stop PILE_SPACING art pixels apart, at most
# PILE_MAX of them, under a cross that shows once the first arrives.
PILE_SPACING = 26.0
PILE_MAX = 12
# A line's glow (Beat.glow): its part lit with GLOW_COLOR, blurred GLOW_BLUR art pixels past its edge, pulsing
# GLOW_HZ times a second between half and full GLOW_ALPHA.
GLOW_COLOR = "#FFF3B0"
GLOW_BLUR = 18.0
GLOW_ALPHA = 0.75
GLOW_HZ = 1.2
GLOW_IN_S = 0.4
ARROW_WIDTH = 1.2
ARROW_TILT = 0.28

Point = tuple[float, float]


class RenderError(Exception):
    """A file the renderer needs is missing or does not match the timeline."""


# --- each scene, once ----------------------------------------------------------------------


def _drawn_box(pixels: np.ndarray, located: SceneLocations, paper: tuple[int, int, int]) -> tuple[float, float, float, float]:
    """Around everything drawn on the art's background, and every located structure: what "the whole
    scene" frames."""
    drawn = np.abs(pixels[..., :3].astype(np.int16) - np.array(paper, np.int16)).max(axis=2) > 15
    rows, cols = np.flatnonzero(drawn.any(axis=1)), np.flatnonzero(drawn.any(axis=0))
    if not len(rows):
        return 0.0, 0.0, float(pixels.shape[1]), float(pixels.shape[0])
    x0, y0, x1, y1 = float(cols[0]), float(rows[0]), float(cols[-1] + 1), float(rows[-1] + 1)
    for s in located.structures:
        x0, y0, x1, y1 = min(x0, s.box.x0), min(y0, s.box.y0), max(x1, s.box.x1), max(y1, s.box.y1)
    return x0, y0, x1, y1


def _read(path: Path, color_type) -> np.ndarray:
    try:
        return skia.Image.open(str(path)).toarray(colorType=color_type)
    except (RuntimeError, ValueError) as e:
        raise RenderError(f"{path.parent.name}/{path.name} is not an image the renderer can read ({e})") from e


class SceneArt:
    """One scene's art and masks, and the washes made from them, composed on demand."""

    def __init__(self, job: Path, located: SceneLocations):
        self.located = located
        art_file = job / located.art
        if not art_file.exists():
            raise RenderError(f"no art for scene {located.scene} ({located.art})")
        pixels = _read(art_file, skia.kBGRA_8888_ColorType)
        pixels[..., 3] = 255
        self.width, self.height = pixels.shape[1], pixels.shape[0]
        b, g, r = media.paper(pixels)  # the art's background, which the washes fade toward
        self.paper = skia.Color(r, g, b)
        self.art = skia.Image.fromarray(pixels, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kOpaque_AlphaType)
        self.masks: dict[str, skia.Image] = {}
        self._mask_files: dict[str, Path] = {}
        self._crowd_points: dict[str, list[Point]] = {}
        self._discs: dict[str, tuple[float, float, float] | None] = {}
        self._glows: dict[str, skia.Image] = {}
        for s in located.structures:
            mask_file = job / s.mask
            if not mask_file.exists():
                raise RenderError(f"no mask for {s.name} on {located.scene} ({s.mask})")
            grey = _read(mask_file, skia.kGray_8_ColorType)
            if grey.shape != pixels.shape[:2]:
                raise RenderError(f"the mask for {s.name} on {located.scene} is not the art's size")
            self.masks[s.name] = skia.Image.fromarray(np.ascontiguousarray(grey), colorType=skia.kAlpha_8_ColorType)
            self._mask_files[s.name] = mask_file
        self._ghosts: dict[str, skia.Image] = {}
        self._spots: dict[str, skia.Image] = {}
        self._composed: OrderedDict[tuple, skia.Image] = OrderedDict()
        self.content = _drawn_box(pixels, located, (b, g, r))
        self.centre = (self.width / 2, self.height / 2)
        if located.structures:
            self.centre = (sum(s.centroid.x for s in located.structures) / len(located.structures),
                           sum(s.centroid.y for s in located.structures) / len(located.structures))

    def _surface(self, alpha_only: bool = False) -> skia.Surface:
        kind = skia.kAlpha_8_ColorType if alpha_only else skia.kBGRA_8888_ColorType
        return skia.Surface.MakeRaster(skia.ImageInfo.Make(self.width, self.height, kind, skia.kPremul_AlphaType))

    def ghost(self, name: str) -> skia.Image:
        """Where the structure is, with a soft edge: drawn in the background's colour at GHOST_WASH over the art.

        Washes are alpha only (a byte a pixel), so a scene with many structures stays small."""
        if name not in self._ghosts:
            surface = self._surface(alpha_only=True)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            canvas.drawImage(self.masks[name], 0, 0, skia.SamplingOptions(),
                             skia.Paint(Color=skia.ColorWHITE, ImageFilter=skia.ImageFilters.Blur(EDGE_BLUR, EDGE_BLUR)))
            self._ghosts[name] = surface.makeImageSnapshot()
        return self._ghosts[name]

    def spot(self, name: str) -> skia.Image:
        """Everywhere but the structure: drawn in the background's colour at SPOT_WASH, it leaves only that
        one bright."""
        if name not in self._spots:
            surface = self._surface(alpha_only=True)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorWHITE)
            canvas.drawImage(self.masks[name], 0, 0, skia.SamplingOptions(),
                             skia.Paint(BlendMode=skia.BlendMode.kDstOut,
                                        ImageFilter=skia.ImageFilters.Blur(SPOT_BLUR, SPOT_BLUR)))
            self._spots[name] = surface.makeImageSnapshot()
        return self._spots[name]

    def crowd_points(self, name: str) -> list[Point]:
        """Where a space's crowd sits, in the order its dots appear: spread through the space, each at
        least CROWD_SPACING from the others and half that inside its edge. The same every time, so a frame
        depends only on its time."""
        if name not in self._crowd_points:
            inside = _read(self._mask_files[name], skia.kGray_8_ColorType) > 127
            ys, xs = np.nonzero(inside)
            spacing = max(CROWD_SPACING, math.sqrt(len(xs) / CROWD_MAX)) if len(xs) else CROWD_SPACING
            m = int(spacing / 2)
            h, w = inside.shape
            picked: list[Point] = []
            rng = np.random.default_rng(zlib.crc32(name.encode()))
            for i in rng.permutation(len(xs))[:40000]:
                x, y = int(xs[i]), int(ys[i])
                if not (m <= x < w - m and m <= y < h - m and inside[y, x - m] and inside[y, x + m]
                        and inside[y - m, x] and inside[y + m, x]):
                    continue
                if any((x - px) ** 2 + (y - py) ** 2 < spacing ** 2 for px, py in picked):
                    continue
                picked.append((float(x), float(y)))
                if len(picked) == CROWD_MAX:
                    break
            self._crowd_points[name] = picked
        return self._crowd_points[name]

    def glow(self, name: str) -> skia.Image:
        """The structure's light: its mask blurred well past its edge, alpha only."""
        if name not in self._glows:
            surface = self._surface(alpha_only=True)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            canvas.drawImage(self.masks[name], 0, 0, skia.SamplingOptions(),
                             skia.Paint(Color=skia.ColorWHITE, ImageFilter=skia.ImageFilters.Blur(GLOW_BLUR, GLOW_BLUR)))
            self._glows[name] = surface.makeImageSnapshot()
        return self._glows[name]

    def spin_disc(self, name: str) -> tuple[float, float, float] | None:
        """The disc a face-on part turns in: its centre and a radius taking in all of it but stray
        specks; None when it is not round enough to turn in place."""
        if name not in self._discs:
            ys, xs = np.nonzero(_read(self._mask_files[name], skia.kGray_8_ColorType) > 127)
            disc = None
            if len(xs):
                w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
                cx, cy = float(xs.mean()), float(ys.mean())
                r = float(np.percentile(np.hypot(xs - cx, ys - cy), 97)) * 1.04
                if max(w, h) <= SPIN_ROUND * min(w, h) and _looks_the_same_turned(xs - cx, ys - cy, r):
                    disc = (cx, cy, r)
            self._discs[name] = disc
        return self._discs[name]

    def compose(self, ghosts: dict[str, float], spots: dict[str, float]) -> tuple[tuple, skia.Image]:
        """The art with each ghost and spotlight wash at its level (0-1), kept for reuse; and its key."""
        key = (tuple(sorted((n, round(v, 3)) for n, v in ghosts.items() if v > 0.002 and n in self.masks)),
               tuple(sorted((n, round(v, 3)) for n, v in spots.items() if v > 0.002 and n in self.masks)))
        if not key[0] and not key[1]:
            return key, self.art
        if key in self._composed:
            self._composed.move_to_end(key)
            return key, self._composed[key]
        surface = self._surface()
        canvas = surface.getCanvas()
        canvas.drawImage(self.art, 0, 0)
        for name, level in key[0]:
            canvas.drawImage(self.ghost(name), 0, 0, skia.SamplingOptions(),
                             skia.Paint(Color=self.paper, Alphaf=GHOST_WASH * level))
        for name, level in key[1]:
            canvas.drawImage(self.spot(name), 0, 0, skia.SamplingOptions(),
                             skia.Paint(Color=self.paper, Alphaf=SPOT_WASH * level))
        image = surface.makeImageSnapshot()
        self._composed[key] = image
        while len(self._composed) > 6:
            self._composed.popitem(last=False)
        return key, image


def _looks_the_same_turned(dx: np.ndarray, dy: np.ndarray, r: float) -> bool:
    """Whether a shape (its pixels, from its centre) overlaps itself turned a quarter and a half turn by
    SPIN_SAME or more, as a wheel does and a mushroom does not."""
    size = int(math.ceil(r)) + 1
    grid = np.zeros((2 * size + 1, 2 * size + 1), bool)
    keep = (np.abs(dx) <= size) & (np.abs(dy) <= size)
    grid[np.round(dy[keep]).astype(int) + size, np.round(dx[keep]).astype(int) + size] = True
    for turned in (np.rot90(grid), np.rot90(grid, 2)):
        overlap = (grid & turned).sum() / max((grid | turned).sum(), 1)
        if overlap < SPIN_SAME:
            return False
    return True


# --- the camera ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Cam:
    cx: float  # the art point at the view's centre
    cy: float
    zoom: float  # 1.0 fits the whole art in the view


def _fit(view: Rect, art: SceneArt) -> float:
    return min((view[2] - view[0]) / art.width, (view[3] - view[1]) / art.height)


def clamp_cam(cam: Cam, view: Rect, art: SceneArt) -> Cam:
    """Keep the view inside the art; when the art is smaller than the view, centre it."""
    scale = _fit(view, art) * cam.zoom
    half_w, half_h = (view[2] - view[0]) / scale / 2, (view[3] - view[1]) / scale / 2
    cx = art.width / 2 if half_w * 2 >= art.width else min(max(cam.cx, half_w), art.width - half_w)
    cy = art.height / 2 if half_h * 2 >= art.height else min(max(cam.cy, half_h), art.height - half_h)
    return Cam(cx, cy, cam.zoom)


def mix_cam(a: Cam, b: Cam, x: float) -> Cam:
    return Cam(p.lerp(a.cx, b.cx, x), p.lerp(a.cy, b.cy, x), math.exp(p.lerp(math.log(a.zoom), math.log(b.zoom), x)))


def to_frame(cam: Cam, view: Rect, art: SceneArt) -> tuple[float, float, float]:
    """The scale and offset that map art pixels onto the frame."""
    scale = _fit(view, art) * cam.zoom
    return scale, (view[0] + view[2]) / 2 - cam.cx * scale, (view[1] + view[3]) / 2 - cam.cy * scale


def _split_at_box(line: list[Point], box: tuple[float, float, float, float]) -> list[tuple[list[Point], bool, bool]]:
    """A line cut where it goes into and out of a box: (piece, inside the box, the last piece), in order. A straight
    line is sampled finely first, so the cut falls close to the box's edge."""
    if len(line) == 2:
        n = max(int(math.dist(*line) / 6), 1)
        line = [(p.lerp(line[0][0], line[1][0], k / n), p.lerp(line[0][1], line[1][1], k / n)) for k in range(n + 1)]

    def inside(q: Point) -> bool:
        return box[0] <= q[0] <= box[2] and box[1] <= q[1] <= box[3]

    pieces: list[tuple[list[Point], bool]] = []
    for q in line:
        state = inside(q)
        if pieces and pieces[-1][1] == state:
            pieces[-1][0].append(q)
        else:
            if pieces:
                pieces[-1][0].append(q)  # the pieces meet, with no gap between them
            pieces.append(([q], state))
    pieces = [(piece, state) for piece, state in pieces if len(piece) >= 2]
    return [(piece, state, k == len(pieces) - 1) for k, (piece, state) in enumerate(pieces)]


def _up_to(line: list[Point], lead: Point) -> list[Point] | None:
    """The part of `line` up to where the lead particle has got to (its nearest point on the line), or None while
    it has not passed the line's start: an arrow grows only forward."""
    best = None
    for k, (a, b) in enumerate(zip(line, line[1:])):
        length2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1.0
        u = ((lead[0] - a[0]) * (b[0] - a[0]) + (lead[1] - a[1]) * (b[1] - a[1])) / length2
        at = (a[0] + (b[0] - a[0]) * min(max(u, 0.0), 1.0), a[1] + (b[1] - a[1]) * min(max(u, 0.0), 1.0))
        if best is None or math.dist(at, lead) < best[0]:
            best = (math.dist(at, lead), k, u, at)
    _, k, u, at = best
    if k == 0 and u <= 0:
        return None
    return line[:k + 1] + [at]


def _inside(q: Point, box) -> bool:
    return box.x0 <= q[0] <= box.x1 and box.y0 <= q[1] <= box.y1


def _leave(a: Point, b: Point, box) -> Point:
    """Where the line from a toward b leaves the box a sits in: its edge, or a itself when a is outside it."""
    t_out = 1.0
    for lo, hi, start, delta in ((box.x0, box.x1, a[0], b[0] - a[0]), (box.y0, box.y1, a[1], b[1] - a[1])):
        if delta > 0:
            t_out = min(t_out, (hi - start) / delta)
        elif delta < 0:
            t_out = min(t_out, (lo - start) / delta)
    t_out = max(t_out, 0.0)
    return a[0] + (b[0] - a[0]) * t_out, a[1] + (b[1] - a[1]) * t_out


def stop_points(anchors: list[Point]) -> list[Point]:
    """Where the particles stop: each label anchor moved a little toward its neighbour on the
    path (the next stop for the first, the one before for the rest), so the label's dot stays
    in sight beside the particle."""
    points = []
    for i, (x, y) in enumerate(anchors):
        nx, ny = anchors[i - 1] if i else anchors[1]
        d = math.dist((x, y), (nx, ny))
        k = min(STOP_OFFSET, d / 3) / d if d else 0.0
        points.append((x + (nx - x) * k, y + (ny - y) * k))
    return points


def bezier(a: Point, ctrl: Point, b: Point, u: float) -> Point:
    """The point u (0 to 1) along the curve from a to b bent toward ctrl."""
    v = 1 - u
    return (v * v * a[0] + 2 * v * u * ctrl[0] + u * u * b[0], v * v * a[1] + 2 * v * u * ctrl[1] + u * u * b[1])


def _on_leg(points: list[Point], bends, i: int, x: float) -> Point:
    """The point x (0 to 1) along leg i: on its curve when it bends, else on the straight line."""
    if bends and bends[i] is not None:
        return bezier(points[i], bends[i], points[i + 1], x)
    return p.lerp(points[i][0], points[i + 1][0], x), p.lerp(points[i][1], points[i + 1][1], x)


def lead_at(points: list[Point], t: float, bends=None) -> tuple[Point, int]:
    """Where the lead particle is t seconds after it set off, and which trip it is on.

    It holds at a stop, eases to the next (along the leg's curve when `bends` gives it one), and after the last
    stop starts over from the first."""
    legs = [max(math.dist(a, b) / FLOW_SPEED, FLOW_MIN_LEG_S) for a, b in zip(points, points[1:])]
    trip, t = divmod(max(t, 0.0), FLOW_PAUSE_S * len(points) + sum(legs))
    for i, leg in enumerate(legs):
        if t < FLOW_PAUSE_S:
            return points[i], int(trip)
        t -= FLOW_PAUSE_S
        if t < leg:
            return _on_leg(points, bends, i, p.ease(t / leg)), int(trip)
        t -= leg
    return points[-1], int(trip)


def lead_leg(points: list[Point], t: float, bends=None) -> tuple[Point, int, bool]:
    """Where the lead particle is t seconds after it set off, the stop it last left (it is between that stop and
    the next), and whether it has finished its first trip."""
    if t >= trip_s(points):
        return points[-1], len(points) - 1, True
    legs = [max(math.dist(a, b) / FLOW_SPEED, FLOW_MIN_LEG_S) for a, b in zip(points, points[1:])]
    t = max(t, 0.0)
    for i, leg in enumerate(legs):
        if t < FLOW_PAUSE_S:
            return points[i], i, False
        t -= FLOW_PAUSE_S
        if t < leg:
            return _on_leg(points, bends, i, p.ease(t / leg)), i, False
        t -= leg
    return points[-1], len(points) - 1, True


def trip_s(points: list[Point]) -> float:
    """How long the lead particle takes from its first stop to holding at its last."""
    legs = [max(math.dist(a, b) / FLOW_SPEED, FLOW_MIN_LEG_S) for a, b in zip(points, points[1:])]
    return FLOW_PAUSE_S * (len(points) - 1) + sum(legs)


def _along(points: list[Point], d: float) -> Point:
    """The point d art pixels along the path."""
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if d <= length:
            x = d / length if length else 0.0
            return p.lerp(a[0], b[0], x), p.lerp(a[1], b[1], x)
        d -= length
    return points[-1]


def pile_at(points: list[Point], t: float) -> list[tuple[Point, float]]:
    """Blocked particles: the stream's dots, each stopping where the queue behind the last stop has got to,
    PILE_SPACING apart (the first just behind the lead), until PILE_MAX have arrived."""
    total = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    speed, gap = FLOW_SPEED / 2, STREAM_GAP
    dots = []
    for j in range(PILE_MAX):
        free = max(t, 0.0) * speed - j * gap
        stop = total - (j + 1) * PILE_SPACING
        if free < 0 or stop < 0:
            break
        dots.append((_along(points, min(free, stop)), p.clamp01(free / (gap / 2))))
    return dots


def stream_at(points: list[Point], t: float) -> list[tuple[Point, float]]:
    """The dots streaming along the path at an even pace, about STREAM_GAP apart, each with
    its fade: they fade in leaving the first stop and out reaching the last."""
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total < 1:
        return []
    count = max(round(total / STREAM_GAP), 1)
    gap = total / count
    shift = (max(t, 0.0) * FLOW_SPEED / 2) % gap
    dots = []
    for j in range(count):
        d = shift + j * gap
        fade = p.clamp01(min(d, total - d) / (gap / 2))
        for i, length in enumerate(lengths):
            if d <= length:
                x = d / length if length else 0.0
                dots.append(((p.lerp(points[i][0], points[i + 1][0], x), p.lerp(points[i][1], points[i + 1][1], x)), fade))
                break
            d -= length
    return dots


def edge_fade(x: float, y: float, view: Rect) -> float:
    """1 while a label's dot is wholly inside the view, down to 0 at its edge: a label the camera moves
    away from fades out rather than clinging to the edge, and a label on screen has its dot on screen."""
    inside = min(x - view[0], view[2] - x, y - view[1], view[3] - y)
    return p.clamp01(inside / EDGE_FADE)


# --- per beat, worked out once --------------------------------------------------------------


@dataclass
class BeatPlan:
    target: Cam
    start: Cam
    move_at: float
    move_s: float
    spot: str | None
    spot_at: float
    prev_spot: str | None  # the beat before's spotlight, fading out (same scene only)
    caption_in: float  # the caption's run on screen, for its fades
    caption_out: float
    key_in: float  # the key line's run on screen, for its fades
    key_out: float
    flow_in: float  # the particles' run: they set off once the run starts and its stops are revealed
    flow_out: float
    fade_from: str | None = None  # the stage this one fades in over (Timeline.stages)

    def cam(self, t: float, view: Rect, art: SceneArt) -> Cam:
        return clamp_cam(mix_cam(self.start, self.target, p.ease((t - self.move_at) / self.move_s)), view, art)


class Renderer:
    def __init__(self, job: Path, timeline: Timeline, layout: Layout | str = "h",
                 locations: Locations | None = None):
        self.job = job
        self.timeline = timeline
        self.layout = LAYOUTS[layout] if isinstance(layout, str) else layout
        self.portrait = self.layout.height > self.layout.width
        self.locations = locations or jobs.read(job / media.LOCATIONS, Locations)
        self._scenes: dict[str, SceneArt] = {}
        scene_ids = {b.scene for b in timeline.beats}
        for scene_id in sorted(scene_ids):
            if self.locations.scene(scene_id) is None:
                raise RenderError(f"scene {scene_id} has no locations")
            self.scene(scene_id)  # load every scene now, so a missing file fails before any frame
        self._index = {id(beat): i for i, beat in enumerate(timeline.beats)}
        # When each structure is first revealed on each scene.
        self.revealed: dict[str, dict[str, float]] = {}
        for beat in timeline.beats:
            for r in beat.reveal:
                self.revealed.setdefault(beat.scene, {}).setdefault(r.name, r.t)
        # Each process's stages, which fade one into the next. What was shown on the stage before is on
        # the next from its first frame, rather than shown again from a pale wash.
        self._process = {sid: (n, k) for n, group in enumerate(timeline.stages) for k, sid in enumerate(group)}
        # The scenes drawn beside a notice card: those with notices, and every stage of a process one of whose
        # stages has them, so its stages keep one framing.
        self._beside = set(timeline.notices)
        for group in timeline.stages:
            if self._beside & set(group):
                self._beside |= set(group)
        for i, beat in enumerate(timeline.beats):
            if i and self.staged(timeline.beats[i - 1].scene, beat.scene):
                before, now = self.revealed.get(timeline.beats[i - 1].scene, {}), self.revealed.get(beat.scene, {})
                for name, at in now.items():
                    if at == beat.t0 and before.get(name, math.inf) <= beat.t0:
                        now[name] = beat.t0 - max(REVEAL_S, LABEL_IN_S)
        self.keyed = any(beat.key_line for beat in timeline.beats)  # a key band only in a video with key lines
        self.plans = self._plan()
        width, height = self.layout.width, self.layout.height
        self.pixels = np.zeros((height, width, 4), np.uint8)
        self.surface = skia.Surface(self.pixels, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        self._background: tuple[tuple, skia.Image] | None = None
        self._cards: dict[str, tuple[skia.Image, float, float]] = {}  # each scene's notice card, drawn once
        self._scene_routes: dict[str, list[Point]] = {}  # points along each scene's arrows, in art pixels
        self._scene_pills: dict[str, list] = {}  # each scene's reaction pills over the whole video, first shown first

    def view(self, scene_id: str) -> Rect:
        """Where the scene's picture goes on the frame: above the lyric band, and left of the notice card when it
        has one."""
        x0, y0, x1, y1 = self.layout.view
        if self.keyed:
            y0 += self.layout.key_h
        return (x0, y0, x1 - NOTICE_W, y1) if scene_id in self._beside else (x0, y0, x1, y1)

    def staged(self, before: str, after: str) -> bool:
        """Whether going from one scene to the other is one stage of a process giving way to a later one. Back to
        an earlier stage is a cut: faded, the cell would be seen coming together again."""
        (p, k), (q, m) = self._process.get(before, (-1, 0)), self._process.get(after, (-2, 0))
        return p == q and m > k

    def scene(self, scene_id: str) -> SceneArt:
        if scene_id not in self._scenes:
            self._scenes[scene_id] = SceneArt(self.job, self.locations.scene(scene_id))
        return self._scenes[scene_id]

    def _aim(self, scene_id: str, target: str | None, zoom: float, focus: Point | None = None) -> Cam:
        """The camera for a beat: on its target, or on the whole scene. An upright frame cannot hold a wide picture
        whole, so there "the whole scene" fills the frame's height and looks at `focus`, where the beat's flow is."""
        art = self.scene(scene_id)
        loc = self.locations.scene(scene_id).get(target) if target else None
        view = self.view(scene_id)
        if loc is None:
            whole = self._whole(scene_id, view)
            if self.portrait and focus is not None:
                return clamp_cam(Cam(focus[0], whole.cy, whole.zoom), view, art)
            return whole
        box_w, box_h = loc.box.x1 - loc.box.x0, loc.box.y1 - loc.box.y0
        fits = min((view[2] - view[0]) / (box_w * FIT_PAD), (view[3] - view[1]) / (box_h * FIT_PAD)) / _fit(view, art)
        zoom = max(1.0, min(zoom, fits))
        if self.portrait:  # a close-up is never wider than the upright frame's whole scene
            zoom = max(zoom, self._whole(scene_id, view).zoom)
        return clamp_cam(Cam(loc.centroid.x, loc.centroid.y, zoom), view, art)

    def _whole(self, scene_id: str, view: Rect) -> Cam:
        """The whole scene: everything drawn on it, fitted to the view with room for labels.

        Art rarely fills its canvas (a membrane is a strip across it), so this frames
        what is drawn rather than the canvas, up to WHOLE_MAX_ZOOM."""
        art = self.scene(scene_id)
        x0, y0, x1, y1 = art.content
        zoom = min((view[2] - view[0]) / ((x1 - x0) * WHOLE_PAD), (view[3] - view[1]) / ((y1 - y0) * WHOLE_PAD))
        if self.portrait:  # what is drawn fills the frame's height; its sides run off, and the camera follows the flow
            zoom = (view[3] - view[1]) / ((y1 - y0) * PORTRAIT_PAD)
        zoom = max(1.0, min(zoom / _fit(view, art), PORTRAIT_MAX_ZOOM if self.portrait else WHOLE_MAX_ZOOM))
        return clamp_cam(Cam((x0 + x1) / 2, (y0 + y1) / 2, zoom), view, art)

    def _plan(self) -> list[BeatPlan]:
        plans: list[BeatPlan] = []
        beats = self.timeline.beats
        for i, beat in enumerate(beats):
            reveals = {r.name: r.t for r in beat.reveal}
            focus = None
            if self.portrait and beat.particles:  # where the beat's flow is: the middle of its stops
                stops = self._path_points(beat.scene, beat.particles[0].path)
                focus = (sum(x for x, _ in stops) / len(stops), sum(y for _, y in stops) / len(stops)) if stops else None
            target = self._aim(beat.scene, beat.camera.target, beat.camera.zoom, focus)
            same = i > 0 and beats[i - 1].scene == beat.scene
            staged = i > 0 and self.staged(beats[i - 1].scene, beat.scene)
            if same:
                start = plans[-1].cam(beat.t0, self.view(beat.scene), self.scene(beat.scene))
                move_at = max(beat.t0, reveals.get(beat.camera.target, beat.t0))
                move_s = MOVE_S if beat.camera.ease == "inOut" else max(beat.t1 - move_at, 0.1)
            elif staged:  # the same framing: the camera goes on from where it was
                start = clamp_cam(plans[-1].cam(beat.t0, self.view(beats[i - 1].scene), self.scene(beats[i - 1].scene)),
                                  self.view(beat.scene), self.scene(beat.scene))
                move_at, move_s = beat.t0, MOVE_S
            else:
                start = Cam(target.cx, target.cy, target.zoom * (HOOK_ZOOM if i == 0 else SETTLE_ZOOM))
                move_at, move_s = beat.t0, SETTLE_S * (1.4 if i == 0 else 1.0)
            prev_spot = beats[i - 1].spotlight if same else None
            spot_at = max(beat.t0, reveals.get(beat.spotlight, beat.t0))
            caption_in = caption_out = beat.t0
            if beat.caption:
                j = i
                while j > 0 and beats[j - 1].caption == beat.caption:
                    j -= 1
                k = i
                while k + 1 < len(beats) and beats[k + 1].caption == beat.caption:
                    k += 1
                caption_in, caption_out = beats[j].t0, beats[k].t1
            j = i
            while j > 0 and beats[j - 1].key_line == beat.key_line:
                j -= 1
            k = i
            while k + 1 < len(beats) and beats[k + 1].key_line == beat.key_line:
                k += 1
            key_in, key_out = beats[j].t0, beats[k].t1
            flow_in = flow_out = beat.t0
            if beat.particles:
                # Beats in a row on one scene with the same paths are one run: the particles
                # keep going across them rather than starting over at every line.
                def same_flow(other) -> bool:
                    return other.scene == beat.scene and [f.path for f in other.particles] == [f.path for f in beat.particles]
                j = i
                while j > 0 and same_flow(beats[j - 1]):
                    j -= 1
                k = i
                while k + 1 < len(beats) and same_flow(beats[k + 1]):
                    k += 1
                revealed = self.revealed.get(beat.scene, {})
                stops = [name for f in beat.particles for name in f.path]
                flow_in = max([beats[j].t0] + [revealed.get(name, beats[j].t0) for name in stops])
                flow_out = beats[k].t1
            plans.append(BeatPlan(target=target, start=start, move_at=move_at, move_s=move_s, spot=beat.spotlight,
                                  spot_at=spot_at, prev_spot=prev_spot, caption_in=caption_in,
                                  caption_out=caption_out, key_in=key_in, key_out=key_out, flow_in=flow_in, flow_out=flow_out,
                                  fade_from=beats[i - 1].scene if staged else None))
        return plans

    def beat_index(self, t: float) -> int:
        """The index of the beat on screen at t (Timeline.beat_at)."""
        return self._index[id(self.timeline.beat_at(t))]

    def art_to_frame(self, t: float, x: float, y: float) -> tuple[float, float]:
        """Where an art pixel of the beat at t lands on the frame."""
        i = self.beat_index(t)
        art = self.scene(self.timeline.beats[i].scene)
        view = self.view(self.timeline.beats[i].scene)
        scale, dx, dy = to_frame(self.plans[i].cam(t, view, art), view, art)
        return x * scale + dx, y * scale + dy

    # --- drawing ---------------------------------------------------------------------------

    def frame(self, t: float) -> np.ndarray:
        """The frame at time t: BGRA pixels, height x width x 4 (a view kept by the renderer)."""
        self._draw_beat(self.surface.getCanvas(), t)
        return self.pixels

    def image(self, t: float) -> skia.Image:
        self.frame(t)
        return self.surface.makeImageSnapshot()

    def _ghosts(self, scene_id: str, reveals: dict[str, float], t: float) -> dict[str, float]:
        """Each structure's ghost wash at t: full until its name is heard, fading out as it is revealed.
        A structure this video never names on the scene is drawn as it is, unlabelled: as a ghost it
        would wash out its part of the picture the whole time the scene is on screen (the first live
        runs left a whole Earth pale through a song)."""
        art = self.scene(scene_id)
        levels = {}
        for name in art.masks:
            r = reveals.get(name)
            levels[name] = 0.0 if r is None else 1.0 - p.ease((t - r) / REVEAL_S)
        return levels

    def _draw_background(self, canvas: skia.Canvas, scene_id: str, cam: Cam, view: Rect,
                         ghosts: dict[str, float], spots: dict[str, float], keep: bool = True) -> None:
        """The scene's picture, framed by the camera, over the whole frame. Kept for the next frame unless
        `keep` is False (a stage fading into the next: two pictures, each drawn in part)."""
        art = self.scene(scene_id)
        washes, composed = art.compose(ghosts, spots)
        key = (scene_id, washes, round(cam.cx, 3), round(cam.cy, 3), round(cam.zoom, 5), view)
        if keep and self._background and self._background[0] == key:
            canvas.drawImage(self._background[1], 0, 0)
            return
        canvas.clear(art.paper)
        scale, dx, dy = to_frame(cam, view, art)
        canvas.save()
        canvas.clipRect(skia.Rect.MakeLTRB(*view))  # beside a notice card, the picture stops at its view
        canvas.translate(dx, dy)
        canvas.scale(scale, scale)
        canvas.drawImage(composed, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
        canvas.restore()
        if keep:
            self._background = (key, self.surface.makeImageSnapshot())

    def _labels(self, canvas: skia.Canvas, scene_id: str, names: list[str], reveals: dict[str, float],
                spots: dict[str, float], cam: Cam, view: Rect, bounds: Rect, t: float,
                avoid: list[Point] = ()) -> list[Rect]:
        """Draws the labels; returns where each pill landed, for what is drawn after them to keep clear."""
        art = self.scene(scene_id)
        located = self.locations.scene(scene_id)
        scale, dx, dy = to_frame(cam, view, art)
        labels = []
        for name in names:
            loc = located.get(name)
            if loc is None:
                continue
            ax, ay = loc.label_anchor.x * scale + dx, loc.label_anchor.y * scale + dy
            alpha = p.ease((t - reveals.get(name, -1e9)) / LABEL_IN_S) * edge_fade(ax, ay, view)
            if alpha > 0.01:
                box = (loc.box.x0 * scale + dx, loc.box.y0 * scale + dy, loc.box.x1 * scale + dx, loc.box.y1 * scale + dy)
                big = self._is_space(scene_id, loc)
                labels.append(p.Label(name=name, color=self.timeline.palette[name], anchor=(ax, ay), box=box, alpha=alpha,
                                      big=big, emphasis=spots.get(name, 0.0)))
        centre = (art.centre[0] * scale + dx, art.centre[1] * scale + dy)
        p.place_labels(labels, bounds, centre, self.layout.label_font, avoid)
        for lb in sorted(labels, key=lambda lb: lb.emphasis):  # the spotlit one on top
            p.draw_label(canvas, lb, self.layout.label_font)
        rects = []
        for lb in labels:
            w, h = lb.size(self.layout.label_font)
            rects.append((lb.x - w / 2, lb.y - h / 2, lb.x + w / 2, lb.y + h / 2))
        return rects

    def _draw_beat(self, canvas: skia.Canvas, t: float) -> None:
        layout = self.layout
        i = self.beat_index(t)
        beat, plan = self.timeline.beats[i], self.plans[i]
        art = self.scene(beat.scene)
        view = self.view(beat.scene)
        cam = plan.cam(t, view, art)
        spots: dict[str, float] = {}
        if plan.spot:
            spots[plan.spot] = 1.0 if plan.prev_spot == plan.spot else p.ease((t - plan.spot_at) / SPOT_S)
        if plan.prev_spot and plan.prev_spot != plan.spot:
            spots[plan.prev_spot] = max(spots.get(plan.prev_spot, 0), 1 - p.ease((t - beat.t0) / SPOT_S))
        reveals = self.revealed.get(beat.scene, {})
        ghosts = self._ghosts(beat.scene, reveals, t)
        fade = p.ease((t - beat.t0) / STAGE_FADE_S) if plan.fade_from else 1.0
        if fade < 1.0:  # the stage before, under this one fading in, framed alike
            before = plan.fade_from
            self._draw_background(canvas, before, cam, view,
                                  self._ghosts(before, self.revealed.get(before, {}), t), {}, keep=False)
            canvas.saveLayerAlpha(None, round(255 * fade))
            self._draw_background(canvas, beat.scene, cam, view, ghosts, spots, keep=False)
            canvas.restore()
        else:
            self._draw_background(canvas, beat.scene, cam, view, ghosts, spots)
        if beat.turning:
            self._turning(canvas, beat, cam, view, t, ghosts, spots, face=True)
        if beat.crowds:
            self._crowds(canvas, beat, cam, view, t, ghosts, spots)
        if beat.turning:
            self._turning(canvas, beat, cam, view, t, ghosts, spots, face=False)
        if beat.routes:
            self._routes(canvas, beat, cam, view, t)
        if beat.glow:
            self._glow(canvas, beat, cam, view, t, ghosts)
        if beat.particles:
            self._particles(canvas, beat, plan, cam, view, t)

        caption_alpha = 0.0
        if beat.caption:
            caption_alpha = p.ease((t - plan.caption_in) / CAPTION_FADE_S) * p.ease((plan.caption_out - t) / CAPTION_FADE_S)
        top = layout.margin + (layout.caption_font * 1.75 + 24 if caption_alpha > 0 else 0)
        notes = self.timeline.notices.get(beat.scene, [])
        tagged = beat.scene in self.timeline.ai_pictures and not notes  # the notice card says it otherwise
        bottom = layout.margin + (AI_TAG_FONT * 1.7 + 10 if tagged else 0)  # labels keep clear of the tag
        bounds = (view[0] + layout.margin, view[1] + top, view[2] - layout.margin, view[3] - bottom)
        arrows = self._arrow_points(beat.scene, cam, view)
        places = self._pill_places(beat.scene, cam, view, bounds, arrows)
        # Labels keep clear of the arrows and of every reaction pill the scene shows, sampled as points.
        pill_points = [(px, py) for x0, y0, x1, y1 in places.values()
                       for px in [x0 + k * (x1 - x0) / 12 for k in range(13)]
                       for py in (y0, (y0 + y1) / 2, y1)]
        self._labels(canvas, beat.scene, beat.labels, reveals, spots, cam, view, bounds, t, arrows + pill_points)
        if beat.conversions:
            self._conversions(canvas, beat, view, bounds, t, places)
        if beat.caption:
            p.draw_caption(canvas, beat.caption, (view[0] + view[2]) / 2, view[1] + layout.margin + 8, layout.caption_font,
                           caption_alpha)
        # Fading in with a stage only where the stage before had none: the same card or tag stays put.
        if tagged:
            before = plan.fade_from in self.timeline.ai_pictures and plan.fade_from not in self.timeline.notices
            p.draw_ai_tag(canvas, view[0] + layout.margin, view[3] - layout.margin, AI_TAG_FONT,
                          1.0 if before else fade)
        if notes:  # what a check found still wrong in this AI-made picture, disclosed beside it
            card, x, y = self._card(beat.scene, view)
            alpha = 1.0 if plan.fade_from in self.timeline.notices else fade
            canvas.drawImage(card, x, y, skia.SamplingOptions(), skia.Paint(Alphaf=alpha))
        p.draw_band(canvas, (0, layout.height - layout.band_h, layout.width, layout.height),
                    self._band_words(self._band_beat(i, t)), t, layout.band_font, lines=layout.band_lines)
        # The key line fades in and out with its run, and stays put through it while the lyrics go by below.
        key_alpha = p.ease((t - plan.key_in) / KEY_FADE_S) * p.ease((plan.key_out - t) / KEY_FADE_S)
        if layout.title_h and self.keyed:
            p.draw_title_key(canvas, (0, 0, layout.width, layout.title_h), self.timeline.title, beat.key_line,
                             self.timeline.key_terms, layout.title_font, layout.key_font, key_alpha)
        elif layout.title_h:
            p.draw_title(canvas, (0, 0, layout.width, layout.title_h), self.timeline.title, layout.title_font)
        if self.keyed and layout.key_h:
            p.draw_key_band(canvas, (0, layout.title_h, layout.width, layout.title_h + layout.key_h), beat.key_line,
                            self.timeline.key_terms, layout.key_font, key_alpha)

    def _pill_places(self, scene_id: str, cam: Cam, view: Rect, bounds: Rect,
                     arrows: list[Point]) -> dict[str, tuple[float, float, float, float]]:
        """Where each reaction pill the scene shows at any time in the video goes, as its rect in frame pixels:
        under its structure, or over it, or the same rows shifted to either side, whichever crosses the fewest
        arrows and no other pill. Placed before the labels, which then keep clear of them, as of the arrows."""
        if scene_id not in self._scene_pills:
            convs = []
            for beat in self.timeline.beats:
                for conv in beat.conversions if beat.scene == scene_id else []:
                    if conv.structure not in [c.structure for c in convs]:
                        convs.append(conv)
            self._scene_pills[scene_id] = convs
        located = self.locations.scene(scene_id)
        scale, dx, dy = to_frame(cam, view, self.scene(scene_id))
        font = self.layout.label_font * 1.25
        places: dict[str, tuple[float, float, float, float]] = {}
        for conv in self._scene_pills[scene_id]:
            loc = located.get(conv.structure)
            if loc is None:
                continue
            w, h = p.conversion_size(conv.text, font)
            x = (loc.box.x0 + loc.box.x1) / 2 * scale + dx
            below, above = loc.box.y1 * scale + dy, loc.box.y0 * scale + dy
            gap = self.layout.label_font * 0.6
            rows = [below + gap + h / 2, above - gap - h / 2, below + gap + 1.5 * h + 8, above - gap - 1.5 * h - 8]
            best = None
            for x_try, y in [(xo, yo) for xo in (x, x - 0.6 * w, x + 0.6 * w) for yo in rows]:
                cx = min(max(x_try, bounds[0] + w / 2), bounds[2] - w / 2)
                cy = min(max(y, bounds[1] + h / 2), bounds[3] - h / 2)
                rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                hit = 50 * sum(1 for r in places.values() if rect[0] < r[2] + 8 and r[0] < rect[2] + 8
                               and rect[1] < r[3] + 8 and r[1] < rect[3] + 8)
                hit += sum(1 for ax, ay in arrows if rect[0] - 8 < ax < rect[2] + 8 and rect[1] - 8 < ay < rect[3] + 8)
                score = hit + 0.01 * math.dist((cx, cy), (x, below))  # nearest of the equally clear spots
                if best is None or score < best[0]:
                    best = (score, rect)
            places[conv.structure] = best[1]
        return places

    def _conversions(self, canvas: skia.Canvas, beat, view: Rect, bounds: Rect, t: float,
                     places: dict[str, tuple[float, float, float, float]]) -> None:
        """What each structure the beat is about turns into what, in the pill placed for it."""
        font = self.layout.label_font * 1.25
        for conv in beat.conversions:
            if conv.structure not in places:
                continue
            x0, y0, x1, y1 = places[conv.structure]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            p.draw_conversion(canvas, (cx, cy), conv.text, self.timeline.palette[conv.structure],
                              p.ease((t - conv.since) / LABEL_IN_S) * edge_fade(cx, cy, view), bounds, font)

    def _card(self, scene_id: str, view: Rect) -> tuple[skia.Image, float, float]:
        """The scene's notice card as an image and where it goes: drawn once, as wrapping its notes and blurring
        its shadow every frame cost an eighth of the frame."""
        if scene_id not in self._cards:
            layout, notes = self.layout, self.timeline.notices[scene_id]
            surface = skia.Surface(layout.width, layout.height)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            top = layout.margin + 8
            bottom = p.draw_notice(canvas, view[2], top, layout.width - layout.margin, notes[:NOTICE_MAX],
                                   NOTICE_FONT, more=max(len(notes) - NOTICE_MAX, 0))
            x0, y0 = max(int(view[2]) - 24, 0), max(int(top) - 24, 0)
            x1, y1 = layout.width, min(int(bottom) + 32, layout.height)
            card = surface.makeImageSnapshot(skia.IRect.MakeLTRB(x0, y0, x1, y1))
            self._cards[scene_id] = (card, float(x0), float(y0))
        return self._cards[scene_id]

    def _turning(self, canvas: skia.Canvas, beat, cam: Cam, view: Rect, t: float,
                 ghosts: dict[str, float], spots: dict[str, float], face: bool) -> None:
        """The parts that turn: a face-on, round one as the disc of the picture around it turned in place (drawn
        first, over the background, as the picture itself); any other as a curved arrow going round it while it
        turns (drawn over the crowds), fading in and out with its speed."""
        art = self.scene(beat.scene)
        scale, dx, dy = to_frame(cam, view, art)
        located = self.locations.scene(beat.scene)
        for turn in beat.turning:
            loc = located.get(turn.structure)
            if loc is None or turn.structure not in art.masks:
                continue
            turns, speed = turn.at(t - beat.t0)
            disc = art.spin_disc(turn.structure)
            if face and disc is not None:
                if turns % 1 == 0:
                    continue  # as painted
                cx, cy, r = disc
                _, composed = art.compose(ghosts, spots)
                canvas.save()
                canvas.clipRect(skia.Rect.MakeLTRB(*view))
                canvas.translate(cx * scale + dx, cy * scale + dy)
                canvas.rotate(360 * turns)
                canvas.clipPath(skia.Path.Circle(0, 0, r * scale), doAntiAlias=True)
                canvas.scale(scale, scale)
                canvas.translate(-cx, -cy)
                canvas.drawImage(composed, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
                canvas.restore()
            elif not face and disc is None:
                top = max(turn.from_speed, turn.speed)
                alpha = (speed / top if top > 0 else 0.0) * (1 - ghosts.get(turn.structure, 0.0))
                if alpha <= 0.01:
                    continue
                box = loc.box
                rx = ARROW_WIDTH * (box.x1 - box.x0) / 2 * scale
                at = ((box.x0 + box.x1) / 2 * scale + dx, loc.centroid.y * scale + dy)
                canvas.save()
                canvas.clipRect(skia.Rect.MakeLTRB(*view))
                p.draw_turning_arrow(canvas, at, rx, ARROW_TILT * rx, turns, alpha)
                canvas.restore()

    def _glow(self, canvas: skia.Canvas, beat, cam: Cam, view: Rect, t: float, ghosts: dict[str, float]) -> None:
        """The line's glowing part: lit with a warm light past its edge, pulsing, from the line's start."""
        art = self.scene(beat.scene)
        if beat.glow not in art.masks:
            return
        pulse = 0.5 + 0.5 * (0.5 + 0.5 * math.sin(2 * math.pi * GLOW_HZ * (t - beat.t0) - math.pi / 2))
        alpha = GLOW_ALPHA * pulse * p.ease((t - beat.t0) / GLOW_IN_S) * (1 - ghosts.get(beat.glow, 0.0))
        if alpha <= 0.01:
            return
        scale, dx, dy = to_frame(cam, view, art)
        canvas.save()
        canvas.clipRect(skia.Rect.MakeLTRB(*view))
        canvas.translate(dx, dy)
        canvas.scale(scale, scale)
        canvas.drawImage(art.glow(beat.glow), 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear),
                         skia.Paint(Color=p.argb(GLOW_COLOR), Alphaf=alpha, BlendMode=skia.BlendMode.kScreen))
        canvas.restore()

    def _crowds(self, canvas: skia.Canvas, beat, cam: Cam, view: Rect, t: float,
                ghosts: dict[str, float], spots: dict[str, float]) -> None:
        """Each space filling up: tinted its dots' colour as it fills, and its dots, drifting in place, appearing
        one by one as it fills and going as it empties. Over the diagram, under the particles; as pale as the
        space while it is a ghost, and dimmed with it when something else is spotlit, unless the beat's
        particles are filling or emptying it, which is what the line is about."""
        art = self.scene(beat.scene)
        scale, dx, dy = to_frame(cam, view, art)
        x = p.clamp01((t - beat.t0) / (beat.t1 - beat.t0))
        for crowd in beat.crowds:
            if crowd.region not in art.masks:
                continue
            level = p.lerp(crowd.start, crowd.end, x)
            moving = any(f.label == crowd.label and crowd.region in (f.path[0], f.path[-1]) for f in beat.particles)
            others = 0.0 if moving else max((v for name, v in spots.items() if name != crowd.region), default=0.0)
            alpha = (1 - ghosts.get(crowd.region, 0.0)) * (1 - SPOT_WASH * others)
            if alpha <= 0.01:
                continue
            canvas.save()
            canvas.translate(dx, dy)
            canvas.scale(scale, scale)
            canvas.drawImage(art.ghost(crowd.region), 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear),
                             skia.Paint(Color=p.argb(crowd.color), Alphaf=CROWD_TINT * level * alpha))
            canvas.restore()
            points = art.crowd_points(crowd.region)
            shown = level * len(points)
            for k, (px, py) in enumerate(points):
                fade = p.clamp01(shown - k)
                if fade <= 0.01:
                    break
                phase = k * 2.39996  # the golden angle, so no two drift together
                speed = 0.5 + 0.08 * (k % 5)
                at = ((px + CROWD_DRIFT * math.sin(speed * t + phase)) * scale + dx,
                      (py + CROWD_DRIFT * math.cos(0.8 * speed * t + 2 * phase)) * scale + dy)
                if crowd.label:
                    p.draw_marker(canvas, at, crowd.color, crowd.label, alpha * fade)
                else:
                    p.draw_crowd_dot(canvas, at, crowd.color, alpha * fade)

    def _is_space(self, scene_id: str, loc) -> bool:
        """A space (the stroma, a lumen): a box covering much of its picture, or spanning most of its width."""
        art = self.scene(scene_id)
        wide, tall = loc.box.x1 - loc.box.x0, loc.box.y1 - loc.box.y0
        return wide * tall > SPACE_SHARE * art.width * art.height or wide > SPACE_SPAN * art.width

    def _path_stops(self, scene_id: str, path: list[str]) -> list[tuple[Point, object]]:
        """Where particles stop along a path, in art pixels, each with the structure it is in: a structure's label
        point, but a space's point just past the edge of the structure next to it on the path, on the space's side,
        so a flow into the lumen ends just under the complex that pumps it rather than across the picture. A space
        in the middle of a path has two points, one beside the part before it and one beside the part after it,
        so the flow runs along the space between them instead of cutting across the picture."""
        located = self.locations.scene(scene_id)
        stops = [loc for name in path if (loc := located.get(name)) is not None]

        def beside(loc, near) -> Point:
            x = min(max(near.label_anchor.x, loc.box.x0), loc.box.x1)
            below = (loc.box.y0 + loc.box.y1) / 2 > near.label_anchor.y
            y = near.box.y1 + SPACE_REACH if below else near.box.y0 - SPACE_REACH
            inset = (loc.box.y1 - loc.box.y0) * 0.1
            return x, min(max(y, loc.box.y0 + inset), loc.box.y1 - inset)

        out: list[tuple[Point, object, bool]] = []
        for i, loc in enumerate(stops):
            if not (self._is_space(scene_id, loc) and len(stops) > 1):
                out.append(((loc.label_anchor.x, loc.label_anchor.y), loc, False))
                continue
            near = [stops[j] for j in (i - 1, i + 1) if 0 <= j < len(stops)]
            points = [beside(loc, n) for n in near]
            if len(points) == 2 and math.dist(*points) < 1:
                points = points[:1]
            out += [(point, loc, True) for point in points]
        anchors = [point for point, _, _ in out]
        if len(anchors) < 2:
            return [(point, loc) for point, loc, _ in out]
        # A label point is moved a little toward its neighbour, to keep the label's dot in sight; a space's point
        # has no dot, and moving it would pull it back toward the edge it was put past.
        return [(a if space else q, loc) for (a, loc, space), q in zip(out, stop_points(anchors))]

    def _path_points(self, scene_id: str, path: list[str]) -> list[Point]:
        return [point for point, _ in self._path_stops(scene_id, path)]

    def _bends(self, scene_id: str, path: list[str]) -> list[Point | None]:
        """Each leg's bend, in art pixels: a hop from one part to the next (both stops parts, not spaces) curves
        up in a gentle arc, the hand-off a textbook draws; a flow into, out of or through a space runs straight.
        The particles ride the same curve as the arrow."""
        pairs = self._path_stops(scene_id, path)
        points, stops = [pt for pt, _ in pairs], [loc for _, loc in pairs]
        bends: list[Point | None] = []
        for i, (a, b) in enumerate(zip(points, points[1:])):
            if self._is_space(scene_id, stops[i]) or self._is_space(scene_id, stops[i + 1]):
                bends.append(None)
                continue
            length = math.dist(a, b) or 1.0
            nx, ny = (b[1] - a[1]) / length, -(b[0] - a[0]) / length  # perpendicular to the leg...
            if ny > 0:
                nx, ny = -nx, -ny  # ...on its upper side
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            bends.append((mid[0] + nx * ROUTE_BEND * length, mid[1] + ny * ROUTE_BEND * length))
        return bends

    def _legs(self, scene_id: str, path: list[str]) -> list[tuple[list[Point], bool, int, int, object]]:
        """A route's arrows, in art pixels: (the line it draws, ends in a space, its first stop, its last stop, the
        part it passes through or None). A
        leg leaves the edge of the part it starts from and ends at the edge of the part it reaches, curved when it
        is a hop from part to part; a flow through a part from one space to the other is one straight leg."""
        pairs = self._path_stops(scene_id, path)
        points, stops = [pt for pt, _ in pairs], [loc for _, loc in pairs]
        bends = self._bends(scene_id, path)
        space = [self._is_space(scene_id, loc) for loc in stops]
        legs, i = [], 0
        while i < len(points) - 1:
            if i + 2 < len(points) and space[i] and not space[i + 1] and space[i + 2]:
                legs.append(([points[i], points[i + 2]], True, i, i + 2, stops[i + 1]))
                i += 2
                continue
            a, b = points[i], points[i + 1]
            if bends[i] is not None:
                curve = [bezier(a, bends[i], b, k / CURVE_STEPS) for k in range(CURVE_STEPS + 1)]
                # From where the curve leaves the part it starts in to where it enters the part it reaches.
                first = next((k for k, q in enumerate(curve) if not _inside(q, stops[i].box)), 0)
                last = next((k for k in range(len(curve) - 1, -1, -1) if not _inside(curve[k], stops[i + 1].box)),
                            len(curve) - 1)
                legs.append((curve[first:last + 1] if last > first else [a, b], False, i, i + 1, None))
            else:
                legs.append(([a if space[i] else _leave(a, b, stops[i].box),
                              b if space[i + 1] else _leave(b, a, stops[i + 1].box)], space[i + 1], i, i + 1, None))
            i += 1
        return legs

    def _arrow_points(self, scene_id: str, cam: Cam, view: Rect) -> list[Point]:
        """Points along every arrow the scene draws at any time in the video, in frame pixels: what labels and
        reaction pills keep clear of. Every arrow, not only those on screen, so a label is placed once for the
        scene and does not jump as each arrow appears."""
        if scene_id not in self._scene_routes:
            routes = []
            for beat in self.timeline.beats:
                for route in beat.routes if beat.scene == scene_id else []:
                    if (route.path, route.label, route.color) not in routes:
                        routes.append((route.path, route.label, route.color))
            samples, drawn = [], set()
            for path, label, color in routes:
                pairs = self._path_stops(scene_id, path)
                for line, _, first, last, _ in self._legs(scene_id, path):
                    hop = (pairs[first][1].name, pairs[last][1].name, label, color)
                    if hop in drawn:
                        continue  # drawn once, by the route that first has it (_routes)
                    drawn.add(hop)
                    for start, end in zip(line, line[1:]):
                        n = max(int(math.dist(start, end) / ARROW_SAMPLE), 1)
                        samples += [(p.lerp(start[0], end[0], k / n), p.lerp(start[1], end[1], k / n))
                                    for k in range(n + 1)]
            self._scene_routes[scene_id] = samples
        scale, dx, dy = to_frame(cam, view, self.scene(scene_id))
        return [(x * scale + dx, y * scale + dy) for x, y in self._scene_routes[scene_id]]

    def _routes(self, canvas: skia.Canvas, beat, cam: Cam, view: Rect, t: float) -> None:
        """Each path the scene's particles have travelled, as arrows from structure to structure: each leg leaves
        the edge of the part it starts from and ends with its tip at the edge of the part it reaches (a space has
        no edge: its leg starts or ends just inside it, beside its neighbour). A leg too short to hold its head is
        left out. A flow through a part is drawn faded across the part's box, as if passing behind it; then the
        markers of what travels each way."""
        art = self.scene(beat.scene)
        scale, dx, dy = to_frame(cam, view, art)
        canvas.save()
        canvas.clipRect(skia.Rect.MakeLTRB(*view))
        badges: list[tuple[Point, str, str, float]] = []
        drawn: set[tuple] = set()  # the hops drawn so far, so a later route never draws one again beside it
        for route in beat.routes:
            pairs = self._path_stops(beat.scene, route.path)
            points = [pt for pt, _ in pairs]
            # The arrow grows behind the lead particle on its first trip, then stays whole.
            set_off = self.plans[self.beat_index(route.since)].flow_in
            lead, at, done = lead_leg(points, t - set_off, self._bends(beat.scene, route.path))
            if t < set_off:
                continue
            grown = []
            for line, into_space, first, last, crossed in self._legs(beat.scene, route.path):
                hop = (pairs[first][1].name, pairs[last][1].name, route.label, route.color)
                if hop in drawn:
                    continue  # an earlier route on the scene draws it, from when its own particles passed
                if done or at >= last:
                    grown.append((line, into_space, crossed))
                elif at >= first and (part := _up_to(line, lead)):
                    grown.append((part, True, crossed))  # up to the particle, which covers its head
                else:
                    continue
                drawn.add(hop)
            for line, into_space, crossed in grown:
                frame = [(x * scale + dx, y * scale + dy) for x, y in line]
                gap = ROUTE_GAP if into_space else ROUTE_EDGE_GAP
                while len(frame) > 2 and math.dist(frame[-2], frame[-1]) <= gap:
                    frame.pop()  # the tip stops short of where it ends, trimmed from the end of the line
                end, before = frame[-1], frame[-2]
                length = math.dist(before, end)
                if length <= gap:
                    continue
                frame[-1] = (end[0] - (end[0] - before[0]) / length * gap, end[1] - (end[1] - before[1]) / length * gap)
                alpha = edge_fade(*frame[-1], view)
                if crossed is None:
                    p.draw_route_leg(canvas, frame, route.color, alpha)
                    continue
                box = (crossed.box.x0 * scale + dx, crossed.box.y0 * scale + dy,
                       crossed.box.x1 * scale + dx, crossed.box.y1 * scale + dy)
                for piece, inside, last_piece in _split_at_box(frame, box):
                    p.draw_route_leg(canvas, piece, route.color, alpha * ((1 - ROUTE_BEHIND) if inside else 1.0),
                                     head=last_piece)
            if done and route.label and grown:  # what travels this way, at the arrow's tail, once it has passed
                tail = grown[0][0][0]
                at_frame = (tail[0] * scale + dx, tail[1] * scale + dy)
                if all(math.dist(at_frame, other[0]) > BADGE_APART for other in badges):
                    badges.append((at_frame, route.color, route.label,
                                   p.ease((t - set_off - trip_s(points)) / LABEL_IN_S) * edge_fade(*at_frame, view)))
        for at_frame, color, label, alpha in badges:
            p.draw_marker(canvas, at_frame, color, label, alpha)
        canvas.restore()

    def _particles(self, canvas: skia.Canvas, beat, plan: BeatPlan, cam: Cam, view: Rect, t: float) -> None:
        """Each path's particles, between its structures' label anchors (always inside them),
        over the diagram and under the labels; they stop beside each label's dot."""
        alpha = p.ease((t - plan.flow_in) / FLOW_IN_S) * p.ease((plan.flow_out - t) / FLOW_IN_S)
        if alpha <= 0.01:
            return
        located = self.locations.scene(beat.scene)
        scale, dx, dy = to_frame(cam, view, self.scene(beat.scene))

        def on_frame(q: Point) -> Point:
            return q[0] * scale + dx, q[1] * scale + dy

        elapsed = t - plan.flow_in
        canvas.save()
        canvas.clipRect(skia.Rect.MakeLTRB(*view))
        for flow in beat.particles:
            points = self._path_points(beat.scene, flow.path)
            bends = None if flow.blocked else self._bends(beat.scene, flow.path)
            if len(points) < 2:
                continue
            if flow.blocked:
                # It goes once, and holds at the blocker; the stream queues up behind it under a cross.
                arrived = trip_s(points)
                lead = lead_at(points, min(elapsed, arrived))[0]
                trail = [on_frame(lead_at(points, then)[0]) for k in range(1, TRAIL + 1 if flow.trail else 1)
                         if 0 <= (then := elapsed - k * TRAIL_DT) < arrived]
                stream = pile_at(points, elapsed)
            else:
                lead, trip = lead_at(points, elapsed, bends)
                trail = []
                for k in range(1, TRAIL + 1 if flow.trail else 1):
                    then = elapsed - k * TRAIL_DT
                    earlier, earlier_trip = lead_at(points, then, bends)
                    if then < 0 or earlier_trip != trip:  # no trail from before it set off, or from the last trip
                        break
                    trail.append(on_frame(earlier))
                stream = stream_at(points, elapsed)
            lead_xy, spaced = on_frame(lead), []
            for dot in trail:
                if math.dist(dot, spaced[-1] if spaced else lead_xy) >= TRAIL_GAP:
                    spaced.append(dot)
            if not flow.blocked and any(r.path == flow.path and r.label == flow.label for r in beat.routes):
                stream = []  # the route's arrows show the way: dots along them would read as beads on a string
            p.draw_particles(canvas, flow.color, [(on_frame(q), fade) for q, fade in stream],
                             lead_xy, spaced, alpha, flow.label)
            if flow.blocked and elapsed >= trip_s(points):
                (x0, y0), (x1, y1) = on_frame(points[-2]), on_frame(points[-1])
                length = math.hypot(x1 - x0, y1 - y0) or 1.0
                ahead = p.MARKER_R + p.BLOCK_R + 8  # just past the lead, on the far side of the blocker
                pulse = 0.8 + 0.2 * math.sin(2 * math.pi * 1.5 * t)
                p.draw_block(canvas, (x1 + (x1 - x0) / length * ahead, y1 + (y1 - y0) / length * ahead),
                             alpha * pulse * p.ease((elapsed - trip_s(points)) / FLOW_IN_S))
        canvas.restore()

    def _band_beat(self, i: int, t: float):
        """The beat whose line the band shows at t: beat i's own, or in the song, late in an
        instrumental gap, the next line's (none of its words has started, so none is marked).
        The camera, spotlight and caption keep to the beat: only the words lead."""
        beats = self.timeline.beats
        beat = beats[i]
        if beat.line_id is not None or self.timeline.video != "song" or i + 1 == len(beats) or not beats[i + 1].words:
            return beat
        lead_at = beat.t1 - LEAD_S
        if lead_at - beat.t0 < BLINK_S:
            lead_at = beat.t0
        return beats[i + 1] if t >= lead_at else beat

    def _band_words(self, beat) -> list[p.BandWord]:
        shown = beat.line.split()
        texts = shown if len(shown) == len(beat.words) else [w.word for w in beat.words]
        words = [p.BandWord(text=x, start=w.start, end=w.end) for x, w in zip(texts, beat.words)]
        for r in beat.reveal:
            first = next((k for k, w in enumerate(beat.words) if abs(w.start - r.t) < 1e-3), None)
            if first is None:
                continue
            parts = r.name.lower().split()
            for k in range(first, min(first + max(len(parts), 1), len(words))):
                if k == first or any(part.startswith(beat.words[k].word.lower()[:4]) for part in parts):
                    words[k].color = self.timeline.palette[r.name]
        return words

