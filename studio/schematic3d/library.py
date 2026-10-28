"""The schematic library: one recipe per machine, each with the shapes fitted to its structure.

A recipe holds everything about one machine, so nothing in the pipeline is written per
machine or per topic: its parts (which chains of the structure entry each one is), the
structures a scene reveals and labels (each one or more parts, with its name, aliases,
colour and label point), the compartments and membrane around it, the camera, and its
motions. Adding a machine is adding recipes/<name>.json and shapes/<name>.json; the
writer's menu (writer_section) and the names a scene may use (offered) come from here.

A motion is a claim the picture makes: which way a part turns, which way ions flow. The
recipe never says which way. It writes the sentences for each way (probes), the
fact-check judges them against the lesson's facts, and a motion is drawn only in a way
the facts support (resolve). A spin's way is asked from both ends of the axle, so a fact
written from either end is understood, and probes that contradict each other turn the
motion off.

The writer is offered only the recipes a person has checked (`checked.by` filled in):
that each part's chains are what the entry names them (structure.entities, kept here so
the check can be read and re-run offline), and that the labels and claims are right.
STUDIO_SCHEMATIC_UNCHECKED=1 offers the unchecked ones too, for trying a recipe locally.

Coordinates are nm in the machine's own frame (studio/schematic3d/fit.py): the axle is
the z axis, the part the axle names `down` lies toward -z.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

HERE = Path(__file__).resolve().parent
ROOTS = [HERE]  # folders holding recipes/ and shapes/; tests add their own
UNCHECKED_ENV = "STUDIO_SCHEMATIC_UNCHECKED"
NAME = re.compile(r"[a-z][a-z0-9_]*")

Vec = tuple[float, float, float]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _hex(value: str) -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{value!r} is not #RRGGBB")
    return value.upper()


Color = Annotated[str, AfterValidator(_hex)]


class Part(Model):
    chains: list[str]
    entities: list[str]  # the entry's names for these chains (structure.entities), as a person checked them
    kind: Literal["capsule", "tube"] = "capsule"
    max_radius: float | None = None  # a capsule's, in nm
    radius: float = 0.55  # a tube's, in nm
    inside: str | None = None  # another part this one sits inside: painted behind all of it, as a rod in a knob
    color: Color | None = None  # only for a part in no structure; the others take their structure's


class Label(Model):
    """Where a structure's label points: a fixed point, a point along one chain, or the middle of one part."""

    at: Vec | None = None
    chain: str | None = None
    part: str | None = None
    along: float = 0.5  # how far along the chain (a tube's path, a capsule's axis)
    offset: Vec = (0.0, 0.0, 0.0)

    @model_validator(mode="after")
    def _one(self) -> Label:
        if sum(x is not None for x in (self.at, self.chain, self.part)) != 1:
            raise ValueError("a label gives exactly one of at, chain or part")
        return self


class Structure(Model):
    name: str  # the label as it reads on screen
    aliases: list[str] = []
    parts: list[str]
    color: Color
    looks: str  # what it looks like in the drawing: the bundle's sam_phrase
    label: Label


class Compartment(Model):
    name: str  # as the renderer writes it on the page, "" for none
    color: Color


class Page(Model):
    """The flat colour behind the machine: one compartment, or one above the membrane and one below."""

    above: Compartment
    below: Compartment | None = None
    split_depth: float = 3.0  # nm behind the axle: where the line between them is drawn


class Membrane(Model):
    parts: list[str]  # the parts that sit in it: it is cut away around them
    head_z: float
    head_radius: float
    spacing: float
    rows: int
    half_width: int
    cut: float


class Camera(Model):
    """Orthographic, looking along +y and a little down. top and bottom (nm, at the axle) fill the art's height."""

    elevation_deg: float
    top: float
    bottom: float
    centre_x: float = 0.0


class Axle(Model):
    ring: str  # fit: the part whose ring of chains defines the axle
    down: str  # fit: the part toward -z
    toward_x: str  # fit: the part toward +x
    up_end: str = ""  # what each end of the axle faces, for the claims about which way a part turns
    down_end: str = ""


