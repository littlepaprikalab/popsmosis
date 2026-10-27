"""Step 3: the art, drawn by the brain as SVG until the Meta key arrives (docs/PLAN.md).

One brain call per scene draws it (studio/prompts/art.md), every structure in one group
marked with its name. Code checks the drawing (studio/svg.py); if anything is wrong it
asks again with the problems and the drawing, up to MAX_TRIES times in all.

Writes, per scene, art/<scene>.svg (the drawing, kept for debugging and for resuming),
art/<scene>.png and masks/<scene>/<slug>.png; then locations.json, last. A refused
drawing is kept as art/<scene>.try<n>.svg. Since the drawing says where every structure
is, the locate step has nothing left to do.

How strict: a drawing that breaks the SVG rules (not XML, text or images in it, scripts,
external references, the wrong canvas) is never used. A structure that is missing,
marked twice, in the wrong colour or too small to point at is asked for again; after the
last try the scene keeps its best drawing and leaves those structures out of
locations.json (the timeline then skips them), as long as at least half of the scene's
structures are located. Fewer than that and the scene could not teach, so the step fails.

Running the step again redraws every scene. A run that stopped partway (no
locations.json yet) keeps the scenes it already drew, unless the scene or the prompt
changed since.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from studio import brain, checks, media, svg
from studio.jobs import read, write
from studio.schemas import Bundle, Location, Locations, Scene, SceneLocations, locations_problems

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "art.md").read_text(encoding="utf-8")
MAX_TRIES = 3


def run(job: Path, ask=brain.ask) -> str:
    bundle = checks.load(job / "bundle.json")
    resume = located(job) is None
    (job / media.LOCATIONS).unlink(missing_ok=True)
    (job / "art").mkdir(exist_ok=True)
    scenes, left_out = [], []
    for scene in bundle.scenes:
        drawing = draw(job, bundle, scene, ask, resume)
        scenes.append(save(job, scene, drawing))
        left_out += [f"{name} on {scene.id}" for name in drawing.left_out(scene)]
    locations = Locations(scenes=scenes)
    problems = locations_problems(locations, bundle)
    if problems:  # a bug here, not in the drawing
        raise media.StepFailed("the locations do not match the bundle: " + "; ".join(problems))
    write(job / media.LOCATIONS, locations)
    total = sum(len(s.structures) for s in bundle.scenes)
    found = sum(len(s.structures) for s in scenes)
    return (f"drew {len(scenes)} scenes, located {found} of {total} structures"
            + (f"; left out {', '.join(left_out)}" if left_out else ""))


def is_done(job: Path) -> bool:
    return located(job) is not None


def located(job: Path) -> Locations | None:
    """locations.json, if it reads, matches the bundle, and the files it points to are there."""
    try:
        locations = read(job / media.LOCATIONS, Locations)
        bundle = checks.load(job / "bundle.json")
    except (OSError, ValueError):
        return None
    if locations_problems(locations, bundle):
        return None
    files = [s.art for s in locations.scenes] + [s.mask for scene in locations.scenes for s in scene.structures]
    return locations if all((job / f).exists() for f in files) else None


# --- one scene ----------------------------------------------------------------------


@dataclass
class Drawing:
    """One drawing of a scene, judged."""

    text: str
    problems: list[str] = field(default_factory=list)  # all of them, for the next try
    usable: bool = False  # no problem with the drawing as a whole
    art: np.ndarray | None = None
    masks: dict[str, np.ndarray] = field(default_factory=dict)
    found: dict[str, svg.Found] = field(default_factory=dict)

    def left_out(self, scene: Scene) -> list[str]:
        return [s.name for s in scene.structures if s.name not in self.found]

    def enough(self, scene: Scene) -> bool:
        return self.usable and len(self.found) >= max(1, math.ceil(len(scene.structures) / 2))


def judge(text: str, scene: Scene) -> Drawing:
    """Check a drawing, and if it can be used, draw it and find its structures."""
    checked = svg.check(text, scene.structures)
    drawing = Drawing(text, checked.all())
    if checked.problems:
        return drawing
    names = [s.name for s in scene.structures if s.name not in checked.structures]
    try:
        drawing.art = svg.rasterise(checked.root)
        all_masks = svg.masks(checked.root, names)
    except ValueError as e:
        drawing.problems.append(str(e))
        return drawing
    drawing.usable = True
    for name in names:
        found = svg.locate(all_masks[name])
        if found is None:
            shown = int(all_masks[name].sum())
            drawing.problems.append(f"{name} shows only {shown} px in the art, too little to point a label at: "
                                    "draw it bigger, or move what covers it")
            continue
        drawing.masks[name], drawing.found[name] = all_masks[name], found
    return drawing


def draw(job: Path, bundle: Bundle, scene: Scene, ask, resume: bool) -> Drawing:
    """The scene's drawing: the saved one when resuming, else the brain's best of MAX_TRIES."""
    saved = job / "art" / f"{scene.id}.svg"
    marker = f"<!-- popsmosis art {key(bundle, scene)} -->\n"
    if resume and saved.exists():
        text = saved.read_text(encoding="utf-8")
        if text.startswith(marker):
            drawing = judge(text[len(marker):], scene)
            if drawing.enough(scene):
                return drawing
    for old in (job / "art").glob(f"{scene.id}.try*.svg"):
        old.unlink()

    best, previous = None, None
    for n in range(1, MAX_TRIES + 1):
        answer = ask("brain", user_prompt(bundle, scene, previous), system=PROMPT,
                     label=f"art {scene.id}" + (f" try {n}" if n > 1 else ""))
        drawing = judge(svg.extract(answer["text"]), scene)
        if drawing.usable and (best is None or len(drawing.found) >= len(best.found)):
            best = drawing
        if drawing.usable and not drawing.problems:
            break
        (job / "art" / f"{scene.id}.try{n}.svg").write_text(drawing.text, encoding="utf-8")
        previous = drawing
    if best is None or not best.enough(scene):
        why = (best or drawing).problems
        raise media.StepFailed(f"scene {scene.id}: no usable drawing in {MAX_TRIES} tries: {why[0]}"
                               + (f" (and {len(why) - 1} more problems)" if len(why) > 1 else ""))
    saved.write_text(marker + best.text, encoding="utf-8")
    return best


def save(job: Path, scene: Scene, drawing: Drawing) -> SceneLocations:
    """Write the scene's art and masks; return its locations."""
    (job / media.art_path(scene.id)).write_bytes(svg.png(drawing.art))
    folder = job / "masks" / scene.id
    shutil.rmtree(folder, ignore_errors=True)  # masks of structures this drawing leaves out
    folder.mkdir(parents=True)
    structures = []
    for s in scene.structures:
        found = drawing.found.get(s.name)
        if found is None:
            continue
        (job / media.mask_path(scene.id, s.name)).write_bytes(svg.png(drawing.masks[s.name]))
        structures.append(Location(name=s.name, mask=media.mask_path(scene.id, s.name), box=found.box,
                                   centroid=found.centroid, label_anchor=found.label_anchor, found_by="svg"))
    return SceneLocations(scene=scene.id, art=media.art_path(scene.id), width=media.ART_WIDTH,
                          height=media.ART_HEIGHT, structures=structures)


