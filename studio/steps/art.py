"""Step 3: the art. Who makes it goes by the brain's provider, as an admin sets it on /admin/studio
(painter(); brain.painter(); docs/PLAN.md, section 5):

    gemini   Gemini 3.1 Flash Image paints each scene (studio/gemini.py), the default for Claude's brain
    muse     Muse Image paints each scene (studio/muse.py), the default for Muse's
    svg      the brain draws each scene as SVG, every structure a named group, so the
             drawing says where each one is and this step writes the masks too. Claude's only

A painted picture is checked by the helper, and the locate step then finds the structures in it
with SAM 3.1 (studio/steps/locate.py), whoever painted it.

A scene drawn to scale (exact(): a graph, or a chart or figure whose sizes must be right) is drawn as SVG by Claude
whoever paints the rest (drawn()): round 22's compound-interest bar chart came back from Gemini with bars at heights
no number in the lesson gave, and the helper's notice about it on screen beside it. Its drawing locates its
structures, so the locate step takes them from it (drawn_locations()). Which scenes Claude draws is an admin's knob,
STUDIO_CLAUDE_DRAWS on /admin/studio (claude_draws()):

    math     the default: every graph and scene to scale, and every picture of a math lesson (math_lesson()) but its
             hook, whatever the brain. Muse Image painted a fractions lesson's six bars as seven, and
             thirds it was asked to group in twos ungrouped, which the helper passed (Oct 30)
    charts   every graph and scene to scale, whatever the brain
    brain    only Claude's brain draws its graphs and scenes to scale; Muse's brain has them painted, as before Oct 30

On Claude's brain the brain draws them; on Muse's, the drawer does (brain.py: Claude Opus 5.5 at high effort), on
the Anthropic key, so a Muse droplet's math costs about what a Claude one's charts do.

Switching makes the art again on the next run, as switching who sings does the song.

The SVG art. One brain call per scene draws it (studio/prompts/art.md), every structure in one group
marked with its name. Code checks the drawing (studio/svg.py); if anything is wrong it
asks again with the problems and the drawing, up to MAX_TRIES times in all. The brain draws what is there and
where; code gives it the house look (svg.style(): the dark page, and a graph's lines, axes and points) before
it is rasterised and its masks are found.

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

Each kept drawing starts with a key made from the topic and its scene, so art drawn for
an earlier bundle never counts as done: if the bundle is written again, the art is too.
Running the step again redraws every scene. A run that stopped partway (no
locations.json yet) keeps the scenes it already drew, unless their scene changed since. So
does a new look (svg.LOOK): art styled in another one is not done, and the step's next run,
by hand too, styles the kept drawings again with no call to the brain; the run after that
redraws, as before.
An answer cut off at the output limit counts as a refused try; any other brain error
fails the step, and the scenes already drawn are kept for the retry.

The painted art. One picture per scene, from Gemini or Muse Image, the scenes painted at the
same time, from a short prompt (paint_prompt()): the scene's image_prompt, which asks for it the
way a teacher asks for a standard textbook diagram, each structure's colour by name, and a line asking
it to label the structures (labels_line()). Live tests (docs/test-rounds.md) found the plain
request right 8 times in 12 where the earlier prompt, with hex codes, a list of shapes and one
biology style for every subject, was right once in 6; on Oct 31 the pictures came back better
again without the style line every prompt ended in (a flat dark slate blue background, no text).
The picture labels its own parts, and the video draws no labels over it, only over a drawing.
The helper looks at each picture for a label that is wrong, for a structure missing or out of
place, and for anything else wrong, told what kind of scene it is. A picture with a structure missing or out of
place, or anything else wrong, can be asked for again, up to MAX_PAINTS pictures in all. The writer's role looks at
every first picture with what the check found (rewrite()), and keeps it or writes the scene's words again for the
next try; only a rewrite is painted again. When that call fails, a picture the check found a problem in is asked
for again from the same words with what was wrong after them. Round 8 found a second picture from the same words no
better 9 times in 11 for the lesser faults; on Nov 2 a rewrite fixed each of three pictures that had gone wrong. A
wrong label
is kept: the same look boxes it and writes a sticky note saying what is true, and the video rings the label
and sticks the note beside it (flags()). So does a mistake that contradicts one of the lesson's facts, named
by its id. One look does it all (look()): since Nov 2 there is no second look at the labels and no review of
the notes. After the last try the scene keeps the picture with the fewest structures out of place, then the
fewest other problems.

When the painter fails for good on a scene (its API's own retries spent, and for a later stage the picture painted
afresh after its redraw failed), the other of Gemini and Muse Image paints that try and the scene's next (fallback()),
when the brain in force may use it (brain.ART_CHOICES) and this machine has its keys (brain.PAINTER_KEYS); the scene
is checked and kept as any other. Until Nov 3 the step failed, and the videos' one retry asked the same painter
again (the owner: "Do we retry on Gemini for images if Muse Image fails?"). Not for a picture the painter would not
make, which is asked for again more plainly, nor for the SVG art. The step's line says how many scenes fell back and
to whom.

Writes, per scene, art/<scene>.png (fitted to the art's size on its own background colour), then
art/<scene>.json, last: the key (as above), the model that painted the kept picture, and the painter the scene fell
back from, why and on which try (fell_back_from: a picture painted so counts as done, as the job's painter's would),
the prompt, that it labels its own parts,
where its wrong labels are, the helper's check of the kept picture and what was wrong with each
try, so the picture counts as done only for this bundle. A refused picture is kept as art/<scene>.try<n>.png.
Removes locations.json: the locate step makes it from these
pictures. Resuming and redrawing work as for the SVG art.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

import skia
from PIL import Image

from pydantic import Field

from studio import brain, checks, gemini, jobs, knobs, ledger, media, muse, repair, svg
from studio.checks import colour_word
from studio.jobs import read, write
from studio.schemas import (PART_TAG, Axes, Box, Bundle, Flag, Location, Locations, Scene, SceneLocations, Strict, Structure,
                             locations_problems, stage_groups)

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "art.md").read_text(encoding="utf-8")
MAX_TRIES = 3  # SVG drawings a scene may take
# Muse Image pictures a scene may take. Two since Oct 28: in round 6 a third picture rarely fixed what
# the second had not, and every one costs a picture and a check.
MAX_PAINTS = 2
REFUSAL_CHARS = 120  # how much of a refused try's problem the step's line keeps
# Which scenes Claude draws as SVG whatever paints the rest (claude_draws()): the knob's choices, its default first.
# The API offers the same (api/src/lib/knobs.ts).
CLAUDE_DRAWS = "STUDIO_CLAUDE_DRAWS"
DRAWS_CHOICES = ("math", "charts", "brain")
# A math lesson, by its course or topic (math_lesson()): the subjects, and what a lesson in another course is still
# math for ("fractions" in a cooking class).
MATH = re.compile(r"\b(math|maths|mathematics|arithmetic|algebra|geometry|trigonometry|pre-?calculus|calculus|"
                  r"statistics|probability|fractions?|decimals?|percent(?:s|ages?)?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Painter:
    """Who paints a scene: its name in the settings, its model, and how to ask it for one picture."""

    name: str
    title: str
    model: str
    make: Callable[[Path, str, str, Path | None], bytes]  # (job, label, prompt, reference) -> the picture's bytes
    declined: type[Exception]  # a picture it would not make: a refused try
    failed: type[Exception]  # any picture it did not make
    redraws: bool  # it can redraw a reference picture (a process's later stages, from its first)


def painter(job: Path) -> str:
    """Who makes this job's art: gemini, muse or svg, the admin's setting for the brain in force as the API's last
    claim said (settings.json), else that brain's default."""
    return brain.painter(jobs.studio_settings(job).get("art"))


def painter_of(job: Path) -> Painter:
    """The painter that paints this job's pictures (not for svg). Looked up at each call, so tests can stand in."""
    return painter_named(painter(job))


def painter_named(name: str) -> Painter:
    """Gemini 3.1 Flash Image for "gemini", else Muse Image."""
    if name == "gemini":
        return Painter("gemini", gemini.NAME, gemini.MODEL, lambda job, label, prompt, reference=None:
                       gemini.paint(job, label, prompt, reference), gemini.Declined, gemini.GeminiError, redraws=False)
    return Painter("muse", "Muse Image", muse.IMAGE_MODEL, lambda *a: muse.paint(*a), muse.Declined, muse.MuseError,
                   redraws=True)


OTHER = {"gemini": "muse", "muse": "gemini"}  # who paints a scene the job's painter failed on (fallback())


