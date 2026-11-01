"""Step 7b: the arrows. Each hop's arrow pointed on its painting by the helper, and every arrow looked at as drawn.

Every arrow in a video shows something moving between places. A crossing goes all the way through one part, from the
space on one side to the space on the other (Na⁺ from inside the cell, through the pump, to outside): the renderer
draws it from the part's outline, and does it well. A hop is a step between two neighbouring places (the inside of the
cell into the pump, ATP into the pump, the blood up to the clot): placed from the parts' rough outlines in a painting
(SAM's masks), it came out sideways, as a stub, or in an odd spot. In a pilot on Nov 1 the helper, shown a painting
and a gridded copy of it, placed 10 of 33 arrows clearly better than the code, 18 about as well, and refused one no
straight arrow can show; so the helper points the hops (the owner, Nov 1).

1. Point. For each painted scene (Timeline.self_labelled) with hops (Renderer.hops()), one helper call: the painting and
   a gridded copy, both fitted into POINT_SIZE, and its hops, each with what moves, from where, through what, to where,
   and the line it plays under. The helper gives each its tail and head, or says the picture cannot show it. A drawing
   (the brain's SVG) has exact outlines: code places its arrows, as it places every crossing.
2. Look. Each hop's arrow the gate lets through (Renderer._wrong_arrow), drawn alone in a still at LOOK_SIZE at a
   moment it shows (Renderer.arrow_time()), and one helper call each: does it show what moves going from where to
   where, as the line says? One it says does not is left out, as the gate leaves one out.

Writes arrows.json (schemas.Arrows), which the renderer reads: a pointed hop is drawn straight from its tail to its
head, as any arrow is drawn (_draw_handoffs); one left out is listed, with why, in the render step's left_out.json.
With no arrows.json (a job made before the step), code places every arrow, as it did.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from studio import brain, ledger, media
from studio.jobs import read, write
from studio.media import StepFailed
from studio.schemas import Arrows, Locations, PlacedArrow, Point, Strict, Timeline, timeline_problems

POINT_SIZE = (960, 540)  # the painting and its grid are fitted into this: about w x h / 750 image tokens each
LOOK_SIZE = (960, 540)  # a still of one arrow
GRID = 50  # pixels of the fitted picture between grid lines, numbered every second line
WORKERS = 16  # calls at once: a call waits on the helper, never on another

POINT_PROMPT = """You place arrows on a textbook illustration for a short science lesson video. Each arrow shows \
something moving in the picture (an ion, a molecule, an electron, a fluid), and the video draws it as a straight arrow \
from where you say it starts to where you say it ends, with what moves riding its head.

You get the picture, then the same picture with a grid: thin lines every 50 pixels, numbered every 100 in red along \
its top and left edges. The numbers are pixel coordinates in this picture: x to the right from 0, y down from 0. Give \
every point in those coordinates.

Each arrow comes with what moves, the place it comes from, any part it passes through, the place it goes, whether it \
is stopped where it arrives, and the line said or sung while it shows. The places are named as the lesson names them: \
find each in the picture by what it is (the picture may label it in other words, "Cytoplasm" for "Inside the cell").

Place each arrow so a learner reads it rightly:
- It starts in or at the place it comes from and ends in or at the place it goes, through any part it passes through.
- At a part (a protein, an organelle, a molecule), it starts or ends at the part's edge or just inside it, on the \
side it leaves or enters by.
- In an open space (a fluid, a region), it starts or ends just inside the space, near the part it leaves or enters, \
not across the picture.
- Something crossing a membrane goes across the membrane, never along it.

If the picture does not show where an arrow should go (a place is missing, or one straight arrow cannot show the move \
truthfully, as for a path that turns through several parts), say it cannot be drawn rather than guess: drawable \
false, its points 0.

For each arrow give its id, drawable, its start and end, and why: one short sentence on where it starts and ends, in \
the picture's terms."""

LOOK_PROMPT = """You check one arrow in a still from a short science lesson video, before the video is made. The arrow \
shows something moving in the picture, and a learner who sees it should read the move the lesson means: what moves, \
from where, to where.

Say it shows the move when the arrow starts in or at the place it comes from, ends in or at the place it goes, and \
runs that way, through any part it passes through. Say it does not when it starts or ends somewhere else, runs the \
other way, runs along a membrane it should cross, or a learner could not tell what it shows. The places are named as \
the lesson names them; the picture may label them in other words. Judge only this arrow, not the rest of the picture.

Answer shows, and why: one short sentence on where the arrow starts and ends in the picture."""


