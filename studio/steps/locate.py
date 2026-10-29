"""Step 4: locate each structure in the art (docs/PLAN.md, section 5).

With the SVG art (an admin's setting for Claude's brain) the art step already wrote the masks
and locations.json, so there is nothing to do.

With painted art, Gemini 3.1 Flash Image's or Muse Image's, SAM 3.1 finds each structure (studio/muse.py):

1. SAM is given the structure's sam_phrase. Every match at least MIN_CONFIDENCE sure
   makes up its mask (a structure the picture shows twice keeps both).
2. If none is that sure, SAM is asked again in plainer words (phrases()): the
   structure's colour and the phrase's last word ("blue complex"), then its colour and
   "region". SAM 3.1 knows colours and everyday nouns, not scientific names or a string
   of adjectives: on the first live runs "golden yellow small flashlight" found nothing
   and "yellow flashlight" found both flashlights. A SAM call costs a quarter of a cent.
3. If none of those is that sure, or what it found is too small to point a label at,
   the helper looks at the art and gives a box around the structure, and SAM finds the
   one thing in that box.
4. If that finds nothing either, the structure is left out of locations.json: the
   timeline then gives it no spotlight and no camera move, as for a structure the SVG
   art left out. Fewer than half of a scene's structures located fails the step.

Every match is held to the structure's colour first (Colours): the bundle gives each
structure in a scene its own colour and Muse Image paints it so, so a match whose pixels
are mostly another structure's colour is another structure, and is dropped, as is one that
is mostly the art's background (unless the structure is itself near its colour). Muse Image
paints a colour named "green" in a green of its own, often paler or duller than the bundle's,
so a paler or duller version of a colour counts as it, and another hue does not (distance()).
On the first
live runs a box drawn around two Earths came back as one Earth's northern half, labelled
"Southern Hemisphere", and a "gray plug" as the pale sky above a membrane. A structure left
out is better than one labelled wrong.

Then each scene's picture is repaired where code can (studio/repair.py): stray specks cleared from a
space that fills, a part the art check found upside down turned over; the masks follow.

Writes masks/<scene>/<structure>.png, then locations.json, last. Each mask is everything
SAM marked, so two structures that overlap in the picture share those pixels.
"""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path

import numpy as np

from studio import brain, checks, ledger, media, muse, repair, svg
from studio.jobs import write
from studio.schemas import (
    Box,
    Location,
    Locations,
    Scene,
    SceneLocations,
    Strict,
    Structure,
    locations_problems,
)
from studio.steps import art
from studio.steps.art import colour_word

MIN_CONFIDENCE = 0.5
WORKERS = 4  # scenes located at once
# A match is the structure's own when, of its pixels that are some structure's colour, at
# least this share are its own. On the first live runs every right match scored 0.58 or more,
# and the one that would have been labelled wrong 0.40.
MIN_OWN_COLOUR = 0.5
# Nor is a match that is mostly the art's background (read off its edge: media.paper) the structure,
# unless its own colour is near the background's: a "gray plug" once came back as the pale sky above a
# membrane, all of it background. Right matches were at most 0.39 background.
MAX_BACKGROUND = 0.6
CLOSE = 20.0  # how near a pixel is to a colour to count as it (distance())
SAME = 10.0  # two structures this near in colour are one colour here (an analogy part and its structure)
JUDGED = 0.2  # a match needs this share of its pixels in some structure's colour to be judged at all
# ...unless the picture has the structure's colour elsewhere (this share of it, or more): then a match with
# almost none is not it. Round 8's "green turbine" came back as a band of brown earth across the picture,
# with the green turbine below it, and was labelled.
PAINTED = 0.001

BOX_PROMPT = """You find one part in an illustration, for a segmentation model that will then trace it. \
Say whether the part is in the picture (`seen`) and give the box around all of it as fractions of the \
picture's width and height, from its top-left corner: `left` and `right` from 0 to 1 across, `top` and \
`bottom` from 0 to 1 down. Make the box tight around the part. When it is not in the picture, set `seen` \
to false and the box to 0, 0, 1, 1."""