def fallback(by: Painter) -> Painter | None:
    """The painter a scene is asked of once `by`, the job's, has failed on it for good (paint()): the other of Gemini
    and Muse Image, when the brain in force may use it (brain.ART_CHOICES) and this machine has its keys
    (brain.PAINTER_KEYS). None otherwise, and the failure fails the step as before."""
    other = OTHER.get(by.name)
    if other is None or other not in brain.ART_CHOICES[brain.provider()]:
        return None
    if not all(os.environ.get(var) for var in brain.PAINTER_KEYS[other]):
        return None
    return painter_named(other)


def drawn(job: Path, bundle: Bundle, scene: Scene) -> bool:
    """Whether the scene is drawn as SVG: every scene when the job's painter is svg; else a scene to scale (exact()),
    and every stage of its process with it (the stages share one picture's framing), when Claude draws it on this
    brain (claude_draws()), unless the scene, or a stage of its process, already has a painting for the scene as the
    bundle now has it: painted before charts were drawn (a job under way when this came in), or instead of a drawing
    that failed. A job keeps the art it has, rather than paying to draw again what is there."""
    if painter(job) == "svg":
        return True
    group = _stages(bundle, scene)
    can_draw = brain.provider() == "claude" or claude_draws() != "brain"
    return _to_scale(bundle, group) and can_draw and not any(_painting_for(job, bundle, s) for s in group)


def claude_draws() -> str:
    """The knob's choice of what Claude draws as SVG (CLAUDE_DRAWS): one of DRAWS_CHOICES, the first unless set."""
    value = knobs.env(CLAUDE_DRAWS).strip().lower()
    return value if value in DRAWS_CHOICES else DRAWS_CHOICES[0]


def math_lesson(bundle: Bundle) -> bool:
    """Whether its course or topic is math: "6th grade math", "AP Statistics", "dividing fractions"."""
    return bool(MATH.search(f"{bundle.course} {bundle.topic}"))


def exact(bundle: Bundle, scene: Scene) -> bool:
    """Whether the scene's sizes and counts must be right: a graph, or marked to scale (Scene.exact()), or, with the
    knob at math, any picture of a math lesson but its hook. Drawn as SVG, it is drawn to scale from the
    facts' numbers."""
    return scene.exact() or (claude_draws() == "math" and scene.id not in EVERYDAY and math_lesson(bundle))


def _stages(bundle: Bundle, scene: Scene) -> list[Scene]:
    """The scene and the other stages of its process, or the scene alone."""
    stage = Stage.of(bundle, scene)
    return stage_groups(bundle.scenes)[stage.process] if stage else [scene]


def _to_scale(bundle: Bundle, group: list[Scene]) -> bool:
    # The hook is an everyday picture: marked to scale by mistake, it is still painted.
    return any(s.axes or (exact(bundle, s) and s.id not in EVERYDAY) for s in group)


def _painting_for(job: Path, bundle: Bundle, scene: Scene) -> bool:
    """Whether art/<scene>.json records a painting of the scene as the bundle now has it, by whichever painter."""
    try:
        made = json.loads(_painting(job, scene).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(made, dict) and made.get("key") == key(bundle, scene)


def drawn_locations(job: Path, bundle: Bundle, scene: Scene) -> SceneLocations | None:
    """A drawn scene's art, masks and locations from its kept drawing, with no call to the brain; None when it has
    no drawing for the scene as the bundle now has it. For the locate step, when the other scenes are painted."""
    text = _saved(job, bundle, scene)
    if text is None:
        return None
    drawing = judge(text[len(_marker(bundle, scene)):], scene)
    return save(job, scene, drawing) if drawing.enough(scene) else None


def run(job: Path, ask=brain.ask) -> str:
    if painter(job) != "svg":
        return run_painted(job, ask)
    bundle = checks.load(job / "bundle.json")
    resume = located(job) is None
    media.restart(job, media.LOCATIONS)
    (job / "art").mkdir(exist_ok=True)
    scenes, left_out, refused = [], [], []
    for scene in bundle.scenes:
        drawing = draw(job, bundle, scene, ask, resume)
        scenes.append(save(job, scene, drawing))
        left_out += [f"{name} on {scene.id}" for name in drawing.left_out(scene)]
        refused += [f"{scene.id}: {why}" for why in drawing.refused]
    locations = Locations(scenes=scenes)
    problems = locations_problems(locations, bundle)
    if problems:  # a bug here, not in the drawing
        raise media.StepFailed("the locations do not match the bundle: " + "; ".join(problems))
    write(job / media.LOCATIONS, locations)
    total = sum(len(s.structures) for s in bundle.scenes)
    found = sum(len(s.structures) for s in scenes)
    return (f"drew {len(scenes)} scenes, located {found} of {total} structures"
            + refusals(refused, "drawing")
            + (f"; left out {', '.join(left_out)}" if left_out else ""))


def is_done(job: Path) -> bool:
    """The art is there for this bundle, made by the painter the settings name (or by the other, where it failed:
    _painted())."""
    try:
        bundle = checks.load(job / "bundle.json")
    except (OSError, ValueError):
        return False
    if painter(job) != "svg":
        return all(_saved(job, bundle, scene) is not None if drawn(job, bundle, scene) else _painted(job, bundle, scene)
                   for scene in bundle.scenes)
    return located(job) is not None and all(_saved(job, bundle, scene) is not None for scene in bundle.scenes)


def located(job: Path) -> Locations | None:
    """locations.json, if it reads, matches the bundle, the files it points to are there, and
    the art it locates on (drawn as SVG or painted by Muse Image) was made for this bundle's scenes."""
    try:
        locations = read(job / media.LOCATIONS, Locations)
        bundle = checks.load(job / "bundle.json")
    except (OSError, ValueError):
        return None
    if locations_problems(locations, bundle):
        return None
    files = [s.art for s in locations.scenes] + [s.mask for scene in locations.scenes for s in scene.structures]
    if not all((job / f).exists() for f in files):
        return None
    for scene in bundle.scenes:
        drawn = {s.found_by for s in locations.scene(scene.id).structures}
        if "svg" in drawn and (not _saved(job, bundle, scene) or locations.scene(scene.id).look != svg.LOOK):
            return None  # drawn for another scene, or styled in another look
        if drawn - {"svg"} and not _painted(job, bundle, scene):
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
    refused: list[str] = field(default_factory=list)  # draw(): why each other try of the scene was not kept
    values: list = field(default_factory=list)  # the numbers it marks for the video to write (svg.values())

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
        styled = svg.style(checked.root, scene.structures, graph=bool(scene.axes))
        drawing.art = svg.rasterise(styled)
        all_masks = svg.masks(styled, names)
    except ValueError as e:
        drawing.problems.append(str(e))
        return drawing
    except RecursionError:
        drawing.problems.append("the SVG nests groups too deeply; keep it to a few levels")
        return drawing
    drawing.usable = True
    drawing.values = svg.values(checked.root, [s.name for s in scene.structures])
    # A line's label anchor is on the line, not on a point marked on it, which is wider and would take it.
    pointed = [n for n in names if any(el.get(svg.ROLE) == "point" for g in styled.iter() if g.get(svg.MARK) == n
                                       for el in g.iter())]
    lines = svg.masks(svg.without_points(styled), pointed) if pointed else {}
    for name in names:
        found = svg.locate(all_masks[name], anchor_in=lines.get(name))
        if found is None:
            shown = int(all_masks[name].sum())
            drawing.problems.append(f"{name} shows only {shown} px in the art, too little to point a label at: "
                                    "draw it bigger, or move what covers it")
            continue
        drawing.masks[name], drawing.found[name] = all_masks[name], found
    return drawing


def drawer() -> str:
    """Who draws the SVG: Claude's brain itself, else Claude as the drawer (brain.py), whatever Muse's brain is."""
    return "brain" if brain.provider() == "claude" else "drawer"


def draw(job: Path, bundle: Bundle, scene: Scene, ask, resume: bool) -> Drawing:
    """The scene's drawing: the saved one when resuming, else Claude's best of MAX_TRIES."""
    marker = _marker(bundle, scene)
    text = _saved(job, bundle, scene) if resume else None
    if text is not None:
        drawing = judge(text[len(marker):], scene)
        if drawing.enough(scene):
            return drawing
    for old in (job / "art").glob(f"{media.file_name(scene.id)}.try*.svg"):
        old.unlink()

    best, previous, drawn = None, None, []
    for n in range(1, MAX_TRIES + 1):
        try:
            answer = ask(drawer(), user_prompt(bundle, scene, previous), system=PROMPT,
                         label=f"art {scene.id}" + (f" try {n}" if n > 1 else ""))
            drawing = judge(svg.extract(answer["text"]), scene)
        except brain.Truncated:
            drawing = Drawing("", ["the answer was cut off before </svg>; draw it simpler, under 40 KB"])
        drawn.append(drawing)
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
    _painting(job, scene).unlink(missing_ok=True)  # the art is SVG now, whatever painted it before
    for old in (job / "art").glob(f"{media.file_name(scene.id)}.try*.png"):
        old.unlink()
    best.refused = [d.problems[0] for d in drawn if d is not best and d.problems]
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
                          height=media.ART_HEIGHT, structures=structures, look=svg.LOOK, values=drawing.values)


