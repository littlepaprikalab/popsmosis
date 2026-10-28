"""Scenes drawn as schematics (docs/SCHEMATIC3D.md): fitting a structure, building a machine for a
scene, drawing it, the art step's fallback to an illustration, and the renderer.

The network and the brain are stood in for (tests/sample_schematic.py): a made-up membrane
machine shaped like ATP synthase, also built as if OPM had not placed it.
"""

import hashlib
import json
import math
import subprocess
import sys

import numpy as np
import pytest

skia = pytest.importorskip("skia", reason="skia-python is not installed (or its libEGL is missing)")
gemmi = pytest.importorskip("gemmi", reason="gemmi reads the structure files")

from studio import jobs, media  # noqa: E402
from studio.brain import strict_schema  # noqa: E402
from studio.schematic3d import build as building  # noqa: E402
from studio.schematic3d import fit as fitting  # noqa: E402
from studio.schematic3d import sources  # noqa: E402
from studio.schematic3d.draw import Machine  # noqa: E402
from studio.schematic3d.model import Built  # noqa: E402
from studio.schemas import Bundle, Locations, Timeline  # noqa: E402
from studio.steps import art  # noqa: E402
from studio.tests import sample_schematic as toy  # noqa: E402
from studio.tests.sample import bundle_data  # noqa: E402
from studio.tests.test_art import FakeArtist  # noqa: E402

LESSON = {"topic": "ATP synthesis", "course": "AP Biology", "core_idea": "Proton flow turns the rotor.",
          "facts": ["The rotor is a ring of c subunits with a central gamma stalk attached."]}
PARTS = [(name, toy.COLORS[name]) for name in toy.PARTS]


def built(monkeypatch, tmp_path, brain=None, oriented=True) -> Built:
    toy.FakeSources(oriented=oriented).install(monkeypatch)
    return building.build(LESSON, toy.MACHINE, PARTS, brain or toy.FakeBrain(), tmp_path / "structures", key="k")


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    """The sample job with its hook built as the toy machine (tests/sample_schematic.py)."""
    return toy.write_job(tmp_path_factory.mktemp("schematic") / "job")


# --- fitting ----------------------------------------------------------------------------------


def _structure(tmp_path, tilt=None):
    path = toy.write_structure(tmp_path / "toy.pdb")
    structure = gemmi.read_structure(str(path))
    if tilt is not None:  # the same machine, lying at an angle, as a deposited entry might
        structure[0].transform_pos_and_adp(gemmi.Transform(gemmi.Mat33(tilt.tolist()), gemmi.Vec3(12, -7, 30)))
    return structure


def test_in_opms_frame_the_membrane_stays_put_and_the_ring_stands_in_it(tmp_path):
    owners = {c: "Rotor" for c in toy.RING} | {"g": "Internal rod", "b": None} | {c: "Catalytic knob" for c in toy.KNOB}
    fitted = fitting.fit(_structure(tmp_path), owners, half_thickness=1.5)
    ring = [s for s in fitted.shapes if s["structure"] == "Rotor"]
    assert len(ring) == len(toy.RING)
    for s in ring:
        a, b = (np.array(q) for q in s["points"])
        assert abs((b - a)[2]) / np.linalg.norm(b - a) > 0.98 and abs((a + b)[2] / 2) < 0.2  # upright, in the membrane
    assert fitted.sides == {"Rotor": "in the membrane", "Internal rod": "below the membrane",
                            "Catalytic knob": "below the membrane"}
    assert fitted.footprint and all(abs(x) < 7 and abs(y) < 7 for x, y in fitted.footprint)
    stalk = next(s for s in fitted.shapes if s["chain"] == "b")
    assert stalk["kind"] == "tube" and stalk["structure"] is None  # long and thin; in no structure
    knob = [s for s in fitted.shapes if s["chain"] == "A"]
    assert len(knob) == 2 and all(s["kind"] == "capsule" for s in knob)  # 500 residues: two lobes


