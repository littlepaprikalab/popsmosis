"""A schematic scene without the network or the brain, for tests and for looking at frames.

    python -m studio.tests.sample_schematic jobs/sample-3d [--stills OUT_DIR]

A made-up membrane machine shaped like ATP synthase (a ring of eight chains in the membrane, a
rod, a knob of six chains below and a long stalk beside them), written as a structure file
oriented as OPM orients one; stand-ins for the structure sources (FakeSources) and for the
brain's three schematic calls (FakeBrain); and the sample job (sample_media.py: the light
reactions, with tones for audio) with its hook drawn as that machine, built by the real art
step and timed by the real timeline step.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

from studio import checks, jobs, media
from studio.schematic3d import sources
from studio.schemas import Bundle, Locations
from studio.steps import art, timeline
from studio.tests import sample_media
from studio.tests.sample import bundle_data

MACHINE = "toy ATP synthase"
PDB = "1TOY"
RING = "GHIJKLMN"
KNOB = "ABCDEF"
PARTS = {"Rotor": ("1", "Toy subunit c"), "Internal rod": ("2", "Toy gamma chain"),
         "Catalytic knob": ("3", "Toy subunit alpha"), "Stator": ("4", "Toy subunit b")}
COLORS = {"Rotor": "#E07A2F", "Internal rod": "#3366CC", "Catalytic knob": "#2E8B57", "Stator": "#B5487A"}


def toy_chains() -> dict[str, list[tuple[float, float, float]]]:
    """Backbone points in Å, membrane in the plane z = 0, the inner side toward -z, as OPM puts them."""
    chains = {}
    for k, name in enumerate(RING):
        a = 2 * math.pi * k / len(RING)
        chains[name] = [(28 * math.cos(a), 28 * math.sin(a), h) for h in np.linspace(-18, 18, 40)]
    chains["g"] = [(1.5 * math.cos(h), 1.5 * math.sin(h), z) for h, z in zip(np.linspace(0, 6, 120), np.linspace(-15, -95, 120))]
    for k, name in enumerate(KNOB):
        a = 2 * math.pi * k / len(KNOB)
        cx, cy = 30 * math.cos(a), 30 * math.sin(a)
        rng = np.random.default_rng(k)
        chains[name] = [(cx + 9 * rng.normal(), cy + 9 * rng.normal(), -110 + 14 * rng.normal()) for _ in range(500)]
    chains["b"] = [(62 + 2 * math.sin(z / 9), 4.0, z) for z in np.linspace(10, -140, 160)]
    return chains


def write_structure(path: Path) -> Path:
    import gemmi

    structure = gemmi.Structure()
    model = gemmi.Model("1")
    for name, points in toy_chains().items():
        chain = gemmi.Chain(name)
        for i, point in enumerate(points, 1):
            residue = gemmi.Residue()
            residue.name, residue.seqid = "ALA", gemmi.SeqId(i, " ")
            atom = gemmi.Atom()
            atom.name, atom.element, atom.pos = "CA", gemmi.Element("C"), gemmi.Position(*point)
            residue.add_atom(atom)
            chain.add_residue(residue)
        model.add_chain(chain)
    structure.add_model(model)
    structure.setup_entities()
    path.parent.mkdir(parents=True, exist_ok=True)
    structure.write_pdb(str(path))
    return path


def toy_entry() -> sources.Entry:
    chains = {"1": list(RING), "2": ["g"], "3": list(KNOB), "4": ["b"]}
    entities = [sources.Entity(id=i, description=name, chains=chains[i], kind="protein", organisms=["Toy organism"])
                for i, name in PARTS.values()]
    return sources.Entry(id=PDB, title="A toy rotary machine", method="MADE UP", resolution=3.0, entities=entities,
                         assembly=[c for i in ("1", "2", "3", "4") for c in chains[i]])


class FakeSources:
    """Stands in for studio/schematic3d/sources.py: one machine, placed in its membrane."""

    def __init__(self, oriented: bool = True, fail: str = ""):
        self.oriented, self.fail = oriented, fail
        self.calls: list[str] = []

    def install(self, monkeypatch) -> FakeSources:
        for name in ("search", "details", "membrane", "coordinates"):
            monkeypatch.setattr(sources, name, getattr(self, name))
        return self

    def search(self, phrase: str, rows: int = 8) -> list[str]:
        self.calls.append(f"search {phrase}")
        if self.fail == "search":
            raise sources.SourceError("search.rcsb.org: unreachable")
        return [PDB]

    def details(self, ids: list[str]) -> list[sources.Entry]:
        self.calls.append("details " + ",".join(ids))
        return [toy_entry()] if PDB in ids else []

    def membrane(self, pdb: str) -> sources.Placement | None:
        self.calls.append(f"membrane {pdb}")
        return sources.Placement(membrane="Toy membrane", half_thickness=1.5) if self.oriented else None

    def coordinates(self, pdb: str, folder: Path, oriented: bool) -> Path:
        self.calls.append(f"coordinates {pdb} {'OPM' if oriented else 'RCSB'}")
        return write_structure(folder / f"{pdb.lower()}.pdb")


class FakeBrain:
    """Stands in for brain.ask in the build's three calls (label "... find", "... choose", "... review");
    `then` answers anything else (the art step's illustrations)."""

    def __init__(self, choose: dict | None = None, review: dict | None = None, then=None):
        self.choose = choose or {"entry": PDB, "why": "the whole toy machine",
                                 "structures": [{"structure": name, "entities": [i]} for name, (i, _) in PARTS.items()]}
        self.review = review or {"entry": "RIGHT", "entry_problem": "",
                                 "structures": [{"structure": name, "verdict": "RIGHT", "problem": ""} for name in PARTS],
                                 "above": "LUMEN", "below": "STROMA"}
        self.then = then
        self.labels: list[str] = []
        self.prompts: dict[str, str] = {}

    def __call__(self, role, prompt, schema=None, images=(), *, system=None, label=None):
        self.labels.append(label)
        self.prompts[label] = prompt
        if label.endswith(" find"):
            return {"membrane": True, "phrases": ["toy rotary synthase"], "entries": []}
        if label.endswith(" choose"):
            return self.choose
        if label.endswith(" review"):
            return self.review
        return self.then(role, prompt, schema, images, system=system, label=label)


def bundle() -> Bundle:
    """The sample bundle with its hook drawn as the toy machine, the song opening on it."""
    data = bundle_data()
    hook = next(s for s in data["scenes"] if s["id"] == "hook")
    hook.update(schematic=MACHINE, image_prompt="The toy machine in its membrane.",
                structures=[{"name": name, "aliases": [], "sam_phrase": name.lower(), "color_hex": COLORS[name]}
                            for name in PARTS])
    lines = {"v1-1": ("Protons turn the rotor", ["rotor"]),
             "v1-2": ("The internal rod spins inside the catalytic knob", ["internal rod", "catalytic knob"])}
    for line in data["lyrics"]:
        if line["id"] in lines:
            line["text"], line["names"] = lines[line["id"]]
            line["fact_id"] = "f3"
    narration = data["narration"][0]
    narration.update(text="The rotor of ATP synthase turns as protons flow through it.", names=["rotor"], fact_id="f3")
    data["frq"]["model_answer_steps"][1] = "The flow of protons turns the rotor."  # the exam question reviews the hook
    for d in data["direction"]:
        if d["line_id"] in ("v1-1", "n1"):
            d["scene"] = "hook"
        if d["line_id"] == "v1-2":
            d.update(scene="hook", camera={"target": "rotor", "zoom": 2.0}, spotlight="internal rod")
    the_bundle, _ = checks.review(Bundle.model_validate(data))
    return the_bundle


def write_job(job: Path, monkeypatch=None) -> Path:
    """The sample job with its hook built by the art step (with the stand-ins) and its timelines made."""
    sample_media.write_job(job, art=True, audio=True)
    the_bundle = bundle()
    jobs.write(job / "bundle.json", the_bundle)
    for kind, timing in sample_media.timings(the_bundle).items():
        jobs.write(job / media.timing_path(kind), timing)
    hook = next(s for s in the_bundle.scenes if s.id == "hook")
    if monkeypatch is None:
        import pytest

        with pytest.MonkeyPatch.context() as patch:
            FakeSources().install(patch)
            located = art.draw_schematic(job, the_bundle, hook, FakeBrain())
    else:
        FakeSources().install(monkeypatch)
        located = art.draw_schematic(job, the_bundle, hook, FakeBrain())
    # The hook's own art and masks from sample_media are an ellipse; the schematic replaces them.
    scenes = [located if s.scene == "hook" else s for s in jobs.read(job / media.LOCATIONS, Locations).scenes]
    jobs.write(job / media.LOCATIONS, Locations(scenes=scenes))
    timeline.run(job)
    return job


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m studio.tests.sample_schematic", description=__doc__.split("\n\n")[1])
    parser.add_argument("job", type=Path)
    parser.add_argument("--stills", type=Path, help="also save a still every 2 s of the song here")
    args = parser.parse_args()
    write_job(args.job)
    if args.stills:
        import skia

        from studio.render.renderer import Renderer
        from studio.schemas import Timeline

        song = jobs.read(args.job / media.timeline_path("song"), Timeline)
        renderer = Renderer(args.job, song)
        args.stills.mkdir(parents=True, exist_ok=True)
        t = 0.0
        while t < song.duration:
            renderer.image(t).save(str(args.stills / f"song_{t:05.1f}s.png"), skia.kPNG)
            t += 2.0
    print(args.job)