def key(bundle: Bundle, scene: Scene) -> str:
    """Changes when the topic or the scene does (its prompt, structures, colours), and for a process's later
    stage when its first stage does, which it is redrawn from.

    Not when the art prompt does: a prompt change should not redraw finished jobs.
    """
    data = [bundle.topic, _dump(scene)]
    if (stage := Stage.of(bundle, scene)) and stage.number > 1:
        data.append(_dump(stage.first))
    if (source := style_source(bundle, scene)) is not None:  # painted in its look: again whenever it is
        data.append(["style", _dump(source)])
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _dump(scene: Scene) -> dict:
    # Without an empty stage_of or axes, or a false to_scale: a scene that is no stage, no graph and not to scale
    # keeps the key it had before they existed, so finished jobs are not painted again. Never with song_only, which
    # says which video plays the picture, not what it shows: a song's scene the lesson takes too keeps its painting.
    return scene.model_dump(exclude={"song_only"} | {name for name in ("stage_of", "axes", "to_scale")
                                                     if not getattr(scene, name)})


@dataclass
class Stage:
    """Where a scene stands in the process it is a stage of (Scene.stage_of)."""

    process: str
    number: int  # from 1
    count: int
    first: Scene  # the first stage: its picture is what the later ones are redrawn from

    @classmethod
    def of(cls, bundle: Bundle, scene: Scene) -> Stage | None:
        if not scene.stage_of:
            return None
        group = stage_groups(bundle.scenes).get(scene.stage_of, [])
        if len(group) < 2:
            return None
        return cls(scene.stage_of, [s.id for s in group].index(scene.id) + 1, len(group), group[0])


def style_source(bundle: Bundle, scene: Scene) -> Scene | None:
    """The overview, when the scene is painted in its look: the owner saw the pump's pictures each in a style of its
    own (Nov 1), the pump an orange cluster in one and a glossy lobed shape in the next, painted from separate prompts.
    So a scene that shares a part with the overview is redrawn from the overview's picture (STYLE_FROM), as a process's
    later stage is from its first. Not the hook (an everyday picture), the overview itself, or a later stage, which
    follows its first stage, and that one the overview."""
    if scene.id in EVERYDAY or scene.id == "overview":
        return None
    overview = next((s for s in bundle.scenes if s.id == "overview"), None)
    stage = Stage.of(bundle, scene)
    if overview is None or (stage is not None and stage.number > 1):
        return None
    shared = {s.name for s in scene.structures} & {s.name for s in overview.structures}
    return overview if shared else None


def user_prompt(bundle: Bundle, scene: Scene, previous: Drawing | None = None) -> str:
    parts = [f"Topic: {bundle.topic}" + (f" ({bundle.course})" if bundle.course else ""),
             f"Audience: {bundle.audience}",
             f"Core idea: {bundle.core_idea}",
             f"Scene: {scene.id}\nWhat it shows: {scene.described()}"]
    lines = []
    for s in scene.structures:
        also = f" (also called {', '.join(s.aliases)})" if s.aliases else ""
        lines.append(f'- {s.name}{also}: {s.sam_phrase}. Colour {s.color_hex}. '
                     f'Group: <g {svg.MARK}="{s.name}">')
    parts.append("Structures to draw, each as one group:\n" + "\n".join(lines))
    if scene.axes:  # styled by code (svg.style), and found in the art as lines of their own (studio/render/axes.py),
        # so in no structure's group
        named = " and ".join(f'"{name}" at the {end}' for name, end in ((scene.axes.x, "right end of the horizontal one"),
                                                                       (scene.axes.y, "top of the vertical one")) if name)
        parts.append(f'Axes: this scene is a graph. Draw its two axes as two plain <line>s in one <g {svg.ROLE}="axes">, '
                     "outside every structure's group, with nothing written on or beside them: the video writes "
                     f'{named}. Draw the rest as "Graphs" in your instructions says.')
    if exact(bundle, scene):  # drawn as SVG for its sizes (drawn()): the numbers they are worked out from
        parts.append(("" if scene.axes else 'To scale: this scene\'s sizes must be right, as "To scale" in your '
                      "instructions says. ") + "The lesson's facts, for the numbers the sizes come from:\n"
                     + "\n".join(f"- {f.text}" for f in bundle.facts))
        said = _said_over(bundle, scene)
        if said:  # the values the video writes are the ones the voice says, written as it writes them
            parts.append('What the voice says over this scene, for its values ("Values" in your instructions):\n'
                         + "\n".join(f"- {line}" for line in said))
    others = [o for o in bundle.scenes if o.id != scene.id]
    if others:
        parts.append("The video's other scenes, drawn separately; draw shared structures the same way:\n" + "\n".join(
            f"- {o.id}: {o.described()} Structures: "
            + ", ".join(f"{s.name} {s.color_hex}" for s in o.structures) for o in others))
    if previous is not None:
        parts.append("Your last drawing of this scene, below, was refused:\n"
                     + "\n".join(f"- {p}" for p in previous.problems)
                     + "\nDraw the whole scene again with these fixed, and answer with the SVG only."
                     + (f"\n<last_drawing>\n{previous.text}\n</last_drawing>" if previous.text else ""))
    return "\n\n".join(parts)


def _said_over(bundle: Bundle, scene: Scene) -> list[str]:
    """The narration's sentences that play over the scene, in order; for the song's own scene, its sung lines."""
    over = {d.line_id for d in bundle.direction if d.scene == scene.id}
    return [line.text for line in (bundle.lyrics if scene.song_only else bundle.narration) if line.id in over]


# --- the painted art: Gemini 3.1 Flash Image or Muse Image -------------------------------------

# After every scene's prompt. The look is the scene's own (its image_prompt ends with the look its
# kind of textbook gives it: a modern cell biology illustration, an astronomy schematic), so this
# says only what every picture needs. A dark slate blue background, as a modern study infographic
# has: the colours stand out on it, and so do the renderer's white label pills. Code reads the
# background's colour off each picture's edge (media.paper), so any plain colour works.
# Where each part sits is the scene's own prompt's to say (studio/prompts/bundle.md): said here, "every part
# sits inside whatever holds it" wrapped two of round 6's everyday scenes (a dam, a pond) in cell-like bubbles.
# A painted picture labels its own parts, those that need a label, and the video draws no labels over it: only
# over a drawing (the brain's SVG). Until Oct 31 every prompt ended in a style line (a plain, flat dark slate blue
# background, rounded proteins, only the parts named, no text) and the video labelled the picture itself; Muse Image's
# pictures came back better without the line, and its own labels were worth keeping even where one was wrong. The
# line that replaced it is the operator's, word for word, as tried by hand on two overviews: "Clearly label
# the structures" brought the sodium-potassium pump back with a real bilayer, both channels through it and every
# label on its part, but labelled everything in sight; asking for the nonobvious ones only kept Pavlov's panels to
# the labels they need. Through the API (not the app the operator tried it in), Muse Image then made a slide of
# every picture, 8 times in 8 at either size: a title, a legend or colour key, and the Colours line's names written
# into the labels ("Sodium-Potassium Pump (Orange)"). The second sentence stopped all three, 2 times in 2.
# On Oct 31 "nonobvious" held for a diagram (a membrane came back labelled with its pump, channels, ATP and both
# spaces) and failed for a real scene, where every object has a name a student would not know: a boat came back
# labelled "oarlock" and "thwart", a living room "Wall Vent" and "Wall Outlet" twice, a theatre "Fly System". So a
# science diagram keeps the operator's line, and any other scene labels its parts and nothing else (labels_line()).
# On Nov 1 the diagram's line lost to the list too, painted blind side by side on two overviews: its labels named the
# parts in textbook words the narration never says ("Voltage-gated ion channel", "Calvin Cycle") 31% of the time and
# pointed wrong 17%; with the list every label had the narration's word and 9% pointed wrong, with no extras or crowding.
NO_SLIDE = "No title, legend, key or caption, and no colour names in the labels."
BOXED = "Write each label in black letters on its own small white box."
NO_LABELS = "No labels, title, legend, key, caption or any other writing."
SCIENCE_STYLE = "clean scientific style"  # how the brief has a science scene's image_prompt open (prompts/bundle.md)
# A biology picture is asked for in the Campbell textbook look, the owner's choice (Nov 2): "the images are much better
# looking". A style, not a figure of theirs. Added here at paint time, never in the bundle: when the brief had the
# writer name the book, the fact-check struck it from every picture as a claim (eed1ff6). Biology is the course's, or,
# in any other science course, a scene that draws the parts of a cell.
CAMPBELL_STYLE = "clean biology Campbell style"  # the owner's words for it
BIOLOGY_COURSE = re.compile(r"biolog|anatomy|physiolog|life science|genetic|microbio|ecolog|botany|zoology", re.I)
CELL_PARTS = re.compile(r"\b(?:cell membrane|organelles?|proteins?|chloroplasts?|mitochondri\w*|enzymes?|DNA|RNA"
                        r"|ribosomes?|neurons?|chromosomes?|phospholipid\w*|bilayer|cytoplasm)\b", re.I)