class Arc(Model):
    """A red arrow around the axle, showing a spin's way: centred on around_deg, sweep_deg long."""

    radius: float
    z: float
    around_deg: float
    sweep_deg: float


class Spin(Model):
    kind: Literal["spin"]
    id: str
    parts: list[str]
    turn_s: float  # seconds a turn, on screen
    claim: str  # with {direction} and {end}: "While it makes ATP, its rotor turns {direction} as seen from {end}."
    arcs: list[Arc] = []

    @field_validator("claim")
    @classmethod
    def _slots(cls, value: str) -> str:
        if "{direction}" not in value or "{end}" not in value:
            raise ValueError("a spin's claim has {direction} and {end} in it")
        return value


class Particle(Model):
    label: str = ""  # written on it, as "H⁺"
    radius: float


class Crowd(Model):
    """Particles that wait on the side a flow comes from, for each way it may go."""

    forward: list[Vec] = []
    backward: list[Vec] = []


class Flow(Model):
    kind: Literal["flow"]
    id: str
    path: list[Vec]
    forward: str  # the claim when it goes along the path as written
    backward: str  # and the other way
    particle: Particle
    crowd: Crowd = Crowd()
    every_s: float = 2.0  # a particle sets off this often...
    travel_s: float = 2.0  # ...and takes this long along the path
    front: Vec = (0.0, 0.0, 0.0)  # moved this way, so the particles pass in front of the parts
    moves_with: str | None = None  # a spin: the particles move only while it turns


Motion = Annotated[Union[Spin, Flow], Field(discriminator="kind")]


class Checked(Model):
    by: str = ""  # who checked the recipe; "" until a person has
    on: str = ""
    notes: str = ""


class Source(Model):
    pdb: str
    title: str
    url: str
    citation: str
    entities: dict[str, str]  # chain -> the entry's name for it


class Recipe(Model):
    name: str
    title: str
    shows: str  # for the writer: what a scene of it shows
    use_for: str  # for the writer: which lessons it fits, and which it does not
    checked: Checked
    structure: Source
    parts: dict[str, Part]
    axle: Axle
    structures: list[Structure]
    page: Page
    membrane: Membrane | None = None
    camera: Camera
    motions: list[Motion] = []

    @model_validator(mode="after")
    def _consistent(self) -> Recipe:
        problems = recipe_problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def structure_of(self, part: str) -> Structure | None:
        return next((s for s in self.structures if part in s.parts), None)

    def motion(self, motion_id: str) -> Spin | Flow | None:
        return next((m for m in self.motions if m.id == motion_id), None)