class Spot(Strict):
    """Where the helper sees one structure, as fractions of the art's width and height."""

    seen: bool
    left: float
    top: float
    right: float
    bottom: float


def run(job: Path, ask=brain.ask) -> str:
    if art.painter(job) == "svg":
        if is_done(job):
            return "nothing to do: the art step located every structure"
        raise media.StepFailed("no SVG art and locations.json for this bundle; run the art step first (its SVG "
                               "art locates every structure)")
    bundle = checks.load(job / "bundle.json")
    if not art.is_done(job):
        raise media.StepFailed(f"no {art.painter_of(job).title} art for this bundle; run the art step first")
    media.restart(job, media.LOCATIONS)
    scenes, left_out, boxed, dropped = [], [], [], 0
    # The scenes at the same time: each waits on SAM and the helper, one structure after another.
    found_all = ledger.parallel(lambda scene: find_and_repair(job, bundle, scene, ask), bundle.scenes, WORKERS)
    repaired = []
    for scene, (located, how, wrong, done) in zip(bundle.scenes, found_all):
        repaired += [f"{scene.id}: {what}" for what in done]
        dropped += wrong
        if len(located) < max(1, math.ceil(len(scene.structures) / 2)):
            missing = [s.name for s in scene.structures if s.name not in located]
            raise media.StepFailed(f"scene {scene.id}: SAM 3.1 found {len(located)} of {len(scene.structures)} "
                                   f"structures; not {', '.join(missing)}")
        scenes.append(save(job, scene, located, how))
        left_out += [f"{s.name} on {scene.id}" for s in scene.structures if s.name not in located]
        boxed += [name for name, by in how.items() if by == "sam-box"]
    locations = Locations(scenes=scenes)
    problems = locations_problems(locations, bundle)
    if problems:  # a bug here, not in the art
        raise media.StepFailed("the locations do not match the bundle: " + "; ".join(problems))
    write(job / media.LOCATIONS, locations)
    total = sum(len(s.structures) for s in bundle.scenes)
    found = sum(len(s.structures) for s in scenes)
    return (f"located {found} of {total} structures with SAM 3.1"
            + (f", {len(boxed)} in the helper's box" if boxed else "")
            + (f"; dropped {dropped} match{'es' if dropped != 1 else ''} in another structure's colour"
               if dropped else "")
            + (f"; left out {', '.join(left_out)}" if left_out else "")
            + (f"; repaired {'; '.join(repaired)}" if repaired else ""))


def is_done(job: Path) -> bool:
    return art.located(job) is not None and art.is_done(job)


def find_and_repair(job: Path, bundle, scene: Scene, ask):
    """find_all, then the scene's picture repaired where code can (studio/repair.py), its masks with it."""
    located, how, wrong = find_all(job, scene, ask)

    def check(job: Path, bundle, scene: Scene) -> art.ArtCheck:
        return art.ArtCheck(**ask("helper", art.check_prompt(bundle, scene), art.ArtCheck,
                                  [job / media.art_path(scene.id)], system=art.CHECK_PROMPT,
                                  label=f"repair check {scene.id}"))

    return located, how, wrong, repair.repair(job, bundle, scene, located, ask, check)


def find_all(job: Path, scene: Scene, ask) -> tuple[dict[str, tuple[np.ndarray, svg.Found]], dict[str, str], int]:
    """Each structure SAM found on the scene's art: its mask and where it is; how it was found; and
    how many matches were dropped for being another structure's colour."""
    picture = job / media.art_path(scene.id)
    colours = Colours(picture, scene)
    located, how = {}, {}
    for structure in scene.structures:
        result = find(job, scene, structure, picture, ask, colours)
        if result is not None:
            mask, found, by = result
            located[structure.name], how[structure.name] = (mask, found), by
    return located, how, colours.dropped


