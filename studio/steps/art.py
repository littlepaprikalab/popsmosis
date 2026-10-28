"""Step 3: the art, drawn by the brain as SVG until the Meta key arrives (docs/PLAN.md).

One brain call per scene draws it (studio/prompts/art.md), every structure in one group
marked with its name. Code checks the drawing (studio/svg.py); if anything is wrong it
asks again with the problems and the drawing, up to MAX_TRIES times in all.

Writes, per scene (paths from studio/media.py), art/<scene>.svg (the drawing, kept for debugging and for resuming),
art/<scene>.png and masks/<scene>/<slug>.png; then locations.json, last. A refused
drawing is kept as art/<scene>.try<n>.svg. Since the drawing says where every structure
is, the locate step has nothing left to do.

How strict: a drawing that breaks the SVG rules (not XML, text or images in it, scripts,
external references, the wrong canvas) is never used. A structure that is missing,
marked twice, in the wrong colour or too small to point at is asked for again; after the
last try the scene keeps its best drawing and leaves those structures out of
locations.json (the timeline then skips them), as long as at least half of the scene's
structures are located. Fewer than that and the scene could not teach, so the step fails.

A scene that names a molecular machine (scene.schematic) is drawn from the machine's published
3D structure instead (studio/schematic3d/build.py): the step finds and fits the structure,
keeps what it built (art/<scene>.schematic.json), writes its still as the art and a mask per
structure, and locates each structure there. The renderer draws the scene live from what was
built; the still serves the karaoke fallback and the stored copy. When the machine cannot be
built, the scene is drawn as an illustration like any other, and the step says why.

Each kept drawing starts with a key made from the topic and its scene, so art drawn for
an earlier bundle never counts as done: if the bundle is written again, the art is too.
Running the step again redraws every scene. A run that stopped partway (no
locations.json yet) keeps the scenes it already drew, unless their scene changed since.
An answer cut off at the output limit counts as a refused try; any other brain error
fails the step, and the scenes already drawn are kept for the retry.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from studio import brain, checks, media, svg
from studio.jobs import read, write
from studio.schematic3d.build import BuildFailed
from studio.schematic3d.model import Built
from studio.schemas import Bundle, Location, Locations, Scene, SceneLocations, locations_problems

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "art.md").read_text(encoding="utf-8")
MAX_TRIES = 3


def run(job: Path, ask=brain.ask) -> str:
    bundle = checks.load(job / "bundle.json")
    resume = located(job) is None
    media.restart(job, media.LOCATIONS)
    (job / "art").mkdir(exist_ok=True)
    scenes, left_out = [], []
    illustrated = []
    for scene in bundle.scenes:
        if scene.schematic:
            try:
                located_scene = draw_schematic(job, bundle, scene, ask)
            except BuildFailed as e:
                (job / media.schematic_path(scene.id)).unlink(missing_ok=True)
                illustrated.append(f"{scene.id} ({e})")
            else:
                found = {s.name for s in located_scene.structures}
                scenes.append(located_scene)
                left_out += [f"{s.name} on {scene.id}" for s in scene.structures if s.name not in found]
                continue
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
    schematics = sum(1 for located_scene in scenes if located_scene.schematic)
    return (f"drew {len(scenes)} scenes" + (f" ({schematics} as schematics)" if schematics else "")
            + f", located {found} of {total} structures"
            + (f"; left out {', '.join(left_out)}" if left_out else "")
            + (f"; drew as illustrations {'; '.join(illustrated)}" if illustrated else ""))


def is_done(job: Path) -> bool:
    return located(job) is not None


def located(job: Path) -> Locations | None:
    """locations.json, if it reads, matches the bundle, the files it points to are there, and
    the art drawn as SVG was drawn for this bundle's scenes."""
    try:
        locations = read(job / media.LOCATIONS, Locations)
        bundle = checks.load(job / "bundle.json")
    except (OSError, ValueError):
        return None
    if locations_problems(locations, bundle):
        return None
    files = [s.art for s in locations.scenes] + [s.mask for scene in locations.scenes for s in scene.structures]
    files += [s.schematic for s in locations.scenes if s.schematic]
    if not all((job / f).exists() for f in files):
        return None
    for scene in bundle.scenes:
        drawn = locations.scene(scene.id)
        if drawn.schematic is not None:
            if not scene.schematic or _built(job, bundle, scene) is None:
                return None
        elif any(s.found_by == "svg" for s in drawn.structures) and not _saved(job, bundle, scene):
            return None
    return locations


def _kept(job: Path, scene: Scene, attempt: int | None = None) -> Path:
    """art/<scene>.svg, the drawing kept; art/<scene>.try<n>.svg, a refused one."""
    return job / "art" / (media.file_name(scene.id) + (f".try{attempt}" if attempt else "") + ".svg")


def _marker(bundle: Bundle, scene: Scene) -> str:
    return f"<!-- popsmosis art {key(bundle, scene)} -->\n"


