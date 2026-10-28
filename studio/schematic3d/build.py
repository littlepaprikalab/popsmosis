"""Build the machine a scene shows from its published 3D structure, for one lesson (docs/SCHEMATIC3D.md).

    python -m studio.schematic3d.build MACHINE "Part,Other part" OUT_DIR [--topic T] [--course C]

draws one without a job, with the brain and the network, into OUT_DIR (schematic.json, still.png).

1. Find. The brain gives search phrases and entries it knows (prompts/schematic_find.md); RCSB
   returns candidates with their subunits, each limited to its first biological assembly (one
   machine, not a crystal's copies); for a machine in a membrane, OPM's placement of each.
2. Choose. The brain picks the entry and the subunits that make each of the scene's structures,
   as the lesson's facts describe them (prompts/schematic_choose.md).
3. Fit. Code downloads the coordinates (OPM's, oriented in the membrane, when OPM placed the
   entry) and fits shapes to them (fit.py).
4. Review. A fresh brain call judges the entry and each match against the lesson's facts, names
   the compartments above and below the membrane from where the parts ended up, and says which
   of them the scene's other structures are ("cytoplasm": below): those are labelled on the page,
   and the particles can travel through them (prompts/schematic_review.md). When it finds something wrong, the choice is made once more
   with its objections, and reviewed again; a part still wrong is drawn but never labelled.
5. Frame. The camera fits the machine with room around it for the labels.

Any step that cannot be done (nothing found, the entry judged wrong, fewer than half the
scene's structures found and right, a source that does not answer) raises BuildFailed, and the
art step draws the scene as an illustration instead.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field

from studio import brain, checks, media
from studio.schemas import Strict
from studio.schematic3d import fit as fitting
from studio.schematic3d import sources
from studio.schematic3d.model import Built, Camera, Compartment, Membrane, Page, Shape, Source, Structure

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
FIND, CHOOSE, REVIEW = ((PROMPTS / f"schematic_{name}.md").read_text(encoding="utf-8") for name in ("find", "choose", "review"))
MAX_CANDIDATES = 12
ROUNDS = 2  # a choice the review objects to is made once more, with the objections
MAX_CHAINS = 150  # more chains than this is not one machine a diagram can label
ELEVATION_DEG = 13.0
FILL_HEIGHT, FILL_WIDTH = 0.78, 0.6  # the machine fills at most this much of the art: the rest is room for labels
ABOVE, BELOW, AROUND = "#F39A3E", "#FCE7A2", "#E4EEF6"  # the compartments' colours, and a page with no membrane


class BuildFailed(Exception):
    """The machine could not be built for this scene; it is drawn as an illustration instead."""


# --- what the brain answers ------------------------------------------------------------------


class Leads(Strict):
    membrane: bool = Field(description="Whether the machine sits in a membrane")
    phrases: list[str] = Field(description="One to three search phrases, in the words of entry titles, no symbols")
    entries: list[str] = Field(description="PDB ids you know show the whole machine, best first; [] if none")


class Pick(Strict):
    structure: str = Field(description="A structure of the scene, by its name as given")
    entities: list[str] = Field(description='The ids of the entities that make it up ("1", "2"); [] if none')


class Choice(Strict):
    entry: str = Field(description='The chosen entry\'s id; "" if no candidate shows the machine')
    why: str
    structures: list[Pick]


class Verdict(Strict):
    structure: str
    verdict: Literal["RIGHT", "WRONG"]
    problem: str = Field(description='WRONG: what is wrong, in one sentence. Otherwise ""')


class Place(Strict):
    structure: str
    side: Literal["above", "below", "neither"]


class Review(Strict):
    entry: Literal["RIGHT", "WRONG"]
    entry_problem: str = Field(description='WRONG: what is wrong, in one sentence. Otherwise ""')
    structures: list[Verdict]
    above: str = Field(description='The compartment above the membrane, in capitals; "" for none')
    below: str = Field(description='The compartment below the membrane, in capitals; "" for none')
    places: list[Place] = Field(description="Each of the other structures: the compartment above the membrane, "
                                            "the one below, or neither")


# --- the build -------------------------------------------------------------------------------


def build(lesson: dict, machine: str, structures: list[tuple[str, str]], ask, folder: Path, key: str = "",
          label: str = "schematic") -> Built:
    """The machine named `machine`, with `structures` ((name, #RRGGBB), the scene's) matched to its parts.

    `lesson` is {"topic", "course", "core_idea", "facts"}; structure files are downloaded into `folder`."""
    names = [name for name, _ in structures]
    asked = {"lesson": lesson, "machine": machine, "structures": names}
    leads = Leads.model_validate(ask("brain", json.dumps(asked, ensure_ascii=False), Leads, system=FIND,
                                     label=f"{label} find"))
    candidates, placements = _candidates(leads)
    offered = [_described(entry, placements.get(entry.id)) for entry in candidates]
    objections: dict | None = None
    for attempt in range(1, ROUNDS + 1):
        again = f" {attempt}" if attempt > 1 else ""
        choice = Choice.model_validate(ask("brain", json.dumps(
            {**asked, "candidates": offered, **({"last_try": objections} if objections else {})}, ensure_ascii=False),
            Choice, system=CHOOSE, label=f"{label} choose{again}"))
        entry = next((c for c in candidates if c.id.upper() == choice.entry.strip().upper()), None)
        if entry is None:
            raise BuildFailed(f"no entry shows {machine}" + (f" ({choice.why})" if choice.why else ""))
        placement = placements.get(entry.id)
        owners, subunits, fitted = _fitted(entry, placement, _picked(choice, entry, names), folder)
        found = [name for name in names if name in subunits]
        others = [name for name in names if name not in subunits]
        review = Review.model_validate(ask("brain", json.dumps({
            "lesson": lesson, "machine": machine,
            "entry": {"id": entry.id, "title": entry.title, "organisms": entry.organisms},
            "membrane": bool(placement),
            "parts": [{"name": name, "subunits": subunits[name], "where": fitted.sides.get(name, "")} for name in found],
            "others": others if placement else [],
        }, ensure_ascii=False), Review, system=REVIEW, label=f"{label} review{again}"))
        wrong = {v.structure: v.problem for v in review.structures if v.verdict == "WRONG" and v.structure in found}
        if attempt == ROUNDS or (review.entry == "RIGHT" and not wrong):
            break
        objections = {"entry": choice.entry, "structures": [p.model_dump() for p in choice.structures],
                      "review": ([f"the entry: {review.entry_problem}"] if review.entry == "WRONG" else [])
                      + [f"{name}: {problem}" for name, problem in wrong.items()]}
    if review.entry == "WRONG":
        raise BuildFailed(f"{entry.id} is not {machine}: {review.entry_problem}")
    kept = [name for name in found if name not in wrong]
    by_name = {checks.fold(name): name for name in others}
    sides: dict[str, str] = {}  # a compartment structure -> its side, one structure a side
    if placement:
        for place in review.places:
            name = by_name.get(checks.fold(place.structure.strip()))
            if name and place.side in ("above", "below") and place.side not in sides.values():
                sides[name] = place.side
    notes = [f"{entry.id}, {entry.title}: {choice.why}"]
    notes += [f"{name} left unlabelled: {problem}" for name, problem in wrong.items()]
    notes += [f"{name} is the compartment {side} the membrane" for name, side in sides.items()]
    notes += [f"{name} not found in {entry.id}" for name in names if name not in found and name not in sides]
    if len(kept) + len(sides) < max(1, math.ceil(len(names) / 2)):
        raise BuildFailed(f"only {len(kept)} of {len(names)} structures found in {entry.id} and judged right; "
                          + "; ".join(notes[1:]))

    colors = dict(structures)
    shapes = [Shape(chain=s["chain"], structure=s["structure"] if s["structure"] in kept else None, kind=s["kind"],
                    points=s["points"], radius=s["radius"]) for s in fitted.shapes]
    if placement:  # a compartment the scene labels is named by its label, not on the page
        labelled = set(sides.values())
        page = Page(above=Compartment(name="" if "above" in labelled else review.above.strip().upper(), color=ABOVE),
                    below=Compartment(name="" if "below" in labelled else review.below.strip().upper(), color=BELOW))
    else:
        page = Page(above=Compartment(name="", color=AROUND))
    camera, half_width = frame(shapes, placement.half_thickness if placement else None, set(sides.values()))
    membrane = (Membrane(name=placement.membrane, half_thickness=placement.half_thickness,
                         footprint=fitted.footprint, half_width=half_width) if placement else None)
    return Built(key=key, machine=machine,
                 source=Source(pdb=entry.id, title=entry.title, organisms=entry.organisms, method=entry.method,
                               resolution=entry.resolution, oriented_by="OPM" if placement else "principal axes"),
                 structures=[Structure(name=name, color=colors[name], chains=sorted(c for c, o in owners.items() if o == name),
                                       subunits=subunits[name]) for name in kept]
                 + [Structure(name=name, color=colors[name], chains=[], subunits=[], kind="compartment", side=side)
                    for name, side in sides.items()],
                 shapes=shapes, page=page, membrane=membrane, camera=camera, notes=notes)


def _fitted(entry: sources.Entry, placement: sources.Placement | None, picked: dict[str, list[sources.Entity]],
            folder: Path) -> tuple[dict[str, str | None], dict[str, list[str]], fitting.Fitted]:
    """The chains drawn, each with its structure (or None); each structure's subunits; and the fitted shapes."""
    try:
        path = sources.coordinates(entry.id, folder, oriented=placement is not None)
        import gemmi

        structure = gemmi.read_structure(str(path))
    except sources.SourceError as e:
        raise BuildFailed(f"the coordinates of {entry.id} could not be fetched: {e}") from e
    except (RuntimeError, ValueError) as e:
        raise BuildFailed(f"the coordinates of {entry.id} do not read: {e}") from e
    in_file = {chain.name for chain in structure[0]}
    machine_chains = {c for entity in entry.entities for c in entity.chains}
    drawn = sorted(machine_chains & in_file) if placement else [c for c in entry.assembly if c in in_file]
    owners: dict[str, str | None] = {c: None for c in drawn}
    subunits: dict[str, list[str]] = {}
    for name, entities in picked.items():
        for entity in entities:
            for chain in entity.chains:
                if chain in owners:
                    owners[chain] = name
                    subunits.setdefault(name, [])
                    if entity.description not in subunits[name]:
                        subunits[name].append(entity.description)
    try:
        return owners, subunits, fitting.fit(structure, owners, placement.half_thickness if placement else None)
    except ValueError as e:
        raise BuildFailed(f"{entry.id} could not be fitted: {e}") from e


def _candidates(leads: Leads) -> tuple[list[sources.Entry], dict[str, sources.Placement]]:
    """The entries the leads find, in order, each one machine of fewer than MAX_CHAINS chains; and for a
    machine in a membrane, OPM's placement of each (only placed ones are kept when any is)."""
    ids: list[str] = [i.strip().upper() for i in leads.entries[:5] if i.strip()]
    try:
        for phrase in leads.phrases[:3]:
            ids += sources.search(phrase, rows=6)
        ids = list(dict.fromkeys(ids))
        entries = [e for e in sources.details(ids) if 0 < len(e.assembly) <= MAX_CHAINS][:MAX_CANDIDATES]
        placements = {}
        if leads.membrane:
            for e in entries:
                found = sources.membrane(e.id)
                if found:
                    placements[e.id] = found
    except sources.SourceError as e:
        raise BuildFailed(f"the structure sources did not answer: {e}") from e
    if leads.membrane and placements:
        entries = [e for e in entries if e.id in placements]
    if not entries:
        raise BuildFailed("no structure found for " + ", ".join(repr(p) for p in leads.phrases or leads.entries))
    return entries, placements


def _described(entry: sources.Entry, placement: sources.Placement | None) -> dict:
    chains = set(entry.assembly)
    return {"id": entry.id, "title": entry.title, "organisms": entry.organisms, "method": entry.method,
            "resolution": entry.resolution,
            "membrane": f"{placement.membrane} (placed by OPM)" if placement else None,
            "entities": [{"id": e.id, "name": e.description, "kind": e.kind,
                          "chains": [c for c in e.chains if c in chains]}
                         for e in entry.entities if any(c in chains for c in e.chains)]}


def _picked(choice: Choice, entry: sources.Entry, names: list[str]) -> dict[str, list[sources.Entity]]:
    """Each of the scene's structures and the entities chosen for it: unknown names and ids dropped, and an
    entity given to two structures kept by the first."""
    by_name = {checks.fold(name): name for name in names}
    entities = {e.id: e for e in entry.entities}
    taken: set[str] = set()
    out: dict[str, list[sources.Entity]] = {}
    for pick in choice.structures:
        name = by_name.get(checks.fold(pick.structure.strip()))
        if name is None:
            continue
        mine = [entities[i.strip()] for i in pick.entities if i.strip() in entities and i.strip() not in taken]
        taken |= {e.id for e in mine}
        if mine:
            out.setdefault(name, []).extend(mine)
    return out


COMPARTMENT_ROOM = 6.0  # nm of a labelled compartment kept in view beyond the membrane: room for its label and the particles


def frame(shapes: list[Shape], half_thickness: float | None, labelled: set[str] = frozenset()) -> tuple[Camera, int]:
    """The camera that shows the whole machine with room for labels around it, and how far (in heads)
    the membrane must reach to the sides to fill the view. `labelled` holds the sides ("above", "below")
    whose compartment the scene labels: more of each of those stays in view."""
    elev = math.radians(ELEVATION_DEG)
    xs, us = [], []
    for shape in shapes:
        pts = np.array(shape.points, float)
        u = pts[:, 2] * math.cos(elev) + pts[:, 1] * math.sin(elev)
        xs += [pts[:, 0].min() - shape.radius, pts[:, 0].max() + shape.radius]
        us += [u.min() - shape.radius, u.max() + shape.radius]
    if half_thickness is not None:  # some of each compartment shows above and below the membrane
        room = {side: COMPARTMENT_ROOM if side in labelled else 2.5 for side in ("above", "below")}
        us += [-(half_thickness + room["below"]) * math.cos(elev), (half_thickness + room["above"]) * math.cos(elev)]
    aspect = media.ART_WIDTH / media.ART_HEIGHT
    span = max((max(us) - min(us)) / FILL_HEIGHT, (max(xs) - min(xs)) / (FILL_WIDTH * aspect))
    middle = (max(us) + min(us)) / 2 / math.cos(elev)
    height = span / math.cos(elev)
    camera = Camera(elevation_deg=ELEVATION_DEG, top=round(middle + height / 2, 3), bottom=round(middle - height / 2, 3),
                    centre_x=round((max(xs) + min(xs)) / 2, 3))
    half_width = math.ceil(span * aspect * 1.25 / 2 / 0.95) + 3  # the renderer's view is wider than the art
    return camera, half_width


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m studio.schematic3d.build", description=__doc__.split("\n\n")[1])
    parser.add_argument("machine")
    parser.add_argument("structures", help="the parts to label, comma-separated")
    parser.add_argument("out", type=Path)
    parser.add_argument("--topic", default="")
    parser.add_argument("--course", default="")
    args = parser.parse_args()
    palette = ["#E07A2F", "#3366CC", "#2E8B57", "#B5487A", "#8E6BBF", "#C9A227"]
    parts = [(name.strip(), palette[i % len(palette)]) for i, name in enumerate(args.structures.split(",")) if name.strip()]
    lesson = {"topic": args.topic or args.machine, "course": args.course, "core_idea": "", "facts": []}
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        built = build(lesson, args.machine, parts, brain.ask, args.out)
    except BuildFailed as e:
        sys.exit(f"not built: {e}")
    (args.out / "schematic.json").write_text(built.model_dump_json(indent=1), encoding="utf-8")
    from studio.schematic3d.draw import Machine

    pixels = Machine(built).still()
    import skia

    rgba = np.dstack([pixels, np.full(pixels.shape[:2], 255, np.uint8)])
    skia.Image.fromarray(rgba, colorType=skia.kRGBA_8888_ColorType).save(str(args.out / "still.png"), skia.kPNG)
    print("\n".join([f"{built.machine}: {built.source.pdb} ({built.source.oriented_by})"] + built.notes))