# The look the brief ends a cell or molecular scene with says "the membrane a phospholipid bilayer" (prompts/bundle.md).
# Said of a scene with no membrane among its parts, it has the painter make a bilayer of whatever wall is there: the
# lungs' red blood cell close-up (Nov 2) came back with its capillary's wall painted as one, which no capillary is.
BILAYER = re.compile(r"[;,]?\s*the membrane (?:is )?a phospholipid bilayer", re.I)
MEMBRANE = re.compile(r"membrane|bilayer", re.I)
EVERYDAY = ("hook",)  # the everyday picture; no picture draws the analogy since Nov 1 (checks._no_analogy_scene)
WORKERS = 4  # scenes painted at once: each one waits on the painter's API, not on this machine

CHECK_PROMPT = """You check one illustration for a short educational video before it is used. The picture \
labels its own parts; the video adds no labels to it.

In `labels`, give each label in the picture that is wrong: on or pointing at the wrong part, naming \
something the scene does not show, or misspelled or garbled. The video flags each one you give on the picture as a \
mistake, so give only those that are wrong: never one that is right, roughly right or acceptable, a part with no \
label, or a label whose line merely ends a little off its part. For each: `text`, the writing as it reads there; \
`note`, the sticky note for the student, under 8 words, saying what is true ("Should read: thylakoid space", "This \
is cytochrome b6f"); and a tight box round the writing as fractions of the picture's width and height from its \
top-left corner: `left` and `right` from 0 to 1 across, `top` and `bottom` from 0 to 1 down. Leave it empty when \
every label is right or the picture has none.

List in `misplaced` each structure below that is missing from the picture, in the wrong place, or facing \
the wrong way for what the scene should show (an organelle outside what holds it, a protein's head on the \
wrong side of its membrane), each in a few words. A picture with any of these is painted again.

List in `wrong` anything else that would mislead a learner, each in a few words: a part of the wrong shape \
or too hard to make out, or anything scientifically wrong for what the scene should show.

Judge both as the kind of scene it is, against what it should show, not the whole topic: what the scene \
leaves out is not missing. The video animates each flow itself, as dots \
moving between the structures, so a flow or arrow the picture leaves out is not a problem; one drawn the \
wrong way is. List only problems, never what you checked and found right. Leave it empty when the picture \
is right. Do not list matters of taste or style.

Then, in `notes`, give one for each problem above where the picture contradicts one of the lesson's \
facts, so that it would teach the student something false that the lesson teaches: a part in the wrong place, facing \
the wrong way, of the wrong shape or in the wrong number. Never one about a detail the facts do not mention, such as \
a building's roof or a costume, even if it is wrong. For each: `fact`, the id of the fact it contradicts; `text`, one \
short, plain sentence for the student saying what the picture shows and what is true, under 25 words ("The \
chromosomes' bends point the wrong way: each is pulled toward its pole by its middle, so the bend leads."); `note`, \
the sticky note the video puts there, under 8 words ("The inner face is negative"); and a box round where it is, as \
for a label, or 0 for all four when it is about the whole picture or no one place. A wrong label is never a \
note. Leave it empty when nothing in the picture contradicts the facts."""


class ArtCheck(Strict):
    """The helper's look at one picture."""

    # Each label the picture got wrong, as written and what is wrong; kept, and flagged on the picture (flags()).
    # A check made before Oct 31 has `text` (the writing, painted out) and `unnamed` (round things the video's own
    # labels would have left unnamed) instead: the picture was to have no labels of its own.
    labels: list[str] = Field(default_factory=list)
    misplaced: list[str]  # each structure missing, out of place or facing the wrong way; a repaint
    wrong: list[str]  # anything else wrong for the scene, a few words each; recorded, not repainted
    # The problems that would teach something false, as a sentence each for the student: the video shows
    # those of the picture it uses beside it (notices). [] in a check made before.
    notes: list[str] = Field(default_factory=list)
    # Notes the check wrote that name no fact of the lesson (look() drops them, and the video never shows them):
    # kept here for the admin's review, so a dropped correction is seen, not lost. [] in a check made before Nov 3.
    unfounded: list[str] = Field(default_factory=list)


class LabelFlag(Strict):
    """A wrong label in a picture: the writing, the sticky note the video puts beside it, and the box round it, as
    fractions of the art's width and height."""

    text: str
    note: str
    left: float
    top: float
    right: float
    bottom: float


class CheckedNote(Strict):
    """A mistake in a picture that contradicts one of the lesson's facts: the fact's id, a sentence for the student,
    the sticky note the video puts there, and the box round where it is (all 0: the whole picture)."""

    fact: str
    text: str
    note: str
    left: float
    top: float
    right: float
    bottom: float


class PictureCheck(Strict):
    """The helper's one look at a picture (CHECK_PROMPT): kept as an ArtCheck, with its boxes (look())."""

    labels: list[LabelFlag]
    misplaced: list[str]
    wrong: list[str]
    notes: list[CheckedNote]


# The picture as the check sees it: shrunk to this width, which costs about 40% of the tokens of the art's 1792 and
# still read every wrong label of twelve test pictures, misspellings and wrong pointers alike (Nov 2).
CHECK_WIDTH = 1024


def look(job: Path, bundle: Bundle, scene: Scene, ask, label: str, path: Path | None = None
         ) -> tuple[ArtCheck, list[dict], list[dict]]:
    """The helper's one look at a picture of the scene (the kept art, or `path`): its check, the wrong labels it boxed
    (flags()), and its notes as sticky notes: at their place, boxed, or with no one place (all 0), which the video
    sticks in a corner of the picture (notices()). A note that names no fact of the lesson is
    dropped: a correction must rest on what the lesson teaches, which the fact-check passed, not on the checker's own
    knowledge (round 20's kidney note was false). One look replaces the check, the second look at its labels and the
    brain's review of its notes, the owner's choice on Nov 2: too many layers."""
    with tempfile.TemporaryDirectory(prefix="popsmosis-check-") as tmp:
        small = _shrunk(path or job / media.art_path(scene.id), Path(tmp))
        answer = PictureCheck(**ask("helper", check_prompt(bundle, scene), PictureCheck, [small], system=CHECK_PROMPT,
                                    label=label))
    facts = {f.id for f in bundle.facts}
    notes = [n for n in answer.notes if n.fact.strip() in facts and n.text.strip()]
    flags = [f.model_dump() for f in answer.labels if _boxed(f) and f.note.strip()]
    pinned = [{"text": n.text, "note": n.note, **({"left": n.left, "top": n.top, "right": n.right, "bottom": n.bottom}
                                                  if _boxed(n) else NO_PLACE)}
              for n in notes if n.note.strip()]
    check = ArtCheck(labels=[f"'{f.text}': {f.note}" if f.note.strip() else f.text for f in answer.labels],
                     misplaced=answer.misplaced,
                     wrong=answer.wrong, notes=[n.text for n in notes],
                     unfounded=[f"{n.fact}: {n.text}" for n in answer.notes if n not in notes and n.text.strip()])
    return check, flags, pinned


def _shrunk(path: Path, folder: Path) -> Path:
    """The picture at CHECK_WIDTH, saved in `folder`, as a model is shown it."""
    small = folder / "picture.png"
    with Image.open(path) as picture:
        if picture.width > CHECK_WIDTH:
            picture = picture.resize((CHECK_WIDTH, round(picture.height * CHECK_WIDTH / picture.width)), Image.LANCZOS)
        picture.save(small)
    return small