def test_without_opm_the_machine_stands_along_its_longest_axis_however_it_lies(tmp_path):
    theta = 0.7
    tilt = np.array([[1, 0, 0], [0, math.cos(theta), -math.sin(theta)], [0, math.sin(theta), math.cos(theta)]])
    owners = {c: "Rotor" for c in toy.RING} | {c: None for c in "gb" + toy.KNOB}
    fitted = fitting.fit(_structure(tmp_path, tilt), owners, half_thickness=None)
    points = np.vstack([np.array(s["points"]) for s in fitted.shapes])
    extent = points.max(axis=0) - points.min(axis=0)
    assert extent[2] == max(extent)  # z is the long axis, whatever the file's frame
    assert not fitted.footprint and not fitted.sides


# --- building a machine for a scene -------------------------------------------------------------


def test_a_machine_is_built_from_the_entry_the_brain_chose_with_the_subunits_it_matched(monkeypatch, tmp_path):
    brain = toy.FakeBrain()
    machine = built(monkeypatch, tmp_path, brain)
    assert machine.source.pdb == toy.PDB and machine.source.oriented_by == "OPM"
    assert [(s.name, s.color) for s in machine.structures] == PARTS
    assert next(s for s in machine.structures if s.name == "Catalytic knob").chains == sorted(toy.KNOB)
    assert machine.page.above.name == "LUMEN" and machine.page.below.name == "STROMA"
    assert machine.membrane and machine.membrane.half_thickness == 1.5 and machine.membrane.footprint
    assert [label.rsplit(" ", 1)[1] for label in brain.labels] == ["find", "choose", "review"]
    review = json.loads(brain.prompts["schematic review"])
    assert {p["name"]: p["where"] for p in review["parts"]}["Catalytic knob"] == "below the membrane"
    assert review["parts"][0]["subunits"] == ["Toy subunit c"]  # judged by the entry's own names


def test_a_part_the_review_finds_wrong_is_drawn_but_never_labelled(monkeypatch, tmp_path):
    review = toy.FakeBrain().review
    review["structures"][3] = {"structure": "Stator", "verdict": "WRONG", "problem": "subunit b is only part of it"}
    machine = built(monkeypatch, tmp_path, toy.FakeBrain(review=review))
    assert [s.name for s in machine.structures] == ["Rotor", "Internal rod", "Catalytic knob"]
    assert next(s for s in machine.shapes if s.chain == "b").structure is None  # still drawn
    assert any("Stator left unlabelled: subunit b is only part of it" in note for note in machine.notes)


def test_a_choice_the_review_objects_to_is_made_again_with_its_objections(monkeypatch, tmp_path):
    first = toy.FakeBrain().choose
    stator_missing = {**first, "structures": first["structures"][:3]}

    def review(label):
        verdicts = toy.FakeBrain().review
        if not label.endswith(" 2"):  # the first round: the stator was left out
            return {**verdicts, "structures": [{"structure": "Rotor", "verdict": "WRONG", "problem": "needs the gamma stalk"}]}
        return verdicts

    brain = toy.FakeBrain(choose=lambda label: first if label.endswith(" 2") else stator_missing, review=review)
    machine = built(monkeypatch, tmp_path, brain)
    assert [s.name for s in machine.structures] == list(toy.PARTS)
    assert brain.labels == ["schematic find", "schematic choose", "schematic review", "schematic choose 2",
                            "schematic review 2"]
    retry = json.loads(brain.prompts["schematic choose 2"])["last_try"]
    assert retry["review"] == ["Rotor: needs the gamma stalk"] and len(retry["structures"]) == 3
    lesson = json.loads(brain.prompts["schematic review"])["lesson"]
    assert lesson == LESSON  # the facts go with it


