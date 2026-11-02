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
import re
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import skia

from studio import jobs, media
from studio.checks import CARRIED_WORDS
from studio.render import axes, routing
from studio.render import primitives as p
from studio.render.layouts import LAYOUTS, Layout, Rect
from studio.schemas import Arrows, Locations, SceneLocations, Timeline

GHOST_WASH = 0.85  # how pale an unrevealed structure is
SPOT_WASH = 0.6  # how pale everything but the spotlit structure is
EDGE_BLUR = 3.0  # sigma of a ghost's edge, in art pixels
SPOT_BLUR = 1.2  # a spotlight's edge stays crisp, so the bright structure keeps its outline
REVEAL_S = 0.4  # ghost to full
LABEL_IN_S = 0.35
# Past LABELS_CALM labels on screen, those of parts the line is not about rest at LABEL_REST, and their reaction
# pills with them: round 21's digestion overview showed seven labels, two reaction pills and a five-row legend at
# once, and a 7th grader could not tell which part the voice meant. A part is in play while this line or the one before names it, it is spotlit or framed,
# or particles travel to it; a label eases between rest and full as a line starts. At rest a label is gone: grey
# pills at 0.4 still crowded the picture, and read as switched off rather than as quieter (the owner, Oct 31).
LABELS_CALM = 4
LABEL_REST = 0.0
SPACE_SHARE = 0.2  # a structure whose box covers this share of its picture is a space: its pill sits inside it
SPACE_REACH = 55.0  # art pixels: a path's stop in a space sits this far past its neighbour's edge
ACROSS_SHARE = 0.5  # ...and, beside an upright wall, at least this share of the way on to the space's middle
LANE_GAP = 64.0  # art pixels between two things travelling the same stops side by side: room for two marked
# particles (Na⁺ out, K⁺ in) to pass without touching
SPACE_SPAN = 0.6  # or whose box spans this share of its width (a lumen is a thin strip across the picture)
SPACE_FILL = 0.25  # and fills at least this share of its box: a curve across a chart has a big box but is no space
# Fewer kinds of motion (the owner, Nov 1): every kind guesses where things are in a painted picture, from the outlines
# SAM finds, and each guess was a way to draw motion wrong. Off until someone misses them:
CROWDS = False  # a space filling with dots (Bundle.builds_up), placed through the space's outline
# The sanity gate: an arrow failing any of these is left out (Renderer.left_out), never drawn wrong. Nov 1's wrong
# arrows each failed one: K+ sideways into the cell, two with no length, H+ ending above the picture.
ARROW_MIN = 25.0  # art pixels: shorter reads as no arrow
ARROW_REACH = 60.0  # art pixels an end may fall outside the box of the part or space it starts or ends in
ARROW_TURN = 0.3  # the least cosine between an arrow and the way from where it starts to where it goes
LINE_RATIO = 12.0  # a structure this many times longer than it is wide is a line: its label hangs from one end
LINE_END = 0.12  # how far in from that end, as a share of its length: on the line, clear of its tip
SPOT_S = 0.6
MOVE_S = 1.8  # a camera move between two stations
SETTLE_S = 2.5  # the drift after a cut, so every shot opens moving
SETTLE_ZOOM = 1.06
# A shot never stands still: from the start of its line the camera drifts, pushing in (or, after pulling back, pulling
# on back while the picture still fills the view) up to DRIFT further, about two thirds of it in DRIFT_S, ever more
# slowly. Lines in a row framing the same target are one shot, so the drift runs on across them.
DRIFT = 0.05
DRIFT_S = 8.0
STAGE_FADE_S = 0.8  # one stage of a process fading into the next (Timeline.stages), in place of a cut
NOTICE_W = 440  # frame px at the right kept for a picture's notice card (Timeline.notices); the picture moves over
NOTICE_GAP = 16  # frame px above and below the card in an upright frame, where it sits across the top of the picture
NOTICE_MAX = 3  # notes on the card; any more are counted
NOTICE_FONT = 22
AI_TAG_FONT = 18  # the tag on every AI-made picture (Timeline.ai_pictures) without a notice card
BRAND_GAP = 12  # frame px between the brand and the title band's bottom: a two-line topic clears it
HOOK_ZOOM = 1.15  # frame 0 starts this far in and pulls back: motion from the first frame
# A cut between two scenes that draw the same part, one far closer than the other (an overview and its close-up),
# goes through that part: the last ZOOM_THROUGH_S of the wider shot push in on it before the cut, and a cut back out
# opens on it close and pulls back. A learner sees where the close-up is. Only when the part is ZOOM_THROUGH_SCALE
# times bigger, by area, on one scene than on the other.
ZOOM_THROUGH_S = 0.8
ZOOM_THROUGH_SCALE = 2.0
ZOOM_THROUGH_MAX = 3.0  # as close as the camera goes on the part: it stops sooner where the part fills the view
FIT_PAD = 1.35  # a zoomed-in target keeps this much room around its box
CLOSE_MAX_ZOOM = 3.0  # of the whole art: as close as a shot counted from the whole scene goes; a picture blurs past it
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
# A flow that travels a long way along several parts (filtrate down the nephron, magma up through the volcano,
# sediment down the river) is drawn as an arrow where it passes from each part into the next (routing.handoff),
# not as particles: in rounds 20 and 21 the particles cut across the nephron's bends and rose through the rock of the
# volcano, following masks that are only roughly the parts. A crossing (O₂ across the membrane, H⁺ through ATP
# synthase, salt out of the tubule) keeps its particles: the short trip through the wall is what it teaches. The
# bundle says which (Particles.moves); where it does not, the route does (_journey).
BENT = 1.5  # a route this many times longer than the straight line between its ends bends with its parts: a journey
THROUGH_MAX = 200.0  # art pixels: a route running further than this inside a part between its ends travels along it
HANDOFF_STEP_S = 0.5  # between one hand-off's arrow and the next's, as they draw in and as the pulse runs along them
HANDOFF_GROW_S = 0.35  # an arrow drawing itself in, from its tail
HANDOFF_REST = 0.7  # how strong an arrow is while the pulse is on another
ROUTE_GROW_S = 1.0  # an arrow along a whole way drawing itself in
# Off a graph every flow is an arrow, never particles travelling along it (the owner, Nov 1: dots running across a
# painted picture went sideways and through the membrane): one particle of what it carries sits at the arrow's tail,
# jiggling in place (at its head, before the cross, when the flow is blocked), and the spaces' crowds jiggle in theirs.
FACE_DEPTH = 40.0  # art pixels: a path meets a part at its end this far in from its face toward the stop beside it
TAIL_JIGGLE_PX = 5.0  # frame pixels the particle at an arrow's tail jiggles
TAIL_SIZE = 0.9  # its size, as a share of a lead particle's
# How many ride an arrow: as many as the moment's own words count for what moves ("three sodium ions slip in"), packed
# together at its head and waiting at its tail (the owner, Nov 1), up to COUNT_MAX; one when they count none or more.
COUNT_MAX = 5
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "a dozen": 12, "dozens of": 24}
# Where each of a few particles sits around the head, in their own widths: across the way they move, then behind.
CLUSTER = {1: [(0.0, 0.0)], 2: [(-0.5, 0.0), (0.5, 0.0)], 3: [(-0.5, 0.29), (0.5, 0.29), (0.0, -0.58)],
           4: [(-0.5, 0.5), (0.5, 0.5), (-0.5, -0.5), (0.5, -0.5)],
           5: [(-1.0, 0.43), (0.0, 0.43), (1.0, 0.43), (-0.5, -0.43), (0.5, -0.43)]}