def recipe_problems(recipe: Recipe) -> list[str]:
    problems = []
    if not NAME.fullmatch(recipe.name):
        problems.append(f"name {recipe.name!r} is not lower_snake_case")
    parts = recipe.parts
    for field in ("ring", "down", "toward_x"):
        if getattr(recipe.axle, field) not in parts:
            problems.append(f"axle.{field} names no part")
    owners: dict[str, str] = {}
    names = [s.name for s in recipe.structures]
    if len(names) != len(set(names)):
        problems.append("two structures share a name")
    for s in recipe.structures:
        for part in s.parts:
            if part not in parts:
                problems.append(f"{s.name} names unknown part {part}")
            elif part in owners:
                problems.append(f"part {part} is in both {owners[part]} and {s.name}")
            owners[part] = s.name
        label = s.label
        if label.part and label.part not in s.parts:
            problems.append(f"{s.name}'s label is on part {label.part}, which is not one of its parts")
        if label.chain and not any(label.chain in parts[p].chains for p in s.parts if p in parts):
            problems.append(f"{s.name}'s label is on chain {label.chain}, which is not in its parts")
    for name, part in parts.items():
        if name not in owners and not part.color:
            problems.append(f"part {name} is in no structure and has no colour")
        if part.inside and part.inside not in parts:
            problems.append(f"part {name} is inside unknown part {part.inside}")
        for chain in part.chains:
            entity = recipe.structure.entities.get(chain)
            if entity is None:
                problems.append(f"chain {chain} of {name} is not one of the entry's entities")
            elif entity not in part.entities:
                problems.append(f"chain {chain} of {name} is {entity!r} in the entry, not one of {part.entities}")
    if recipe.membrane:
        problems += [f"the membrane names unknown part {p}" for p in recipe.membrane.parts if p not in parts]
        if recipe.page.below is None:
            problems.append("a membrane needs a compartment on each side (page.below)")
    ids = [m.id for m in recipe.motions]
    problems += [f"motion id {i!r} is not lower_snake_case" for i in ids if not NAME.fullmatch(i)]
    if len(ids) != len(set(ids)):
        problems.append("two motions share an id")
    spins = {m.id for m in recipe.motions if m.kind == "spin"}
    for m in recipe.motions:
        if m.kind == "spin":
            problems += [f"spin {m.id} turns unknown part {p}" for p in m.parts if p not in parts]
            if not (recipe.axle.up_end and recipe.axle.down_end):
                problems.append(f"spin {m.id} needs axle.up_end and axle.down_end for its claims")
        elif m.moves_with and m.moves_with not in spins:
            problems.append(f"flow {m.id} moves with {m.moves_with}, which is not a spin")
        elif len(m.path) < 2:
            problems.append(f"flow {m.id} has a path of fewer than two points")
    return problems


# --- the library ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    recipe: Recipe
    shapes: dict  # fit.py's output: {"recipe", "shapes", "ring_radius", "membrane_footprint"}
    digest: str  # changes when the recipe or its shapes do


def _files() -> dict[str, tuple[Path, Path]]:
    found = {}
    for root in ROOTS:
        for path in sorted((root / "recipes").glob("*.json")):
            found.setdefault(path.stem, (path, root / "shapes" / path.name))
    return found


def all_names() -> list[str]:
    """Every recipe in the library, checked or not: what a stored bundle may name."""
    return sorted(_files())


def offered() -> list[str]:
    """The recipes the writer may choose: the checked ones (and the rest with STUDIO_SCHEMATIC_UNCHECKED=1)."""
    unchecked = os.environ.get(UNCHECKED_ENV, "").strip() not in ("", "0")
    return [name for name in all_names() if unchecked or load(name).recipe.checked.by.strip()]


def load(name: str) -> Entry:
    files = _files()
    if name not in files:
        raise KeyError(f"no schematic recipe {name!r}")
    return _load(*files[name])


@functools.cache
def _load(recipe_path: Path, shapes_path: Path) -> Entry:
    recipe_text = recipe_path.read_text(encoding="utf-8")
    recipe = Recipe.model_validate_json(recipe_text)
    if recipe.name != recipe_path.stem:
        raise ValueError(f"{recipe_path.name} names itself {recipe.name!r}")
    shapes_text = shapes_path.read_text(encoding="utf-8")
    shapes = json.loads(shapes_text)
    problems = shapes_problems(recipe, shapes)
    if problems:
        raise ValueError(f"{shapes_path.name}: " + "; ".join(problems))
    digest = hashlib.sha256((recipe_text + "\0" + shapes_text).encode("utf-8")).hexdigest()[:16]
    return Entry(recipe, shapes, digest)


def clear_cache() -> None:
    _load.cache_clear()


def shapes_problems(recipe: Recipe, shapes: dict) -> list[str]:
    """Fitted shapes that do not match the recipe: a chain missing, or a shape for an unknown part."""
    problems = []
    if shapes.get("recipe") != recipe.name:
        problems.append(f"the shapes were fitted for {shapes.get('recipe')!r}")
    fitted = {(s["part"], s["chain"]) for s in shapes.get("shapes", [])}
    wanted = {(name, chain) for name, part in recipe.parts.items() for chain in part.chains}
    problems += [f"no shape for chain {c} of {p}" for p, c in sorted(wanted - fitted)]
    problems += [f"a shape for chain {c} of {p}, which the recipe does not have" for p, c in sorted(fitted - wanted)]
    return problems