class PointedArrow(Strict):
    """Where the helper puts one arrow, in the fitted picture's pixels."""

    id: str
    drawable: bool
    start: Point
    end: Point
    why: str


class Pointing(Strict):
    """The helper's arrows on one painting."""

    arrows: list[PointedArrow]


class Look(Strict):
    """The helper's look at one arrow as drawn."""

    shows: bool
    why: str


def key(scene: str, flow) -> tuple:
    """A flow's arrow, as the renderer keys it."""
    return scene, tuple(flow.path), flow.label, flow.blocked


def move(flow) -> str:
    """What the arrow shows, in words: 'Na⁺ moves from "Inside the cell" to "Sodium-potassium pump"'."""
    what = flow.label or "something"
    first, *through, last = flow.path
    via = f" through {', '.join(f'“{n}”' for n in through)}" if through else ""
    if flow.blocked:
        return f"{what} moves from “{first}”{via} toward “{last}”, and is stopped there"
    return f"{what} moves from “{first}”{via} to “{last}”"


def gridded(picture: Image.Image) -> Image.Image:
    """The picture with a grid every GRID pixels, numbered every second line along its top and left edges."""
    grid = picture.copy()
    draw = ImageDraw.Draw(grid)
    font = ImageFont.load_default(size=13)
    for x in range(0, picture.width, GRID):
        draw.line([(x, 0), (x, picture.height)], fill=(255, 60, 60) if x % (2 * GRID) == 0 else (255, 150, 150))
        if x % (2 * GRID) == 0:
            draw.text((x + 2, 1), str(x), fill=(200, 0, 0), font=font, stroke_width=2, stroke_fill="white")
    for y in range(0, picture.height, GRID):
        draw.line([(0, y), (picture.width, y)], fill=(255, 60, 60) if y % (2 * GRID) == 0 else (255, 150, 150))
        if y % (2 * GRID) == 0 and y:
            draw.text((2, y + 1), str(y), fill=(200, 0, 0), font=font, stroke_width=2, stroke_fill="white")
    return grid


def point(job: Path, scene: str, hops: list[tuple], names: list[str], folder: Path, ask) -> dict[tuple, PlacedArrow]:
    """One helper call for a painting's hops ((flow, line) each); each hop it answers, placed in art pixels."""
    picture = Image.open(job / media.art_path(scene)).convert("RGB")
    scale = min(POINT_SIZE[0] / picture.width, POINT_SIZE[1] / picture.height)
    small = picture.resize((round(picture.width * scale), round(picture.height * scale)), Image.LANCZOS)
    shown = [folder / f"{media.file_name(scene)}.png", folder / f"{media.file_name(scene)}.grid.png"]
    small.save(shown[0])
    gridded(small).save(shown[1])
    listed = "\n".join(f"a{n}: {move(flow)}. The line: “{line}”" for n, (flow, line) in enumerate(hops, 1))
    answer = Pointing(**ask(
        "helper", f"The picture is {small.width} x {small.height} pixels. The lesson names these parts and places in "
                  f"it: {', '.join(names)}.\n\nThe arrows to place:\n{listed}",
        Pointing, shown, system=POINT_PROMPT, label=f"arrows point {scene}"))

    def art(q: Point) -> Point:
        return Point(x=min(max(q.x / scale, 0.0), picture.width), y=min(max(q.y / scale, 0.0), picture.height))

    placed = {}
    for n, (flow, _) in enumerate(hops, 1):
        found = next((a for a in answer.arrows if a.id.strip().lower() == f"a{n}"), None)
        if found is None:  # not answered: code places it
            continue
        placed[key(scene, flow)] = PlacedArrow(
            scene=scene, path=flow.path, carries=flow.label, blocked=flow.blocked, placed=found.why,
            **({"start": art(found.start), "end": art(found.end)} if found.drawable else
               {"left_out": f"the picture cannot show it: {found.why}"}))
    return placed