@pytest.mark.parametrize("change, reason", [
    ({"choose": {"entry": "", "why": "none shows it whole", "structures": []}}, "no entry shows"),
    ({"review": {"entry": "WRONG", "entry_problem": "it is a bacterial enzyme", "structures": [], "above": "",
                 "below": ""}}, "is not toy ATP synthase: it is a bacterial enzyme"),
    ({"choose": {"entry": toy.PDB, "why": "", "structures": [{"structure": "Rotor", "entities": ["1"]}]}},
     "only 1 of 4 structures"),
])
def test_a_machine_that_cannot_be_trusted_is_not_built(monkeypatch, tmp_path, change, reason):
    with pytest.raises(building.BuildFailed, match=reason):
        built(monkeypatch, tmp_path, toy.FakeBrain(**change))


def test_sources_that_do_not_answer_fail_the_build_not_the_job(monkeypatch, tmp_path):
    toy.FakeSources(fail="search").install(monkeypatch)
    with pytest.raises(building.BuildFailed, match="did not answer"):
        building.build(LESSON, toy.MACHINE, PARTS, toy.FakeBrain(), tmp_path, key="k")


def test_a_machine_opm_has_not_placed_is_drawn_without_a_membrane(monkeypatch, tmp_path):
    machine = built(monkeypatch, tmp_path, oriented=False)
    assert machine.membrane is None and machine.source.oriented_by == "principal axes"
    assert machine.page.below is None and machine.page.above.name == ""


def test_the_brains_answers_use_only_what_both_providers_accept():
    for model in (building.Leads, building.Choice, building.Review):
        strict_schema(model)


def test_only_one_copy_of_the_machine_is_drawn(monkeypatch, tmp_path):
    entry = toy.toy_entry()
    entry.assembly = [c for c in entry.assembly if c not in "EF"]  # the entry's first assembly leaves two out
    fake = toy.FakeSources(oriented=False).install(monkeypatch)
    monkeypatch.setattr(sources, "details", lambda ids: [entry])
    machine = building.build(LESSON, toy.MACHINE, PARTS, toy.FakeBrain(), tmp_path, key="k")
    assert {s.chain for s in machine.shapes}.isdisjoint({"E", "F"})
    assert "coordinates 1TOY RCSB" in fake.calls


# --- drawing ----------------------------------------------------------------------------------


def _pixels(machine: Machine, **kwargs) -> np.ndarray:
    surface = skia.Surface(machine.W, machine.H)
    machine.draw(surface.getCanvas(), **kwargs)
    return surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)


def test_masks_hold_what_shows_of_each_structure_and_a_washed_one_is_paler(monkeypatch, tmp_path):
    machine = Machine(built(monkeypatch, tmp_path))
    masks = machine.masks()
    assert set(masks) == set(toy.PARTS)
    assert sum(m.astype(int) for m in masks.values()).max() == 1 and all(m.sum() > 500 for m in masks.values())
    knob, rotor = masks["Catalytic knob"], masks["Rotor"]
    plain = _pixels(machine)[..., :3].astype(int)
    washed = _pixels(machine, washes={"Catalytic knob": 0.6})[..., :3].astype(int)
    assert washed[knob].mean() > plain[knob].mean() + 30 and (washed[rotor] == plain[rotor]).all()


def test_with_no_membrane_the_page_is_one_colour(monkeypatch, tmp_path):
    machine = Machine(built(monkeypatch, tmp_path, oriented=False))
    assert tuple(_pixels(machine)[2, 2, :3]) == tuple(int(building.AROUND[i:i + 2], 16) for i in (1, 3, 5))