class Snapshot(Model):
    """art/<scene>.schematic.json: everything a schematic scene is drawn from, kept with the job, so a
    re-render draws what was checked even after the library changes (studio/steps/art.py writes it)."""

    key: str  # changes when the scene, the recipe or its shapes, or the motions do
    recipe: Recipe
    shapes: dict
    motions: dict[str, int]  # the motions drawn, each its way (resolve)
    claims: list[str]  # what those motions claim, as the fact-check passed it


# --- what the writer reads ----------------------------------------------------------------------


def writer_section() -> str:
    """The bundle prompt's list of the machines a scene can draw as a schematic; "" when none is offered."""
    names = offered()
    if not names:
        return ""
    lines = []
    for name in names:
        recipe = load(name).recipe
        structures = "; ".join(
            s.name + (f" (also {', '.join(repr(a) for a in s.aliases)})" if s.aliases else "") + f": {s.looks}"
            for s in recipe.structures)
        lines.append(f'- `"{name}"`: {recipe.title}. {recipe.shows} Use it for {recipe.use_for}\n'
                     f"  Its structures: {structures}.")
        for motion in recipe.motions:
            lines.append("  " + _motion_menu(recipe, motion))
    return "\n".join(lines)


def _motion_menu(recipe: Recipe, motion: Spin | Flow) -> str:
    if motion.kind == "spin":
        names = list(dict.fromkeys(s.name for part in motion.parts if (s := recipe.structure_of(part))))
        example = motion.claim.format(direction="clockwise or counterclockwise", end=recipe.axle.down_end)
        return (f"The {' and the '.join(names)} turn only if a fact says which way, seen from "
                f"{recipe.axle.up_end} or from {recipe.axle.down_end}, as in: \"{example}\"")
    what = motion.particle.label or motion.id
    return f"The {what} flow only if a fact says which way, as in: \"{motion.forward}\" or \"{motion.backward}\""


# --- motions: the claims, and the ways the facts support ---------------------------------------


DIRECTIONS = (("clockwise", -1), ("counterclockwise", 1))  # seen from the up end; +1 is counterclockwise about +z


def probes(recipe: Recipe) -> list[tuple[str, str]]:
    """Every claim the recipe's motions could make, as (id, sentence): each way, and for a spin from each end."""
    out = []
    for motion in recipe.motions:
        if motion.kind == "spin":
            for end_key, end in (("up", recipe.axle.up_end), ("down", recipe.axle.down_end)):
                for direction, _ in DIRECTIONS:
                    out.append((f"{recipe.name}.{motion.id}.{end_key}-{direction}",
                                motion.claim.format(direction=direction, end=end)))
        else:
            out += [(f"{recipe.name}.{motion.id}.forward", motion.forward),
                    (f"{recipe.name}.{motion.id}.backward", motion.backward)]
    return out


def _sense(probe_id: str) -> int:
    way = probe_id.rsplit(".", 1)[1]
    if way in ("forward", "backward"):
        return 1 if way == "forward" else -1
    end, direction = way.split("-", 1)
    sense = dict(DIRECTIONS)[direction]
    return sense if end == "up" else -sense  # a turn seen from the other end looks the other way round


def resolve(recipe: Recipe, verdicts: dict[str, str]) -> dict[str, int]:
    """The motions to draw and their way (+1 or -1), from the fact-check's verdict on each probe.

    A motion is drawn when the facts support exactly one way of it; none, or both, and it is not."""
    senses: dict[str, set[int]] = {m.id: set() for m in recipe.motions}
    for probe_id, _ in probes(recipe):
        if verdicts.get(probe_id) == "SUPPORTED":
            senses[probe_id.split(".")[1]].add(_sense(probe_id))
    return {motion: next(iter(found)) for motion, found in senses.items() if len(found) == 1}
