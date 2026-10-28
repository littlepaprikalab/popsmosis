"""A machine as built for one scene: what art/<scene>.schematic.json holds (studio/schematic3d/build.py).

Everything the drawing needs, so a re-render draws exactly what was built, without the network
or the brain: the structure entry it came from, the scene's structures and the chains that make
each one, the fitted shapes, the page and membrane around them, and the camera. Coordinates are
nm in the machine's frame: z is the membrane's normal (or the machine's long axis), the
membrane's middle at z = 0, the side named `below` toward -z.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Vec = tuple[float, float, float]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Shape(Model):
    chain: str
    structure: str | None  # the scene's structure it belongs to; None: part of the machine, never labelled
    kind: Literal["capsule", "tube"]
    points: list[Vec]  # a capsule's two cap centres, or a tube's path
    radius: float  # nm


class Structure(Model):
    name: str  # the scene's structure, as the bundle names it
    color: str  # #RRGGBB, the bundle's
    chains: list[str]
    subunits: list[str]  # what the entry calls those chains
    # A part of the machine, or a compartment beside its membrane ("cytoplasm"), which the
    # scene labels and the particles can travel through; it has no chains.
    kind: Literal["part", "compartment"] = "part"
    side: Literal["above", "below"] | None = None  # a compartment's side of the membrane


class Compartment(Model):
    name: str  # written in its corner of the page (until a label names it); "" for none
    color: str


class Page(Model):
    above: Compartment  # the whole page when there is no membrane
    below: Compartment | None = None


class Membrane(Model):
    name: str  # OPM's: "Thylakoid", "Eukaryo. plasma", ...
    half_thickness: float  # nm, of the hydrocarbon core
    footprint: list[tuple[float, float]]  # x, y of the machine where it crosses the membrane: cut away around it
    head_radius: float = 0.44
    spacing: float = 0.95
    rows: int = 9
    half_width: int = 44
    cut: float = 0.95


class Camera(Model):
    """Orthographic, looking along +y and a little down; `top` and `bottom` fill the art's height."""

    elevation_deg: float
    top: float
    bottom: float
    centre_x: float


class Source(Model):
    pdb: str
    title: str
    organisms: list[str]
    method: str
    resolution: float | None
    oriented_by: Literal["OPM", "principal axes"]


class Built(Model):
    key: str  # changes when the scene or the topic does
    machine: str  # the scene's `schematic`, as the writer named the machine
    source: Source
    structures: list[Structure]  # the scene's structures that were found in the entry and passed the review
    shapes: list[Shape]
    page: Page
    membrane: Membrane | None
    camera: Camera
    notes: list[str]  # what the build decided, for the record: the entry chosen and why, what was left out