def key(bundle: Bundle, scene: Scene) -> str:
    """Changes when the scene, the topic or the prompt does: a saved drawing is reused only if it has not."""
    data = json.dumps([bundle.topic, scene.model_dump(), PROMPT], sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def user_prompt(bundle: Bundle, scene: Scene, previous: Drawing | None = None) -> str:
    parts = [f"Topic: {bundle.topic}" + (f" ({bundle.course})" if bundle.course else ""),
             f"Core idea: {bundle.core_idea}",
             f"Scene: {scene.id}\nWhat it shows: {scene.image_prompt}"]
    lines = []
    for s in scene.structures:
        also = f" (also called {', '.join(s.aliases)})" if s.aliases else ""
        lines.append(f'- {s.name}{also}: {s.sam_phrase}. Colour {s.color_hex}. '
                     f'Group: <g {svg.MARK}="{s.name}">')
    parts.append("Structures to draw, each as one group:\n" + "\n".join(lines))
    others = [o for o in bundle.scenes if o.id != scene.id]
    if others:
        parts.append("The video's other scenes, drawn separately; draw shared structures the same way:\n" + "\n".join(
            f"- {o.id}: {o.image_prompt} Structures: "
            + ", ".join(f"{s.name} {s.color_hex}" for s in o.structures) for o in others))
    if previous is not None:
        parts.append("Your last drawing of this scene, below, was refused:\n"
                     + "\n".join(f"- {p}" for p in previous.problems)
                     + "\nDraw the whole scene again with these fixed, and answer with the SVG only.\n"
                     + f"<last_drawing>\n{previous.text}\n</last_drawing>")
    return "\n\n".join(parts)
