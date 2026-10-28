"""Step 4: locate each structure in the art (docs/PLAN.md, section 5).

With the SVG art (STUDIO_ART=svg) the art step already wrote the masks and
locations.json, so there is nothing to do.

With Muse Image's art (STUDIO_ART=muse, the default) SAM 3.1 finds each structure (studio/muse.py):

1. SAM is given the structure's sam_phrase. Every match at least MIN_CONFIDENCE sure
   makes up its mask (a structure the picture shows twice keeps both).
2. If none is that sure, or what it found is too small to point a label at, the helper
   looks at the art and gives a box around the structure, and SAM finds the one thing in
   that box.
3. If that finds nothing either, the structure is left out of locations.json: the
   timeline then gives it no spotlight and no camera move, as for a structure the SVG
   art left out. Fewer than half of a scene's structures located fails the step.

Writes masks/<scene>/<structure>.png, then locations.json, last. Each mask is everything
SAM marked, so two structures that overlap in the picture share those pixels.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np

from studio import brain, checks, media, muse, svg
from studio.jobs import write
from studio.schemas import Box, Location, Locations, Scene, SceneLocations, Strict, Structure, locations_problems
from studio.steps import art

MIN_CONFIDENCE = 0.5

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
    if brain.art() != "muse":
        if is_done(job):
            return "nothing to do: the art step located every structure"
        raise media.StepFailed("no SVG art and locations.json for this bundle; run the art step first (its SVG "
                               "art locates every structure)")
    bundle = checks.load(job / "bundle.json")
    if not art.is_done(job):
        raise media.StepFailed("no Muse Image art for this bundle; run the art step first")
    media.restart(job, media.LOCATIONS)
    scenes, left_out, boxed = [], [], []
    for scene in bundle.scenes:
        located, how = find_all(job, scene, ask)
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
            + (f"; left out {', '.join(left_out)}" if left_out else ""))


def is_done(job: Path) -> bool:
    return art.located(job) is not None and art.is_done(job)


def find_all(job: Path, scene: Scene, ask) -> tuple[dict[str, tuple[np.ndarray, svg.Found]], dict[str, str]]:
    """Each structure SAM found on the scene's art: its mask and where it is; and how it was found."""
    picture = job / media.art_path(scene.id)
    located, how = {}, {}
    for structure in scene.structures:
        result = find(job, scene, structure, picture, ask)
        if result is not None:
            mask, found, by = result
            located[structure.name], how[structure.name] = (mask, found), by
    return located, how


def find(job: Path, scene: Scene, structure: Structure, picture: Path,
         ask) -> tuple[np.ndarray, svg.Found, str] | None:
    """A structure's mask on the art, where it is, and how it was found ("sam" or "sam-box"); None
    when SAM cannot find enough of it to point a label at."""
    label = f"locate {scene.id} {structure.name}"
    sure = [m for m in muse.segment(job, label, picture, phrase=structure.sam_phrase)
            if m.confidence is None or m.confidence >= MIN_CONFIDENCE]
    if sure:
        mask = np.logical_or.reduce([m.mask for m in sure])
        found = svg.locate(mask)
        if found is not None:
            return mask, found, "sam"
    box = helper_box(scene, structure, picture, ask)
    if box is None:
        return None
    matched = muse.segment(job, label + " box", picture, box=box)
    if not matched:
        return None
    best = max(matched, key=lambda m: -1.0 if m.confidence is None else m.confidence)
    found = svg.locate(best.mask)
    return (best.mask, found, "sam-box") if found is not None else None


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