def look(still: Path, scene: str, flow, line: str, ask) -> Look:
    """One helper call on a still of the flow's arrow alone: does it show the move?"""
    if flow.blocked:
        drawn = "a dashed line in its colour ending in a cross where it is stopped"
    else:
        drawn = "a dotted track in its colour, with a short solid stretch gliding along it toward where it goes"
    if not flow.label:
        rider = "" if flow.blocked else ", ending in a chevron"
    else:
        rider = f", and a particle marked “{flow.label}” " + ("waiting before the cross" if flow.blocked
                                                             else "at the front of that stretch")
    return Look(**ask("helper", f"The line said or sung while it shows: “{line}”\n\nThe arrow: {drawn}{rider}.\n\n"
                                f"Does it show this: {move(flow)}?",
                      Look, [still], system=LOOK_PROMPT, label=f"arrows look {scene} {flow.label} {' > '.join(flow.path)}"))


def run(job: Path, ask=brain.ask) -> str:
    from studio.render.renderer import Renderer, RenderError

    media.restart(job, media.ARROWS)
    locations = read(job / media.LOCATIONS, Locations)
    timelines = [read(job / media.timeline_path(video), Timeline) for video in media.videos(job)]
    for video, timeline in zip(media.videos(job), timelines):
        problems = timeline_problems(timeline, locations)
        if problems:
            raise StepFailed(f"the {video} timeline: " + "; ".join(problems))
    try:
        found: dict[tuple, tuple[str, object, str]] = {}
        for timeline in timelines:
            for scene, flow, line in Renderer(job, timeline, locations=locations, arrows=Arrows(arrows=[])).hops():
                found.setdefault(key(scene, flow), (scene, flow, line))
    except RenderError as e:
        raise StepFailed(str(e)) from e
    painted = {scene for timeline in timelines for scene in timeline.self_labelled}
    scenes: dict[str, list[tuple]] = {}
    for scene, flow, line in found.values():
        if scene in painted:
            scenes.setdefault(scene, []).append((flow, line))

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        placed: dict[tuple, PlacedArrow] = {}
        for answer in ledger.parallel(
                lambda item: point(job, item[0], item[1], [s.name for s in locations.scene(item[0]).structures],
                                   folder, ask), scenes.items(), WORKERS):
            placed.update(answer)
        for k, (scene, flow, _) in found.items():
            placed.setdefault(k, PlacedArrow(scene=scene, path=flow.path, carries=flow.label, blocked=flow.blocked))

        # Each arrow the gate lets through, alone in a still at a moment it shows, from the first video it is in.
        stills, seen = [], set()
        for timeline in timelines:
            renderer = Renderer(job, timeline, locations=locations, arrows=Arrows(arrows=list(placed.values())))
            out = {(a["scene"], tuple(a["path"]), a["carries"]) for a in renderer.left_out}
            for scene, flow, _ in renderer.hops():
                k = key(scene, flow)
                if k in seen or k[:3] in out:
                    continue
                seen.add(k)
                renderer.only = k
                pixels = renderer.frame(renderer.arrow_time(scene, flow))
                path = folder / f"look-{len(stills) + 1}.png"
                Image.fromarray(pixels[..., [2, 1, 0]]).resize(LOOK_SIZE, Image.LANCZOS).save(path)
                stills.append((path, k))
            del renderer
        looks = ledger.parallel(lambda item: look(item[0], found[item[1]][0], found[item[1]][1], found[item[1]][2], ask),
                                stills, WORKERS)
    for (_, k), seen_as in zip(stills, looks):
        placed[k] = placed[k].model_copy(update={
            "seen": seen_as.why, **({} if seen_as.shows else {"left_out": f"as drawn it does not show it: {seen_as.why}"})})

    write(job / media.ARROWS, Arrows(arrows=[placed[k] for k in found]))
    pointed = sum(1 for a in placed.values() if a.start is not None)
    refused = sum(1 for a in placed.values() if a.placed and a.start is None)
    unseen = sum(1 for look_ in looks if not look_.shows)
    return (f"{pointed} hop(s) pointed on {len(scenes)} painting(s), {refused} the picture cannot show; "
            f"{len(looks)} arrow(s) looked at, {unseen} left out as drawn wrong ({media.ARROWS})")


def is_done(job: Path) -> bool:
    """arrows.json is there, newer than the locations and every timeline it was made from."""
    made = job / media.ARROWS
    if not made.exists():
        return False
    sources = [job / media.LOCATIONS, *(job / media.timeline_path(video) for video in media.videos(job))]
    return all(s.exists() and s.stat().st_mtime <= made.stat().st_mtime for s in sources)