# The arrow moves (the owner, Nov 1): the route is a faint dotted track, and a short solid arrow glides along it from
# tail to head, again and again, so the way the flow goes is seen and the arrow on screen stays short.
GLIDE_SHARE = 0.35  # the gliding arrow's length, as a share of its route...
GLIDE_MAX = 170.0  # ...and at most this many frame pixels
GLIDE_SPEED = 200.0  # frame pixels a second...
# ...or slower, so a glide takes this long at least: on a short arrow (oxygen tank to chamber) one at 300 crossed in
# a third of a second, and again and again, too fast to follow (the owner, Nov 2, as crossings at 2 s were, Nov 1)
GLIDE_MIN_S = 3.0
GLIDE_PAUSE_S = 0.5  # between one glide and the next
TRACK_ALPHA = 0.95  # the track under it, black on white: plain on any picture between glides (Nov 2)
ARROW_SEEN = 0.6  # a still to look at an arrow by shows its gliding arrow this share of the way along (arrow_time)
# A crossing (straight through one part, _crossing) is its particles crossing, not an arrow (the owner, Nov 1): the line
# is sure now, and ions streaming through the pump look like what the textbook says. As many as the words count (three
# Na+), else CROSS_MANY, one after another, each a little to one side of the line and wobbling, so they read as a flow.
CROSS_S = 3.5  # seconds a particle takes to cross, on average: at 2 they looked rushed (the owner, Nov 1)
# One pace for all, evenly spaced, and only as many as the way has room for (CROSS_ROOM): each at its own pace,
# three "gas" bunched on the rocket's short nozzle, stacked on one another and popping in and out (the owner, Nov 2).
CROSS_MANY = 3  # in flight at once when the words give no count...
CROSS_ROOM = 1.5  # ...and the way has room for them this many particle widths apart
CROSS_SPREAD = 12.0  # frame pixels each keeps to one side of the line, at most
CROSS_WIGGLE = 8.0  # frame pixels each swings left and right of its place as it goes...
CROSS_SWINGS = 1.5  # ...this many times on the way across, as a molecule jostles through
CROSS_FADE = 0.15  # the share of the way each fades in over as it leaves, and out over as it arrives
# A note (an arrow's one-liner): tried this far (art pixels) from what it is about,
# on each side, and kept where it covers the least of the picture, nearer winning a tie (NOTE_FAR at the farthest),
# clear of the parts' label points and other notes (NOTE_CROWD each). Beside its arrow or not at all: placed farther,
# the gunpowder's note sat across the picture with a line through the Bastille, and read as pointing at nothing (Nov 1).
NOTE_REACH = (30.0, 90.0)
NOTE_SAID = 0.6  # a note is left out when this share of its words is in the line or the key line it first plays under
NOTE_SIDES = ((1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (-1, -1), (1, 1), (-1, 1))
NOTE_FAR = 0.05
NOTE_CROWD = 0.3
STREAM_GAP = 160.0  # art pixels between the particles streaming along the path, each written with what it is
ROUTE_BEND = 0.25  # a hop's curve bows out by this share of its length, at its middle
CURVE_STEPS = 24  # straight pieces a curved arrow is drawn with
ARROW_SAMPLE = 14.0  # art pixels between the points along an arrow that labels keep clear of
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
THIN_MIN = 2  # dots where particles are scarce (Crowd.thin), however small the space
# A crowd is there from the start (timeline.CROWD_START): in a space not yet named it shows half pale. Round 22's
# cytoplasm, named at 66 s, hid its potassium till then.
CROWD_GHOST = 0.5
TINT_STEP = 0.005  # a crowd's tint changes in steps this big: the picture with it is kept while it holds
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
# Parts react to what happens to them, so the picture moves with the story: a part pops POP bigger as its name is
# first heard, over POP_S; bumps BUMP bigger as a particle reaches it, over BUMP_S; and pulses on the beat while
# particles pass through it (a pump, a carrier handing on): on each beat of the song (Timeline.bpm; PULSE_HZ in the
# lesson) it swells quickly by PULSE_GROW, at most PULSE_PX art pixels at its far edge so a long part only breathes,
# and eases back. Each is drawn over the picture's own copy of the part, grown about its middle, so the copy never
# shows: rocking turned the copy aside and its edge showed beneath, the part doubled (the owner, Nov 1). Never a
# space, a part that turns, or anything on a graph.
POP = 0.08
POP_S = 0.45
BUMP = 0.05
BUMP_S = 0.35
PULSE_GROW = 0.05
PULSE_PX = 14.0
PULSE_HZ = 1.2  # the lesson's pulses a second: no song to beat to
PULSE_ATTACK = 0.15  # the share of a beat it takes to swell; it eases back over the rest
CUT_BLUR = 0.8  # a reacting part's edge, in art pixels: soft, so the mask's steps do not show
AXIS_GAP = 10.0  # frame pixels between an axis's end and its name
VALUE_GAP = 8.0  # frame pixels between a value's point and its writing (Beat.values)
VALUE_FONT, TICK_FONT = 1.0, 0.85  # an amount's writing and a tick's, as shares of the label font
NAME_FONT = 1.1  # a name a graph's drawing placed (Value.label), as a share of the label font
MEANING_FONT = 0.8  # its meaning written under it (Beat.meanings), as a share of the label font
MEANING_OUT_S = 0.6  # a meaning fading out, as only the name stays
ARROW_WORD_FONT, ARROW_WORD_GAP = 0.9, 14.0  # a graph arrow's word beside it: a share of the label font; frame pixels
VALUE_EDGE = 6.0  # frame pixels: a value this near the edge of where it may be written fades out, as a camera move
# takes it off; a tick at rest just under its axis, low in the picture, stays clear
DRAWN_INK = 15  # how far (the largest RGB channel) a drawn pixel is from the background, as _drawn_box has it

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


def pulse(t: float, hz: float) -> float:
    """A beat's swell at t, 0-1: up quickly over PULSE_ATTACK of the beat, then easing back to rest by the next."""
    phase = (t * hz) % 1.0
    if phase < PULSE_ATTACK:
        return p.ease(phase / PULSE_ATTACK)
    return ((1 - phase) / (1 - PULSE_ATTACK)) ** 2


def painted_label_boxes(pixels: np.ndarray) -> list[Rect]:
    """Each painted label's white box in a picture (an RGB or BGR array): a near-white patch, wider than tall, nearly
    all white, grey or black, with dark letters in it, as (left, top, right, bottom) in its pixels. A white leader
    line from a box to its part joins the patch; the box is its rows and columns at least half white (_core), as a
    line is a pixel or two across: joined, it reached into the part's colour, and the rocket's chamber gave 2 of 6
    (Nov 2)."""
    a = pixels.astype(np.int16)
    white = (a.min(-1) > 228) & (a.max(-1) - a.min(-1) < 24)
    dark = a.max(-1) < 120
    plain = (a.max(-1) - a.min(-1)) < 30
    h, w = white.shape
    seen = np.zeros_like(white)
    found = []
    for y0, x0 in zip(*np.nonzero(white)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        todo, ys, xs = [(y0, x0)], [], []
        while todo:
            y, x = todo.pop()
            ys.append(y), xs.append(x)
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and white[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    todo.append((ny, nx))
        if len(ys) < 40:
            continue
        box = _core(np.array(ys), np.array(xs))
        if box is None:
            continue
        top, left, bottom, right = box
        bh, bw = bottom - top + 1, right - left + 1
        if not (8 <= bh <= 90 and 20 <= bw <= 400 and bw > bh):
            continue
        area = bh * bw
        inside = (slice(top, bottom + 1), slice(left, right + 1))
        letters = dark[inside].sum() / area
        if plain[inside].mean() > 0.9 and white[inside].sum() / area > 0.45 and 0.03 < letters < 0.45:
            found.append((float(left), float(top), float(right + 1), float(bottom + 1)))
    return found


CORE_ROW, CORE_COL = 10, 5  # white pixels a row and a column of a label's box hold at least; a leader line, 1 to 3


def _core(ys: np.ndarray, xs: np.ndarray) -> tuple[int, int, int, int] | None:
    """A white patch's box without its leader line: its longest run of rows holding CORE_ROW white pixels or more,
    then within them its longest run of columns holding CORE_COL or more. (top, left, bottom, right), or None."""
    def longest(counts: np.ndarray, least: int) -> tuple[int, int] | None:
        best, start = None, None
        for k, ok in enumerate(np.append(counts >= least, False)):
            if ok and start is None:
                start = k
            elif not ok and start is not None:
                if best is None or k - start > best[1] - best[0] + 1:
                    best = (start, k - 1)
                start = None
        return best

    y0, x0 = int(ys.min()), int(xs.min())
    rows = longest(np.bincount(ys - y0), CORE_ROW)
    if rows is None:
        return None
    keep = (ys - y0 >= rows[0]) & (ys - y0 <= rows[1])
    cols = longest(np.bincount(xs[keep] - x0), CORE_COL)
    if cols is None:
        return None
    return rows[0] + y0, cols[0] + x0, rows[1] + y0, cols[1] + x0


class SceneArt:
    """One scene's art and masks, and the washes made from them, composed on demand."""

    def cells(self, name: str) -> np.ndarray:
        """The part's outline on the routing grid (routing.cells)."""
        if name not in self._cells:
            self._cells[name] = routing.cells(_read(self._mask_files[name], skia.kGray_8_ColorType) > 127)
        return self._cells[name]

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
        self._half = pixels[::2, ::2, :3].copy()  # for finding its painted labels (label_boxes())
        self._label_boxes: list[Rect] | None = None
        self.masks: dict[str, skia.Image] = {}
        self._mask_files: dict[str, Path] = {}
        self._crowd_points: dict[str, list[Point]] = {}
        self._discs: dict[str, tuple[float, float, float] | None] = {}
        self._glows: dict[str, skia.Image] = {}
        self._cuts: dict[str, skia.Image] = {}
        self._axes: tuple[axes.Line, axes.Line] | None = None
        self._drawn: np.ndarray | None = None
        self._cells: dict[str, np.ndarray] = {}
        self._shapes: dict[str, tuple[float, tuple[float, float] | None]] = {}
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

    def _shape(self, name: str) -> tuple[float, tuple[float, float] | None]:
        """How much of its box the structure fills, and for a line (a curve on a chart, an arrow's shaft) the point
        its label hangs from: near one end, as a textbook labels a curve, and not where it crosses the others."""
        if name not in self._shapes:
            ys, xs = np.nonzero(_read(self._mask_files[name], skia.kGray_8_ColorType) > 127)
            if len(xs) < 2:
                self._shapes[name] = (1.0, None)
                return self._shapes[name]
            fill = len(xs) / ((np.ptp(xs) + 1) * (np.ptp(ys) + 1))
            pts = np.stack([xs, ys], axis=1).astype(float)
            mid = pts.mean(axis=0)
            _, _, axes_ = np.linalg.svd(pts - mid, full_matrices=False)
            along = (pts - mid) @ axes_[0]
            length = float(np.ptp(along))
            end = None
            if length > LINE_RATIO * len(xs) / max(length, 1.0):  # long and thin: its width is area / length
                lo, hi = float(along.min()), float(along.max())
                ends = [pts[(along >= hi - LINE_END * length) & (along <= hi - LINE_END * length / 3)].mean(axis=0),
                        pts[(along <= lo + LINE_END * length) & (along >= lo + LINE_END * length / 3)].mean(axis=0)]
                dx, dy = abs(axes_[0][0]), abs(axes_[0][1])
                if min(dx, dy) > 0.35:  # sloped: its upper end, where a chart labels its curves
                    pick = min(ends, key=lambda e: e[1])
                else:  # level or upright: the end away from the picture's middle (a price line's end at its axis)
                    pick = max(ends, key=lambda e: math.dist(e, self.centre))
                end = (float(pick[0]), float(pick[1]))
            self._shapes[name] = (float(fill), end)
        return self._shapes[name]

    def fill(self, name: str) -> float:
        return self._shape(name)[0]

    def line_end(self, name: str) -> tuple[float, float] | None:
        return self._shape(name)[1]

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
            loc = self.located.get(name)
            if loc is not None and self.fill(name) < SPACE_FILL:
                # An outline that is a thin strip of its space (round 22's cytoplasm came back as the band just under
                # the membrane: a full crowd of K⁺ was four dots): the space's box, but where another part is drawn.
                box = np.zeros_like(inside)
                box[int(loc.box.y0):int(loc.box.y1), int(loc.box.x0):int(loc.box.x1)] = True
                for other, path in self._mask_files.items():
                    if other != name:
                        box &= ~(_read(path, skia.kGray_8_ColorType) > 127)
                inside |= box
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

    def cut(self, name: str) -> skia.Image:
        """The structure's mask with a soft edge, alpha only: what a reacting part is cut out of the picture with."""
        if name not in self._cuts:
            surface = self._surface(alpha_only=True)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            canvas.drawImage(self.masks[name], 0, 0, skia.SamplingOptions(),
                             skia.Paint(Color=skia.ColorWHITE, ImageFilter=skia.ImageFilters.Blur(CUT_BLUR, CUT_BLUR)))
            self._cuts[name] = surface.makeImageSnapshot()
        return self._cuts[name]

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

    def axis_lines(self) -> tuple[axes.Line, axes.Line]:
        """Its graph's x axis and y axis, in art pixels, as found in the picture (studio/render/axes.py). One not
        found runs along the bottom or the left of what is drawn, from the end of the other where it was found."""
        if self._axes is None:
            pixels = self.art.toarray(colorType=skia.kBGRA_8888_ColorType)
            covered = np.zeros(pixels.shape[:2], bool)
            for mask_file in self._mask_files.values():
                covered |= _read(mask_file, skia.kGray_8_ColorType) > 127
            far = axes.distance(pixels, (skia.ColorGetB(self.paper), skia.ColorGetG(self.paper),
                                         skia.ColorGetR(self.paper)))
            del pixels
            across, up = axes.find(far, covered)
            x0, y0, x1, y1 = self.content
            across = across or axes.Line(at=up.end if up else y1, start=up.at if up else x0, end=x1)
            up = up or axes.Line(at=across.start, start=y0, end=across.at)
            self._axes = across, up
            self._drawn = (far > DRAWN_INK) | covered
        return self._axes

    def label_boxes(self) -> list[Rect]:
        """The labels painted on the picture, each black on its small white box (art.BOXED), as art-pixel rects:
        found by code, once, so an arrow's caption keeps off them (Nov 2: "Third Estate pays the heaviest taxes" lay
        across the painted "Privileges"). None on a picture whose labels have no boxes."""
        if self._label_boxes is None:
            self._label_boxes = [tuple(v * 2.0 for v in box) for box in painted_label_boxes(self._half)]
        return self._label_boxes

    def ink_share(self, box: tuple[float, float, float, float]) -> float:
        """How much of an art-pixel box has something drawn in it, as drawn_share() has it, but what lies off the art
        counts as bare: a post-it may sit in the frame beside the picture."""
        self.axis_lines()
        x0, y0 = max(int(box[0]), 0), max(int(box[1]), 0)
        x1, y1 = min(int(math.ceil(box[2])), self.width), min(int(math.ceil(box[3])), self.height)
        whole = max((box[2] - box[0]) * (box[3] - box[1]), 1.0)
        return float(self._drawn[y0:y1, x0:x1].sum()) / whole if x1 > x0 and y1 > y0 else 0.0

    def drawn_share(self, box: tuple[float, float, float, float]) -> float:
        """How much of an art-pixel box has something drawn in it: 1 over the picture, 0 over bare background, and
        1 off the art, where nothing can be read."""
        self.axis_lines()
        x0, y0 = max(int(box[0]), 0), max(int(box[1]), 0)
        x1, y1 = min(int(math.ceil(box[2])), self.width), min(int(math.ceil(box[3])), self.height)
        inside = max(x1 - x0, 0) * max(y1 - y0, 0)
        whole = max((box[2] - box[0]) * (box[3] - box[1]), 1.0)
        return (float(self._drawn[y0:y1, x0:x1].sum()) + whole - inside) / whole if inside else 1.0

    def compose(self, ghosts: dict[str, float], spots: dict[str, float],
                tints: tuple = ()) -> tuple[tuple, skia.Image]:
        """The art with each ghost and spotlight wash at its level (0-1), and each crowd's tint over its space
        ((name, colour, alpha), Renderer._crowd_tints), kept for reuse; and its key."""
        key = (tuple(sorted((n, round(v, 3)) for n, v in ghosts.items() if v > 0.002 and n in self.masks)),
               tuple(sorted((n, round(v, 3)) for n, v in spots.items() if v > 0.002 and n in self.masks)),
               tuple(sorted(tint for tint in tints if tint[0] in self.masks and tint[2] > 0)))
        if not key[0] and not key[1] and not key[2]:
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
        if key[1]:  # added up, then washed once: two spotlights crossing wash the rest as one does, not less
            canvas.saveLayerAlpha(None, round(255 * SPOT_WASH))
            for name, level in key[1]:
                canvas.drawImage(self.spot(name), 0, 0, skia.SamplingOptions(),
                                 skia.Paint(Color=self.paper, Alphaf=level, BlendMode=skia.BlendMode.kPlus))
            canvas.restore()
        for name, color, alpha in key[2]:
            canvas.drawImage(self.ghost(name), 0, 0, skia.SamplingOptions(),
                             skia.Paint(Color=p.argb(color), Alphaf=alpha))
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


def fills(view: Rect, picture: Rect) -> bool:
    """Whether the picture, where the frame shows it, covers the whole view with a pixel to spare, so paper cleared
    under it would be painted over everywhere: the art is opaque (SceneArt), and so is every wash composed over it."""
    return (picture[0] <= view[0] - 1 and picture[1] <= view[1] - 1
            and picture[2] >= view[2] + 1 and picture[3] >= view[3] + 1)


# A post-it sits this far (frame pixels) from the ring of the label it is about, or, where every spot that near
# covers the picture, further out, with a line back to the ring: beside it, below, above, then at a corner. Never
# further: notes that sat 180 pixels out, or were pushed further by the frame's edge, drew long lines across the
# picture (the owner, Nov 2). A note whose line would still run past POSTIT_MAX_LINE, or whose every spot covers
# the picture (POSTIT_MAX_COVER), is not shown.
POSTIT_GAP = 14.0
POSTIT_REACH = (POSTIT_GAP, 90.0)
POSTIT_MAX_LINE = 130.0
POSTIT_MAX_COVER = 0.35
POSTIT_SIDES = ((1, 0), (-1, 0), (0, 1), (0, -1), (-1, 1), (1, 1), (-1, -1), (1, -1))  # (across, down) from the ring
POSTIT_FRAMINGS = 31  # framings a post-it's spot is judged over: the whole scene and up to 10 beats, 3 times each


def _postit_rect(ring: Rect, size: tuple[float, float], side: int, gap: float) -> Rect:
    """A post-it of this size `gap` frame pixels off the ring, on POSTIT_SIDES[side]: level with the ring's middle
    across or up and down from it, or past one of its corners."""
    (sx, sy), (w, h) = POSTIT_SIDES[side], size
    x0, y0, x1, y1 = ring
    mx = (x1 + gap + w / 2) if sx > 0 else (x0 - gap - w / 2) if sx < 0 else (x0 + x1) / 2
    my = (y1 + gap + h / 2) if sy > 0 else (y0 - gap - h / 2) if sy < 0 else (y0 + y1) / 2
    return mx - w / 2, my - h / 2, mx + w / 2, my + h / 2


def _within(rect: Rect, bounds: Rect) -> Rect:
    """The rect moved, not resized, to lie inside bounds where it fits."""
    x0, y0, x1, y1 = rect
    shift_x = max(bounds[0] - x0, 0) - max(x1 - bounds[2], 0)
    shift_y = max(bounds[1] - y0, 0) - max(y1 - bounds[3], 0)
    return x0 + shift_x, y0 + shift_y, x1 + shift_x, y1 + shift_y


def _overlap(a: Rect, b: Rect) -> float:
    """How much of rect a overlaps rect b, as a share of a."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h / max((a[2] - a[0]) * (a[3] - a[1]), 1.0) if w > 0 and h > 0 else 0.0


def _gap(a: Rect, b: Rect) -> float:
    """How far apart two rects are: 0 where they touch or overlap."""
    return math.hypot(max(b[0] - a[2], a[0] - b[2], 0), max(b[1] - a[3], a[1] - b[3], 0))


def _nearest(a: Rect, b: Rect) -> Point:
    """The point of rect a nearest rect b."""
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return min(max(bx, a[0]), a[2]), min(max(by, a[1]), a[3])


def _anchor(place: Rect, note: Rect) -> Point:
    """Where a note about a place points: the point of the place's middle half nearest the note, inside the thing
    on the side the note is on, not its middle, which for a whole fortress ran the line across the picture."""
    cx, cy, w, h = (place[0] + place[2]) / 2, (place[1] + place[3]) / 2, place[2] - place[0], place[3] - place[1]
    return _nearest((cx - w / 4, cy - h / 4, cx + w / 4, cy + h / 4), note)


def _line_length(note: Rect, ring: Rect, ringed: bool) -> float:
    """How long the line from a post-it to what it is about is: none when it touches its ring or covers the place."""
    if ringed:
        return _gap(note, ring) if _gap(note, ring) > POSTIT_GAP + 1 else 0.0
    q = _anchor(ring, note)
    return _gap(note, (*q, *q))


def _inside(q: Point, box) -> bool:
    return box.x0 <= q[0] <= box.x1 and box.y0 <= q[1] <= box.y1


def _jitter(k: int) -> float:
    """A number from 0 to 1 that is always the same for k: a particle's own pace or swing, varied but fixed."""
    return math.sin(k * 12.9898 + 4.1414) * 43758.5453 % 1.0


def _repeats(note: str, said: str) -> bool:
    """Whether most of a note's words (NOTE_SAID, cut to their first five letters, small words left out) are in what is
    said or written with it: "Crowd seizes the Bastille's gunpowder" under "a Paris crowd stormed the Bastille for
    gunpowder" says nothing new."""
    def words(text: str) -> set[str]:
        return {w[:5] for w in re.findall(r"[a-z]+", text.casefold().replace("'s", "")) if len(w) > 3}
    mine = words(note)
    return bool(mine) and len(mine & words(said)) >= NOTE_SAID * len(mine)


def _in_space(at: float, loc, upright: bool) -> float:
    """A crossing's end kept inside the space it reaches, a tenth of the way in from its box's edges, as a space's
    stop is (_path_stops): ATP synthase's close-up of Nov 1 ran from the bottom of the picture to the top, and its H⁺
    arrow ended past the picture's top edge."""
    lo, hi = (loc.box.y0, loc.box.y1) if upright else (loc.box.x0, loc.box.x1)
    inset = (hi - lo) * 0.1
    return min(max(at, lo + inset), hi - inset)


def _width(label: str) -> float:
    """How wide one particle at an arrow is drawn (frame pixels), with a little room between two side by side: a
    pill as wide as its word ("Na⁺" is wider than it is tall, and three packed by its height hid each other's N)."""
    if p.kind_of(label) == "symbol":
        return p.marker_width(label, TAIL_SIZE) + 2.0
    return 2 * (p.PARTICLE_R + p.HALO) * TAIL_SIZE + 2.0


def count_in(line: str, label: str) -> int:
    """How many of what `label` names the line says move: a number right before its name or symbol ("three sodium
    ions", "two potassium", "3 Na⁺"); 1 when it gives none ("Three go out and two come in" names nothing), or more
    than COUNT_MAX."""
    if not label:
        return 1
    names = sorted({label, *CARRIED_WORDS.get(label, ())}, key=len, reverse=True)
    numbers = "|".join(re.escape(n) for n in sorted(NUMBERS, key=len, reverse=True))
    found = re.search(rf"(?<![\w.])(\d+|{numbers})\s+(?:{'|'.join(re.escape(n) for n in names)})(?:e?s)?(?![\w⁺⁻])",
                      line, re.IGNORECASE)
    if not found:
        return 1
    n = int(found.group(1)) if found.group(1).isdigit() else NUMBERS[found.group(1).lower()]
    return n if 1 <= n <= COUNT_MAX else 1


def cluster(at: Point, ahead: Point, n: int, width: float) -> list[Point]:
    """Where n particles `width` across sit, packed together around `at`: the first row across the way they move
    (`ahead`, a unit vector), the rest just behind it, back ones first so the front ones are drawn over them."""
    ux, uy = ahead
    vx, vy = -uy, ux
    spots = [(at[0] + width * (a * vx - b * ux), at[1] + width * (a * vy - b * uy))
             for a, b in CLUSTER.get(n, CLUSTER[1])]
    return sorted(spots, key=lambda q: (q[0] - at[0]) * ux + (q[1] - at[1]) * uy)


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
    bend = bends[i] if bends else None
    if bend is not None:
        return bezier(points[i], bend, points[i + 1], x)
    return p.lerp(points[i][0], points[i + 1][0], x), p.lerp(points[i][1], points[i + 1][1], x)


def _leg_seconds(points: list[Point], bends=None) -> list[float]:
    """How long the lead particle takes over each leg: its length at FLOW_SPEED, and at least FLOW_MIN_LEG_S."""
    return [max(math.dist(a, b) / FLOW_SPEED, FLOW_MIN_LEG_S) for a, b in zip(points, points[1:])]


def _dense(points: list[Point], bends=None) -> list[Point]:
    """The whole path as one line: each leg's curve, or the straight hop."""
    out = [points[0]]
    for i in range(len(points) - 1):
        bend = bends[i] if bends else None
        if bend is not None:
            out += [bezier(points[i], bend, points[i + 1], k / CURVE_STEPS) for k in range(1, CURVE_STEPS + 1)]
        else:
            out.append(points[i + 1])
    return out


def _cut(points: list[Point], a: float, b: float) -> list[Point]:
    """The part of the line from a to b along it (pixels from its start)."""
    out, done = [], 0.0
    for p0, p1 in zip(points, points[1:]):
        d = math.dist(p0, p1)
        if d <= 0:
            continue
        lo, hi = max(a - done, 0.0), min(b - done, d)
        if lo < hi:
            q0 = (p0[0] + (p1[0] - p0[0]) * lo / d, p0[1] + (p1[1] - p0[1]) * lo / d)
            q1 = (p0[0] + (p1[0] - p0[0]) * hi / d, p0[1] + (p1[1] - p0[1]) * hi / d)
            if not out or math.dist(out[-1], q0) > 0.5:
                out.append(q0)
            out.append(q1)
        done += d
        if done >= b:
            break
    return out


def glide_speed(total: float, piece: float) -> float:
    """Frame pixels a second a glide runs at, its arrow `piece` long on a route `total` long: GLIDE_SPEED, or
    slower on a short route, so it takes GLIDE_MIN_S."""
    return min(GLIDE_SPEED, (total + piece) / GLIDE_MIN_S)


def _glide(line: list[Point], elapsed: float) -> list[Point]:
    """The gliding arrow on a route at `elapsed` seconds into its flow (frame pixels): a piece GLIDE_SHARE of the route
    long (at most GLIDE_MAX) running from its tail to its head (glide_speed), shrinking into the head as it arrives,
    then GLIDE_PAUSE_S with none before the next; [] when none shows."""
    total = routing.length(line)
    if total < 2.0:
        return []
    piece = min(GLIDE_SHARE * total, GLIDE_MAX)
    speed = glide_speed(total, piece)
    period = (total + piece) / speed + GLIDE_PAUSE_S
    head = (elapsed % period) * speed
    if head > total + piece:
        return []
    start, end = max(head - piece, 0.0), min(head, total)
    return _cut(line, start, end) if end - start > 6.0 else []


def lead_at(points: list[Point], t: float, bends=None) -> tuple[Point, int]:
    """Where the lead particle is t seconds after it set off, and which trip it is on.

    It holds at a stop, eases to the next (along the leg's curve when `bends` gives it one), and after the last
    stop starts over from the first."""
    legs = _leg_seconds(points, bends)
    trip, t = divmod(max(t, 0.0), FLOW_PAUSE_S * len(points) + sum(legs))
    for i, leg in enumerate(legs):
        if t < FLOW_PAUSE_S:
            return points[i], int(trip)
        t -= FLOW_PAUSE_S
        if t < leg:
            return _on_leg(points, bends, i, p.ease(t / leg)), int(trip)
        t -= leg
    return points[-1], int(trip)


def trip_s(points: list[Point], bends=None) -> float:
    """How long the lead particle takes from its first stop to holding at its last."""
    return FLOW_PAUSE_S * (len(points) - 1) + sum(_leg_seconds(points, bends))


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


def stream_at(points: list[Point], t: float, bends=None) -> list[tuple[Point, float]]:
    """The dots streaming along the path at an even pace, about STREAM_GAP apart, each with
    its fade: they fade in leaving the first stop and out reaching the last. Along each leg's route or curve."""
    points = _dense(points, bends)
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
    # Each of the beat's flows' run, in the order of its particles: (from, to). A flow sets off once its run starts
    # and the stops named while it runs are revealed (on a picture that labels its own parts, at once), and goes on
    # across the beats in a row on the scene that carry it, whatever else joins it or leaves.
    runs: list[tuple[float, float]]
    drift_from: float  # when the shot's drift set off: the start of the line its move is on
    drift: float = 1.0  # which way the camera drifts: 1 pushing in, -1 pulling back
    fade_from: str | None = None  # the stage this one fades in over (Timeline.stages)
    push_at: float | None = None  # when the shot pushes in on the part the next scene is a close-up of...
    push_to: Cam | None = None  # ...and where it ends, at the cut

    def cam(self, t: float, view: Rect, art: SceneArt) -> Cam:
        at = mix_cam(self.start, self.target, p.ease((t - self.move_at) / self.move_s))
        on = 1 + self.drift * DRIFT * (1 - math.exp(-max(t - self.drift_from, 0.0) / DRIFT_S))
        cam = clamp_cam(Cam(at.cx, at.cy, at.zoom * on), view, art)
        if self.push_at is not None and t > self.push_at:
            cam = clamp_cam(mix_cam(cam, self.push_to, p.ease((t - self.push_at) / ZOOM_THROUGH_S)), view, art)
        return cam


class Renderer:
    def __init__(self, job: Path, timeline: Timeline, layout: Layout | str = "h",
                 locations: Locations | None = None, arrows: Arrows | None = None):
        self.job = job
        # Each arrow as the arrows step placed and looked at it (arrows.json), by its flow: its tail and head where
        # the helper pointed them on a painting, and why it is left out, if it is. None read: code places them all.
        if arrows is None and (job / media.ARROWS).exists():
            arrows = jobs.read(job / media.ARROWS, Arrows)
        self._placed = {(a.scene, tuple(a.path), a.carries, a.blocked): a for a in (arrows.arrows if arrows else [])}
        self.only: tuple | None = None  # a flow's key: draw that flow's arrow alone (a still to look at it by)
        self.timeline = timeline
        self.layout = LAYOUTS[layout] if isinstance(layout, str) else layout
        if timeline.core_idea:
            self.layout = self.layout.pinned()
        self.portrait = self.layout.height > self.layout.width
        self.locations = locations or jobs.read(job / media.LOCATIONS, Locations)
        self._scenes: dict[str, SceneArt] = {}
        self._through: dict[tuple, list[Point] | None] = {}  # each crossing's and each arrow's line, worked out once
        self._frame_tints: tuple = ()  # the crowds' tints of the frame being drawn (_crowd_tints)
        # The spaces the lesson fills (Bundle.builds_up, as the beats' crowds): spaces wherever they are drawn big.
        self._crowded = {c.region for beat in timeline.beats for c in beat.crowds if not c.thin}
        if not CROWDS:
            timeline = timeline.model_copy(update={"beats": [b.model_copy(update={"crowds": []}) if b.crowds else b
                                                             for b in timeline.beats]})
            self.timeline = timeline
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
        # Where and when each part's name is first heard, which it pops at. A later scene's reveal is the label
        # carried over from an earlier one, which is no news.
        first: dict[str, tuple[float, str]] = {}
        for beat in timeline.beats:
            for r in beat.reveal:
                if r.name not in first or r.t < first[r.name][0]:
                    first[r.name] = (r.t, beat.scene)
        self._pops: dict[str, list[tuple[str, float]]] = {}
        for name, (at, scene_id) in first.items():
            self._pops.setdefault(scene_id, []).append((name, at))
        self._arrival_times: dict[tuple, tuple[float, list[tuple[float, str]], list[str]]] = {}
        # Each process's stages, which fade one into the next. What was shown on the stage before is on
        # the next from its first frame, rather than shown again from a pale wash.
        self._process = {sid: (n, k) for n, group in enumerate(timeline.stages) for k, sid in enumerate(group)}
        # The scenes drawn beside a notice card: those with notices, and every stage of a process one of whose
        # stages has them, so its stages keep one framing.
        self._beside = set(timeline.notices)
        for group in timeline.stages:
            if self._beside & set(group):
                self._beside |= set(group)
        # An upright frame has no room beside the picture: there the card goes across the frame above it, and the
        # picture starts below the tallest card, so every scene with one keeps the same framing.
        self._card_h = 0.0
        shown = [notes for scene_id, notes in timeline.notices.items() if scene_id in scene_ids]  # the hook cut
        if self.portrait and shown:  # carries the song's notices, scenes it never shows included
            self._card_h = max(self._card_height(notes) for notes in shown) + 2 * NOTICE_GAP
        for i, beat in enumerate(timeline.beats):
            if i and self.staged(timeline.beats[i - 1].scene, beat.scene):
                before, now = self.revealed.get(timeline.beats[i - 1].scene, {}), self.revealed.get(beat.scene, {})
                for name, at in now.items():
                    if at == beat.t0 and before.get(name, math.inf) <= beat.t0:
                        now[name] = beat.t0 - max(REVEAL_S, LABEL_IN_S)
        # Two things travelling the same stops on a scene (H⁺ and O₂ leaving Photosystem II for the lumen) would
        # run one on top of the other: each after the first gets a lane of its own, beside it.
        self._lanes: dict[tuple[str, tuple[str, ...], str], int] = {}
        taken: dict[tuple[str, frozenset], list[str]] = {}
        for beat in timeline.beats:
            for flow in beat.particles:
                labels = taken.setdefault((beat.scene, frozenset(flow.path)), [])
                if flow.label not in labels:
                    labels.append(flow.label)
                self._lanes.setdefault((beat.scene, tuple(flow.path), flow.label), labels.index(flow.label))
        self.keyed = any(beat.key_line for beat in timeline.beats)  # a key band only in a video with key lines
        self.brand = p.brand_size(self.layout.brand_font)
        self.plans = self._plan()
        width, height = self.layout.width, self.layout.height
        self.pixels = np.zeros((height, width, 4), np.uint8)
        self.surface = skia.Surface(self.pixels, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        self._background: tuple[tuple, skia.Image] | None = None
        # The background drawn last and where its picture went, (key, (scale, dx, dy)): kept once it comes twice running.
        self._last_background: tuple[tuple, tuple[float, float, float]] | None = None
        self._cards: dict[str, tuple[skia.Image, float, float]] = {}  # each scene's notice card, drawn once
        self._scene_routes: dict[str, list[Point]] = {}  # points along each scene's arrows, in art pixels
        self._travelling: dict[tuple[str, str], bool] = {}  # (scene, what travels) -> drawn as hand-off arrows
        self._handoff_arrows: dict[tuple, list] = {}  # each such flow's arrows, in art pixels, with the part entered
        self._note_spots: dict[tuple, Point] = {}  # where each note sits (an arrow's one-liner, a part's mapping), art
        self._noted: dict[str, list[Rect]] = {}  # the notes placed on each scene, art pixels, for the next to keep clear
        self._leaders: dict[str, list[tuple[Point, Point]]] = {}  # and the lines joining each to what it is about
        self._arrowed_labels: dict[str, set[str]] = {}  # per scene, what travels as arrows (the legend's)
        self._scene_values: dict[str, list] = {}  # per scene, every value it writes in the video (_value_points)
        self._scene_pills: dict[str, list] = {}  # each scene's reaction pills over the whole video, first shown first
        self._axis_spots: dict[str, tuple[int, int]] = {}  # where each graph's two axis names sit beside their ends
        self._postits: dict[str, list[tuple[int, float] | None]] = {}  # per scene: post-it side and reach, or None
        # The sanity gate, once for every arrow off a graph (a graph's are drawn exactly): what fails is never drawn,
        # nor what the arrows step left out (one it found cannot be drawn on the picture, or saw drawn wrong).
        self._wrong: dict[tuple, str] = {}
        for beat in timeline.beats:
            for flow in beat.particles if not self._graph(beat.scene) else ():
                key = (beat.scene, tuple(flow.path), flow.label, flow.blocked)
                if key not in self._wrong:
                    placed = self._placed.get(key)
                    self._wrong[key] = (placed.left_out if placed else "") or self._wrong_arrow(beat.scene, flow)
        self.left_out = [{"scene": scene, "path": list(path), "carries": label, "why": why}
                         for (scene, path, label, _), why in self._wrong.items() if why]
        # How many of a thing go through a part at once, from the first line anywhere that counts them ("three sodium
        # bind"), so a crossing of that part under a line with no number keeps the lesson's ratio: three Na+ out for
        # two K+ in through the pump, while the sodium channel, never counted, keeps CROSS_MANY.
        self._counts: dict[tuple[str, str], int] = {}
        for beat in timeline.beats:
            located = self.locations.scene(beat.scene)
            for flow in beat.particles:
                if (n := count_in(beat.line, flow.label)) > 1:
                    for name in flow.path:
                        if (loc := located.get(name)) is not None and not self._is_space(beat.scene, loc):
                            self._counts.setdefault((flow.label, name), n)
        # The arrows whose one-liner only repeats what is said or written as it first plays: no note for them.
        self._said: set[tuple] = set()
        seen: set[tuple] = set()
        for beat in timeline.beats:
            for flow in beat.particles:
                key = (beat.scene, tuple(flow.path), flow.label, flow.blocked)
                if key not in seen:
                    seen.add(key)
                    if getattr(flow, "says", "") and _repeats(flow.says, f"{beat.line} {beat.key_line}"):
                        self._said.add(key)

    def view(self, scene_id: str) -> Rect:
        """Where the scene's picture goes on the frame: above the key band and the lyric band, and left of the
        notice card when it has one, or in an upright frame below it."""
        x0, y0, x1, y1 = self.layout.view
        if self.keyed:
            y1 -= self.layout.key_h
        if scene_id not in self._beside:
            return (x0, y0, x1, y1)
        return (x0, y0 + self._card_h, x1, y1) if self.portrait else (x0, y0, x1 - NOTICE_W, y1)

    def staged(self, before: str, after: str) -> bool:
        """Whether going from one scene to the other is one stage of a process giving way to a later one. Back to
        an earlier stage is a cut: faded, the cell would be seen coming together again."""
        (p, k), (q, m) = self._process.get(before, (-1, 0)), self._process.get(after, (-2, 0))
        return p == q and m > k

    def scene(self, scene_id: str) -> SceneArt:
        if scene_id not in self._scenes:
            self._scenes[scene_id] = SceneArt(self.job, self.locations.scene(scene_id))
        return self._scenes[scene_id]

    def _aim(self, scene_id: str, target: str | None, zoom: float, focus: Point | None = None,
             from_whole: bool = False, keep: Sequence[Point] = ()) -> Cam:
        """The camera for a beat: on its target, or on the whole scene. An upright frame cannot hold a wide picture
        whole, so there "the whole scene" fills the frame's height and looks at `focus`, where the beat's flow is.
        With `from_whole` (TimelineCamera.from_whole), `zoom` counts from the whole scene's framing, and the shot is
        never wider than it; and a close-up with a flow on it (`keep`, the flow's stops) frames the part and every
        stop, looking between them as far as the part stays in view: counted from the whole scene, close-ups came
        closer, and a flow's far stop fell out of the frame (in the upright hook cut, half of them)."""
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
        cx, cy = loc.centroid.x, loc.centroid.y
        if from_whole and keep:
            x0, x1 = min(loc.box.x0, *(x for x, _ in keep)), max(loc.box.x1, *(x for x, _ in keep))
            y0, y1 = min(loc.box.y0, *(y for _, y in keep)), max(loc.box.y1, *(y for _, y in keep))
            fits = min(fits, min((view[2] - view[0]) / ((x1 - x0) * FIT_PAD),
                                 (view[3] - view[1]) / ((y1 - y0) * FIT_PAD)) / _fit(view, art))
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if from_whole:
            whole = self._whole(scene_id, view).zoom
            zoom = max(whole, min(whole * zoom, fits, CLOSE_MAX_ZOOM))
        else:
            zoom = max(1.0, min(zoom, fits))
        if self.portrait:  # a close-up is never wider than the upright frame's whole scene
            zoom = max(zoom, self._whole(scene_id, view).zoom)
        if (cx, cy) != (loc.centroid.x, loc.centroid.y):  # looking between the stops, the part stays in view
            scale = _fit(view, art) * zoom
            half_w, half_h = (view[2] - view[0]) / scale / 2, (view[3] - view[1]) / scale / 2
            cx = min(max(cx, loc.box.x1 - half_w), loc.box.x0 + half_w) if loc.box.x1 - loc.box.x0 < 2 * half_w \
                else loc.centroid.x
            cy = min(max(cy, loc.box.y1 - half_h), loc.box.y0 + half_h) if loc.box.y1 - loc.box.y0 < 2 * half_h \
                else loc.centroid.y
        return clamp_cam(Cam(cx, cy, zoom), view, art)

    def _whole(self, scene_id: str, view: Rect) -> Cam:
        """The whole scene: everything drawn on it, fitted to the view with room for labels.

        Art rarely fills its canvas (a membrane is a strip across it), so this frames
        what is drawn rather than the canvas, up to WHOLE_MAX_ZOOM."""
        art = self.scene(scene_id)
        x0, y0, x1, y1 = self._written_box(scene_id, view)
        zoom = min((view[2] - view[0]) / ((x1 - x0) * WHOLE_PAD), (view[3] - view[1]) / ((y1 - y0) * WHOLE_PAD))
        if self.portrait:  # what is drawn fills the frame's height; its sides run off, and the camera follows the flow
            zoom = (view[3] - view[1]) / ((y1 - y0) * PORTRAIT_PAD)
        zoom = max(1.0, min(zoom / _fit(view, art), PORTRAIT_MAX_ZOOM if self.portrait else WHOLE_MAX_ZOOM))
        return clamp_cam(Cam((x0 + x1) / 2, (y0 + y1) / 2, zoom), view, art)

    def _written_box(self, scene_id: str, view: Rect) -> tuple[float, float, float, float]:
        """What is drawn on the scene (SceneArt.content), grown to hold what the video writes on it where its drawing
        placed it: each name with its meaning, each tick and each value, as big as they are with the art fitted to the
        view (bigger, in art pixels, than in any closer framing). A graph's names under its axis fell below the
        picture, under the bands, when the camera framed the lines alone (Oct 31)."""
        art = self.scene(scene_id)
        x0, y0, x1, y1 = art.content
        scale = _fit(view, art)
        located = self.locations.scene(scene_id)
        glosses = {m.structure: m.text for b in self.timeline.beats if b.scene == scene_id for m in b.meanings}
        for v in located.values:
            rect, _ = self._value_rect(v, scale, 0.0, 0.0)
            rects = [rect]
            if v.label and v.text in glosses:
                rects.append(self._meaning_rect(glosses[v.text], rect, v.side)[0])
            for r in rects:
                x0, y0 = min(x0, max(r[0] / scale, 0.0)), min(y0, max(r[1] / scale, 0.0))
                x1, y1 = max(x1, min(r[2] / scale, art.width)), max(y1, min(r[3] / scale, art.height))
        return x0, y0, x1, y1

    def _plan(self) -> list[BeatPlan]:
        plans: list[BeatPlan] = []
        beats = self.timeline.beats
        focus = None
        for i, beat in enumerate(beats):
            reveals = {r.name: r.t for r in beat.reveal}
            # Upright, where the beat's flow is: the middle of its first flow's stops. A line whose first flow the line
            # before was already showing on the scene keeps looking where it was, so a line that sets the scene's other
            # flows running beside it does not swing the camera over to one of them.
            carried = (focus is not None and beat.particles and beats[i - 1].scene == beat.scene
                       and beat.particles[0] in beats[i - 1].particles)
            if not carried:
                focus = None
                if self.portrait and beat.particles:
                    stops = self._pointed(beat.scene, beat.particles[0]) or self._path_points(beat.scene,
                                                                                              beat.particles[0].path)
                    focus = (sum(x for x, _ in stops) / len(stops), sum(y for _, y in stops) / len(stops)) if stops else None
            keep = [point for flow in beat.particles
                    for point in self._pointed(beat.scene, flow) or self._path_points(beat.scene, flow.path)] \
                if beat.camera.from_whole and beat.camera.target else []
            target = self._aim(beat.scene, beat.camera.target, beat.camera.zoom, focus, beat.camera.from_whole, keep)
            same = i > 0 and beats[i - 1].scene == beat.scene
            staged = i > 0 and self.staged(beats[i - 1].scene, beat.scene)
            drift_from = beat.t0
            if same and plans[-1].target == target:  # one shot: its move and its drift go on as they were
                start, move_at, move_s = plans[-1].start, plans[-1].move_at, plans[-1].move_s
                drift_from = plans[-1].drift_from
            elif same:
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
                through = self._through_part(beats[i - 1].scene, beat.scene) if i else None
                if through:
                    part, closer = through
                    if closer:  # into a close-up: the shot before pushes in on its part, up to the cut
                        before = beats[i - 1]
                        if before.t1 - before.t0 >= 2 * ZOOM_THROUGH_S:
                            plans[-1].push_at = before.t1 - ZOOM_THROUGH_S
                            plans[-1].push_to = self._aim(before.scene, part, ZOOM_THROUGH_MAX)
                    else:  # back out: open close on the part, and pull back
                        start, move_s = self._aim(beat.scene, part, ZOOM_THROUGH_MAX), MOVE_S
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
            runs = []
            revealed = self.revealed.get(beat.scene, {})
            for flow in beat.particles:
                # Beats in a row on one scene that carry the flow are one run: its particles keep going across them
                # rather than starting over at every line, or when a line that sums up sets the others going beside it.
                def carries_on(other, flow=flow) -> bool:
                    return other.scene == beat.scene and any(f.path == flow.path and f.label == flow.label
                                                             for f in other.particles)
                j = i
                while j > 0 and carries_on(beats[j - 1]):
                    j -= 1
                k = i
                while k + 1 < len(beats) and carries_on(beats[k + 1]):
                    k += 1
                # It waits for its stops' names, so it never runs to a part not yet labelled: only for a name heard
                # while it runs, and not at all on a picture that labels its own parts. Waiting for one heard a minute
                # later hid every flow through the inside of the cell until then (Nov 1).
                waits = [] if beat.scene in self.timeline.self_labelled else [
                    at for name in flow.path if (at := revealed.get(name)) is not None and at < beats[k].t1]
                runs.append((max([beats[j].t0] + waits), beats[k].t1))
            plans.append(BeatPlan(target=target, start=start, move_at=move_at, move_s=move_s, spot=beat.spotlight,
                                  spot_at=spot_at, prev_spot=prev_spot, caption_in=caption_in,
                                  caption_out=caption_out, key_in=key_in, key_out=key_out, runs=runs,
                                  drift_from=drift_from, drift=self._drift(start, target),
                                  fade_from=beats[i - 1].scene if staged else None))
        return plans

    def _through_part(self, before: str, after: str) -> tuple[str, bool] | None:
        """The part a cut from scene `before` to `after` goes through, and whether `after` shows it closer: the part
        both draw that is the most bigger on one than on the other, by its share of what is drawn, when that is at
        least ZOOM_THROUGH_SCALE times. None when they share no part, or show it alike."""
        if before == after or self.staged(before, after):
            return None
        a = {loc.name: loc for loc in self.locations.scene(before).structures}
        b = {loc.name: loc for loc in self.locations.scene(after).structures}

        def share(scene_id: str, loc) -> float:
            x0, y0, x1, y1 = self.scene(scene_id).content
            return (loc.box.x1 - loc.box.x0) * (loc.box.y1 - loc.box.y0) / max((x1 - x0) * (y1 - y0), 1.0)

        best = None
        for name in a.keys() & b.keys():
            ratio = share(after, b[name]) / max(share(before, a[name]), 1e-6)
            scale = max(ratio, 1 / ratio)
            if scale >= ZOOM_THROUGH_SCALE and (best is None or scale > best[0]):
                best = (scale, name, ratio > 1)
        return (best[1], best[2]) if best else None

    @staticmethod
    def _drift(start: Cam, target: Cam) -> float:
        """Which way a shot drifts: on back after pulling back, while that keeps the whole picture filling the view
        (past it, a painted picture's edge would show), and pushing in otherwise."""
        return -1.0 if start.zoom > target.zoom * 1.001 and target.zoom * (1 - DRIFT) >= 1.0 else 1.0

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

    def _spots(self, i: int, t: float) -> dict[str, float]:
        """Each spotlight's level on beat i at t. The beat before's stays on until this one's starts coming in (as
        its part is sung), then the two cross, from wherever the old one had got to: the wash over the rest of the
        picture holds steady. It lifted between the two and came back on every line, and every painted label
        flickered with it (the owner, Nov 2)."""
        beat, plan = self.timeline.beats[i], self.plans[i]
        spots: dict[str, float] = {}
        if plan.spot:
            spots[plan.spot] = 1.0 if plan.prev_spot == plan.spot else p.ease((t - plan.spot_at) / SPOT_S)
        if plan.prev_spot and plan.prev_spot != plan.spot:
            before = self.plans[i - 1]
            came = (p.ease((t - before.spot_at) / SPOT_S)
                    if before.spot == plan.prev_spot and before.prev_spot != before.spot else 1.0)
            leaves = plan.spot_at if plan.spot else beat.t0
            spots[plan.prev_spot] = max(spots.get(plan.prev_spot, 0.0), came * (1 - p.ease((t - leaves) / SPOT_S)))
        return spots

    def _ghosts(self, scene_id: str, reveals: dict[str, float], t: float) -> dict[str, float]:
        """Each structure's ghost wash at t: full until its name is heard, fading out as it is revealed.
        A structure this video never names on the scene is drawn as it is, unlabelled: as a ghost it
        would wash out its part of the picture the whole time the scene is on screen (the first live
        runs left a whole Earth pale through a song)."""
        art = self.scene(scene_id)
        if scene_id in self.timeline.unwashed:  # a painting that is no diagram: shown as painted (Timeline.unwashed)
            return dict.fromkeys(art.masks, 0.0)
        levels = {}
        for name in art.masks:
            r = reveals.get(name)
            levels[name] = 0.0 if r is None else 1.0 - p.ease((t - r) / REVEAL_S)
        return levels

    def _draw_background(self, canvas: skia.Canvas, scene_id: str, cam: Cam, view: Rect,
                         ghosts: dict[str, float], spots: dict[str, float], keep: bool = True,
                         tints: tuple = ()) -> None:
        """The scene's picture, framed by the camera, over the whole frame. Kept for the next frame unless
        `keep` is False (a stage fading into the next: two pictures, each drawn in part).

        A moving camera makes a new background every frame, so one is kept (a copy of the whole frame) only when
        it comes twice running, and drawn the second time just as the first, so the frames are the same as when
        every one was kept. Where the picture fills its view, the paper is cleared only around the view."""
        art = self.scene(scene_id)
        washes, composed = art.compose(ghosts, spots, tints)
        key = (scene_id, washes, round(cam.cx, 3), round(cam.cy, 3), round(cam.zoom, 5), view)
        if keep and self._background and self._background[0] == key:
            canvas.drawImage(self._background[1], 0, 0)
            return
        again = keep and self._last_background is not None and self._last_background[0] == key
        scale, dx, dy = self._last_background[1] if again else to_frame(cam, view, art)
        canvas.save()
        if keep and fills(view, (dx, dy, dx + art.width * scale, dy + art.height * scale)):
            canvas.clipRect(skia.Rect.MakeLTRB(*view), skia.ClipOp.kDifference)  # the title and band strips
        canvas.clear(art.paper)
        canvas.restore()
        canvas.save()
        canvas.clipRect(skia.Rect.MakeLTRB(*view))  # beside a notice card, the picture stops at its view
        canvas.translate(dx, dy)
        canvas.scale(scale, scale)
        canvas.drawImage(composed, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
        canvas.restore()
        if again:
            self._background = (key, self.surface.makeImageSnapshot())
        elif keep:  # the copy kept was the frame before's, as it always was: never one from an earlier rest
            self._last_background, self._background = (key, (scale, dx, dy)), None

    def _in_play(self, i: int) -> set[str] | None:
        """The parts beat i is about (LABELS_CALM); None when every label shows in full."""
        beat = self.timeline.beats[i]
        if len(beat.labels) <= LABELS_CALM:
            return None
        before = self.timeline.beats[i - 1] if i else None
        named = set(beat.named) | (set(before.named) if before and before.scene == beat.scene else set())
        travelled = {stop for flow in beat.particles for stop in flow.path}
        return named | travelled | {beat.spotlight, beat.camera.target, beat.glow} - {None}

    def _resting(self, i: int, t: float) -> dict[str, float]:
        """Each label's strength on beat i at t: 1.0 in play, LABEL_REST at rest, easing from the beat before's."""
        beat = self.timeline.beats[i]

        def strength(k: int) -> dict[str, float]:
            play = self._in_play(k)
            return {n: 1.0 if play is None or n in play else LABEL_REST for n in self.timeline.beats[k].labels}

        now = strength(i)
        if not i or self.timeline.beats[i - 1].scene != beat.scene:
            return now
        was = strength(i - 1)
        x = p.ease((t - beat.t0) / LABEL_IN_S)
        return {n: was.get(n, v) + (v - was.get(n, v)) * x for n, v in now.items()}

    def _labels(self, canvas: skia.Canvas, scene_id: str, names: list[str], reveals: dict[str, float],
                spots: dict[str, float], cam: Cam, view: Rect, bounds: Rect, t: float,
                avoid: list[Point] = (), strength: dict[str, float] | None = None,
                meanings: dict | None = None) -> list[Rect]:
        """Draws the labels, each at its strength (1.0 when not given); returns where each pill landed, for what is
        drawn after them to keep clear. A name the drawing placed (Value.label, a graph's) is written there in its
        structure's colour, as a textbook prints a curve's name, with no pill, and never rests: it is part of the
        graph, and the pills keep clear of it. It is written as its structure is revealed, or from the scene's first
        frame when this video never names the structure there: drawn in full and nameless, a graph's four lines on
        Oct 31 left the learner to guess which was the old supply and which the new. A placed name with a meaning on
        (`meanings`, Beat.meanings) has it written beside it, smaller, on the side away from its point."""
        art = self.scene(scene_id)
        located = self.locations.scene(scene_id)
        scale, dx, dy = to_frame(cam, view, art)
        baked = {v.text: v for v in located.values if v.label}
        labels, rects = [], []
        for name in [*names, *(n for n in baked if n not in names and n not in reveals)]:
            loc = located.get(name)
            if loc is None:
                continue
            if name in baked:
                # Never cut off by the frame's edge or the bands: one the camera leaves out is not written, and one
                # near the edge fades as it nears it, as a value does.
                rect, size = self._value_rect(baked[name], scale, dx, dy)
                gloss = (meanings or {}).get(name)
                on = (p.ease((t - gloss.since) / LABEL_IN_S) * p.ease((gloss.until - t) / MEANING_OUT_S)
                      if gloss else 0.0)
                under, small = self._meaning_rect(gloss.text, rect, baked[name].side) if on > 0.01 else (None, 0.0)

                def inside(r: Rect) -> float:
                    inset = min(r[0] - view[0], r[1] - bounds[1], view[2] - r[2], view[3] - r[3])
                    return min(1.0, max(inset, 0.0) / VALUE_EDGE)

                alpha = p.ease((t - reveals.get(name, -1e9)) / LABEL_IN_S) * inside(rect)
                if alpha > 0.01:
                    p.draw_name(canvas, name, rect, size, art.paper, self.timeline.palette[name], alpha)
                    drawn = [rect]
                    shown = alpha * on * (inside(under) if under else 0.0)  # a close-up may leave it out, not the name
                    if shown > 0.01:
                        p.draw_name(canvas, gloss.text, under, small, art.paper, self.timeline.palette[name], shown,
                                    weight="SemiBold")
                        drawn.append(under)
                    for r in drawn:
                        rects.append(r)
                        avoid = [*avoid, *((r[0] + k * (r[2] - r[0]) / 8, y) for k in range(9)
                                           for y in (r[1], (r[1] + r[3]) / 2, r[3]))]
                continue
            at = art.line_end(name) if name in art.masks else None
            ax, ay = ((at[0], at[1]) if at else (loc.label_anchor.x, loc.label_anchor.y))
            ax, ay = ax * scale + dx, ay * scale + dy
            alpha = (p.ease((t - reveals.get(name, -1e9)) / LABEL_IN_S) * edge_fade(ax, ay, view)
                     * (strength or {}).get(name, 1.0))
            if alpha > 0.01:
                box = (loc.box.x0 * scale + dx, loc.box.y0 * scale + dy, loc.box.x1 * scale + dx, loc.box.y1 * scale + dy)
                big = self._is_space(scene_id, loc)
                labels.append(p.Label(name=name, color=self.timeline.palette[name], anchor=(ax, ay), box=box, alpha=alpha,
                                      big=big, emphasis=spots.get(name, 0.0)))
        centre = (art.centre[0] * scale + dx, art.centre[1] * scale + dy)
        p.place_labels(labels, bounds, centre, self.layout.label_font, avoid)
        for lb in sorted(labels, key=lambda lb: lb.emphasis):  # the spotlit one on top
            p.draw_label(canvas, lb, self.layout.label_font)
        for lb in labels:
            w, h = lb.size(self.layout.label_font)
            rects.append((lb.x - w / 2, lb.y - h / 2, lb.x + w / 2, lb.y + h / 2))
        return rects

    def _flags(self, canvas: skia.Canvas, scene_id: str, cam: Cam, view: Rect, bounds: Rect, alpha: float) -> None:
        """Each label the art check found wrong in the scene's painted picture (Timeline.flags), from the scene's first
        frame: an amber ring round it, framed with the picture, and a post-it saying what is true, on the side of the
        ring _place_postits() chose, joined to it by a line when it sits away from it. A post-it keeps its size and
        its gap from the ring as the camera moves, and stays inside the picture's bounds; it fades out with its ring
        as the camera leaves the label, as a label does (edge_fade()), rather than stay on screen pointing at nothing."""
        art = self.scene(scene_id)
        if scene_id not in self._postits:
            self._postits[scene_id] = self._place_postits(scene_id, view, bounds)
        scale, dx, dy = to_frame(cam, view, art)
        canvas.save()
        canvas.clipRect(skia.Rect.MakeLTRB(*view))  # beside a notice card, the picture stops at its view
        for flag, spot in zip(self.timeline.flags[scene_id], self._postits[scene_id]):
            if spot is None:  # no spot near enough: left off rather than drawn far away (POSTIT_MAX_LINE)
                continue
            box = flag.box
            label = (box.x0 * scale + dx, box.y0 * scale + dy, box.x1 * scale + dx, box.y1 * scale + dy)
            ring = p.flag_ring(label)
            shown = alpha * edge_fade((ring[0] + ring[2]) / 2, (ring[1] + ring[3]) / 2, view)
            if shown <= 0.01:
                continue
            note = _within(_postit_rect(ring, p.postit_size(flag.note), *spot), bounds)
            if flag.ring:
                p.draw_flag(canvas, label, shown)
                if _gap(note, ring) > POSTIT_GAP + 1:
                    p.draw_postit_line(canvas, _nearest(note, ring), _nearest(ring, note), shown)
            else:  # a note about a place: a line from the post-it to a dot just inside the place (_anchor())
                point = _anchor(label, note)
                if not (note[0] <= point[0] <= note[2] and note[1] <= point[1] <= note[3]):
                    p.draw_postit_line(canvas, _nearest(note, (*point, *point)), point, shown)
                    p.draw_postit_dot(canvas, point, shown)
            p.draw_postit(canvas, note, flag.note, shown)
        canvas.restore()

    def _place_postits(self, scene_id: str, view: Rect, bounds: Rect) -> list[tuple[int, float] | None]:
        """Which side of its label's ring each flag's post-it sits on, and how far out (POSTIT_SIDES, POSTIT_REACH):
        of those, the one that covers the least of the picture (its parts and its own labels, ink_share()), no other
        post-it and no other flagged label, the nearest of those alike, over every framing the video shows the scene
        in (the whole scene, and the camera at the start, middle and end of each of its beats): the picture moves and
        grows under a post-it that keeps its size. Chosen once, so a post-it never jumps. None for a note with no
        spot near enough (its line past POSTIT_MAX_LINE over the whole scene) or none that leaves the picture seen
        (POSTIT_MAX_COVER): it is left off."""
        art = self.scene(scene_id)
        cams = [self._whole(scene_id, view)] + [
            plan.cam(t, view, art) for beat, plan in zip(self.timeline.beats, self.plans) if beat.scene == scene_id
            for t in (beat.t0, (beat.t0 + beat.t1) / 2, max(beat.t0, beat.t1 - 1e-3))][:POSTIT_FRAMINGS - 1]
        flags = self.timeline.flags[scene_id]
        chosen: list[tuple[int, float] | None] = []
        for i, flag in enumerate(flags):
            size = p.postit_size(flag.note)
            best = None
            whole = cams[0]
            for k, gap in enumerate(POSTIT_REACH):
                for side in range(len(POSTIT_SIDES)):
                    cost = 0.0
                    for cam in cams:
                        scale, dx, dy = to_frame(cam, view, art)
                        rings = [p.flag_ring((f.box.x0 * scale + dx, f.box.y0 * scale + dy, f.box.x1 * scale + dx,
                                              f.box.y1 * scale + dy)) for f in flags]
                        rect = _within(_postit_rect(rings[i], size, side, gap), bounds)
                        placed = [_within(_postit_rect(rings[j], p.postit_size(flags[j].note), *chosen[j]), bounds)
                                  for j in range(len(chosen)) if chosen[j] is not None]
                        cost += art.ink_share(((rect[0] - dx) / scale, (rect[1] - dy) / scale, (rect[2] - dx) / scale,
                                               (rect[3] - dy) / scale))
                        cost += sum(_overlap(rect, other) for other in placed + rings)
                    scale, dx, dy = to_frame(whole, view, art)
                    ring = p.flag_ring((flag.box.x0 * scale + dx, flag.box.y0 * scale + dy, flag.box.x1 * scale + dx,
                                        flag.box.y1 * scale + dy))
                    reach = _line_length(_within(_postit_rect(ring, size, side, gap), bounds), ring, flag.ring)
                    if reach > POSTIT_MAX_LINE:
                        continue
                    score = (round(cost / len(cams), 2), k, side)
                    if best is None or score < best[0]:
                        best = (score, (side, gap))
            chosen.append(best[1] if best is not None and best[0][0] <= POSTIT_MAX_COVER else None)
        return chosen

    def _draw_beat(self, canvas: skia.Canvas, t: float) -> None:
        layout = self.layout
        i = self.beat_index(t)
        beat, plan = self.timeline.beats[i], self.plans[i]
        art = self.scene(beat.scene)
        view = self.view(beat.scene)
        cam = plan.cam(t, view, art)
        spots = self._spots(i, t)
        reveals = self.revealed.get(beat.scene, {})
        ghosts = self._ghosts(beat.scene, reveals, t)
        self._frame_tints = tints = self._crowd_tints(beat, t, ghosts, spots) if beat.crowds else ()
        fade = p.ease((t - beat.t0) / STAGE_FADE_S) if plan.fade_from else 1.0
        if fade < 1.0:  # the stage before, under this one fading in, framed alike
            before = plan.fade_from
            self._draw_background(canvas, before, cam, view,
                                  self._ghosts(before, self.revealed.get(before, {}), t), {}, keep=False)
            canvas.saveLayerAlpha(None, round(255 * fade))
            self._draw_background(canvas, beat.scene, cam, view, ghosts, spots, keep=False, tints=tints)
            self._reacting(canvas, beat, plan, cam, view, t, ghosts, spots)  # its parts fade in with their stage
            canvas.restore()
        else:
            self._draw_background(canvas, beat.scene, cam, view, ghosts, spots, tints=tints)
        if beat.turning:
            self._turning(canvas, beat, cam, view, t, ghosts, spots, face=True)
        if fade >= 1.0:
            self._reacting(canvas, beat, plan, cam, view, t, ghosts, spots)
        if beat.crowds:
            self._crowds(canvas, beat, cam, view, t, ghosts, spots)
        if beat.turning:
            self._turning(canvas, beat, cam, view, t, ghosts, spots, face=False)
        if beat.glow:
            self._glow(canvas, beat, cam, view, t, ghosts)
        if beat.particles:
            self._particles(canvas, beat, plan, cam, view, t)

        caption_alpha = 0.0
        if beat.caption:
            caption_alpha = p.ease((t - plan.caption_in) / CAPTION_FADE_S) * p.ease((plan.caption_out - t) / CAPTION_FADE_S)
        notes = self.timeline.notices.get(beat.scene, [])
        tagged = beat.scene in self.timeline.ai_pictures and not notes  # the notice card says it otherwise
        # The brand is in the picture's bottom right corner, unless the picture has moved over for a notice card
        # (it is then under the card) or there is a title band (it is in that).
        branded = not layout.title_h and beat.scene not in self._beside
        bounds = self._bounds(beat.scene, view, caption_alpha > 0)
        arrows = self._arrow_points(beat.scene, cam, view)
        legend = self._legend(beat, t)
        legend_rect = p.legend_rect(bounds[2], bounds[3], [label for _, label, _ in legend]) if legend else None
        if legend_rect:  # labels and reaction pills keep clear of it as of an arrow
            x0, y0, x1, y1 = legend_rect
            arrows = arrows + [(x0 + i * (x1 - x0) / 8, y0 + j * (y1 - y0) / 4) for i in range(9) for j in range(5)]
        places = self._pill_places(beat.scene, cam, view)
        # A graph's axis names are on from its first frame; a stage fading in fades in its own, unless the stage
        # before has the same.
        same_axes = plan.fade_from is not None and (self.timeline.axes.get(plan.fade_from)
                                                    == self.timeline.axes.get(beat.scene))
        axis_rects = self._axis_names(canvas, beat.scene, cam, view, bounds, 1.0 if same_axes else fade)
        # Values sit anywhere on the picture below the caption, but in its corners' tag, brand and legend: a tick under
        # its axis is low in the picture, where the labels' bounds stop short of the margin and the tag.
        corners = [r for r in (
            p.ai_tag_rect(view[0] + layout.margin, view[3] - layout.margin, AI_TAG_FONT) if tagged else None,
            (view[2] - layout.margin - self.brand[0], view[3] - layout.margin - self.brand[1], view[2] - layout.margin,
             view[3] - layout.margin) if branded else None,
            legend_rect) if r]
        self._values(canvas, beat, cam, view, (view[0], bounds[1], view[2], view[3]), corners, t, fade)
        # Labels keep clear of the arrows, of every reaction pill the scene shows, of the axis names and of every
        # value the scene writes, sampled as points.
        pill_points = self._value_points(beat.scene, cam, view) + [(px, py) for x0, y0, x1, y1 in [*places.values(), *axis_rects]
                       for px in [x0 + k * (x1 - x0) / 12 for k in range(13)]
                       for py in (y0, (y0 + y1) / 2, y1)]
        strength = self._resting(i, t)
        if beat.scene not in self.timeline.self_labelled:  # a painted picture labels its own parts
            self._labels(canvas, beat.scene, beat.labels, reveals, spots, cam, view, bounds, t, arrows + pill_points,
                         strength, {m.structure: m for m in beat.meanings})
        if beat.scene in self.timeline.flags:
            self._flags(canvas, beat.scene, cam, view, bounds, fade)
        if beat.conversions:
            self._conversions(canvas, beat, view, bounds, t, places, strength)
        if legend_rect:
            p.draw_legend(canvas, legend_rect, [(color, label, alpha * fade) for color, label, alpha in legend],
                          self._arrowed(beat.scene))
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
        # The key line fades in and out with its run, and stays put through it while the lyrics go by below. It sits
        # right on top of the lyric band, so the eye reads the step and the line sung together: at the top of the
        # frame, a learner reading the lyrics never saw it (the owner, Oct 31).
        key_alpha = p.ease((t - plan.key_in) / KEY_FADE_S) * p.ease((plan.key_out - t) / KEY_FADE_S)
        if layout.title_h:
            p.draw_title(canvas, (0, 0, layout.width, layout.title_h), self.timeline.title, layout.title_font,
                         core=self.timeline.core_idea, core_size=layout.pin_font,
                         clear=self.brand[0] + 2 * layout.margin)
        key_top = layout.height - layout.band_h - (layout.key_h if self.keyed else 0)
        if self.keyed and layout.key_h:
            p.draw_key_band(canvas, (0, key_top, layout.width, layout.height - layout.band_h), beat.key_line,
                            self.timeline.key_terms, layout.key_font, key_alpha)
        # The brand, in the same place on every frame: the bottom right corner of the title band where there is one,
        # else of the picture, across from the tag.
        corner = layout.title_h - BRAND_GAP if layout.title_h else key_top - layout.margin
        p.draw_brand(canvas, layout.width - layout.margin - self.brand[0], corner - self.brand[1], layout.brand_font)

    def _bounds(self, scene_id: str, view: Rect, caption: bool) -> Rect:
        """Where labels and pills keep inside on the scene's frames: the view less its margin, the caption's band
        across the top while one shows, and the AI tag's and the brand's strip along the bottom."""
        layout = self.layout
        top = layout.margin + (layout.caption_font * 1.75 + 24 if caption else 0)
        tagged = scene_id in self.timeline.ai_pictures and not self.timeline.notices.get(scene_id, [])
        branded = not layout.title_h and scene_id not in self._beside
        strip = max(AI_TAG_FONT * 1.7 if tagged else 0.0, self.brand[1] if branded else 0.0)
        bottom = layout.margin + (strip + 10 if strip else 0)  # labels keep clear of the tag and the brand
        return (view[0] + layout.margin, view[1] + top, view[2] - layout.margin, view[3] - bottom)

    def _pill_bounds(self, scene_id: str, view: Rect) -> Rect:
        """Where a reaction pill keeps inside, the same on every frame of its scene: clear of the caption's band if
        the scene ever shows a caption, so a caption coming on does not push a pill down."""
        return self._bounds(scene_id, view, any(b.caption for b in self.timeline.beats if b.scene == scene_id))

    def _pill_places(self, scene_id: str, cam: Cam, view: Rect) -> dict[str, tuple[float, float, float, float]]:
        """Where each reaction pill the scene shows at any time in the video goes, as its rect in frame pixels: under
        its structure, or over it, or the same rows shifted to either side, whichever crosses the fewest arrows and no
        other pill. The spot is chosen once for the scene, framed whole (_pill_spots), and the pill rides it as the
        camera moves: chosen again every frame, it flipped between two spots as clear as each other as the camera
        drifted (the owner, Nov 2: "the position of these pills should stay fixed even when the camera drifts").
        Placed before the labels, which then keep clear of them, as of the arrows. Each keeps inside _pill_bounds, the
        same all scene, on a spot clear of every arrow and legend the scene shows, framed whole."""
        spots = self._pill_spots(scene_id, view)
        inside = self._pill_bounds(scene_id, view)
        places: dict[str, tuple[float, float, float, float]] = {}
        for conv in self._scene_pills[scene_id]:
            if conv.structure in spots:
                places[conv.structure] = self._pill_rect(scene_id, conv, cam, view, inside, spots[conv.structure])
        return places

    def _pill_candidates(self, scene_id: str, conv, cam: Cam, view: Rect) -> list[tuple[float, float]]:
        """The centres a reaction pill may take beside its structure, in frame pixels, in the order they are tried."""
        loc = self.locations.scene(scene_id).get(conv.structure)
        scale, dx, dy = to_frame(cam, view, self.scene(scene_id))
        w, h = p.conversion_size(conv.text, self.layout.label_font * 1.25)
        x = (loc.box.x0 + loc.box.x1) / 2 * scale + dx
        below, above = loc.box.y1 * scale + dy, loc.box.y0 * scale + dy
        gap = self.layout.label_font * 0.6
        rows = [below + gap + h / 2, above - gap - h / 2, below + gap + 1.5 * h + 8, above - gap - 1.5 * h - 8]
        return [(xo, yo) for xo in (x, x - 0.6 * w, x + 0.6 * w) for yo in rows]

    def _pill_rect(self, scene_id: str, conv, cam: Cam, view: Rect, bounds: Rect, spot: int) -> Rect:
        """The pill's rect at candidate `spot`, kept inside bounds."""
        w, h = p.conversion_size(conv.text, self.layout.label_font * 1.25)
        x, y = self._pill_candidates(scene_id, conv, cam, view)[spot]
        cx = min(max(x, bounds[0] + w / 2), bounds[2] - w / 2)
        cy = min(max(y, bounds[1] + h / 2), bounds[3] - h / 2)
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def _pill_spots(self, scene_id: str, view: Rect) -> dict[str, int]:
        """Each reaction pill's spot on the scene (_pill_candidates), chosen once with the picture framed whole: the
        one crossing the fewest arrows and legends (every one the scene shows) and no other pill, the nearest of those
        as clear, the first of those as near."""
        key = ("pill spots", scene_id)
        if key in self._through:
            return self._through[key]
        if scene_id not in self._scene_pills:
            convs = []
            for beat in self.timeline.beats:
                for conv in beat.conversions if beat.scene == scene_id else []:
                    if conv.structure not in [c.structure for c in convs]:
                        convs.append(conv)
            self._scene_pills[scene_id] = convs
        art = self.scene(scene_id)
        whole = clamp_cam(Cam(art.width / 2, art.height / 2, 1.0), view, art)
        bounds = self._pill_bounds(scene_id, view)
        arrows = self._arrow_points(scene_id, whole, view)
        for beat in self.timeline.beats:  # every legend the scene shows, as an arrow's points
            legend = self._legend(beat, beat.t1) if beat.scene == scene_id else []
            if legend:
                x0, y0, x1, y1 = p.legend_rect(bounds[2], bounds[3], [label for _, label, _ in legend])
                arrows += [(x0 + i * (x1 - x0) / 8, y0 + j * (y1 - y0) / 4) for i in range(9) for j in range(5)]
        located = self.locations.scene(scene_id)
        spots: dict[str, int] = {}
        placed: list[Rect] = []
        for conv in self._scene_pills[scene_id]:
            if located.get(conv.structure) is None:
                continue
            x, below = self._pill_candidates(scene_id, conv, whole, view)[0]
            best = None
            for k in range(len(self._pill_candidates(scene_id, conv, whole, view))):
                rect = self._pill_rect(scene_id, conv, whole, view, bounds, k)
                hit = 50 * sum(1 for r in placed if rect[0] < r[2] + 8 and r[0] < rect[2] + 8
                               and rect[1] < r[3] + 8 and r[1] < rect[3] + 8)
                hit += sum(1 for ax, ay in arrows if rect[0] - 8 < ax < rect[2] + 8 and rect[1] - 8 < ay < rect[3] + 8)
                score = hit + 0.01 * math.dist(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), (x, below))
                if best is None or score < best[0] - 1e-6:
                    best = (score, k, rect)
            spots[conv.structure] = best[1]
            placed.append(best[2])
        self._through[key] = spots
        return spots

    def _axis_rect(self, side: str, spot: int, end: tuple[float, float], size: tuple[float, float]) -> Rect:
        """Where an axis name goes beside its axis's end (frame pixels): the x axis's right end, below it, past it or
        above it; the y axis's top, left of it, above it or right of it."""
        (x, y), (w, h), g = end, size, AXIS_GAP
        if side == "x":
            return [(x - w, y + g, x, y + g + h), (x + g, y - h / 2, x + g + w, y + h / 2),
                    (x - w, y - g - h, x, y - g)][spot]
        return [(x - g - w, y, x - g, y + h), (x - w / 2, y - g - h, x + w / 2, y - g),
                (x + g, y, x + g + w, y + h)][spot]

    def _axis_ends(self, scene_id: str, cam: Cam, view: Rect) -> dict[str, tuple[float, float]]:
        """Where each axis name is written from, in frame pixels: the x axis's right end, the y axis's top."""
        art = self.scene(scene_id)
        across, up = art.axis_lines()
        scale, dx, dy = to_frame(cam, view, art)
        return {"x": (across.end * scale + dx, across.at * scale + dy), "y": (up.at * scale + dx, up.start * scale + dy)}

    def _axis_names(self, canvas: skia.Canvas, scene_id: str, cam: Cam, view: Rect, bounds: Rect,
                    alpha: float) -> list[Rect]:
        """A graph's axis names (Timeline.axes), written at its axes' ends from the scene's first frame, since the
        picture has no writing; none on a painted picture, which labels its own axes (Timeline.self_labelled). Each
        takes the spot beside its end that, framed whole, stays in the frame over the least of the picture, chosen
        once so it never jumps. Where the camera leaves its end out (a close-up, the
        upright frame's sides), it stays at the frame's edge on that side, so the graph still reads: what "up" and
        "across" measure. Returns where they landed."""
        names = self.timeline.axes.get(scene_id)
        if not names or scene_id in self.timeline.self_labelled:
            return []
        art = self.scene(scene_id)
        font = self.layout.label_font
        sizes = {side: (p.text_width(p.font("Bold", font), text), font * 1.3)
                 for side, text in (("x", names.x), ("y", names.y)) if text}
        if scene_id not in self._axis_spots:  # from the scene alone, so every frame range picks the same
            whole = self._whole(scene_id, view)
            scale, dx, dy = to_frame(whole, view, art)
            ends = self._axis_ends(scene_id, whole, view)
            m = self.layout.margin
            inside = (view[0] + m, view[1] + m, view[2] - m, view[3] - m)
            picked = []
            for side in ("x", "y"):
                if side not in sizes:
                    picked.append(0)
                    continue

                def score(spot: int) -> tuple:
                    x0, y0, x1, y1 = self._axis_rect(side, spot, ends[side], sizes[side])
                    off = (x0 < inside[0] or y0 < inside[1] or x1 > inside[2] or y1 > inside[3])
                    drawn = art.drawn_share(((x0 - dx) / scale, (y0 - dy) / scale, (x1 - dx) / scale, (y1 - dy) / scale))
                    return off, round(drawn, 2), spot
                picked.append(min(range(3), key=score))
            self._axis_spots[scene_id] = (picked[0], picked[1])
        rects = []
        ends = self._axis_ends(scene_id, cam, view)
        for side, spot in zip(("x", "y"), self._axis_spots[scene_id]):
            if side not in sizes:
                continue
            x0, y0, x1, y1 = self._axis_rect(side, spot, ends[side], sizes[side])
            shift_x = min(max(bounds[0] - x0, 0.0), bounds[2] - x1)  # into the frame, along its axis or across it
            shift_y = min(max(bounds[1] - y0, 0.0), bounds[3] - y1)
            rect = (x0 + shift_x, y0 + shift_y, x1 + shift_x, y1 + shift_y)
            if alpha > 0.01:
                p.draw_axis_name(canvas, names.x if side == "x" else names.y, rect, font, art.paper, alpha)
                rects.append(rect)
        return rects

    def _meaning_rect(self, text: str, name: Rect, side: str) -> tuple[Rect, float]:
        """Where a placed name's meaning is written, and its font size: under the name, lined up with it on the side
        away from its point (above it for a name above its point), so the name stays put as the meaning comes and
        goes."""
        size = self.layout.label_font * MEANING_FONT
        w, h = p.text_width(p.font("SemiBold", size), text), size * 1.25
        x0, y0, x1, y1 = name
        left = {"right": x0, "left": x1 - w}.get(side, (x0 + x1) / 2 - w / 2)
        top = y0 - h if side == "above" else y1
        return (left, top, left + w, top + h), size

    def _value_rect(self, v, scale: float, dx: float, dy: float) -> tuple[Rect, float]:
        """Where a value (or a name the drawing placed) is written (frame pixels), beside its point on its side, and
        its font size."""
        name = getattr(v, "label", False)  # a Value the drawing placed a name with; a beat's Written is a number
        size = self.layout.label_font * (TICK_FONT if v.tick else NAME_FONT if name else VALUE_FONT)
        w, h, g = p.text_width(p.font("Bold" if v.tick else "ExtraBold", size), v.text), size * 1.2, VALUE_GAP
        x, y = v.at.x * scale + dx, v.at.y * scale + dy
        rect = {"above": (x - w / 2, y - g - h, x + w / 2, y - g), "below": (x - w / 2, y + g, x + w / 2, y + g + h),
                "left": (x - g - w, y - h / 2, x - g, y + h / 2), "right": (x + g, y - h / 2, x + g + w, y + h / 2)}[v.side]
        return rect, size

    def _values(self, canvas: skia.Canvas, beat, cam: Cam, view: Rect, area: Rect, corners: list[Rect], t: float,
                alpha: float) -> None:
        """The numbers a drawn chart shows (Beat.values), each beside its point on its side, fading in from its
        `since`, an amount popping as it is said. Only inside `area` and clear of the `corners` (the AI tag, the
        brand, the legend): one the camera leaves out (a close-up, the upright frame's sides) is not written rather
        than cut off, which would be misread, and one near the edge fades as it nears it, so a camera move never
        makes it blink."""
        if not beat.values:
            return
        art = self.scene(beat.scene)
        scale, dx, dy = to_frame(cam, view, art)
        for v in beat.values:
            rect, size = self._value_rect(v, scale, dx, dy)
            if any(rect[0] < c[2] and c[0] < rect[2] and rect[1] < c[3] and c[1] < rect[3] for c in corners):
                continue
            inset = min(rect[0] - area[0], rect[1] - area[1], area[2] - rect[2], area[3] - rect[3])
            shown = alpha * p.ease((t - v.since) / LABEL_IN_S) * min(1.0, inset / VALUE_EDGE)
            if shown > 0.01:
                pop = 0.0 if v.tick else 1 - p.ease((t - v.since) / p.VALUE_POP_S)
                p.draw_value(canvas, v.text, rect, size, art.paper, shown, v.tick, pop)

    def _value_points(self, scene_id: str, cam: Cam, view: Rect) -> list[Point]:
        """Points over every value the scene writes at any time in the video, in frame pixels, for the labels to keep
        clear of: all of them from the scene's first frame, so a label is placed once and does not jump as a value
        is said. A few points each (its corners, its middle and its sides' middles), as each one costs the placing."""
        if scene_id not in self._scene_values:
            seen = {}
            for beat in self.timeline.beats:
                for v in beat.values if beat.scene == scene_id else []:
                    seen.setdefault((v.text, v.at.x, v.at.y, v.side), v)
            self._scene_values[scene_id] = list(seen.values())
        scale, dx, dy = to_frame(cam, view, self.scene(scene_id))
        points = []
        for v in self._scene_values[scene_id]:
            (x0, y0, x1, y1), _ = self._value_rect(v, scale, dx, dy)
            points += [(x, y) for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1)]
        return points

    def _conversions(self, canvas: skia.Canvas, beat, view: Rect, bounds: Rect, t: float,
                     places: dict[str, tuple[float, float, float, float]],
                     strength: dict[str, float] | None = None) -> None:
        """What each structure the beat is about turns into what, in the pill placed for it, resting with its
        structure's label (LABELS_CALM)."""
        font = self.layout.label_font * 1.25
        for conv in beat.conversions:
            if conv.structure not in places:
                continue
            x0, y0, x1, y1 = places[conv.structure]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            p.draw_conversion(canvas, (cx, cy), conv.text, self.timeline.palette[conv.structure],
                              p.ease((t - conv.since) / LABEL_IN_S) * edge_fade(cx, cy, view)
                              * (strength or {}).get(conv.structure, 1.0), bounds, font)

    def _card(self, scene_id: str, view: Rect) -> tuple[skia.Image, float, float]:
        """The scene's notice card as an image and where it goes: drawn once, as wrapping its notes and blurring
        its shadow every frame cost an eighth of the frame."""
        if scene_id not in self._cards:
            layout, notes = self.layout, self.timeline.notices[scene_id]
            surface = skia.Surface(layout.width, layout.height)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            if self.portrait:  # across the frame, in the room kept above the picture
                left, top = layout.margin, view[1] - self._card_h + NOTICE_GAP
            else:  # beside the picture, level with its top, below the key band
                left, top = view[2], view[1] + layout.margin + 8
            bottom = p.draw_notice(canvas, left, top, layout.width - layout.margin, notes[:NOTICE_MAX],
                                   NOTICE_FONT, more=max(len(notes) - NOTICE_MAX, 0))
            x0, y0 = max(int(left) - 24, 0), max(int(top) - 24, 0)
            x1, y1 = layout.width, min(int(bottom) + 32, layout.height)
            card = surface.makeImageSnapshot(skia.IRect.MakeLTRB(x0, y0, x1, y1))
            self._cards[scene_id] = (card, float(x0), float(y0))
        return self._cards[scene_id]

    def _card_height(self, notes: list[str]) -> float:
        """How tall a card of these notes is across an upright frame: drawn once where nothing shows it."""
        width = self.layout.width - 2 * self.layout.margin
        scratch = skia.Surface(int(width) + 1, 1)
        return p.draw_notice(scratch.getCanvas(), 0, 0, width, notes[:NOTICE_MAX], NOTICE_FONT,
                             more=max(len(notes) - NOTICE_MAX, 0))

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
                _, composed = art.compose(ghosts, spots, self._frame_tints)
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

    def _arrivals(self, scene_id: str, flow) -> tuple[float, list[tuple[float, str]], list[str]]:
        """A flow's trip, as its lead particle makes it (lead_at): how long a trip takes; when into each trip it
        reaches each part it stops at (not a space), with the part; and the parts it passes through on the way."""
        key = (scene_id, tuple(flow.path), flow.label, flow.blocked)
        if key not in self._arrival_times:
            lane = self._lanes.get((scene_id, tuple(flow.path), flow.label), 0)
            points = self._path_points(scene_id, flow.path, flow.blocked, lane)
            stops = [loc for _, loc in self._path_stops(scene_id, flow.path, lane)]
            legs = _leg_seconds(points, None if flow.blocked else self._bends(scene_id, flow.path, lane))
            part = [not self._is_space(scene_id, loc) for loc in stops]
            reached = [(k * FLOW_PAUSE_S + sum(legs[:k]), stops[k].name) for k in range(1, len(points)) if part[k]]
            through = [stops[k].name for k in range(1, len(points) - 1) if part[k]]
            self._arrival_times[key] = (FLOW_PAUSE_S * len(points) + sum(legs), reached, through)
        return self._arrival_times[key]

    def _reacting(self, canvas: skia.Canvas, beat, plan: BeatPlan, cam: Cam, view: Rect, t: float,
                  ghosts: dict[str, float], spots: dict[str, float]) -> None:
        """The parts reacting to what happens to them: popping as their name is first heard, bumping as a particle
        reaches them (a blocked one only the once), and rocking while particles pass through them, or, a part too
        long to rock, swelling and easing back. Each is the picture as it is now (its washes included) cut out by
        the part's mask, turned and grown about its middle, over its painted copy. Nothing on a graph reacts: grown or
        turned, a curve would no longer say what the graph says."""
        if beat.scene in self.timeline.axes:
            return
        art = self.scene(beat.scene)
        located = self.locations.scene(beat.scene)
        turning = {turn.structure for turn in beat.turning}
        grow: dict[str, float] = {}
        work: dict[str, float] = {}  # how hard each part passed through works, 0-1, with its flows' fades
        for name, at in self._pops.get(beat.scene, []):
            if 0 <= t - at < POP_S:
                grow[name] = grow.get(name, 0.0) + POP * math.sin(math.pi * (t - at) / POP_S)
        for flow, (flow_in, flow_out) in zip(beat.particles, plan.runs):
            alpha = p.ease((t - flow_in) / FLOW_IN_S) * p.ease((flow_out - t) / FLOW_IN_S)
            if alpha <= 0.01 or self._wrong.get((beat.scene, tuple(flow.path), flow.label, flow.blocked)):
                continue
            if self._as_arrow(beat.scene, flow):  # each part bumps once, as the arrow into it draws in
                for k, (_, _, loc) in enumerate(self._handoffs(beat.scene, flow)):
                    at = t - flow_in - k * HANDOFF_STEP_S - HANDOFF_GROW_S
                    if 0 <= at < BUMP_S and not self._is_space(beat.scene, loc):
                        grow[loc.name] = grow.get(loc.name, 0.0) + BUMP * math.sin(math.pi * at / BUMP_S)
                if not flow.blocked:  # and a part it passes through works while it runs, as under particles
                    for name in self._arrivals(beat.scene, flow)[2]:
                        work[name] = max(work.get(name, 0.0), alpha)
                continue
            trip, reached, through = self._arrivals(beat.scene, flow)
            if trip <= 0:
                continue  # none of its stops is on the scene
            into = t - flow_in if flow.blocked else (t - flow_in) % trip
            for at, name in reached:
                if 0 <= into - at < BUMP_S:
                    grow[name] = grow.get(name, 0.0) + BUMP * math.sin(math.pi * (into - at) / BUMP_S)
            for name in through if not flow.blocked else []:
                work[name] = max(work.get(name, 0.0), alpha)
        scale, dx, dy = to_frame(cam, view, art)
        beat_pulse = pulse(t, self.timeline.bpm / 60 if self.timeline.bpm else PULSE_HZ)
        for name in sorted(set(grow) | set(work)):
            loc = located.get(name)
            if (loc is None or name not in art.masks or self._is_space(beat.scene, loc)
                    or (name in turning and art.spin_disc(name) is not None)):  # a wheel turning in place already moves
                continue
            size = 1 + grow.get(name, 0.0)
            if name in work and name not in turning and ghosts.get(name, 0.0) < 0.5:
                reach = max(loc.box.x1 - loc.box.x0, loc.box.y1 - loc.box.y0) / 2
                size += min(PULSE_GROW, PULSE_PX / max(reach, 1.0)) * work[name] * beat_pulse
            if size - 1 < 1e-3:
                continue
            _, composed = art.compose(ghosts, spots, self._frame_tints)
            cx, cy = loc.centroid.x, loc.centroid.y
            canvas.save()
            canvas.clipRect(skia.Rect.MakeLTRB(*view))
            canvas.translate(cx * scale + dx, cy * scale + dy)
            canvas.scale(scale * size, scale * size)
            canvas.translate(-cx, -cy)
            canvas.saveLayer(skia.Rect.MakeLTRB(loc.box.x0 - 4, loc.box.y0 - 4, loc.box.x1 + 4, loc.box.y1 + 4))
            canvas.drawImage(art.cut(name), 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear),
                             skia.Paint(Color=skia.ColorWHITE))
            canvas.drawImage(composed, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear),
                             skia.Paint(BlendMode=skia.BlendMode.kSrcIn))
            canvas.restore()
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
        located = self.locations.scene(beat.scene)
        # A few where they are scarce (Crowd.thin), only in a space (the outside of a cell, not the pump at a
        # path's end), each on points of its own at the end of the space's list, so no dot sits on another.
        crowded = {c.region for c in beat.crowds if not c.thin}  # a space: something crowds it
        thin = [c for c in beat.crowds if c.thin and c.region in art.masks and (c.region in crowded or (
                (loc := located.get(c.region)) is not None and self._is_space(beat.scene, loc)))]
        reserved: dict[str, int] = {}
        slices: dict[int, list] = {}
        for crowd in thin:
            points = art.crowd_points(crowd.region)
            n = max(THIN_MIN, round(len(points) * crowd.start))
            used = reserved.get(crowd.region, 0)
            slices[id(crowd)] = points[max(len(points) - used - n, 0):len(points) - used]
            reserved[crowd.region] = used + n
        for crowd in beat.crowds:
            if crowd.region not in art.masks or (crowd.thin and id(crowd) not in slices):
                continue
            level = p.lerp(crowd.start, crowd.end, x)
            alpha = self._crowd_alpha(beat, crowd, spots, ghosts)
            if alpha <= 0.01:
                continue
            if crowd.thin:  # its few dots, untinted: the space is the other crowd's
                points = slices[id(crowd)]
                self._crowd_dots(canvas, crowd, points, len(points), (scale, dx, dy), t, alpha)
                continue
            points = art.crowd_points(crowd.region)  # its tint is in the picture (_crowd_tints)
            points = points[:len(points) - reserved.get(crowd.region, 0)]
            self._crowd_dots(canvas, crowd, points, level * len(points), (scale, dx, dy), t, alpha)

    def _crowd_alpha(self, beat, crowd, spots: dict[str, float], ghosts: dict[str, float]) -> float:
        """How strongly a crowd shows: half as pale as its space while that is a ghost (it is there from the start), and
        dimmed with it when something else is spotlit, unless the beat's particles fill or empty it."""
        moving = any(f.label == crowd.label and crowd.region in (f.path[0], f.path[-1]) for f in beat.particles)
        others = 0.0 if moving else max((v for name, v in spots.items() if name != crowd.region), default=0.0)
        ghost = ghosts.get(crowd.region, 0.0) * CROWD_GHOST
        return (1 - ghost) * (1 - SPOT_WASH * others)

    def _crowd_tints(self, beat, t: float, ghosts: dict[str, float], spots: dict[str, float]) -> tuple:
        """Each filling space's tint at t, (name, colour, alpha), in steps of TINT_STEP: drawn into the picture
        (SceneArt.compose), which is kept while it does not change, so a frame no longer washes every space over
        the whole frame again. That wash was nearly half of a frame's time on the pump's song (Nov 1)."""
        art = self.scene(beat.scene)
        x = p.clamp01((t - beat.t0) / (beat.t1 - beat.t0))
        tints = []
        for crowd in beat.crowds:
            if crowd.thin or crowd.region not in art.masks:
                continue
            alpha = CROWD_TINT * p.lerp(crowd.start, crowd.end, x) * self._crowd_alpha(beat, crowd, spots, ghosts)
            step = round(alpha / TINT_STEP) * TINT_STEP
            if step > 0:
                tints.append((crowd.region, crowd.color, round(step, 4)))
        return tuple(tints)

    @staticmethod
    def _crowd_dots(canvas: skia.Canvas, crowd, points: list, shown: float, frame: tuple, t: float,
                    alpha: float = 1.0) -> None:
        """A crowd's dots, drifting in place: the first `shown` of `points` (art pixels), the last fading in."""
        scale, dx, dy = frame
        for k, (px, py) in enumerate(points):
            fade = p.clamp01(shown - k)
            if fade <= 0.01:
                break
            phase = (k + (7 if crowd.thin else 0)) * 2.39996  # the golden angle, so no two drift together
            speed = 0.5 + 0.08 * (k % 5)
            at = ((px + CROWD_DRIFT * math.sin(speed * t + phase)) * scale + dx,
                  (py + CROWD_DRIFT * math.cos(0.8 * speed * t + 2 * phase)) * scale + dy)
            if p.carried(crowd.label):
                p.draw_marker(canvas, at, crowd.color, crowd.label, alpha * fade)
            else:
                p.draw_crowd_dot(canvas, at, crowd.color, alpha * fade)

    def _is_space(self, scene_id: str, loc) -> bool:
        """A space (the stroma, a lumen): a box covering much of its picture, or spanning most of its width. One the
        lesson fills is a space however little of its box its outline fills: the pump's cytoplasm came back as red
        speckle on the pink (3% of its box), and K⁺ crossed the picture sideways to the speckle's label point."""
        art = self.scene(scene_id)
        wide, tall = loc.box.x1 - loc.box.x0, loc.box.y1 - loc.box.y0
        big = wide * tall > SPACE_SHARE * art.width * art.height or wide > SPACE_SPAN * art.width
        return big and (loc.name not in art.masks or loc.name in self._crowded or art.fill(loc.name) >= SPACE_FILL)

    def _path_stops(self, scene_id: str, path: list[str], lane: int = 0) -> list[tuple[Point, object]]:
        """Where particles stop along a path, in art pixels, each with the structure it is in: a structure's label
        point, but a space's point just past the edge of the structure next to it on the path, on the space's side,
        so a flow into the lumen ends just under the complex that pumps it rather than across the picture. A space
        in the middle of a path has two points, one beside the part before it and one beside the part after it,
        so the flow runs along the space between them instead of cutting across the picture. In lane 1 or 2 (a
        second thing travelling the same stops, _lanes) every point moves aside, so the two run side by side."""
        located = self.locations.scene(scene_id)
        stops = [loc for name in path if (loc := located.get(name)) is not None]
        art = self.scene(scene_id)

        def beside(loc, near) -> Point:
            inset = (loc.box.y1 - loc.box.y0) * 0.1
            upright = loc.box.y1 - loc.box.y0 > loc.box.x1 - loc.box.x0
            if upright and loc.box.y0 + inset <= near.label_anchor.y <= loc.box.y1 - inset:
                level = across(loc, near)
                if level is not None:
                    return level
            x = min(max(near.label_anchor.x, loc.box.x0), loc.box.x1)
            below = (loc.box.y0 + loc.box.y1) / 2 > near.label_anchor.y
            y = near.box.y1 + SPACE_REACH if below else near.box.y0 - SPACE_REACH
            return x, min(max(y, loc.box.y0 + inset), loc.box.y1 - inset)

        def across(loc, near) -> Point | None:
            """An upright space level with its neighbour (an alveolus beside the wall it shares with a capillary):
            the point inside the space, going from the neighbour's label point toward the space at the same
            height, past where it starts by SPACE_REACH or ACROSS_SHARE of the way on to its middle; None when the
            space does not start that way. A wide space (a lumen, the air above a chimney) has its neighbours above
            or below it. Round 21's lungs put both spaces' points at the top, above and below the wall's, and the
            gases ran up and down the capillary instead of across the membrane."""
            cells = art.cells(loc.name)
            y, x = near.label_anchor.y, near.label_anchor.x
            step = routing.GRID if loc.centroid.x >= x else -routing.GRID
            row = int(y // routing.GRID)
            if not 0 <= row < cells.shape[0]:
                return None
            while loc.box.x0 <= x <= loc.box.x1 or (x < loc.box.x0 and step > 0) or (x > loc.box.x1 and step < 0):
                col = int(x // routing.GRID)
                if 0 <= col < cells.shape[1] and cells[row, col]:
                    # Far enough in that the crossing reads: from just inside the edge, the gases crossed the
                    # lungs' membrane in a hop too short to see.
                    deep = max(SPACE_REACH, ACROSS_SHARE * abs(loc.centroid.x - x))
                    return min(max(x + math.copysign(deep, step), loc.box.x0), loc.box.x1), y
                x += step
            return None

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
        # A part at either end of the path is met at its face toward the stop beside it, a little way in, never at its
        # label point, which can lie anywhere in it: in the pump's close-up of Nov 1 it was out in the lobes above the
        # membrane, and ATP was drawn crossing the membrane to reach it.
        if len(out) > 1:
            given = [point for point, _, _ in out]
            for i in (0, len(out) - 1):
                point, loc, fixed = out[i]
                if not fixed:
                    out[i] = (self._face(scene_id, loc, given[1 if i == 0 else -2]), loc, True)
        anchors = [point for point, _, _ in out]
        if len(anchors) < 2:
            return [(point, loc) for point, loc, _ in out]
        # A label point is moved a little toward its neighbour, to keep the label's dot in sight; a space's point
        # has no dot, and moving it would pull it back toward the edge it was put past.
        pairs = [(a if space else q, loc) for (a, loc, space), q in zip(out, stop_points(anchors))]
        if lane:
            (x0, y0), (x1, y1) = anchors[0], anchors[-1]
            length = math.hypot(x1 - x0, y1 - y0) or 1.0
            side = LANE_GAP * (1 if lane % 2 else -1) * ((lane + 1) // 2)
            nx, ny = -(y1 - y0) / length * side, (x1 - x0) / length * side
            pairs = [((x + nx, y + ny), loc) for (x, y), loc in pairs]
        return pairs

    def _path_points(self, scene_id: str, path: list[str], blocked: bool = False, lane: int = 0) -> list[Point]:
        """Where the particles travel, in art pixels: the path's stops, except that a flow between a part and a
        space leaves or enters the part at its edge on the space's side, where the arrow does (water is split at
        Photosystem II's lumen face: an H⁺ set off from the middle of the complex looked to fall through it), and a
        flow through one part from one side to the other goes straight through it (_crossing). A blocked flow still
        runs into its blocker, where it piles up."""
        crossing = None if blocked else self._crossing(scene_id, path, lane)
        if crossing:
            return crossing
        pairs = self._path_stops(scene_id, path, lane)
        points = [point for point, _ in pairs]
        if len(pairs) < 2:
            return points
        # Not when the space's point lies in the part's box (the pump's box held the whole strip of the outside the
        # Nov 1 picture found): leaving the box toward it lands on it, and the arrow had no length. The stop is
        # already at the part's face (_face).
        (a, first), (b, second) = pairs[0], pairs[1]
        if not self._is_space(scene_id, first) and self._is_space(scene_id, second) and not _inside(b, first.box):
            points[0] = _leave(a, b, first.box)
        (y, before), (z, last) = pairs[-2], pairs[-1]
        if (not blocked and self._is_space(scene_id, before) and not self._is_space(scene_id, last)
                and not _inside(y, last.box)):
            points[-1] = _leave(z, y, last.box)
        return points

    def _bends(self, scene_id: str, path: list[str], lane: int = 0) -> list[Point | None]:
        """Each leg's bend, in art pixels: a hop from one part to the next (both stops parts, not spaces) curves
        up in a gentle arc, the hand-off a textbook draws; a flow into, out of or through a space runs straight.
        The particles ride the same curve as the arrow."""
        pairs = self._path_stops(scene_id, path, lane)
        if self._crossing(scene_id, path, lane):
            # Straight through the part, holding in its middle: two legs for the particles, and one for each pair of
            # the stops the arrows and the labels' keep-clear points count (a space in the middle counts twice).
            return [None] * max(2, len(pairs) - 1)
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

    def _crossing(self, scene_id: str, path: list[str], lane: int = 0) -> list[Point] | None:
        """A flow through one part from one side of it to the other (Na⁺ out through the pump, H⁺ through ATP
        synthase): from just past the part's edge on the side it comes from, straight through the part's middle
        (where the lead holds, as at any stop), to just past its far edge, across the way the two sides lie; a second
        thing crossing the same part runs in a lane
        beside it, the same side whichever way it goes. None for any other path. Before Nov 1 the particles ran to the
        part's label point and on, bending in the part; a space the picture outlined patchily (the inside of a cell
        that filled a fifth of its box) was taken for a part, and Na⁺ and K⁺ crossed each other in the pump."""
        key = ("crossing", scene_id, tuple(path), lane)
        if key in self._through:
            return self._through[key]
        located = self.locations.scene(scene_id)
        stops = [loc for name in path if (loc := located.get(name)) is not None]
        found = None
        if len(stops) == 3 and len({s.name for s in stops}) == 3:
            # Which way it crosses goes by where its ends lie beside the part (_path_stops), not by their label points:
            # a space's label point can sit at the far edge of the picture. On Nov 1 the inside of the cell had its
            # point at the left edge, so the sides read as left and right of the pump, and the sodium ran sideways.
            ends = self._path_stops(scene_id, path)
            (ax, ay), (bx, by) = ends[0][0], ends[-1][0]
            mid = stops[1].label_anchor
            part = stops[1]
            upright = abs(by - ay) >= abs(bx - ax)  # the sides lie above and below it: it crosses up or down
            if upright and min(ay, by) < mid.y < max(ay, by):
                across = part.label_anchor.x + LANE_GAP * (1 if lane % 2 else -1) * ((lane + 1) // 2)
                near, far = self._extent(scene_id, part, across, upright=True)
                down = by > ay
                start, end = (near - SPACE_REACH, far + SPACE_REACH) if down else (far + SPACE_REACH, near - SPACE_REACH)
                start, end = (_in_space(start, stops[0], True) if self._is_space(scene_id, stops[0]) else start,
                              _in_space(end, stops[2], True) if self._is_space(scene_id, stops[2]) else end)
                found = [(across, start), (across, (near + far) / 2), (across, end)]
            elif not upright and min(ax, bx) < mid.x < max(ax, bx):
                across = part.label_anchor.y + LANE_GAP * (1 if lane % 2 else -1) * ((lane + 1) // 2)
                near, far = self._extent(scene_id, part, across, upright=False)
                right = bx > ax
                start, end = (near - SPACE_REACH, far + SPACE_REACH) if right else (far + SPACE_REACH, near - SPACE_REACH)
                start, end = (_in_space(start, stops[0], False) if self._is_space(scene_id, stops[0]) else start,
                              _in_space(end, stops[2], False) if self._is_space(scene_id, stops[2]) else end)
                found = [(start, across), ((near + far) / 2, across), (end, across)]
        self._through[key] = found
        return found

    def _extent(self, scene_id: str, part, at: float, upright: bool) -> tuple[float, float]:
        """Where a part starts and ends along the line through it at `at` (a column when upright, else a row), in
        art pixels, read off its outline; its box's edges when the line misses it."""
        cells = self.scene(scene_id).cells(part.name)
        index = int(at // routing.GRID)
        line = cells[:, index] if upright and 0 <= index < cells.shape[1] else (
            cells[index, :] if not upright and 0 <= index < cells.shape[0] else None)
        hits = [] if line is None else [i for i, on in enumerate(line) if on]
        if not hits:
            return (part.box.y0, part.box.y1) if upright else (part.box.x0, part.box.x1)
        return hits[0] * routing.GRID, (hits[-1] + 1) * routing.GRID

    def _legs(self, scene_id: str, path: list[str], lane: int = 0) -> list[tuple[list[Point], bool, int, int, object]]:
        """A route's arrows, in art pixels: (the line it draws, ends in a space, its first stop, its last stop, the
        part it passes through or None). A
        leg leaves the edge of the part it starts from and ends at the edge of the part it reaches, curved when it
        is a hop from part to part; a flow through a part from one space to the other is one straight leg."""
        pairs = self._path_stops(scene_id, path, lane)
        points, stops = [pt for pt, _ in pairs], [loc for _, loc in pairs]
        bends = self._bends(scene_id, path, lane)
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
        """Points along every path the scene's particles travel at any time in the video, in frame pixels: what labels and reaction pills keep clear of. Every path, not only
        those travelled on screen, so a label is placed once for the scene and does not jump as each flow starts."""
        if scene_id not in self._scene_routes:
            routes = []
            for beat in self.timeline.beats:
                for route in beat.routes if beat.scene == scene_id else []:
                    if (route.path, route.label, route.color) not in routes:
                        routes.append((route.path, route.label, route.color))
            samples, drawn = [], set()
            for path, label, color in routes:
                flow = next((f for f in self._flows_of(scene_id, path, label) if self._as_arrow(scene_id, f)), None)
                if flow is not None and not self._graph(scene_id):  # its arrow
                    line = self._flow_line(scene_id, flow)
                    for start, end in zip(line, line[1:]):
                        n = max(int(math.dist(start, end) / ARROW_SAMPLE), 1)
                        samples += [(p.lerp(start[0], end[0], k / n), p.lerp(start[1], end[1], k / n))
                                    for k in range(n + 1)]
                    continue
                if flow is not None:  # its arrows, not the route between its stops
                    for tail, tip, _ in self._handoffs(scene_id, flow):
                        n = max(int(math.dist(tail, tip) / ARROW_SAMPLE), 1)
                        samples += [(p.lerp(tail[0], tip[0], k / n), p.lerp(tail[1], tip[1], k / n)) for k in range(n + 1)]
                    continue
                lane = self._lanes.get((scene_id, tuple(path), label), 0)
                pairs = self._path_stops(scene_id, path, lane)
                for line, _, first, last, _ in self._legs(scene_id, path, lane):
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

    def _flows_of(self, scene_id: str, path: list[str], label: str) -> list:
        """The scene's flows on these stops carrying this, as the beats have them."""
        return [f for b in self.timeline.beats if b.scene == scene_id for f in b.particles
                if f.path == path and f.label == label]

    def _arrowed(self, scene_id: str) -> set[str]:
        """What travels on the scene as hand-off arrows, which the legend keys with an arrow; once per scene."""
        if scene_id not in self._arrowed_labels:
            self._arrowed_labels[scene_id] = {f.label for b in self.timeline.beats if b.scene == scene_id
                                              for f in b.particles if f.label and self._as_arrow(scene_id, f)}
        return self._arrowed_labels[scene_id]

    def _legend(self, beat, t: float) -> list[tuple[str, str, float]]:
        """The key to what the scene shows with no words of its own: (colour, kind, alpha) for each kind whose particles
        cannot carry their name (a word, not a symbol printed on them: p.carried) and nothing beside them says it: an
        arrow on this beat with no one-liner (Particles.says), and a space's crowd of unmarked dots. Only while they
        show: a key listing what had moved before named, as the owner saw on Nov 1, a "glucose" no longer on screen.
        Not on a graph, whose arrow has its word beside it (_draw_handoffs): a key in the corner saying "price" read
        as a button, far from the arrow it named."""
        kinds: dict[str, tuple[str, str, float]] = {}
        graph = self._graph(beat.scene)

        def add(label: str, color: str, alpha: float) -> None:
            if label and not p.carried(label) and label not in kinds:
                kinds[label] = (color, label, alpha)

        for flow in beat.particles if not graph else ():
            if not " ".join(flow.says.split()):
                add(flow.label, flow.color, p.ease((t - beat.t0) / LABEL_IN_S))
        for crowd in beat.crowds:
            if max(crowd.start, crowd.end) > 0:  # an empty space shows no dots to name
                add(crowd.label, crowd.color, 1.0)
        return list(kinds.values())

    def _particles(self, canvas: skia.Canvas, beat, plan: BeatPlan, cam: Cam, view: Rect, t: float) -> None:
        """Each path's particles, between its structures' label anchors (always inside them),
        over the diagram and under the labels; they stop beside each label's dot. Each flow fades in and out with
        its own run."""
        scale, dx, dy = to_frame(cam, view, self.scene(beat.scene))

        def on_frame(q: Point) -> Point:
            return q[0] * scale + dx, q[1] * scale + dy

        canvas.save()
        canvas.clipRect(skia.Rect.MakeLTRB(*view))
        for flow, (flow_in, flow_out) in zip(beat.particles, plan.runs):
            alpha = p.ease((t - flow_in) / FLOW_IN_S) * p.ease((flow_out - t) / FLOW_IN_S)
            key = (beat.scene, tuple(flow.path), flow.label, flow.blocked)
            if alpha <= 0.01 or self._wrong.get(key) or self.only not in (None, key):
                continue
            elapsed = t - flow_in
            lane = self._lanes.get((beat.scene, tuple(flow.path), flow.label), 0)
            if not flow.blocked and not self._graph(beat.scene) and self._crossing(beat.scene, flow.path, lane):
                said = count_in(beat.line, flow.label)
                line = self._flow_line(beat.scene, flow)
                room = routing.length(line) * _fit(self.view(beat.scene), self.scene(beat.scene))  # framed whole
                self._draw_crossing(canvas, flow, line, elapsed, alpha, on_frame, t,
                                    said if said > 1 else next((n for name in flow.path
                                                                if (n := self._counts.get((flow.label, name)))), 1),
                                    room)
                continue
            if self._as_arrow(beat.scene, flow):
                self._draw_handoffs(canvas, beat.scene, flow, elapsed, alpha, on_frame, t, count_in(beat.line, flow.label))
                continue
            points = self._path_points(beat.scene, flow.path, flow.blocked, lane)
            bends = None if flow.blocked else self._bends(beat.scene, flow.path, lane)
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
                stream = stream_at(points, elapsed, bends)
            lead_xy, spaced = on_frame(lead), []
            for dot in trail:
                if math.dist(dot, spaced[-1] if spaced else lead_xy) >= TRAIL_GAP:
                    spaced.append(dot)
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

    def _travels(self, scene_id: str, flow) -> bool:
        """Whether a flow is drawn as an arrow at each hand-off rather than as particles: as the bundle says
        (Particles.moves), else when any flow on the scene carrying the same thing is a journey (_journey), so one
        thing moves one way on a scene. Never a blocked flow, which piles up at its blocker, nor one carrying a
        symbol ("H⁺", "O₂", "ATP"): an ion or a small molecule is a particle, whatever the bundle says. A word too
        long to write on a particle ("glucose", "magma"), or nothing at all, is always an arrow: a particle says what
        it is (the owner, Nov 1). A short word ("air") goes as the bundle says."""
        if flow.blocked or p.symbol(flow.label):
            return False
        if not p.carried(flow.label):  # a word ("glucose") or nothing: an arrow with its one-liner, never bare dots
            return True
        if flow.moves:
            return flow.moves == "travels"
        # Unmarked dots too, all one way on a scene: judged a flow at a time, round 22's greenhouse would send its
        # sunlight down as dots and its heat out as arrows, on one picture.
        key = (scene_id, flow.label)
        if key not in self._travelling:
            self._travelling[key] = any(self._journey(scene_id, f) for b in self.timeline.beats if b.scene == scene_id
                                        for f in b.particles if f.label == flow.label and not f.blocked and not f.moves)
        return self._travelling[key]

    def _journey(self, scene_id: str, flow) -> bool:
        """Whether a flow's route is a journey along its parts: it bends with them (BENT), or runs far inside a part
        between two others (THROUGH_MAX), as filtrate down the loop of Henle or magma up the volcano do."""
        lane = self._lanes.get((scene_id, tuple(flow.path), flow.label), 0)
        points = self._path_points(scene_id, flow.path, False, lane)
        if len(points) < 2:
            return False
        stops = [loc.name for _, loc in self._path_stops(scene_id, flow.path, lane)]
        if stops[0] == stops[-1]:
            return False  # out through a part and back into the space it left: it crosses that part, going nowhere
        route = _dense(points, self._bends(scene_id, flow.path, lane))
        if routing.length(route) > BENT * max(math.dist(points[0], points[-1]), 1.0):
            return True
        art = self.scene(scene_id)
        return any(routing.inside(route, art.cells(name)) > THROUGH_MAX for name in stops[1:-1])

    def _handoffs(self, scene_id: str, flow) -> list[tuple[Point, Point, object]]:
        """A travelling flow's arrows, tail and tip in art pixels, each with the part it enters: one where it passes
        from each part into the next (routing.handoff), pointing on toward the stop after; beside another's on the
        same stops (its lane). Worked out once per flow, as every frame asks."""
        key = (scene_id, tuple(flow.path), flow.label)
        if key not in self._handoff_arrows:
            art = self.scene(scene_id)
            lane = self._lanes.get(key, 0)
            pairs = self._path_stops(scene_id, flow.path, lane)
            arrows = []
            for i in range(len(pairs) - 1):
                toward = pairs[min(i + 2, len(pairs) - 1)][0]
                (x0, y0), (x1, y1) = routing.handoff(art.cells(pairs[i][1].name), art.cells(pairs[i + 1][1].name),
                                                     toward)
                if lane:  # beside the other's, across the arrow
                    length = math.hypot(x1 - x0, y1 - y0) or 1.0
                    nx, ny = -(y1 - y0) / length * LANE_GAP * lane, (x1 - x0) / length * LANE_GAP * lane
                    x0, y0, x1, y1 = x0 + nx, y0 + ny, x1 + nx, y1 + ny
                if (x0, y0) != (x1, y1):
                    arrows.append(((x0, y0), (x1, y1), pairs[i + 1][1]))
            self._handoff_arrows[key] = arrows
        return self._handoff_arrows[key]

    def _as_arrow(self, scene_id: str, flow) -> bool:
        """Whether a flow is drawn as an arrow: every flow off a graph (FACE_DEPTH's note says why), and on a graph,
        those that travel (_travels)."""
        return not self._graph(scene_id) or self._travels(scene_id, flow)

    def _face(self, scene_id: str, loc, toward: Point) -> Point:
        """Where a path meets a part at its end, in art pixels: the point of its outline nearest `toward` (the stop
        beside it), moved FACE_DEPTH in toward its middle; its label point when it has no outline here."""
        try:
            cells = self.scene(scene_id).cells(loc.name)
        except KeyError:
            return loc.label_anchor.x, loc.label_anchor.y
        ys, xs = np.nonzero(cells)
        if not len(xs):
            return loc.label_anchor.x, loc.label_anchor.y
        cx, cy = (xs + 0.5) * routing.GRID, (ys + 0.5) * routing.GRID
        k = int(np.argmin((cx - toward[0]) ** 2 + (cy - toward[1]) ** 2))
        ex, ey = float(cx[k]), float(cy[k])
        mx, my = loc.centroid.x, loc.centroid.y
        d = math.dist((ex, ey), (mx, my))
        if d < 1.0:
            return ex, ey
        step = min(FACE_DEPTH, d)
        return ex + (mx - ex) / d * step, ey + (my - ey) / d * step

    def _wrong_arrow(self, scene_id: str, flow) -> str:
        """Why a flow's arrow would mislead, or "": too short to read, leaving the picture, starting or ending away
        from where its path says, or pointing away from where it goes (the way from its first stop to its last)."""
        art = self.scene(scene_id)
        try:
            line = self._flow_line(scene_id, flow)
        except (KeyError, ValueError, RuntimeError) as e:
            return f"no line: {e}"
        if len(line) < 2 or (routing.length(line) < ARROW_MIN and not flow.blocked):  # a blocked one ends at its cross
            return "too short to read"
        if any(not (0 <= x <= art.width and 0 <= y <= art.height) for x, y in line):
            return "leaves the picture"
        located = self.locations.scene(scene_id)
        stops = [loc for name in flow.path if (loc := located.get(name)) is not None]
        if len(stops) < 2:
            return ""
        first, last = stops[0], stops[-1]
        for end, loc, what in ((line[0], first, "starts"), (line[-1], last, "ends")):
            b = loc.box
            if not (b.x0 - ARROW_REACH <= end[0] <= b.x1 + ARROW_REACH
                    and b.y0 - ARROW_REACH <= end[1] <= b.y1 + ARROW_REACH):
                return f"{what} away from {loc.name}"
        # A pointed arrow's way is the helper's, read off the painting and looked at in a still (studio/steps/arrows.py):
        # this guess from the boxes failed two right ones on Nov 1, a part to a wide strip of space beside it.
        if first.name == last.name or self._pointed(scene_id, flow):
            return ""

        # The way it should go: from a part's middle, or from where it starts in a space (beside the part it goes to,
        # not the space's middle: the stroke's O2 set off beside the clot); to a part's middle, or a space by its box,
        # which a patchy outline does not move.
        ax, ay = line[0] if self._is_space(scene_id, first) else (first.centroid.x, first.centroid.y)
        bx, by = (((last.box.x0 + last.box.x1) / 2, (last.box.y0 + last.box.y1) / 2)
                  if self._is_space(scene_id, last) else (last.centroid.x, last.centroid.y))
        vx, vy, wx, wy = line[-1][0] - line[0][0], line[-1][1] - line[0][1], bx - ax, by - ay
        norm = math.hypot(vx, vy) * math.hypot(wx, wy)
        if norm and (vx * wx + vy * wy) / norm < ARROW_TURN:
            return f"points away from {last.name}"
        return ""

    def _pointed(self, scene_id: str, flow) -> list[Point] | None:
        """A hop's arrow as the helper pointed it on the painting (arrows.json): straight from its tail to its head, art
        pixels; None where code places it."""
        placed = self._placed.get((scene_id, tuple(flow.path), flow.label, flow.blocked))
        if placed is None or placed.start is None or placed.end is None:
            return None
        return [(placed.start.x, placed.start.y), (placed.end.x, placed.end.y)]

    def hops(self) -> list[tuple[str, object, str]]:
        """Each flow drawn as an arrow from place to place, once: off a graph, and not a crossing (_crossing), which
        code draws as its particles crossing. Each with its scene and the line it first plays under."""
        found: dict[tuple, tuple[str, object, str]] = {}
        for beat in self.timeline.beats:
            for flow in beat.particles if not self._graph(beat.scene) else ():
                key = (beat.scene, tuple(flow.path), flow.label, flow.blocked)
                lane = self._lanes.get((beat.scene, tuple(flow.path), flow.label), 0)
                if key not in found and (flow.blocked or not self._crossing(beat.scene, flow.path, lane)):
                    found[key] = (beat.scene, flow, beat.line)
        return list(found.values())

    def arrow_time(self, scene_id: str, flow) -> float:
        """A moment that shows a hop's arrow (hops()): in the run of its first beat, as near that beat's middle as it
        can be with its gliding arrow ARROW_SEEN of the way along, so a still of it shows which way it goes. A blocked
        one's arrow stands still, whole once drawn in: the beat's middle, or as soon as it is whole."""
        i, k = next((i, k) for i, beat in enumerate(self.timeline.beats) if beat.scene == scene_id
                    for k, f in enumerate(beat.particles) if f == flow)
        beat, (flow_in, flow_out) = self.timeline.beats[i], self.plans[i].runs[k]
        middle, start = (beat.t0 + beat.t1) / 2, flow_in + ROUTE_GROW_S
        if flow.blocked:
            return min(max(middle, start), flow_out - 0.01)
        shown = [self.art_to_frame(middle, *q) for q in self._flow_line(scene_id, flow)]
        total = routing.length(shown)
        speed = glide_speed(total, min(GLIDE_SHARE * total, GLIDE_MAX))
        period = (total + min(GLIDE_SHARE * total, GLIDE_MAX)) / speed + GLIDE_PAUSE_S
        along = ARROW_SEEN * total / speed  # into a glide, as its head gets that far
        t = start + max(round((middle - start - along) / period), 0) * period + along
        return t if t < flow_out else (flow_in + flow_out) / 2

    def _flow_line(self, scene_id: str, flow) -> list[Point]:
        """A flow's arrow off a graph, art pixels: where the helper pointed it on the painting (_pointed); else
        straight through the part it crosses (_crossing); else its stops' legs, as particles ran them before Nov 1
        (_path_points, _bends); up to its blocker when blocked. Routed along the parts' outlines until Nov 1, which
        guessed from SAM's masks as the stops do: the helper points a hop on a painting instead."""
        key = ("flow", scene_id, tuple(flow.path), flow.label, flow.blocked)
        if key not in self._through:
            lane = self._lanes.get((scene_id, tuple(flow.path), flow.label), 0)
            crossing = None if flow.blocked else self._crossing(scene_id, flow.path, lane)
            pointed = self._pointed(scene_id, flow)
            if pointed:
                line = pointed
            elif crossing:
                line = list(crossing)
            else:
                points = self._path_points(scene_id, flow.path, flow.blocked, lane)
                line = _dense(points, None if flow.blocked else self._bends(scene_id, flow.path, lane)) \
                    if len(points) > 1 else points
            self._through[key] = line
        return self._through[key]

    def _draw_tail(self, canvas: skia.Canvas, flow, shown: list[Point], alpha: float, t: float,
                   grown: float = 1.0, count: int = 1) -> None:
        """One particle of what the flow carries, jiggling in place just behind its arrow's tail (frame pixels), so the
        arrow says what moves; for a blocked flow, just short of the cross at its head, where it piles up, both once
        the arrow drawing itself in (`grown`) has reached the blocker. A flow carrying nothing has no particle."""
        if len(shown) < 2 or not (flow.label or flow.blocked):
            return
        total = routing.length(shown)
        if total < 1.0:
            return
        head = flow.blocked
        end = shown[-1] if head else shown[0]
        toward = _along(shown, max(total - 30.0, 0.0)) if head else _along(shown, min(30.0, total))
        d = math.dist(end, toward) or 1.0
        ux, uy = (end[0] - toward[0]) / d, (end[1] - toward[1]) / d  # pointing away from the arrow's body
        if head:  # the cross sits at the head, once the arrow gets there; the particle waits just before it
            alpha *= p.clamp01((grown - 0.9) / 0.1)
            at = (end[0] - ux * (p.BLOCK_R + p.MARKER_R + 6), end[1] - uy * (p.BLOCK_R + p.MARKER_R + 6))
            p.draw_block(canvas, end, alpha)
            if not flow.label:
                return
        else:
            at = (end[0] + ux * (p.MARKER_R + 4), end[1] + uy * (p.MARKER_R + 4))
        phase = (zlib.crc32(flow.label.encode("utf-8")) % 628) / 100
        at = (at[0] + TAIL_JIGGLE_PX * math.sin(2.3 * t + phase), at[1] + TAIL_JIGGLE_PX * math.cos(1.9 * t + 2 * phase))
        ahead = (ux, uy) if head else (-ux, -uy)  # the way it moves
        if count > 1 and not head:  # a few waiting at the tail sit just behind it, clear of the arrow
            back = _width(flow.label) * (0.5 if count == 2 else 0.58)
            at = (at[0] + ux * back, at[1] + uy * back)
        self._draw_kinds(canvas, at, ahead, flow, alpha, count)

    def _draw_crossing(self, canvas: skia.Canvas, flow, line: list[Point], elapsed: float, alpha: float, on_frame,
                       t: float, count: int = 1, room: float = 0.0) -> None:
        """A crossing's particles streaming along its line (art pixels) from one side of the part to the other, again and
        again: `count` of them a cycle (CROSS_MANY when the words give none, and no more than `room`, the way's length
        with the picture framed whole, has room for: the length on screen grows as the camera moves in, and the count
        changed mid-crossing), evenly spaced at one pace, off the line by its own share of CROSS_SPREAD and swinging
        left and right of it on the way (CROSS_WIGGLE), fading in as it leaves and out as it arrives. A frame depends
        only on its time."""
        shown = [on_frame(q) for q in line]
        total = routing.length(shown)
        if len(shown) < 2 or total < 1.0:
            return
        (x0, y0), (x1, y1) = shown[0], shown[-1]
        d = math.hypot(x1 - x0, y1 - y0) or 1.0
        nx, ny = -(y1 - y0) / d, (x1 - x0) / d  # across the way it goes
        many = count if count > 1 else max(1, min(CROSS_MANY, int((room or total) / (CROSS_ROOM * _width(flow.label)))))
        for k in range(many):
            start, place = _jitter(k + 17), _jitter(k + 41)
            gone = (elapsed / CROSS_S + k / many) % 1.0
            x, y = _along(shown, gone * total)
            side = CROSS_SPREAD * (2 * place - 1) + CROSS_WIGGLE * math.sin(2 * math.pi * (CROSS_SWINGS * gone + start))
            fade = p.clamp01(gone / CROSS_FADE) * p.clamp01((1 - gone) / CROSS_FADE)
            p.draw_kind(canvas, (x + nx * side, y + ny * side), flow.color, flow.label, alpha * fade, size=TAIL_SIZE)

    def _draw_kinds(self, canvas: skia.Canvas, at: Point, ahead: Point, flow, alpha: float, count: int) -> None:
        """`count` particles of what the flow carries, packed together around `at` (cluster)."""
        for spot in cluster(at, ahead, count, _width(flow.label)):
            p.draw_kind(canvas, spot, flow.color, flow.label, alpha, size=TAIL_SIZE)

    def _draw_rider(self, canvas: skia.Canvas, flow, shown: list[Point], piece: list[Point], alpha: float, t: float,
                    elapsed: float, count: int = 1) -> None:
        """The flow's particle riding the head of its gliding arrow (frame pixels), the owner's idea (Nov 1): it says
        what moves and the arrow trailing it which way. It fades out at the head as the glide ends, and waits at the
        tail, jiggling and fading in, through the pause before the next."""
        total = routing.length(shown)
        length = min(GLIDE_SHARE * total, GLIDE_MAX)
        speed = glide_speed(total, length)
        period = (total + length) / speed + GLIDE_PAUSE_S
        head = (elapsed % period) * speed
        if piece and head <= total + length:
            fade = 1.0 if head <= total else p.clamp01(1 - (head - total) / max(length, 1.0))
            tip, behind = piece[-1], _along(piece, max(routing.length(piece) - 20.0, 0.0))
            d = math.dist(tip, behind) or 1.0
            ahead = ((tip[0] - behind[0]) / d, (tip[1] - behind[1]) / d)
            if count > 1:  # the bunch's front row at the head, the rest riding just behind it
                back = _width(flow.label) * (0.29 if count == 3 else 0.5 if count in (4, 5) else 0.0)
                tip = (tip[0] - ahead[0] * back, tip[1] - ahead[1] * back)
            self._draw_kinds(canvas, tip, ahead, flow, alpha * fade, count)
            return
        waited = (elapsed % period) - (total + length) / speed  # into the pause, or setting off
        fade = p.ease(waited / GLIDE_PAUSE_S) if head > total + length else 1.0
        self._draw_tail(canvas, flow, shown, alpha * fade, t, count=count)

    def _draw_handoffs(self, canvas: skia.Canvas, scene_id: str, flow, elapsed: float, alpha: float,
                       on_frame, t: float = 0.0, count: int = 1) -> None:
        """A travelling flow's arrows. Off a graph, one dashed arrow along its whole way (_flow_line), drawing itself
        in from its tail as it sets off, its dashes then moving along it, with its one-liner (Particles.says) beside
        it, joined to it. On a
        graph, an arrow at each hand-off drawn in one after another, then a pulse running along them in order, with
        its one-liner, or else its word ("price rises"), beside the first."""
        if not self._graph(scene_id):
            line = self._flow_line(scene_id, flow)
            if len(line) < 2:
                return
            grown = p.ease(elapsed / ROUTE_GROW_S)
            shown = [on_frame(q) for q in line]
            if flow.blocked:  # the whole way to the cross, still
                p.draw_route_arrow(canvas, shown, flow.color, alpha, grown, phase=0.0)
            else:
                p.draw_route_track(canvas, shown, flow.color, alpha * TRACK_ALPHA, grown)
                gliding = elapsed - ROUTE_GROW_S  # it glides once its track has drawn itself in
                piece = _glide(shown, gliding) if gliding >= 0 else []
                rider = bool(flow.label) and gliding >= 0  # its particle rides the gliding arrow's head
                if piece:
                    p.draw_route_arrow(canvas, piece, flow.color, alpha, 1.0, dashed=False, head=not rider)
                if rider:
                    self._draw_rider(canvas, flow, shown, piece, alpha, t, gliding, count)
            if flow.blocked or not (flow.label and elapsed >= ROUTE_GROW_S):
                self._draw_tail(canvas, flow, shown, alpha, t, grown, count)
            says = getattr(flow, "says", "")
            if says and (scene_id, tuple(flow.path), flow.label, flow.blocked) not in self._said:
                middle = _along(line, routing.length(line) / 2) if len(line) > 1 else line[0]
                self._draw_note(canvas, scene_id, ("says", tuple(flow.path), flow.label, says), says, middle,
                                line, flow.color, alpha * p.ease((elapsed - ROUTE_GROW_S / 2) / ROUTE_GROW_S),
                                on_frame)
            return
        arrows = self._handoffs(scene_id, flow)
        n = len(arrows)
        drawn_in = n * HANDOFF_STEP_S + HANDOFF_GROW_S
        cycle = n * HANDOFF_STEP_S + FLOW_PAUSE_S
        for k, (tail, tip, _) in enumerate(arrows):
            grown = p.ease((elapsed - k * HANDOFF_STEP_S) / HANDOFF_GROW_S)
            pulse = 0.0
            if elapsed >= drawn_in:
                into = (elapsed - drawn_in) % cycle - k * HANDOFF_STEP_S
                pulse = math.sin(math.pi * into / HANDOFF_STEP_S) if 0 <= into < HANDOFF_STEP_S else 0.0
                strength = HANDOFF_REST + (1 - HANDOFF_REST) * pulse
            else:
                strength = 1.0
            p.draw_handoff(canvas, on_frame(tail), on_frame(tip), flow.color, alpha * strength, grown, pulse)
        says = getattr(flow, "says", "")
        if arrows and (flow.label or says):  # its one-liner beside it, still, as a textbook annotates a shift
            tail, tip = on_frame(arrows[0][0]), on_frame(arrows[0][1])
            word = says or self._arrow_word(scene_id, flow.label, tail, tip)
            size = self.layout.label_font * ARROW_WORD_FONT
            w, h = p.text_width(p.font("Bold", size), word), size * 1.2
            length = math.hypot(tip[0] - tail[0], tip[1] - tail[1]) or 1.0
            nx, ny = -(tip[1] - tail[1]) / length, (tip[0] - tail[0]) / length  # to the right of the way it goes
            mx, my = (tail[0] + tip[0]) / 2, (tail[1] + tip[1]) / 2
            if abs(nx) >= abs(ny):
                left = mx + ARROW_WORD_GAP if nx > 0 else mx - ARROW_WORD_GAP - w
                rect = (left, my - h / 2, left + w, my + h / 2)
            else:
                top = my + ARROW_WORD_GAP if ny > 0 else my - ARROW_WORD_GAP - h
                rect = (mx - w / 2, top, mx + w / 2, top + h)
            first = p.ease(elapsed / HANDOFF_GROW_S)
            p.draw_name(canvas, word, rect, size, self.scene(scene_id).paper, flow.color, alpha * first, weight="Bold")

    def _note_spot(self, scene_id: str, key: tuple, anchor: Point, size: tuple[float, float],
                   keep_clear: list[Point], shown: tuple[float, float, float, float] | None = None) -> Point | None:
        """Where a note's middle sits, in art pixels, chosen once for the scene: near what it is about, where it
        covers the least of the picture (ink_share), clear of every part's label point, of `keep_clear` (its arrow),
        of the notes placed before it and of the labels painted on the picture (SceneArt.label_boxes); on the
        picture, never off its edge. None when no spot beside it fits."""
        if key not in self._note_spots:
            art = self.scene(scene_id)
            w, h = size
            points = [(loc.label_anchor.x, loc.label_anchor.y) for loc in self.locations.scene(scene_id).structures]
            painted = art.label_boxes() if scene_id in self.timeline.self_labelled else []
            placed = self._noted.setdefault(scene_id, [])
            leaders = self._leaders.setdefault(scene_id, [])

            def crosses(box, a, b) -> bool:  # a line from a to b passes through the box
                return any(box[0] <= a[0] + (b[0] - a[0]) * k / 16 <= box[2] and
                           box[1] <= a[1] + (b[1] - a[1]) * k / 16 <= box[3] for k in range(1, 16))

            best = None
            for reach in NOTE_REACH:
                for ux, uy in NOTE_SIDES:
                    cx, cy = anchor[0] + ux * (reach + w / 2), anchor[1] + uy * (reach + h / 2)
                    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                    x0, y0, x1, y1 = shown or (0.0, 0.0, art.width, art.height)
                    if box[0] < max(x0, 0) or box[1] < max(y0, 0) or box[2] > min(x1, art.width) \
                            or box[3] > min(y1, art.height):
                        continue
                    cost = art.ink_share(box) + reach / NOTE_REACH[-1] * NOTE_FAR
                    cost += sum(NOTE_CROWD for q in [*points, *keep_clear]
                                if box[0] - h / 2 <= q[0] <= box[2] + h / 2 and box[1] - h / 2 <= q[1] <= box[3] + h / 2)
                    cost += sum(NOTE_CROWD * 4 for r in [*placed, *painted]
                                if r[0] < box[2] and box[0] < r[2] and r[1] < box[3] and box[1] < r[3])
                    # Nor across another note's line, nor its own line across another note (Nov 1: the house's
                    # line ran through the channels' note).
                    near = (min(max(anchor[0], box[0]), box[2]), min(max(anchor[1], box[1]), box[3]))
                    cost += sum(NOTE_CROWD * 2 for a, b in leaders if crosses(box, a, b))
                    cost += sum(NOTE_CROWD * 2 for r in placed if crosses(r, near, anchor))
                    if best is None or cost < best[0]:
                        best = (cost, (cx, cy), box)
            if best is None:  # no room beside it: left out
                self._note_spots[key] = None
                return None
            placed.append(best[2])
            box = best[2]
            leaders.append(((min(max(anchor[0], box[0]), box[2]), min(max(anchor[1], box[1]), box[3])), anchor))
            self._note_spots[key] = best[1]
        return self._note_spots[key]

    def _draw_note(self, canvas: skia.Canvas, scene_id: str, key: tuple, text: str, anchor: Point,
                   keep_clear: list[Point], color: str, alpha: float, on_frame) -> None:
        """A note in a pill (p.draw_note) near what it is about (`anchor`, art pixels), joined to it by a line."""
        if alpha <= 0.01:
            return
        (fx0, fy0), (fx1, _) = on_frame((0.0, 0.0)), on_frame((1000.0, 0.0))
        scale = (fx1 - fx0) / 1000.0 or 1.0
        size = self.layout.label_font * p.NOTE_FONT
        w, h = p.note_size(text, size)
        clip = canvas.getDeviceClipBounds()  # the view: what the frame shows of the picture, the bands left out
        shown = ((clip.left() - fx0) / scale, (clip.top() - fy0) / scale,
                 (clip.right() - fx0) / scale, (clip.bottom() - fy0) / scale)
        spot = self._note_spot(scene_id, key, anchor, (w / scale, h / scale), keep_clear, shown)
        if spot is None:
            return
        mx, my = on_frame(spot)
        p.draw_note(canvas, (mx - w / 2, my - h / 2, mx + w / 2, my + h / 2), text, size, color, alpha,
                    to=on_frame(anchor))

    def _graph(self, scene_id: str) -> bool:
        """Whether the scene is a graph (Timeline.axes)."""
        return bool(self.timeline.axes.get(scene_id))

    def _arrow_word(self, scene_id: str, label: str, tail: Point, tip: Point) -> str:
        """What a graph's arrow has written beside it: what it carries ("price"), and, when that is what an axis
        measures and the arrow runs along that axis, which way it goes: "price rises" up the price axis, "quantity
        falls" leftward along the quantity axis."""
        axes = self.timeline.axes.get(scene_id)
        dx, dy = tip[0] - tail[0], tip[1] - tail[1]
        said = " ".join(label.split()).casefold()
        if axes and said == " ".join(axes.y.split()).casefold() and abs(dy) > abs(dx):
            return f"{label} {'rises' if dy < 0 else 'falls'}"
        if axes and said == " ".join(axes.x.split()).casefold() and abs(dx) > abs(dy):
            return f"{label} {'rises' if dx > 0 else 'falls'}"
        return label

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