# The writer's role sees a picture that went wrong and writes the scene's words again for the next try (rewrite()).
# The owner's idea (Nov 2): the same words with "refused: ..." after them got the same picture back, and no one who
# could fix the words saw what the painter made of them. Tried on three pictures of the Nov 2 run, each repainted
# once from its rewrite: the airplane wing (three slabs, the check saw only its nose) came back one slim wing with its
# surfaces as strips along its edges; the antibodies (stuck on by their stems) came back gripping the antigen with
# their arms; the phone's filled signal bars came back hollow.
REWRITE_PROMPT = """You write the description an image model paints one picture of a short science lesson from. It \
painted the picture shown, from the full prompt given below, and a checker looked at the picture. Look at the picture \
yourself, and compare it with what the lesson needs the picture to show.

If the picture already shows everything the lesson needs it to show, and shows it right, answer KEEP and nothing \
else: a second painting can come out worse.

Otherwise write the scene's description again, so that the next painting comes out right. Write it as one paragraph \
of plain sentences, opening as the old one opens ("Educational textbook illustration of ..."). Keep everything the \
picture must show and every part, and change whatever made the painter go wrong.

The painter is an image model: it paints what the words say, not what the lesson means. Code adds to your \
description: right after each part's tag it puts that part's colour and label, and at the end, lines about the \
labels' style. So tag each part right after the words that describe it, with its name in double brackets exactly as \
given ("the Moon [[Moon]] on the right"), each part once, and write no colours and no text, labels or numbers \
yourself.

Answer with KEEP, or with the new description only."""


def rewrite_prompt(bundle: Bundle, scene: Scene, sent: str, check: ArtCheck) -> str:
    """What the writer's role is shown of a picture that went wrong: the scene's words, its parts, the whole prompt
    the painter got, the check's findings and the lesson's facts (the picture itself comes as an image)."""
    found = [f"- {kind}: {item}" for kind, items in (("misplaced", check.misplaced), ("wrong", check.wrong),
                                                     ("label", check.labels)) for item in items]
    return "\n\n".join([
        f"Topic: {bundle.topic}" + (f" ({bundle.course})" if bundle.course else ""),
        f"The scene: {scene.id}",
        f"The description you wrote before:\n{scene.described()}",
        "Its parts, each to be tagged once:\n" + "\n".join(f"- {s.name} ({colour_word(s.color_hex)})"
                                                           for s in scene.structures),
        f"The full prompt the painter was given:\n{sent}",
        "What the checker found wrong in the picture:\n" + ("\n".join(found) or "- nothing"),
        "The lesson's facts:\n" + "\n".join(f"- {f.id}: {f.text}" for f in bundle.facts),
        "The picture it painted is the image above.",
    ])


KEEP = "KEEP"


def rewrite(bundle: Bundle, scene: Scene, picture: Path, sent: str, check: ArtCheck, ask, label: str) -> str | None:
    """The writer's role's look at a picture of the scene and what the check found in it (REWRITE_PROMPT): the
    scene's words written again for the next try, "" when it keeps the picture, None when the call fails or its answer
    is no description. A rewrite is for the next try only, never written into the bundle, whose words the fact-check
    passed.

    An answer that opens with KEEP keeps the picture, whatever follows it ("KEEP: it shows the wing"): painted as a
    description, it would have thrown a good picture away for one painted from those words. An empty one, or one that
    tags none of the scene's parts, is not a description of it: as when the call fails, the check's problems decide.
    The nine rewrites of the Nov 2 runs each tagged three parts or more."""
    try:
        with tempfile.TemporaryDirectory(prefix="popsmosis-rewrite-") as tmp:
            answer = ask("brain", rewrite_prompt(bundle, scene, sent, check), None, [_shrunk(picture, Path(tmp))],
                         system=REWRITE_PROMPT, label=label)
    except brain.BrainError as e:
        print(f"  {label}: no answer ({e})", file=sys.stderr)
        return None
    text = answer["text"].strip().strip("`").strip()
    if re.match(rf"\W*{KEEP}\b", text, re.I):
        return ""
    if not text or (scene.structures and len(inline(scene.model_copy(update={"image_prompt": text}))[1])
                    == len(scene.structures)):
        print(f"  {label}: no description in its answer, so the check decides", file=sys.stderr)
        return None
    return text


def _boxed(found) -> bool:
    return 0 <= found.left < found.right <= 1 and 0 <= found.top < found.bottom <= 1


NO_PLACE = {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}  # a pinned note's box when it has no one place


def _placed(pin: dict) -> bool:
    """Whether a pinned note of a record (look()) has a place in the picture: a box, not all 0."""
    try:
        return 0 <= float(pin["left"]) < float(pin["right"]) <= 1 and 0 <= float(pin["top"]) < float(pin["bottom"]) <= 1
    except (KeyError, TypeError, ValueError):
        return False


@dataclass
class Painting:
    """One picture of a scene, checked."""

    path: Path
    prompt: str
    attempt: int
    check: ArtCheck
    refused: list[str] = field(default_factory=list)  # paint(): why each other picture of the scene was not kept
    flags: list[dict] = field(default_factory=list)  # its wrong labels, boxed (look())
    pinned: list[dict] = field(default_factory=list)  # its notes, as sticky notes: at their place, or all 0
    rejected: bool = False  # the writer's role saw it and wrote the scene's words again (rewrite())
    by: Painter | None = None  # who painted it: the job's painter, or the other once that one failed (fallback())

    def problems(self) -> list[str]:
        """What the scene is painted again for: a structure missing or out of place, or anything the picture gets
        wrong (the owner, Nov 2: the airplane wing's slabs and the filled signal bars were "wrong" and kept). A wrong
        label is not: the picture is kept with it, and the video flags it."""
        return list(self.check.misplaced) + list(self.check.wrong)

    def score(self) -> tuple[int, int]:
        """Lower is better. A picture the writer's role rejected counts as one more part out of place and one more
        thing wrong: on Nov 2 it rejected two airplane pictures the check had passed (the stall showed no air
        breaking away over the top), their repaints each had one small fault, and the step kept the rejected
        ones."""
        rejected = int(self.rejected)
        return len(self.check.misplaced) + rejected, len(self.check.labels) + len(self.check.wrong) + rejected


