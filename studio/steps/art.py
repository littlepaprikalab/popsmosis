"""Step 3: the art. Who makes it goes by the brain's provider, as an admin sets it on /admin/studio
(painter(); brain.painter(); docs/PLAN.md, section 5):

    gemini   Gemini 3.1 Flash Image paints each scene (studio/gemini.py), the default for Claude's brain
    muse     Muse Image paints each scene (studio/muse.py), the default for Muse's
    svg      the brain draws each scene as SVG, every structure a named group, so the
             drawing says where each one is and this step writes the masks too. Claude's only

A painted picture is checked by the helper, and the locate step then finds the structures in it
with SAM 3.1 (studio/steps/locate.py), whoever painted it.

Switching makes the art again on the next run, as switching who sings does the song.

The SVG art. One brain call per scene draws it (studio/prompts/art.md), every structure in one group
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

Each kept drawing starts with a key made from the topic and its scene, so art drawn for
an earlier bundle never counts as done: if the bundle is written again, the art is too.
Running the step again redraws every scene. A run that stopped partway (no
locations.json yet) keeps the scenes it already drew, unless their scene changed since.
An answer cut off at the output limit counts as a refused try; any other brain error
fails the step, and the scenes already drawn are kept for the retry.

The painted art. One picture per scene, from Gemini or Muse Image, the scenes painted at the
same time, from a short prompt (paint_prompt()): the scene's image_prompt, which asks for it the
way a teacher asks for a standard textbook diagram, each structure's colour by name, and STYLE.
Live tests (docs/test-rounds.md) found the plain request right 8 times in 12 where the
earlier prompt, with hex codes, a list of shapes and one biology style for every subject,
was right once in 6. The helper looks at each
picture for text, for a structure missing or out of place, and for anything else wrong,
told what kind of scene it is (an analogy is meant not to show the topic's own parts). A
picture with text or a structure missing or out of place is asked for again with what was
wrong, up to MAX_PAINTS pictures in all; anything else wrong is recorded, not repainted
(round 8: a second picture painted for those was no better 9 times in 11). A picture with
text in it is never used (the renderer draws every label); after the last try the scene
keeps the text-free picture with the fewest structures out of place, then the fewest other
problems, and the step fails if there is none.

Writes, per scene, art/<scene>.png (fitted to the art's size on its own background colour), then
art/<scene>.json, last: the key (as above), the model, the prompt, the helper's check of
the kept picture and what was wrong with each try, so the picture counts as done only for
this bundle. A refused picture is kept as art/<scene>.try<n>.png. Removes locations.json: the locate step makes it from these
pictures. Resuming and redrawing work as for the SVG art.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

import skia

from studio import brain, checks, gemini, jobs, ledger, media, muse, svg
from studio.jobs import read, write
from studio.schemas import Bundle, Location, Locations, Scene, SceneLocations, Strict, locations_problems

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "art.md").read_text(encoding="utf-8")
MAX_TRIES = 3  # SVG drawings a scene may take
# Muse Image pictures a scene may take. Two since Oct 28: in round 6 a third picture rarely fixed what
# the second had not, and every one costs a picture and a check.
MAX_PAINTS = 2


@dataclass(frozen=True)
class Painter:
    """Who paints a scene: its name in the settings, its model, and how to ask it for one picture."""

    name: str
    title: str
    model: str
    make: Callable[[Path, str, str], bytes]  # (job, label, prompt) -> the picture's bytes
    declined: type[Exception]  # a picture it would not make: a refused try


def painter(job: Path) -> str:
    """Who makes this job's art: gemini, muse or svg, the admin's setting for the brain in force as the API's last
    claim said (settings.json), else that brain's default."""
    return brain.painter(jobs.studio_settings(job).get("art"))


def painter_of(job: Path) -> Painter:
    """The painter that paints this job's pictures (not for svg). Looked up at each call, so tests can stand in."""
    if painter(job) == "gemini":
        return Painter("gemini", gemini.NAME, gemini.MODEL, lambda *a: gemini.paint(*a), gemini.Declined)
    return Painter("muse", "Muse Image", muse.IMAGE_MODEL, lambda *a: muse.paint(*a), muse.Declined)


def run(job: Path, ask=brain.ask) -> str:
    if painter(job) != "svg":
        return run_painted(job, ask)
    bundle = checks.load(job / "bundle.json")
    resume = located(job) is None
    media.restart(job, media.LOCATIONS)
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
    """The art is there for this bundle, made by the painter the settings name."""
    try:
        bundle = checks.load(job / "bundle.json")
    except (OSError, ValueError):
        return False
    if painter(job) != "svg":
        return all(_painted(job, bundle, scene) for scene in bundle.scenes)
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
        if "svg" in drawn and not _saved(job, bundle, scene):
            return None
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
    _painting(job, scene).unlink(missing_ok=True)  # the art is SVG now, whatever painted it before
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


