"""Draws a timeline's frames (docs/PLAN.md, section 7).

Every frame depends only on its time: Renderer.frame(t) finds the beat that holds t
(or the exam question, from frq.t0) and draws it. Nothing carries over from the frame
before, so frame ranges render in parallel and stills cost one frame each. What does
not change between frames (each scene's art, its masks and the washes made from
them) is made once per scene and kept.

The world is the art's pixels; the camera maps it onto the frame. Before the exam
question the diagram fills the frame above the lyric band; during it, the diagram
moves right of the question card.
"""

from __future__ import annotations

import math
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
SPOT_S = 0.6
MOVE_S = 1.8  # a camera move between two stations
SETTLE_S = 2.5  # the drift after a cut, so every shot opens moving
SETTLE_ZOOM = 1.06
HOOK_ZOOM = 1.15  # frame 0 starts this far in and pulls back: motion from the first frame
FIT_PAD = 1.35  # a zoomed-in target keeps this much room around its box
CAPTION_FADE_S = 0.3
FRQ_IN_S = 1.0


class RenderError(Exception):
    """A file the renderer needs is missing or does not match the timeline."""


# --- each scene, once ----------------------------------------------------------------------


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
        self.art = skia.Image.fromarray(pixels, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kOpaque_AlphaType)
        self.masks: dict[str, skia.Image] = {}
        for s in located.structures:
            mask_file = job / s.mask
            if not mask_file.exists():
                raise RenderError(f"no mask for {s.name} on {located.scene} ({s.mask})")
            grey = _read(mask_file, skia.kGray_8_ColorType)
            if grey.shape != pixels.shape[:2]:
                raise RenderError(f"the mask for {s.name} on {located.scene} is not the art's size")
            self.masks[s.name] = skia.Image.fromarray(np.ascontiguousarray(grey), colorType=skia.kAlpha_8_ColorType)
        self._ghosts: dict[str, skia.Image] = {}
        self._spots: dict[str, skia.Image] = {}
        self._composed: OrderedDict[tuple, skia.Image] = OrderedDict()
        self.centre = (self.width / 2, self.height / 2)
        if located.structures:
            self.centre = (sum(s.centroid.x for s in located.structures) / len(located.structures),
                           sum(s.centroid.y for s in located.structures) / len(located.structures))

    def _surface(self, alpha_only: bool = False) -> skia.Surface:
        kind = skia.kAlpha_8_ColorType if alpha_only else skia.kBGRA_8888_ColorType
        return skia.Surface.MakeRaster(skia.ImageInfo.Make(self.width, self.height, kind, skia.kPremul_AlphaType))

    def ghost(self, name: str) -> skia.Image:
        """Where the structure is, with a soft edge: drawn white at GHOST_WASH over the art.

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
        """Everywhere but the structure: drawn white at SPOT_WASH, it leaves only that one bright."""
        if name not in self._spots:
            surface = self._surface(alpha_only=True)
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorWHITE)
            canvas.drawImage(self.masks[name], 0, 0, skia.SamplingOptions(),
                             skia.Paint(BlendMode=skia.BlendMode.kDstOut,
                                        ImageFilter=skia.ImageFilters.Blur(SPOT_BLUR, SPOT_BLUR)))
            self._spots[name] = surface.makeImageSnapshot()
        return self._spots[name]

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
                             skia.Paint(Color=skia.ColorWHITE, Alphaf=GHOST_WASH * level))
        for name, level in key[1]:
            canvas.drawImage(self.spot(name), 0, 0, skia.SamplingOptions(),
                             skia.Paint(Color=skia.ColorWHITE, Alphaf=SPOT_WASH * level))
        image = surface.makeImageSnapshot()
        self._composed[key] = image
        while len(self._composed) > 6:
            self._composed.popitem(last=False)
        return key, image


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


def mix_rect(a: Rect, b: Rect, x: float) -> Rect:
    return tuple(p.lerp(u, v, x) for u, v in zip(a, b))  # type: ignore[return-value]


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

    def cam(self, t: float, view: Rect, art: SceneArt) -> Cam:
        return clamp_cam(mix_cam(self.start, self.target, p.ease((t - self.move_at) / self.move_s)), view, art)


class Renderer:
    def __init__(self, job: Path, timeline: Timeline, layout: Layout | str = "h",
                 locations: Locations | None = None):
        self.job = job
        self.timeline = timeline
        self.layout = LAYOUTS[layout] if isinstance(layout, str) else layout
        self.locations = locations or jobs.read(job / media.LOCATIONS, Locations)
        self._scenes: dict[str, SceneArt] = {}
        scene_ids = {b.scene for b in timeline.beats} | {timeline.frq.scene}
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
        frq = timeline.frq
        # The card shows frq.labels revealed: those revealed before stay as they were, the
        # rest fill in as the card arrives.
        earlier = self.revealed.get(frq.scene, {})
        self.frq_reveals = {name: earlier.get(name, frq.t0) for name in frq.labels}
        self.plans = self._plan()
        width, height = self.layout.width, self.layout.height
        self.pixels = np.zeros((height, width, 4), np.uint8)
        self.surface = skia.Surface(self.pixels, colorType=skia.kBGRA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        self._background: tuple[tuple, skia.Image] | None = None

    def scene(self, scene_id: str) -> SceneArt:
        if scene_id not in self._scenes:
            self._scenes[scene_id] = SceneArt(self.job, self.locations.scene(scene_id))
        return self._scenes[scene_id]

    def _aim(self, scene_id: str, target: str | None, zoom: float) -> Cam:
        art = self.scene(scene_id)
        loc = self.locations.scene(scene_id).get(target) if target else None
        if loc is None:
            return clamp_cam(Cam(art.width / 2, art.height / 2, 1.0), self.layout.view, art)
        view = self.layout.view
        box_w, box_h = loc.box.x1 - loc.box.x0, loc.box.y1 - loc.box.y0
        fits = min((view[2] - view[0]) / (box_w * FIT_PAD), (view[3] - view[1]) / (box_h * FIT_PAD)) / _fit(view, art)
        zoom = max(1.0, min(zoom, fits))
        return clamp_cam(Cam(loc.centroid.x, loc.centroid.y, zoom), view, art)

    def _frame_structures(self, scene_id: str, view: Rect) -> Cam:
        """The camera that fits every located structure on the scene, with room for labels."""
        art = self.scene(scene_id)
        located = self.locations.scene(scene_id).structures
        if not located:
            return clamp_cam(Cam(art.width / 2, art.height / 2, 1.0), view, art)
        x0, y0 = min(s.box.x0 for s in located), min(s.box.y0 for s in located)
        x1, y1 = max(s.box.x1 for s in located), max(s.box.y1 for s in located)
        fit = _fit(view, art)
        zoom = min((view[2] - view[0]) / ((x1 - x0) * FIT_PAD), (view[3] - view[1]) / ((y1 - y0) * FIT_PAD)) / fit
        return clamp_cam(Cam((x0 + x1) / 2, (y0 + y1) / 2, max(1.0, min(zoom, 2.0))), view, art)

    def _plan(self) -> list[BeatPlan]:
        plans: list[BeatPlan] = []
        beats = self.timeline.beats
        for i, beat in enumerate(beats):
            reveals = {r.name: r.t for r in beat.reveal}
            target = self._aim(beat.scene, beat.camera.target, beat.camera.zoom)
            same = i > 0 and beats[i - 1].scene == beat.scene
            if same:
                start = plans[-1].cam(beat.t0, self.layout.view, self.scene(beat.scene))
                move_at = max(beat.t0, reveals.get(beat.camera.target, beat.t0))
                move_s = MOVE_S if beat.camera.ease == "inOut" else max(beat.t1 - move_at, 0.1)
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
            plans.append(BeatPlan(target=target, start=start, move_at=move_at, move_s=move_s, spot=beat.spotlight,
                                  spot_at=spot_at, prev_spot=prev_spot, caption_in=caption_in,
                                  caption_out=caption_out))
        return plans

    def beat_index(self, t: float) -> int:
        """The index of the beat on screen at t (Timeline.beat_at); the last beat from the exam question on."""
        beat = self.timeline.beat_at(t)
        return self._index[id(beat)] if beat is not None else len(self.timeline.beats) - 1

    def art_to_frame(self, t: float, x: float, y: float) -> tuple[float, float]:
        """Where an art pixel of the beat at t lands on the frame (before the exam question)."""
        i = self.beat_index(t)
        art = self.scene(self.timeline.beats[i].scene)
        scale, dx, dy = to_frame(self.plans[i].cam(t, self.layout.view, art), self.layout.view, art)
        return x * scale + dx, y * scale + dy

    # --- drawing ---------------------------------------------------------------------------

    def frame(self, t: float) -> np.ndarray:
        """The frame at time t: BGRA pixels, height x width x 4 (a view kept by the renderer)."""
        canvas = self.surface.getCanvas()
        if t >= self.timeline.frq.t0:
            self._draw_frq(canvas, t)
        else:
            self._draw_beat(canvas, t)
        return self.pixels

    def image(self, t: float) -> skia.Image:
        self.frame(t)
        return self.surface.makeImageSnapshot()

    def _ghosts(self, scene_id: str, reveals: dict[str, float], t: float) -> dict[str, float]:
        art = self.scene(scene_id)
        levels = {}
        for name in art.masks:
            r = reveals.get(name)
            levels[name] = 1.0 if r is None else 1.0 - p.ease((t - r) / REVEAL_S)
        return levels

    def _draw_background(self, canvas: skia.Canvas, scene_id: str, cam: Cam, view: Rect,
                         ghosts: dict[str, float], spots: dict[str, float]) -> None:
        art = self.scene(scene_id)
        washes, composed = art.compose(ghosts, spots)
        key = (scene_id, washes, round(cam.cx, 3), round(cam.cy, 3), round(cam.zoom, 5), view)
        if self._background and self._background[0] == key:
            canvas.drawImage(self._background[1], 0, 0)
            return
        canvas.clear(skia.ColorWHITE)
        scale, dx, dy = to_frame(cam, view, art)
        canvas.save()
        canvas.translate(dx, dy)
        canvas.scale(scale, scale)
        canvas.drawImage(composed, 0, 0, skia.SamplingOptions(skia.FilterMode.kLinear))
        canvas.restore()
        self._background = (key, self.surface.makeImageSnapshot())

    def _labels(self, canvas: skia.Canvas, scene_id: str, names: list[str], reveals: dict[str, float],
                spots: dict[str, float], cam: Cam, view: Rect, bounds: Rect, t: float) -> None:
        art = self.scene(scene_id)
        located = self.locations.scene(scene_id)
        scale, dx, dy = to_frame(cam, view, art)
        labels = []
        for name in names:
            loc = located.get(name)
            if loc is None:
                continue
            ax, ay = loc.label_anchor.x * scale + dx, loc.label_anchor.y * scale + dy
            alpha = p.ease((t - reveals.get(name, -1e9)) / LABEL_IN_S)
            # A dot the camera has moved off the view fades out rather than clinging to the edge.
            outside = max(view[0] - ax, ax - view[2], view[1] - ay, ay - view[3], 0)
            alpha *= p.clamp01(1 - outside / 40)
            if alpha > 0.01:
                box = (loc.box.x0 * scale + dx, loc.box.y0 * scale + dy, loc.box.x1 * scale + dx, loc.box.y1 * scale + dy)
                labels.append(p.Label(name=name, color=self.timeline.palette[name], anchor=(ax, ay), box=box, alpha=alpha,
                                      emphasis=spots.get(name, 0.0)))
        centre = (art.centre[0] * scale + dx, art.centre[1] * scale + dy)
        p.place_labels(labels, bounds, centre, self.layout.label_font)
        for lb in sorted(labels, key=lambda lb: lb.emphasis):  # the spotlit one on top
            p.draw_label(canvas, lb, self.layout.label_font)

    def _draw_beat(self, canvas: skia.Canvas, t: float) -> None:
        layout = self.layout
        i = self.beat_index(t)
        beat, plan = self.timeline.beats[i], self.plans[i]
        art = self.scene(beat.scene)
        view = layout.view
        cam = plan.cam(t, view, art)
        spots: dict[str, float] = {}
        if plan.spot:
            spots[plan.spot] = 1.0 if plan.prev_spot == plan.spot else p.ease((t - plan.spot_at) / SPOT_S)
        if plan.prev_spot and plan.prev_spot != plan.spot:
            spots[plan.prev_spot] = max(spots.get(plan.prev_spot, 0), 1 - p.ease((t - beat.t0) / SPOT_S))
        reveals = self.revealed.get(beat.scene, {})
        self._draw_background(canvas, beat.scene, cam, view, self._ghosts(beat.scene, reveals, t), spots)

        caption_alpha = 0.0
        if beat.caption:
            caption_alpha = p.ease((t - plan.caption_in) / CAPTION_FADE_S) * p.ease((plan.caption_out - t) / CAPTION_FADE_S)
        top = layout.margin + (layout.caption_font * 1.75 + 24 if caption_alpha > 0 else 0)
        bounds = (view[0] + layout.margin, view[1] + top, view[2] - layout.margin, view[3] - layout.margin)
        self._labels(canvas, beat.scene, beat.labels, reveals, spots, cam, view, bounds, t)
        if beat.caption:
            p.draw_caption(canvas, beat.caption, layout.width / 2, layout.margin + 8, layout.caption_font, caption_alpha)
        p.draw_band(canvas, (0, layout.height - layout.band_h, layout.width, layout.height), self._band_words(beat), t,
                    layout.band_font)

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

    def _draw_frq(self, canvas: skia.Canvas, t: float) -> None:
        layout, frq = self.layout, self.timeline.frq
        art = self.scene(frq.scene)
        into = p.ease((t - frq.t0) / FRQ_IN_S)
        whole = self._frame_structures(frq.scene, layout.frq_view)
        last = self.timeline.beats[-1]
        if last.scene == frq.scene:
            before = self.plans[-1].cam(frq.t0, layout.view, art)
            view = mix_rect(layout.view, layout.frq_view, into)
            cam = clamp_cam(mix_cam(before, whole, into), view, art)
        else:
            view, cam = layout.frq_view, whole

        current = None
        for k, step in enumerate(frq.steps):
            if step.t <= t:
                current = k
        spots: dict[str, float] = {}
        if current is not None:
            step = frq.steps[current]
            previous = frq.steps[current - 1].spotlight if current else None
            if step.spotlight:
                spots[step.spotlight] = 1.0 if previous == step.spotlight else p.ease((t - step.t) / SPOT_S)
            if previous and previous != step.spotlight:
                spots[previous] = max(spots.get(previous, 0), 1 - p.ease((t - step.t) / SPOT_S))
        self._draw_background(canvas, frq.scene, cam, view, self._ghosts(frq.scene, self.frq_reveals, t), spots)
        names = list(self.frq_reveals)
        bounds = (view[0] + layout.margin, view[1] + layout.margin, view[2] - layout.margin, view[3] - layout.margin)
        self._labels(canvas, frq.scene, [n for n in names if self.frq_reveals[n] <= t], self.frq_reveals, spots, cam,
                     view, bounds, t)

        pause_end = frq.predict_t + frq.predict_pause_s
        countdown = pause_end - t if frq.predict_t <= t < pause_end else None
        steps = [p.CardStep(number=k + 1, text=s.text, color=self.timeline.palette.get(s.spotlight) if s.spotlight
                            else None, alpha=p.ease((t - s.t) / 0.35), current=k == current)
                 for k, s in enumerate(frq.steps)]
        p.draw_frq_card(canvas, layout.frq_card, into, frq.prompt, frq.predict_question, countdown,
                        frq.predict_pause_s, steps)
        if into < 1:  # the lyric band drops away as the card comes in
            band_y = layout.height - layout.band_h + layout.band_h * into
            p.draw_band(canvas, (0, band_y, layout.width, band_y + layout.band_h), [], t, layout.band_font)