def run_painted(job: Path, ask) -> str:
    bundle = checks.load(job / "bundle.json")
    by = painter_of(job)
    resume = not is_done(job)
    media.restart(job, media.LOCATIONS)  # the locate step makes it from these pictures
    (job / "art").mkdir(exist_ok=True)
    if not resume:  # a run from scratch draws what is to scale, whatever painted it before
        for scene in bundle.scenes:
            if _to_scale(bundle, _stages(bundle, scene)) and _painting_for(job, bundle, scene):
                _painting(job, scene).unlink()
    to_draw = [scene for scene in bundle.scenes if drawn(job, bundle, scene)]  # to scale: Claude's SVG
    drawn_ids = {scene.id for scene in to_draw}
    to_paint = [scene for scene in bundle.scenes if scene.id not in drawn_ids]
    stages = {scene.id: stage for scene in to_paint if (stage := Stage.of(bundle, scene))}
    # The scenes painted in the overview's look (style_source()), when the overview is painted too, by a painter that
    # can redraw: after the overview, from its picture.
    overview = next((scene for scene in to_paint if scene.id == "overview"), None)
    styled = ({scene.id for scene in to_paint if style_source(bundle, scene) is not None}
              if overview is not None and by.redraws else set())
    again = {scene.id for scene in to_paint if not (resume and _painted(job, bundle, scene))}
    if overview is not None and overview.id in again:
        again |= styled
    # A later stage is redrawn from its first: painted again whenever that one is.
    again |= {sid for sid, stage in stages.items() if stage.first.id in again}
    todo = [scene for scene in to_paint if scene.id in again]
    kept = len(to_paint) - len(todo)
    first = [scene for scene in todo if (scene.id not in stages or stages[scene.id].number == 1)
             and scene.id not in styled]
    middle = [scene for scene in todo if scene.id in styled]
    later = [scene for scene in todo if scene not in first and scene not in middle]

    def make(scene: Scene):
        if scene.id not in drawn_ids:
            return paint(job, bundle, scene, ask)
        try:
            drawing = draw(job, bundle, scene, ask, resume)
        except Exception as e:  # no drawing it can use, or the brain failed: painted, as before SVG for charts
            why = f"{type(e).__name__}: {e}"
            print(f"art: {scene.id}: drawn as SVG failed, painted instead: {why}", file=sys.stderr)
            return paint(job, bundle, scene, ask, instead_of_svg=why)
        save(job, scene, drawing)  # the locate step takes its locations from the drawing kept (drawn_locations())
        return drawing

    # The scenes at the same time, those drawn as SVG with them, then each process's later stages, redrawn from its
    # first stage's picture. One that fails fails the step once the others are saved.
    made = ledger.parallel(make, to_draw + first, WORKERS)
    made, painted_first = made[:len(to_draw)], made[len(to_draw):]
    instead = [(scene, m) for scene, m in zip(to_draw, made) if not isinstance(m, Drawing)]
    # A process's stages are all drawn or all painted: cross-faded, a painting and a drawing share no framing.
    with_painted = {s.id for scene, _ in instead for s in _stages(bundle, scene)}
    instead += [(scene, paint(job, bundle, scene, ask, instead_of_svg="a stage of its process could not be drawn"))
                for scene, m in zip(to_draw, made) if isinstance(m, Drawing) and scene.id in with_painted]
    painted_instead = [scene for scene, _ in instead]
    drawings = [(scene, m) for scene, m in zip(to_draw, made) if isinstance(m, Drawing) and scene.id not in with_painted]
    to_draw = [scene for scene, _ in drawings]
    results = [m for _, m in instead] + painted_first
    first = painted_instead + first
    if middle:  # in the overview's look, from its picture as now painted
        results += ledger.parallel(lambda scene: paint(job, bundle, scene, ask, job / media.art_path(overview.id),
                                                       styled=True), middle, WORKERS)
        first += middle
    results += ledger.parallel(lambda scene: paint(job, bundle, scene, ask, job / media.art_path(stages[scene.id].first.id)),
                               later, WORKERS)
    todo = first + later
    painted, tries = len(results), sum(attempts for _, attempts in results)
    left = [f"{scene.id}: {p}" for scene, (best, _) in zip(todo, results)
            for p in best.check.misplaced + best.check.labels + best.check.wrong]
    refused = [f"{scene.id}: {why}" for scene, (best, _) in zip(todo, results) for why in best.refused]
    lines = []
    if painted:
        lines.append(f"painted {painted} scene{'s' if painted != 1 else ''} with {by.title} in {tries} "
                     f"tr{'ies' if tries != 1 else 'y'}" + (f", kept {kept} painted before" if kept else "")
                     + fell_back_line(job, todo, results)
                     + refusals(refused, "picture") + (f"; the helper still saw: {'; '.join(left)}" if left else ""))
    elif kept or not to_draw:
        lines.append(f"kept the {kept} scene{'s' if kept != 1 else ''} {by.title} painted before")
    if to_draw:
        left_out = [f"{name} on {scene.id}" for scene, drawing in drawings for name in drawing.left_out(scene)]
        lines.append(f"drew {len(to_draw)} scene{'s' if len(to_draw) != 1 else ''} to scale as SVG"
                     + (" with Claude" if drawer() == "drawer" else "") + " ("
                     + ", ".join(scene.id for scene in to_draw) + ")"
                     + refusals([f"{scene.id}: {why}" for scene, drawing in drawings for why in drawing.refused],
                                "drawing")
                     + (f"; left out {', '.join(left_out)}" if left_out else ""))
    if painted_instead:
        lines.append("painted " + ", ".join(scene.id for scene in painted_instead) + " instead of drawing "
                     + ("it" if len(painted_instead) == 1 else "them") + " as SVG")
    return "; ".join(lines)


def refusals(refused: list[str], kind: str) -> str:
    """Why the step's refused pictures or drawings were not kept, for its line (and so `summary`): each
    one's first problem, cut short, since the full ones are in the art check and the kept tries."""
    if not refused:
        return ""
    counted: dict[str, int] = {}  # tries refused for the same reason are named once
    for why in refused:
        why = _cut(why)
        counted[why] = counted.get(why, 0) + 1
    return (f"; {len(refused)} {kind}{'s' if len(refused) != 1 else ''} refused: "
            + "; ".join(why + (f" (×{n})" if n > 1 else "") for why, n in counted.items()))


def _cut(why: str) -> str:
    """A problem cut to REFUSAL_CHARS."""
    return why if len(why) <= REFUSAL_CHARS else why[: REFUSAL_CHARS - 1] + "…"


def fell_back_line(job: Path, scenes: list[Scene], results: list[tuple[Painting, int]]) -> str:
    """The scenes whose kept picture the other painter made, the job's having failed on them (fallback()), for the
    step's line, which an operator sees: how many and who painted them, never what the provider said, which each
    scene's record keeps."""
    by = painter_of(job)
    other: dict[str, list[str]] = {}
    for scene, (best, _) in zip(scenes, results):
        if best.by is not None and best.by.name != by.name:
            other.setdefault(best.by.title, []).append(scene.id)
    return "".join(f"; {len(ids)} scene{'s' if len(ids) != 1 else ''} fell back to {title} when {by.title} failed "
                   f"({', '.join(ids)})" for title, ids in other.items())


def refusal(tried: dict) -> str | None:
    """What one try in paint()'s record was refused for, said plainly; None for a redraw that failed, since
    the same try was then painted afresh."""
    if tried.get("declined"):
        return str(tried["declined"])
    if tried.get("redraw_failed"):
        return None
    found = (tried.get("misplaced") or []) + (tried.get("wrong") or [])
    if found:
        return str(found[0])
    return "the writer asked for it again" if tried.get("rewritten") else None


def paint(job: Path, bundle: Bundle, scene: Scene, ask, reference: Path | None = None,
          instead_of_svg: str = "", styled: bool = False) -> tuple[Painting, int]:
    """The scene's picture: the best of up to MAX_PAINTS from the job's painter, saved as its art. Returns it and
    how many pictures were asked for. A process's later stage is redrawn from its first stage's picture
    (`reference`), so the stages share their framing, by a painter that can redraw (Muse Image); if it will
    not, or the painter cannot, it is painted afresh. If the painter then fails for good (not a picture it would not
    make), this try and the scene's next are asked of the other painter when it may paint here (fallback()).
    `instead_of_svg` says why a scene to be drawn as SVG is painted instead (drawn())."""
    own = by = painter_of(job)
    if not by.redraws:
        reference = None
    for old in (job / "art").glob(f"{media.file_name(scene.id)}.try*.png"):
        old.unlink()
    best, problems, n, tries = None, [], 0, []
    fell_back: dict = {}  # the painter the scene fell back from, why, and on which try
    words = scene  # what the next try is painted from: the scene's own words, then a rewrite of them (rewrite())
    for n in range(1, MAX_PAINTS + 1):
        told = problems if words is scene else []  # a rewrite says what to change itself
        prompt = paint_prompt(bundle, words, told, redrawn=reference is not None and not styled,
                              styled=reference is not None and styled)
        label = f"art {scene.id}" + (f" try {n}" if n > 1 else "")
        picture = None
        while picture is None:  # the redraw, then afresh, then afresh by the other painter: at most three asks
            try:
                picture = by.make(job, label, prompt, reference)
            except by.declined as e:
                problems = [f"{e}; describe the scene plainly, as a textbook diagram"]
                tries.append({"try": n, "declined": str(e)})
                break
            except by.failed as e:
                if reference is not None:
                    tries.append({"try": n, "redraw_failed": str(e)})
                    reference = None  # painted afresh from here on
                elif not fell_back and (other := fallback(by)) is not None:
                    print(f"art: {scene.id}: {by.title} failed, painted with {other.title} instead: {e}",
                          file=sys.stderr)
                    fell_back = {"painter": by.name, "why": _cut(str(e)), "try": n}
                    by = other
                else:
                    raise
                prompt = paint_prompt(bundle, words, told)
        if picture is None:
            continue
        path = job / "art" / f"{media.file_name(scene.id)}.try{n}.png"
        path.write_bytes(svg.png(fit(picture)))
        named = f"{scene.id}" + (f" try {n}" if n > 1 else "")
        check, flagged, pinned = look(job, bundle, scene, ask, f"art check {named}", path)
        painting = Painting(path, prompt, n, check, flags=flagged, pinned=pinned, by=by)
        tries.append({"try": n, **check.model_dump()})
        if best is None or painting.score() < best.score():
            best = painting
        problems = painting.problems()
        if n == MAX_PAINTS:
            break
        # The writer's role looks at every first picture, and the scene is painted again only when it asks for
        # changes (the owner, Nov 2): the check missed the airplane wing's slabs, and a second painting of a good
        # picture can come out worse. When it cannot answer, the check's problems decide, as before.
        asked = rewrite(bundle, scene, path, prompt, check, ask, f"art rewrite {named}")
        if asked:
            words = scene.model_copy(update={"image_prompt": asked})
            tries[-1]["rewritten"] = asked
            painting.rejected = True
        elif asked == "":
            tries[-1]["kept"] = True
            break
        elif not problems:
            break
    if best is None:
        raise media.StepFailed(f"scene {scene.id}: no usable picture in {MAX_PAINTS} tries: {problems[0]}"
                               + (f" (and {len(problems) - 1} more problems)" if len(problems) > 1 else ""))
    best.path.replace(job / media.art_path(scene.id))
    best.refused = [why for tried in tries if tried["try"] != best.attempt and (why := refusal(tried))]
    flagged, pinned = best.flags, best.pinned
    # `labelled`: the picture names its own parts, so the video draws no labels over it (self_labelled()). `model`:
    # who painted the kept picture; `fell_back_from`: the job's painter, when it failed on the scene (_painted()).
    write(_painting(job, scene), {"key": key(bundle, scene), "model": (best.by or own).model, "prompt": best.prompt,
                                  **({"fell_back_from": fell_back} if fell_back else {}),
                                  "labelled": True, **({"flags": flagged} if flagged else {}),
                                  **({"pinned": pinned} if pinned else {}),
                                  **({"instead_of_svg": instead_of_svg} if instead_of_svg else {}),
                                  "redrawn_from": stage.first.id if (stage := Stage.of(bundle, scene)) and reference
                                  and stage.number > 1 else "",
                                  **({"styled_from": "overview"} if styled and reference is not None else {}),
                                  "try": best.attempt, "check": best.check.model_dump(), "tries": tries})
    _kept(job, scene).unlink(missing_ok=True)  # the art is painted now, whatever drew it before
    return best, n