def key(bundle: Bundle, scene: Scene) -> str:
    """Changes when the topic or the scene does (its prompt, structures, colours).

    Not when the art prompt does: a prompt change should not redraw finished jobs.
    """
    data = json.dumps([bundle.topic, scene.model_dump()], sort_keys=True)
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


# --- the painted art: Gemini 3.1 Flash Image or Muse Image -------------------------------------

# After every scene's prompt. The look is the scene's own (its image_prompt ends with the look its
# kind of textbook gives it: a modern cell biology illustration, an astronomy schematic), so this
# says only what every picture needs. A dark slate blue background, as a modern study infographic
# has: the colours stand out on it, and so do the renderer's white label pills. Code reads the
# background's colour off each picture's edge (media.paper), so any plain colour works.
# Where each part sits is the scene's own prompt's to say (studio/prompts/bundle.md): said here, "every part
# sits inside whatever holds it" wrapped two of round 6's everyday scenes (a dam, a pond) in cell-like bubbles.
LOOK = ("On a plain, flat dark slate blue background, every part well inside the frame, with clear space "
        "around each part for its label.")
# Only in a diagram: in an everyday scene it scattered molecules round round 8's sprinter and through its reservoir.
MOLECULES = "Proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons, coils or clusters of atoms."
NO_TEXT = "No text, labels, letters or numbers anywhere in the picture."
STYLE = f"{LOOK} {MOLECULES} {NO_TEXT}"  # a diagram's
EVERYDAY_STYLE = f"{LOOK} {NO_TEXT}"  # the hook's and the analogy's: everyday things, no molecules
EVERYDAY = ("hook", "analogy")
WORKERS = 4  # scenes painted at once: each one waits on the painter's API, not on this machine

CHECK_PROMPT = """You check one illustration for a short educational video before it is used. The video's \
renderer adds every label and caption itself, so the picture must have none of its own.

List in `text` every piece of writing you can see in the picture, exactly as written: words, letters, \
numbers, labels, captions, legends, watermarks, even a single letter. Leave it empty when there is none.

List in `misplaced` each structure below that is missing from the picture, in the wrong place, or facing \
the wrong way for what the scene should show (an organelle outside what holds it, a protein's head on the \
wrong side of its membrane), each in a few words. A picture with any of these is painted again.

List in `wrong` anything else that would mislead a learner, each in a few words: a part of the wrong shape \
or too hard to make out, or anything scientifically wrong for what the scene should show.

Judge both as the kind of scene it is, against what it should show, not the whole topic: what the scene \
leaves out is not missing. The video animates each flow itself, as dots \
moving between the structures, so a flow or arrow the picture leaves out is not a problem; one drawn the \
wrong way is. List only problems, never what you checked and found right. Leave it empty when the picture \
is right. Do not list matters of taste or style."""


class ArtCheck(Strict):
    """The helper's look at one picture."""

    text: list[str]  # every piece of writing seen in it; empty when there is none
    misplaced: list[str]  # each structure missing, out of place or facing the wrong way; a repaint
    wrong: list[str]  # anything else wrong for the scene, a few words each; recorded, not repainted


@dataclass
class Painting:
    """One picture of a scene, checked."""

    path: Path
    prompt: str
    attempt: int
    check: ArtCheck

    def problems(self) -> list[str]:
        """What the scene is painted again for: writing in the picture, a structure missing or out of place."""
        text = [f"it has writing in it ({', '.join(repr(t) for t in self.check.text)}); it must have none"]
        return (text if self.check.text else []) + self.check.misplaced

    def score(self) -> tuple[int, int]:
        """Lower is better, among pictures without writing."""
        return len(self.check.misplaced), len(self.check.wrong)


def run_painted(job: Path, ask) -> str:
    bundle = checks.load(job / "bundle.json")
    by = painter_of(job)
    resume = not is_done(job)
    media.restart(job, media.LOCATIONS)  # the locate step makes it from these pictures
    (job / "art").mkdir(exist_ok=True)
    todo = [scene for scene in bundle.scenes if not (resume and _painted(job, bundle, scene))]
    kept = len(bundle.scenes) - len(todo)
    # The scenes at the same time; one that fails fails the step once the others are saved.
    results = ledger.parallel(lambda scene: paint(job, bundle, scene, ask), todo, WORKERS)
    painted, tries = len(results), sum(attempts for _, attempts in results)
    left = [f"{scene.id}: {p}" for scene, (best, _) in zip(todo, results)
            for p in best.check.misplaced + best.check.wrong]
    return (f"painted {painted} scene{'s' if painted != 1 else ''} with {by.title} in {tries} "
            f"tr{'ies' if tries != 1 else 'y'}"
            + (f", kept {kept} painted before" if kept else "")
            + (f"; the helper still saw: {'; '.join(left)}" if left else ""))