def test_the_command_draws_a_still_of_a_built_machine(monkeypatch, tmp_path):
    path = tmp_path / "schematic.json"
    path.write_text(built(monkeypatch, tmp_path).model_dump_json(), encoding="utf-8")
    root = media.__file__.rsplit("/studio/", 1)[0]
    done = subprocess.run([sys.executable, "-m", "studio.schematic3d.draw", str(path), str(tmp_path / "still.png")],
                          cwd=root, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert skia.Image.open(str(tmp_path / "still.png")).dimensions() == skia.ISize(media.ART_WIDTH, media.ART_HEIGHT)


# --- the writer's side ---------------------------------------------------------------------------


def test_the_writer_names_any_machine_and_older_bundles_read_as_illustrations():
    scene = strict_schema(Bundle)["properties"]["scenes"]["items"]["properties"]
    assert list(scene) == ["id", "schematic", "image_prompt", "structures"]
    assert scene["schematic"]["type"] == "string" and "enum" not in scene["schematic"]
    bundle = Bundle.model_validate(bundle_data())
    assert all(s.schematic == "" for s in bundle.scenes)
    first = bundle.scenes[0]
    old = json.dumps([bundle.topic, {k: v for k, v in first.model_dump().items() if k != "schematic"}], sort_keys=True)
    assert art.key(bundle, first) == hashlib.sha256(old.encode()).hexdigest()[:16]  # its art is not drawn again


# --- the art step and the renderer ------------------------------------------------------------


def test_the_art_step_builds_the_scenes_machine_and_keeps_it(job):
    located = jobs.read(job / media.LOCATIONS, Locations).scene("hook")
    assert located.schematic == media.schematic_path("hook") == "art/hook.schematic.json"
    assert [(s.name, s.found_by) for s in located.structures] == [(name, "schematic3d")
                                                                  for name in [*toy.PARTS, *toy.COMPARTMENTS]]
    assert skia.Image.open(str(job / located.art)).dimensions() == skia.ISize(media.ART_WIDTH, media.ART_HEIGHT)
    bundle = toy.bundle()
    hook = next(s for s in bundle.scenes if s.id == "hook")
    assert art._built(job, bundle, hook) is not None  # done: a retry of the step builds nothing again
    assert art._built(job, bundle, hook.model_copy(update={"schematic": "another machine"})) is None


def test_a_machine_that_cannot_be_built_is_drawn_as_an_illustration(monkeypatch, tmp_path):
    from studio.tests import sample_media

    job = sample_media.write_job(tmp_path / "job")
    (job / media.LOCATIONS).unlink()
    data = bundle_data()
    next(s for s in data["scenes"] if s["id"] == "synthase")["schematic"] = "ATP synthase"
    jobs.write(job / "bundle.json", Bundle.model_validate(data))
    toy.FakeSources(fail="search").install(monkeypatch)
    artist = FakeArtist()
    said = art.run(job, ask=toy.FakeBrain(then=artist))
    assert "drew as illustrations synthase (the structure sources did not answer" in said
    located = jobs.read(job / media.LOCATIONS, Locations).scene("synthase")
    assert located.schematic is None and {s.found_by for s in located.structures} == {"svg"}
    assert "art synthase" in artist.labels() and art.is_done(job)


def test_the_timeline_reveals_the_machines_parts_by_the_words_sung(job):
    song = jobs.read(job / media.timeline_path("song"), Timeline)
    first, second = song.beats[0], song.beats[1]
    assert first.scene == second.scene == "hook"
    assert [r.name for r in first.reveal] == ["Lumen", "Rotor", "Stroma"]
    assert [p.path for p in first.particles] == [["Lumen", "Rotor", "Stroma"]]  # through the machine, as sung
    assert [r.name for r in second.reveal] == ["Internal rod", "Catalytic knob"]
    assert second.spotlight == "Internal rod" and second.camera.target == "Rotor"


def test_the_renderer_draws_the_machine_live_and_a_frame_depends_only_on_its_time(job):
    from studio.render.renderer import Renderer, SchematicArt

    song = jobs.read(job / media.timeline_path("song"), Timeline)
    renderer = Renderer(job, song)
    hook = renderer.scene("hook")
    assert isinstance(hook, SchematicArt)
    ghost = renderer.frame(0.5).copy()  # the rotor is a ghost until "rotor" is sung, near the line's end
    renderer.frame(30.0)
    assert (renderer.frame(0.5) == ghost).all()
    centroid = hook.located.get("Rotor").centroid
    revealed = renderer.frame(4.2)
    for t, pixels in ((0.5, ghost), (4.2, revealed)):
        x, y = (int(v) for v in renderer.art_to_frame(t, centroid.x, centroid.y))
        assert pixels[y, x, :3].min() < 250  # the machine is drawn where the rotor is
    x, y = (int(v) for v in renderer.art_to_frame(4.2, centroid.x, centroid.y))
    gx, gy = (int(v) for v in renderer.art_to_frame(0.5, centroid.x, centroid.y))
    assert revealed[y, x, :3].astype(int).sum() < ghost[gy, gx, :3].astype(int).sum()  # the ghost was paler


def test_the_particles_travel_through_the_machine_from_one_compartment_to_the_other(job):
    from studio.render.renderer import Renderer

    song = jobs.read(job / media.timeline_path("song"), Timeline)
    first = song.beats[0]
    t = first.t1 - 0.3  # every stop revealed by now
    with_dots = Renderer(job, song).frame(t).copy()
    without = song.model_copy(update={"beats": [first.model_copy(update={"particles": []}), *song.beats[1:]]})
    assert (Renderer(job, without).frame(t) != with_dots).any()
    located = jobs.read(job / media.LOCATIONS, Locations).scene("hook")
    lumen, rotor, stroma = (located.get(n).label_anchor.y for n in ("Lumen", "Rotor", "Stroma"))
    assert lumen < rotor < stroma  # the stops run from above the membrane to below it


def test_a_compartment_the_scene_names_is_labelled_washed_and_masked_like_a_part(monkeypatch, tmp_path):
    toy.FakeSources().install(monkeypatch)
    parts = PARTS + [("Stroma", toy.COLORS["Stroma"]), ("Protons", "#999999")]
    machine = building.build(LESSON, toy.MACHINE, parts, toy.FakeBrain(), tmp_path, key="k")
    stroma = next(s for s in machine.structures if s.name == "Stroma")
    assert stroma.kind == "compartment" and stroma.side == "below" and not stroma.chains
    assert machine.page.below.name == "" and machine.page.above.name == "LUMEN"  # its label names it, not the page
    assert "Protons not found in 1TOY" in machine.notes and "Stroma is the compartment below the membrane" in machine.notes
    drawn = Machine(machine)
    masks = drawn.masks()
    below = masks["Stroma"]
    assert below.sum() > 100_000 and not (below & masks["Catalytic knob"]).any()
    top, bottom = drawn.membrane_band()
    assert not below[int(top) + 1:int(bottom) - 1].any()  # not the membrane
    ghost = _pixels(drawn, washes={"Stroma": 0.55})[..., :3].astype(int)
    plain = _pixels(drawn)[..., :3].astype(int)
    assert ghost[below].mean() > plain[below].mean() + 10
    assert abs(ghost[masks["Rotor"]].mean() - plain[masks["Rotor"]].mean()) < 1  # only its edge meets the page


def test_on_the_exam_question_the_schematic_stays_in_its_panel(job):
    from studio.render.layouts import H
    from studio.render.renderer import Renderer

    song = jobs.read(job / media.timeline_path("song"), Timeline)
    assert song.frq.scene == "hook"
    pixels = Renderer(job, song).frame(song.frq.t1 - 0.5)
    x0, y0, x1, y1 = (int(v) for v in H.frq_view)
    assert (pixels[y1 + 10:, x0:x1, :3] == 255).all()  # white below the panel, where the band was
    inside = pixels[y0 + 5:y1 - 5, x0 + 5:x1 - 5, :3]
    assert (inside != 255).any(axis=2).mean() > 0.9  # the panel is the page's colour, not white