def fit(picture: bytes) -> np.ndarray:
    """A painted picture (any size, roughly the art's shape) scaled to fit the art, centred, on its own
    background colour (media.paper): RGB."""
    image = skia.Image.MakeFromEncoded(skia.Data.MakeWithCopy(picture))
    if image is None:
        raise media.StepFailed("the painted picture is not an image skia can read")
    r, g, b = media.paper(image.toarray(colorType=skia.kRGBA_8888_ColorType))
    surface = skia.Surface(media.ART_WIDTH, media.ART_HEIGHT)
    canvas = surface.getCanvas()
    canvas.clear(skia.Color(r, g, b))
    scale = min(media.ART_WIDTH / image.width(), media.ART_HEIGHT / image.height())
    w, h = image.width() * scale, image.height() * scale
    canvas.drawImageRect(image, skia.Rect.MakeXYWH((media.ART_WIDTH - w) / 2, (media.ART_HEIGHT - h) / 2, w, h),
                         skia.SamplingOptions(skia.CubicResampler.Mitchell()))
    return surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)[:, :, :3].copy()


def paint_prompt(bundle: Bundle, scene: Scene, problems: list[str] = (), redrawn: bool = False,
                 styled: bool = False) -> str:
    """The scene as a teacher would ask for it, each structure's colour by name (never a hex code: the
    image model reads names), a graph's axes, and last what to label (labels_line()): the picture labels its own
    parts. The Colours line gives every structure's colour, and a colour the image_prompt gives one right before its
    name is made to agree with it (checks._recolour_image_prompts): the writer's own ("a brown ATP synthase")
    disagreed with it ("ATP synthase orange"). Short on
    purpose: every extra line was a chance to miss. Nothing about the look (the note on NO_SLIDE says why). A later
    stage `redrawn` from its first stage's picture says so first.

    A part the image_prompt tags (schemas.PART_TAG) has its colour and label right after the words that describe it
    (inline()), and is left out of the Colours line and the labels line; a scene with no tags is asked for as
    before."""
    words, left = inline(scene)
    words = without_bilayer(scene, words)
    colours = ", ".join(f"{s.name} {colour_word(s.color_hex)}" for s in left)
    parts = [campbell_styled(bundle, words).strip().rstrip(".") + "."]
    if left or not scene.structures:
        parts.append(f"Colours: {colours}.")
    if loose := loose_line(bundle, scene):
        parts.append(loose)
    if scene.axes:
        parts.append(axes_line(scene.axes))
    if redrawn and (stage := Stage.of(bundle, scene)):
        parts.insert(0, REDRAW.format(process=stage.process))
    elif styled:
        parts.insert(0, STYLE_FROM)
    if problems:
        parts.append("The last picture of this scene was refused: " + "; ".join(problems)
                     + ". Make it again with these fixed.")
    parts.append(labels_line(scene, left))
    return " ".join(parts)


def inline(scene: Scene) -> tuple[str, list[Structure]]:
    """The image_prompt with each tag ("the downwash [[Downwash]]") as its part's colour and label in place ("the
    downwash (orange, label "Downwash")"), the label left out on the hook, which labels nothing (labels_line()); and
    the parts it tags none of, which the Colours and labels lines give as before. A tag names a part by its name or an
    alias, in any case; one that names none, or a part tagged before, is dropped (checks._check_part_tags repairs both in
    the bundle). The owner's design (Nov 2): given as lists after the picture's words, each colour and label has to
    be matched back to its part, and the airplane wing's three came back as three slabs."""
    named = {name.casefold(): s for s in scene.structures for name in (s.name, *s.aliases)}
    labelled = scene.id not in EVERYDAY
    done: list[str] = []

    def place(found: re.Match) -> str:
        part = named.get(found.group(1).strip().casefold())
        if part is None or part.name in done:
            return ""
        done.append(part.name)
        colour = colour_word(part.color_hex)
        return f' ({colour}, label "{part.name}")' if labelled else f" ({colour})"

    words = PART_TAG.sub(place, scene.image_prompt).strip()
    return words, [s for s in scene.structures if s.name not in done]


def is_diagram(scene: Scene) -> bool:
    """Whether the scene is a science diagram or a graph, as the brief has its picture asked for: the parts of one
    fill in as they are named; an everyday sight, a history or a story is shown as painted."""
    return SCIENCE_STYLE in scene.image_prompt.lower() or bool(scene.axes.x or scene.axes.y)


def without_bilayer(scene: Scene, words: str) -> str:
    """The scene's words without the look's bilayer (BILAYER) when none of its parts is a membrane."""
    if any(MEMBRANE.search(name) for s in scene.structures for name in (s.name, *s.aliases)):
        return words
    return BILAYER.sub("", words)


def campbell_styled(bundle: Bundle, image_prompt: str) -> str:
    """A science scene's prompt in Campbell Biology's style (CAMPBELL_STYLE) when the course is biology or the scene
    draws a cell's parts; any other prompt as written."""
    if SCIENCE_STYLE not in image_prompt.lower():
        return image_prompt
    if not (BIOLOGY_COURSE.search(bundle.course or "") or CELL_PARTS.search(image_prompt)):
        return image_prompt
    return re.sub(re.escape(SCIENCE_STYLE), CAMPBELL_STYLE, image_prompt, count=1, flags=re.I)


def loose_line(bundle: Bundle, scene: Scene) -> str:
    """The ions and small molecules the video moves on this scene (MARKER_COLOURS), which the picture draws none of:
    on Nov 1 the pump's painter added its own Na⁺ and K⁺ balls, brown and blue, beside the video's purple and green
    ones. Not one that is a part of the scene ("Oxygen", "Water", "ATP"): that part is drawn."""
    kinds = dict.fromkeys(kind for d in bundle.direction if d.scene == scene.id for f in d.flows()
                          if (kind := checks.notation(f.carries)) in checks.MARKER_COLOURS)
    loose = [k for k in kinds if not any(checks.is_kind(s.name, k) for s in scene.structures)]
    if not loose:
        return ""
    return f"Draw no {' or '.join(loose)} particles anywhere: the video adds its own, moving."


def labels_line(scene: Scene, left: list[Structure] | None = None) -> str:
    """What the picture labels: its own parts by the lesson's names, the words the narration says, and nothing else,
    since everything in a room or on a boat has a name a student would not know, and a diagram's parts have textbook
    names the lesson does not use. A science diagram has no room or furniture to name. The hook, an everyday sight,
    labels nothing (NO_LABELS): its parts' names teach nothing (Nov 1: the pump's hook labelled "Body" and "Couch"),
    and the upright hook video cropped them.

    Each name is quoted after what it labels, by the part's own look (its sam_phrase): the brown step "Privileges".
    Given the names alone, the painter guessed where an idea drawn as a thing goes, and put "Privileges" under the
    French Revolution's figures, not on their step; with each one placed, every label landed on its thing and was
    spelled right, on the French Revolution and the light reactions alike (the owner's choice, Nov 2). Each in black
    on a small white box (BOXED): the video dims the parts not yet named, and the painter's coloured lettering looked
    odd dimmed, where black on white still reads (the owner, Nov 2; tested on the same two pictures).

    `left` are the parts the image_prompt does not tag (inline(), worked out when not given): the others are labelled
    in place, so the line lists only these, after "Also label"; none of them, and it lists nothing."""
    if scene.id in EVERYDAY:
        return NO_LABELS
    listed = inline(scene)[1] if left is None else left
    labels = ", ".join(f'the {s.sam_phrase.strip()} "{s.name}"' if s.sam_phrase.strip() else f'"{s.name}"'
                       for s in listed)
    rest = "." if SCIENCE_STYLE in scene.image_prompt.lower() else ": no room, furniture or background objects."
    if len(listed) < len(scene.structures):
        return (f"Also label: {labels}. " if listed else "") + f"{BOXED} Label nothing else{rest} {NO_SLIDE}"
    return f"Label only: {labels}. {BOXED} Label nothing else{rest} {NO_SLIDE}"