def _saved(job: Path, bundle: Bundle, scene: Scene) -> str | None:
    """The scene's kept drawing, if it was drawn for this scene as the bundle now has it."""
    path, marker = _kept(job, scene), _marker(bundle, scene)
    try:
        with path.open(encoding="utf-8") as f:
            head = f.read(len(marker))
            return head + f.read() if head == marker else None
    except (OSError, UnicodeDecodeError):
        return None


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
    try:
        checked = svg.check(text, scene.structures)
    except RecursionError:
        return Drawing(text, ["the SVG nests groups too deeply; keep it to a few levels"])
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
    except RecursionError:
        drawing.problems.append("the SVG nests groups too deeply; keep it to a few levels")
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
    marker = _marker(bundle, scene)
    text = _saved(job, bundle, scene) if resume else None
    if text is not None:
        drawing = judge(text[len(marker):], scene)
        if drawing.enough(scene):
            return drawing
    for old in (job / "art").glob(f"{media.file_name(scene.id)}.try*.svg"):
        old.unlink()

    best, previous = None, None
    for n in range(1, MAX_TRIES + 1):
        try:
            answer = ask("brain", user_prompt(bundle, scene, previous), system=PROMPT,
                         label=f"art {scene.id}" + (f" try {n}" if n > 1 else ""))
            drawing = judge(svg.extract(answer["text"]), scene)
        except brain.Truncated:
            drawing = Drawing("", ["the answer was cut off before </svg>; draw it simpler, under 40 KB"])
        if drawing.usable and (best is None or len(drawing.found) >= len(best.found)):
            best = drawing
        if drawing.usable and not drawing.problems:
            break
        if drawing.text:
            _kept(job, scene, n).write_text(drawing.text, encoding="utf-8")
        previous = drawing
    if best is None or not best.enough(scene):
        why = (best or drawing).problems
        raise media.StepFailed(f"scene {scene.id}: no usable drawing in {MAX_TRIES} tries: {why[0]}"
                               + (f" (and {len(why) - 1} more problems)" if len(why) > 1 else ""))
    _kept(job, scene).write_text(marker + best.text, encoding="utf-8")
    return best


def save(job: Path, scene: Scene, drawing: Drawing) -> SceneLocations:
    """Write the scene's art and masks; return its locations."""
    (job / media.art_path(scene.id)).write_bytes(svg.png(drawing.art))
    folder = (job / media.mask_path(scene.id, "-")).parent  # masks/<scene>/
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


# --- a scene drawn as a schematic --------------------------------------------------------------


def schematic_key(bundle: Bundle, scene: Scene) -> str:
    data = json.dumps(["schematic", bundle.topic, scene.model_dump()], sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _built(job: Path, bundle: Bundle, scene: Scene) -> Built | None:
    """The machine built for this scene, if it was built for the scene as the bundle now has it."""
    try:
        built = read(job / media.schematic_path(scene.id), Built)
    except (OSError, ValueError):
        return None
    return built if built.key == schematic_key(bundle, scene) else None


def draw_schematic(job: Path, bundle: Bundle, scene: Scene, ask) -> SceneLocations:
    """Build the scene's machine (or take the one built for it), write its still as the art and its masks;
    return its locations. BuildFailed when it cannot be built."""
    from studio.schematic3d.build import build
    from studio.schematic3d.draw import Machine  # skia loads with the step

    built = _built(job, bundle, scene)
    if built is None:
        lesson = {"topic": bundle.topic, "course": bundle.course, "core_idea": bundle.core_idea,
                  "facts": [f.text for f in bundle.facts]}  # what the course means by each part's name
        with tempfile.TemporaryDirectory() as folder:  # the structure file is not kept: what was built is
            built = build(lesson, scene.schematic, [(s.name, s.color_hex) for s in scene.structures], ask,
                          Path(folder), key=schematic_key(bundle, scene), label=f"art {scene.id} schematic")
        write(job / media.schematic_path(scene.id), built)
    machine = Machine(built, media.ART_WIDTH, media.ART_HEIGHT)
    (job / media.art_path(scene.id)).write_bytes(svg.png(machine.still()))
    folder = (job / media.mask_path(scene.id, "-")).parent
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    masks = machine.masks()
    structures = []
    for s in scene.structures:
        found = svg.locate(masks[s.name]) if s.name in masks else None
        if found is None:
            continue
        (job / media.mask_path(scene.id, s.name)).write_bytes(svg.png(masks[s.name]))
        structures.append(Location(name=s.name, mask=media.mask_path(scene.id, s.name), box=found.box,
                                   centroid=found.centroid, label_anchor=found.label_anchor, found_by="schematic3d"))
    return SceneLocations(scene=scene.id, art=media.art_path(scene.id), width=media.ART_WIDTH,
                          height=media.ART_HEIGHT, structures=structures, schematic=media.schematic_path(scene.id))


def key(bundle: Bundle, scene: Scene) -> str:
    """Changes when the topic or the scene does (its prompt, structures, colours).

    Not when the art prompt does: a prompt change should not redraw finished jobs.
    """
    # An illustration's key leaves out the empty `schematic`, so art drawn before it existed still counts.
    data = json.dumps([bundle.topic, scene.model_dump(exclude=None if scene.schematic else {"schematic"})],
                      sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def user_prompt(bundle: Bundle, scene: Scene, previous: Drawing | None = None) -> str:
    parts = [f"Topic: {bundle.topic}" + (f" ({bundle.course})" if bundle.course else ""),
             f"Audience: {bundle.audience}",
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
                     + "\nDraw the whole scene again with these fixed, and answer with the SVG only."
                     + (f"\n<last_drawing>\n{previous.text}\n</last_drawing>" if previous.text else ""))
    return "\n\n".join(parts)