def find(job: Path, scene: Scene, structure: Structure, picture: Path, ask,
         colours: Colours | None = None) -> tuple[np.ndarray, svg.Found, str] | None:
    """A structure's mask on the art, where it is, and how it was found ("sam" or "sam-box"); None
    when SAM cannot find enough of it to point a label at."""
    colours = colours or Colours(picture, scene)
    label = f"locate {scene.id} {structure.name}"
    for n, phrase in enumerate(phrases(structure)):
        sure = [m for m in muse.segment(job, label + (f" try {n + 1}" if n else ""), picture, phrase=phrase)
                if (m.confidence is None or m.confidence >= MIN_CONFIDENCE) and colours.fits(m.mask, structure)]
        if sure:
            mask = np.logical_or.reduce([m.mask for m in sure])
            found = svg.locate(mask)
            if found is not None:
                return mask, found, "sam"
    box = helper_box(scene, structure, picture, ask)
    if box is None:
        return None
    matched = [m for m in muse.segment(job, label + " box", picture, box=box) if colours.fits(m.mask, structure)]
    if not matched:
        return None
    best = max(matched, key=lambda m: -1.0 if m.confidence is None else m.confidence)
    found = svg.locate(best.mask)
    return (best.mask, found, "sam-box") if found is not None else None


# --- asking SAM in plainer words --------------------------------------------------------------


def phrases(structure: Structure) -> list[str]:
    """What SAM is asked for, in order: the bundle's sam_phrase, then its colour and the phrase's last
    word ("golden yellow small flashlight" -> "yellow flashlight"), then its colour and "region"."""
    colour = colour_word(structure.color_hex)
    words = re.findall(r"[a-z0-9]+", structure.sam_phrase.lower())
    last = words[-1] if words else ""
    noun = last if last.isalpha() and len(last) >= 3 and last not in colour.split() else ""  # not "b6f"
    asked = [structure.sam_phrase]
    for phrase in (f"{colour} {noun}" if noun else "", f"{colour} region"):
        if phrase and phrase not in (a.lower() for a in asked):
            asked.append(phrase)
    return asked


# --- holding a match to its structure's colour --------------------------------------------------


def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0-255, ... x 3) to CIE Lab (D65)."""
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]],
                       np.float32).T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def distance(pixels: np.ndarray, refs: np.ndarray) -> np.ndarray:
    """How far each pixel is from each colour, both in Lab ((N, 3) and (R, 3)), as (N, R): CIE94's
    distance with the colour as the standard, its hue counted in full. Lightness counts half, since
    shading moves it most; a paler or duller version of a colour is near it, another hue is not. With
    plain Lab distance, round 8's ATP synthase, painted sage where the bundle said bright green, was
    nearer the white oxygen beside it, and was left out."""
    lightness = pixels[:, None, 0] - refs[None, :, 0]
    chroma = np.hypot(refs[:, 1], refs[:, 2])[None, :]
    duller = np.hypot(pixels[:, 1], pixels[:, 2])[:, None] - chroma
    hue_squared = np.maximum(((pixels[:, None, 1:] - refs[None, :, 1:]) ** 2).sum(-1) - duller ** 2, 0)
    return np.sqrt((lightness / 2) ** 2 + (duller / (1 + 0.045 * chroma)) ** 2 + hue_squared)


class Colours:
    """Which structure each pixel of a scene's art is coloured as: the nearest of the scene's
    structure colours and the art's background (white, or the dark slate Muse Image paints), when it is
    near enough to count as any."""

    def __init__(self, picture: Path, scene: Scene):
        import skia

        rgb = skia.Image.open(str(picture)).toarray(colorType=skia.kRGBA_8888_ColorType)[..., :3]
        refs = [[int(s.color_hex[i:i + 2], 16) for i in (1, 3, 5)] for s in scene.structures] + [media.paper(rgb)]
        self.names = [s.name for s in scene.structures]
        self.refs = _lab(np.array(refs))
        self.pixels = _lab(rgb)
        self.dropped = 0
        sample = self.pixels[::2, ::2].reshape(-1, 3)  # every fourth pixel is plenty to see what is painted
        far = distance(sample, self.refs)
        near = np.where(far.min(1) <= CLOSE, far.argmin(1), -1)
        self.painted = np.bincount(near[near >= 0], minlength=len(refs)) / len(sample) >= PAINTED

    def judge(self, mask: np.ndarray, structure: Structure) -> tuple[float, float | None]:
        """The share of the mask's pixels that are the background, unless the structure is itself near
        its colour (then 0); and of its pixels coloured as some structure, the share coloured as this one,
        None when too few are coloured as any to judge (a picture painted in other colours), or 0 when
        the picture has the structure's colour elsewhere."""
        mine = self.names.index(structure.name)
        pixels = self.pixels[mask]
        if not len(pixels):
            return 0.0, None
        far = distance(pixels, self.refs)
        nearest, close = far.argmin(1), far.min(1) <= CLOSE
        apart = distance(self.refs, self.refs[mine:mine + 1])[:, 0]
        own_colour = apart <= SAME
        own_colour[-1] = False  # the background is never a structure's own colour here
        backdrop = (nearest == len(self.refs) - 1) & close
        background = 0.0 if apart[-1] <= CLOSE else float(backdrop.mean())
        own = (own_colour[nearest] & close).sum()
        other = (~own_colour[nearest] & ~backdrop & close).sum()
        if own + other < JUDGED * len(pixels):
            return background, (0.0 if (self.painted & own_colour).any() else None)
        return background, float(own / (own + other))

    def fits(self, mask: np.ndarray, structure: Structure) -> bool:
        """Whether a match can be the structure: not mostly the background, nor another structure's colour."""
        background, share = self.judge(mask, structure)
        if background > MAX_BACKGROUND or (share is not None and share < MIN_OWN_COLOUR):
            self.dropped += 1
            return False
        return True