def axes_line(axes: Axes) -> str:
    """A graph's axes, each labelled with what it measures (Scene.axes), as the picture labels its parts."""
    named = [f'the {which} one labelled "{name}"' for which, name in (("horizontal", axes.x), ("vertical", axes.y))
             if name]
    return "Its axes are two plain, thin lines" + (", " + " and ".join(named) if named else "") + "."


# What each kind of scene is for, by the ids the bundle prompt gives them (studio/prompts/bundle.md), so the
# helper judges it as what it is. Told only the topic, it refused both round 6 analogies on every try for
# "a dam, not cellular respiration".
SCENE_KINDS = {
    "hook": ("the video's opening: where it can, an everyday sight from the learner's world where the topic "
             "turns up, rather than a diagram of the topic."),
    "overview": "the overview: the whole system at once, each part in its place.",
}
DETAIL = "a close-up of one part of the topic."
# The song's own picture (Scene.song_only), drawn for what its lines sing rather than for the lesson's narration.
SONG = "a picture for the song video: the topic's own parts as the song's lines sing them."
STAGE = ("stage {number} of {count} of {process}: the same subject as its other stages, at the same size and in "
         "the same place, showing what this stage shows.")
# Before the prompt of a scene painted in the overview's look (style_source()), redrawn from the overview's picture.
STYLE_FROM = ("Paint a new picture in the same style as this one: the same colours, line work and shading, with every "
              "part it shares with the new picture drawn the same way. Do not copy its layout. The new picture:")
# Before a later stage's own prompt, when it is redrawn from the first stage's picture (/v1/images/edits).
REDRAW = ("Redraw this picture as a later stage of {process}: the same subject, at the same size and in the same "
          "place, in the same colours and style; change only what this stage shows.")


def check_prompt(bundle: Bundle, scene: Scene) -> str:
    lines = [f"- {s.name}: {s.sam_phrase}" for s in scene.structures]
    stage = Stage.of(bundle, scene)
    kind = (STAGE.format(number=stage.number, count=stage.count, process=stage.process) if stage
            else SONG if scene.song_only else SCENE_KINDS.get(scene.id, DETAIL))
    return (f"Topic: {bundle.topic}" + (f" ({bundle.course})" if bundle.course else "")
            + f"\nThe scene is {kind}"
            + f"\nWhat the scene should show: {scene.described()}"
            + (f"\n{axes_line(scene.axes)} They are asked for, and are no part to find." if scene.axes else "")
            + "\nIt must show each of these:\n" + "\n".join(lines)
            + "\n\nThe lesson's facts:\n" + "\n".join(f"- {f.id}: {f.text}" for f in bundle.facts))


def notices(job: Path, bundle: Bundle) -> dict[str, list[str]]:
    """What the checks found still wrong in the Muse Image pictures the videos use, with no one place in them, as
    notes for the student, by scene: the video sticks each in a corner of the picture rather than pass off a flawed
    picture as right. Each is the short sticky note look() wrote for it, or, in a record made before Nov 2's evening,
    the sentence for the student. None for a drawing (the brain's SVG), which is not checked this way. A note about a
    label the video flags on the picture (flags()) is left out: its sticky note says it there (the owner, Nov 1). One
    about a label that could not be flagged stays. So does any other, but one stuck on the picture where it applies
    (look()'s pinned notes with a box)."""
    found = {}
    for scene in bundle.scenes:
        if not _painted(job, bundle, scene):
            continue
        made = json.loads(_painting(job, scene).read_text(encoding="utf-8"))
        flagged = [str(f.get("text") or "").strip().lower() for f in made.get("flags") or [] if isinstance(f, dict)]
        pins = [p for p in made.get("pinned") or [] if isinstance(p, dict)]
        placed = {" ".join(str(p.get("text") or "").split()) for p in pins if _placed(p)}
        short = {" ".join(str(p.get("text") or "").split()): str(p.get("note") or "").strip()
                 for p in pins if not _placed(p)}
        notes = [short.get(" ".join(n.split())) or n.strip() for n in (made.get("check") or {}).get("notes") or []
                 if n.strip() and not _about_flagged(n, flagged) and " ".join(n.split()) not in placed]
        if notes:
            found[scene.id] = notes
    return found


def _about_flagged(note: str, flagged: list[str]) -> bool:
    """Whether a note is about one of the labels flagged on its picture: it speaks of a label and names one
    ("The 'Leak channel' label is on a teal pump"), as the checker wrote them until Nov 1."""
    text = note.lower()
    return "label" in text and any(label and label in text for label in flagged)


def _made(job: Path, bundle: Bundle, scene: Scene) -> dict | None:
    """art/<scene>.json while the scene's art is the job's painter's picture of it (_painted()), else None."""
    if not _painted(job, bundle, scene):
        return None
    return json.loads(_painting(job, scene).read_text(encoding="utf-8"))


def self_labelled(job: Path, bundle: Bundle) -> list[str]:
    """The scenes whose picture labels its own parts, as paint_prompt() asks every one painted since Oct 31: the
    video draws no labels over them. Not a drawing (the brain's SVG), nor a picture painted before, with its writing
    painted out for the video's labels."""
    return [scene.id for scene in bundle.scenes if ((_made(job, bundle, scene) or {}).get("labelled") is True)]


def flags(job: Path, bundle: Bundle) -> dict[str, list[Flag]]:
    """Each label the check found wrong in a painted picture, by scene, as the box round it in art pixels and its
    sticky note (look()): the video flags each one on the picture with its note beside it. Then each note about one
    place in it, with no ring: a sticky note joined to the place."""
    found = {}
    for scene in bundle.scenes:
        made = []
        record = _made(job, bundle, scene) or {}
        for flag in [*(record.get("flags") or []), *({**pin, "ring": False} for pin in record.get("pinned") or [])]:
            try:
                made.append(Flag(note=flag["note"], ring=flag.get("ring", True), box=Box(
                    x0=flag["left"] * media.ART_WIDTH, y0=flag["top"] * media.ART_HEIGHT,
                    x1=flag["right"] * media.ART_WIDTH, y1=flag["bottom"] * media.ART_HEIGHT)))
            except (KeyError, TypeError, ValueError):  # a record written by hand, or a note with no place (all 0)
                continue
        if made:
            found[scene.id] = made
    return found


def review(job: Path) -> list[dict]:
    """What the checker found in each scene's kept picture and in every try before it, for an admin's review of the
    droplet (studio/review.py): from art/<scene>.json, while it is a picture of the scene as the bundle now has it.
    Whoever painted it, whatever brain is in force now: a drawing removes the record and a painting the drawing, so
    a record with the scene's key is the picture's. A drawn scene was not looked at: its model and check are None."""
    bundle = read(job / "bundle.json", Bundle)
    scenes = []
    for scene in bundle.scenes:
        entry: dict = {"scene": scene.id, "model": None, "kept": None, "check": None, "tries": []}
        try:
            made = json.loads(_painting(job, scene).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            made = None
        if isinstance(made, dict) and made.get("key") == key(bundle, scene) and (job / media.art_path(scene.id)).exists():
            entry.update(model=made.get("model"), kept=made.get("try"), check=made.get("check"), tries=made.get("tries") or [])
        scenes.append(entry)
    return scenes


def _painting(job: Path, scene: Scene) -> Path:
    """art/<scene>.json: what the painted picture of the scene was made from."""
    return job / media.check_path(scene.id)


def _painted(job: Path, bundle: Bundle, scene: Scene) -> bool:
    """Whether the scene's art is the job's painter's picture of the scene as the bundle now has it: painted by it, or
    by the other painter when it failed on the scene (fell_back_from: paint())."""
    try:
        made = json.loads(_painting(job, scene).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not (isinstance(made, dict) and made.get("key") == key(bundle, scene)
            and (job / media.art_path(scene.id)).exists()):
        return False
    by = painter_of(job)
    fell = made.get("fell_back_from")
    return made.get("model") == by.model or (
        isinstance(fell, dict) and fell.get("painter") == by.name and by.name in OTHER
        and made.get("model") == painter_named(OTHER[by.name]).model)