def paint(job: Path, bundle: Bundle, scene: Scene, ask) -> tuple[Painting, int]:
    """The scene's picture: the best of up to MAX_PAINTS from the job's painter, saved as its art. Returns it and
    how many pictures were asked for."""
    by = painter_of(job)
    for old in (job / "art").glob(f"{media.file_name(scene.id)}.try*.png"):
        old.unlink()
    best, problems, n, tries = None, [], 0, []
    for n in range(1, MAX_PAINTS + 1):
        prompt = paint_prompt(bundle, scene, problems)
        label = f"art {scene.id}" + (f" try {n}" if n > 1 else "")
        try:
            picture = by.make(job, label, prompt)
        except by.declined as e:
            problems = [f"{e}; describe the scene plainly, as a textbook diagram"]
            tries.append({"try": n, "declined": str(e)})
            continue
        path = job / "art" / f"{media.file_name(scene.id)}.try{n}.png"
        path.write_bytes(svg.png(fit(picture)))
        check = ArtCheck(**ask("helper", check_prompt(bundle, scene), ArtCheck, [path], system=CHECK_PROMPT,
                               label=f"art check {scene.id}" + (f" try {n}" if n > 1 else "")))
        painting = Painting(path, prompt, n, check)
        tries.append({"try": n, **check.model_dump()})
        if not check.text and (best is None or painting.score() < best.score()):
            best = painting
        problems = painting.problems()
        if not problems:
            break
    if best is None:
        raise media.StepFailed(f"scene {scene.id}: no usable picture in {MAX_PAINTS} tries: {problems[0]}"
                               + (f" (and {len(problems) - 1} more problems)" if len(problems) > 1 else ""))
    best.path.replace(job / media.art_path(scene.id))
    write(_painting(job, scene), {"key": key(bundle, scene), "model": by.model, "prompt": best.prompt,
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


def paint_prompt(bundle: Bundle, scene: Scene, problems: list[str] = ()) -> str:
    """The scene as a teacher would ask for it, each structure's colour by name (never a hex code: the
    image model reads names), and STYLE (EVERYDAY_STYLE for the hook and the analogy). Short on purpose:
    every extra line was a chance to miss."""
    colours = ", ".join(f"{s.name} {colour_word(s.color_hex)}" for s in scene.structures)
    parts = [scene.image_prompt.strip().rstrip(".") + ".", f"Colours: {colours}."]
    if problems:
        parts.append("The last picture of this scene was refused: " + "; ".join(problems)
                     + ". Make it again with these fixed.")
    parts.append(EVERYDAY_STYLE if scene.id in EVERYDAY else STYLE)
    return " ".join(parts)


# Hue bands in degrees, for colour_word.
HUES = ((10, "red"), (40, "orange"), (70, "yellow"), (160, "green"), (190, "teal"), (250, "blue"),
        (290, "purple"), (335, "pink"), (360, "red"))


def colour_word(color_hex: str) -> str:
    """A colour as everyday words, which image and segmentation models know: "#90BE6D" -> "light green"."""
    r, g, b = (int(color_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    if saturation < 0.15:
        return "white" if value > 0.85 else "black" if value < 0.2 else "grey"
    name = next(word for limit, word in HUES if hue * 360 < limit)
    if name in ("orange", "red") and value < 0.55:
        return "brown"
    if value < 0.4:
        return f"dark {name}"
    if saturation < 0.5 and value > 0.6:
        return f"light {name}"
    return name


# What each kind of scene is for, by the ids the bundle prompt gives them (studio/prompts/bundle.md), so the
# helper judges it as what it is. Told only the topic, it refused both round 6 analogies on every try for
# "a dam, not cellular respiration".
SCENE_KINDS = {
    "hook": ("the video's opening: where it can, an everyday sight from the learner's world where the topic "
             "turns up, rather than a diagram of the topic."),
    "analogy": ("the lesson's analogy: an everyday object standing in for the real thing, each of its parts "
                "in the colour of the structure it stands for. It is meant to show that object, not the "
                "topic's own parts. Judge it as a picture of the object."),
    "overview": "the overview: the whole system at once, each part in its place.",
}
DETAIL = "a close-up of one part of the topic."


def check_prompt(bundle: Bundle, scene: Scene) -> str:
    lines = [f"- {s.name}: {s.sam_phrase}" for s in scene.structures]
    return (f"Topic: {bundle.topic}" + (f" ({bundle.course})" if bundle.course else "")
            + f"\nThe scene is {SCENE_KINDS.get(scene.id, DETAIL)}"
            + f"\nWhat the scene should show: {scene.image_prompt}\nIt must show each of these:\n" + "\n".join(lines))


def _painting(job: Path, scene: Scene) -> Path:
    """art/<scene>.json: what the painted picture of the scene was made from."""
    return job / "art" / f"{media.file_name(scene.id)}.json"


def _painted(job: Path, bundle: Bundle, scene: Scene) -> bool:
    """Whether the scene's art is the job's painter's picture of the scene as the bundle now has it."""
    try:
        made = json.loads(_painting(job, scene).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(made, dict) and made.get("key") == key(bundle, scene) and made.get("model") == painter_of(job).model
            and (job / media.art_path(scene.id)).exists())