def helper_box(scene: Scene, structure: Structure, picture: Path, ask) -> Box | None:
    """The helper's box around the structure, in art pixels; None when it does not see it."""
    also = f" (also called {', '.join(structure.aliases)})" if structure.aliases else ""
    spot = Spot(**ask("helper", f"The picture shows: {scene.image_prompt}\nFind: {structure.name}{also}, "
                                f"{structure.sam_phrase}, coloured {structure.color_hex}.",
                      Spot, [picture], system=BOX_PROMPT, label=f"locate box {scene.id} {structure.name}"))
    left, right = sorted(min(1.0, max(0.0, v)) for v in (spot.left, spot.right))
    top, bottom = sorted(min(1.0, max(0.0, v)) for v in (spot.top, spot.bottom))
    x0, x1 = math.floor(left * media.ART_WIDTH), math.ceil(right * media.ART_WIDTH)
    y0, y1 = math.floor(top * media.ART_HEIGHT), math.ceil(bottom * media.ART_HEIGHT)
    if not spot.seen or x1 - x0 < 2 or y1 - y0 < 2 or (right - left) * (bottom - top) > 0.9:
        return None  # not seen, a sliver, or the whole picture: SAM would learn nothing from it
    return Box(x0=x0, y0=y0, x1=x1, y1=y1)


def save(job: Path, scene: Scene, located: dict[str, tuple[np.ndarray, svg.Found]],
         how: dict[str, str]) -> SceneLocations:
    """Write the scene's masks; return its locations."""
    folder = (job / media.mask_path(scene.id, "-")).parent  # masks/<scene>/
    shutil.rmtree(folder, ignore_errors=True)  # masks of structures this run leaves out
    folder.mkdir(parents=True)
    structures = []
    for s in scene.structures:
        if s.name not in located:
            continue
        mask, found = located[s.name]
        (job / media.mask_path(scene.id, s.name)).write_bytes(svg.png(mask))
        structures.append(Location(name=s.name, mask=media.mask_path(scene.id, s.name), box=found.box,
                                   centroid=found.centroid, label_anchor=found.label_anchor, found_by=how[s.name]))
    return SceneLocations(scene=scene.id, art=media.art_path(scene.id), width=media.ART_WIDTH,
                          height=media.ART_HEIGHT, structures=structures)

