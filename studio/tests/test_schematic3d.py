"""Scenes drawn from the schematic library (docs/SCHEMATIC3D.md): the recipes, fitting, drawing, the
writer's choice, the fact-checked motions, the art step and the renderer.

Two recipes run through the same code: the library's ATP synthase, and a made-up pump with no
membrane (studio/tests/schematic/), so nothing in the pipeline can come to rely on one machine.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

skia = pytest.importorskip("skia", reason="skia-python is not installed (or its libEGL is missing)")

from studio import checks, jobs, media  # noqa: E402
from studio.brain import strict_schema  # noqa: E402
from studio.schematic3d import library  # noqa: E402
from studio.schematic3d.draw import Machine, turned  # noqa: E402
from studio.schemas import Bundle, FactCheckReport, Locations, Timeline  # noqa: E402
from studio.steps import art, factcheck  # noqa: E402
from studio.steps import bundle as bundle_step  # noqa: E402
from studio.tests import sample_schematic  # noqa: E402
from studio.tests.sample import bundle_data  # noqa: E402

TOY_ROOT = Path(__file__).resolve().parent / "schematic"
ATP = library.load("atp_synthase")


@pytest.fixture
def toy(monkeypatch):
    """The library with the made-up pump in it too."""
    monkeypatch.setattr(library, "ROOTS", [*library.ROOTS, TOY_ROOT])
    library.clear_cache()
    yield library.load("toy_pump")
    library.clear_cache()


@pytest.fixture(scope="module")
def job(tmp_path_factory):
    """The sample job with its hook drawn as the ATP synthase schematic (tests/sample_schematic.py)."""
    return sample_schematic.write_job(tmp_path_factory.mktemp("schematic") / "job")


# --- the recipes --------------------------------------------------------------------------------


def test_every_recipe_loads_with_shapes_for_every_chain_and_chains_named_as_the_entry_names_them():
    for name in library.all_names():
        entry = library.load(name)  # validates the recipe, and its shapes against it
        assert entry.recipe.name == name and not library.shapes_problems(entry.recipe, entry.shapes)
    parts = ATP.recipe.parts
    assert {ATP.recipe.structure.entities[c] for c in parts["rotor"].chains} == {"ATP synthase subunit c, chloroplastic"}


def test_a_recipe_that_names_a_chain_as_another_entity_is_refused():
    data = json.loads((library.HERE / "recipes" / "atp_synthase.json").read_text())
    data["parts"]["rotor"]["chains"].append("A")  # an alpha subunit, not a c subunit
    with pytest.raises(ValueError, match="chain A of rotor is 'ATP synthase subunit alpha"):
        library.Recipe.model_validate(data)
    data = json.loads((library.HERE / "recipes" / "atp_synthase.json").read_text())
    data["structures"][1]["parts"].append("rotor")
    with pytest.raises(ValueError, match="part rotor is in both Rotor and Internal rod"):
        library.Recipe.model_validate(data)


def test_the_writer_is_offered_only_recipes_a_person_has_checked(toy, monkeypatch):
    monkeypatch.delenv(library.UNCHECKED_ENV, raising=False)
    assert not ATP.recipe.checked.by  # not signed yet
    assert library.offered() == ["toy_pump"]
    assert set(library.all_names()) == {"atp_synthase", "toy_pump"}  # a stored bundle may still name either
    monkeypatch.setenv(library.UNCHECKED_ENV, "1")
    assert library.offered() == ["atp_synthase", "toy_pump"]


def test_no_way_of_turning_is_set_by_the_recipe():
    spin = ATP.recipe.motion("rotor")
    assert "{direction}" in spin.claim and "clockwise" not in json.dumps(ATP.recipe.model_dump(mode="json"))


# --- the claims a motion makes, and the way the facts support -----------------------------------


def test_each_way_is_a_claim_and_a_spin_is_asked_from_both_ends():
    probes = dict(library.probes(ATP.recipe))
    assert len(probes) == 6
    assert probes["atp_synthase.rotor.down-counterclockwise"] == (
        "While ATP synthase makes ATP, its rotor and internal rod turn counterclockwise as seen from the stroma.")
    assert probes["atp_synthase.protons.forward"].startswith("Protons flow through ATP synthase from the thylakoid")


@pytest.mark.parametrize("supported, drawn", [
    ({"rotor.up-clockwise"}, {"rotor": -1}),
    ({"rotor.down-clockwise"}, {"rotor": 1}),  # seen from the other end, the same turn goes the other way round
    ({"rotor.up-clockwise", "rotor.down-counterclockwise"}, {"rotor": -1}),  # one turn, said from both ends
    ({"rotor.up-clockwise", "rotor.up-counterclockwise"}, {}),  # the facts contradict themselves: still
    ({"rotor.up-clockwise", "rotor.down-clockwise"}, {}),
    (set(), {}),  # the facts say nothing: still
    ({"protons.backward"}, {"protons": -1}),
])
def test_a_motion_is_drawn_only_in_the_one_way_the_facts_support(supported, drawn):
    verdicts = {f"atp_synthase.{probe}": "SUPPORTED" for probe in supported}
    verdicts["atp_synthase.rotor.down-counterclockwise"] = verdicts.get("atp_synthase.rotor.down-counterclockwise",
                                                                        "UNSUPPORTED")
    assert library.resolve(ATP.recipe, verdicts) == drawn


# --- fitting ----------------------------------------------------------------------------------


def toy_structure(axis: np.ndarray):
    """A ring of six upright chains, a knob below it and a stalk off to one side, all tilted onto
    `axis`: what fit needs to find the axle, in angstroms as a structure file has them."""
    gemmi = pytest.importorskip("gemmi")
    z = axis / np.linalg.norm(axis)
    x = np.cross(z, [0.3, 1.0, 0.2])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    origin = np.array([12.0, -7.0, 30.0])

    def place(u, v, w):
        return origin + u * x + v * y + w * z

    chains = {}
    for k, name in enumerate("GHIJKL"):
        a = 2 * math.pi * k / 6
        chains[name] = [place(30 * math.cos(a), 30 * math.sin(a), h) for h in np.linspace(-20, 20, 12)]
    chains["A"] = [place(4 * math.cos(h), 4 * math.sin(h), -60 + h) for h in np.linspace(-10, 10, 12)]
    chains["b"] = [place(55, 2, h) for h in np.linspace(10, -70, 24)]
    st = gemmi.Structure()
    model = gemmi.Model("1")
    for name, pts in chains.items():
        chain = gemmi.Chain(name)
        for i, pt in enumerate(pts, 1):
            res = gemmi.Residue()
            res.name, res.seqid = "ALA", gemmi.SeqId(i, " ")
            atom = gemmi.Atom()
            atom.name, atom.element, atom.pos = "CA", gemmi.Element("C"), gemmi.Position(*pt)
            res.add_atom(atom)
            chain.add_residue(res)
        model.add_chain(chain)
    st.add_model(model)
    return st


TOY_FIT = {"name": "toy", "parts": {"ring": {"chains": list("GHIJKL"), "kind": "capsule"},
                                    "knob": {"chains": ["A"], "kind": "capsule"},
                                    "stalk": {"chains": ["b"], "kind": "tube"}},
           "axle": {"ring": "ring", "down": "knob", "toward_x": "stalk"}, "membrane": {"parts": ["ring"]}}


@pytest.mark.parametrize("axis", [np.array([0, 0, 1.0]), np.array([1.0, 0.4, -0.3])])
def test_fit_finds_the_axle_however_the_structure_lies(axis):
    from studio.schematic3d.fit import fit_structure

    fitted = fit_structure(toy_structure(axis), TOY_FIT)
    ring = [s for s in fitted["shapes"] if s["part"] == "ring"]
    assert len(ring) == 6 and fitted["ring_radius"] == pytest.approx(3.0, abs=0.1)  # nm, from 30 angstroms
    for s in ring:
        a, b = np.array(s["a"]), np.array(s["b"])
        assert abs((b - a)[2]) / np.linalg.norm(b - a) > 0.99  # upright: along the axle
        assert np.hypot(*((a + b) / 2)[:2]) == pytest.approx(3.0, abs=0.1) and abs(((a + b) / 2)[2]) < 0.1
    knob = next(s for s in fitted["shapes"] if s["part"] == "knob")
    assert (np.array(knob["a"]) + np.array(knob["b"]))[2] / 2 == pytest.approx(-6.0, abs=0.3)  # below the ring
    stalk = np.array(next(s for s in fitted["shapes"] if s["part"] == "stalk")["path"])
    assert stalk[:, 0].mean() == pytest.approx(5.5, abs=0.2) and abs(stalk[:, 1].mean()) < 0.3  # toward +x
    assert fitted["membrane_footprint"]  # the ring sits in the membrane


def test_fit_refuses_a_recipe_naming_chains_the_structure_lacks():
    from studio.schematic3d.fit import fit_structure

    recipe = json.loads(json.dumps(TOY_FIT))
    recipe["parts"]["knob"]["chains"] = ["A", "Z"]
    with pytest.raises(ValueError, match="no protein chains Z"):
        fit_structure(toy_structure(np.array([0, 0, 1.0])), recipe)


# --- drawing ----------------------------------------------------------------------------------


def _pixels(machine: Machine, t: float, **kwargs) -> np.ndarray:
    surface = skia.Surface(machine.W, machine.H)
    machine.draw(surface.getCanvas(), t, **kwargs)
    return surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)


def test_a_spin_turns_its_parts_from_the_first_frame_its_way_and_nothing_else():
    assert turned(np.array([[1.0, 0, 5]]), math.pi / 2) == pytest.approx(np.array([[0, 1.0, 5]]))  # z stays
    for sense in (1, -1):
        machine = Machine(ATP.recipe, ATP.shapes, {"rotor": sense}, 480, 274)
        assert machine.angle("rotor", 1.5) == pytest.approx(sense * 2 * math.pi * 1.5 / 6.0)  # full speed at once
        rotor = next(s for s in machine.shapes if s.part == "rotor")
        knob = next(s for s in machine.shapes if s.part == "knob")
        assert not np.allclose(machine.placed(rotor, 0.5), rotor.points)
        assert np.allclose(machine.placed(knob, 0.5), knob.points)
        sweep = machine.arrows[0]  # the arc shows the same way round: its angle grows with a +1 turn
        angles = np.unwrap(np.arctan2(sweep[:, 1], sweep[:, 0]))
        assert np.sign(angles[-1] - angles[0]) == sense


def test_a_frame_depends_only_on_its_time_and_a_still_machine_never_changes():
    moving = Machine(ATP.recipe, ATP.shapes, {"rotor": -1, "protons": 1}, 480, 274)
    a, b = _pixels(moving, 0.0), _pixels(moving, 1.0)
    assert moving.moving and (a != b).any()
    assert (_pixels(moving, 1.0) == b).all()
    still = Machine(ATP.recipe, ATP.shapes, {}, 480, 274)
    assert not still.moving and not still.arrows and not still.particles(3.0)  # no claim, no arrow, no proton
    assert (_pixels(still, 0.0) == _pixels(still, 2.5)).all()


def test_protons_wait_and_flow_only_while_the_rotor_they_move_with_turns():
    flowing = Machine(ATP.recipe, ATP.shapes, {"rotor": 1, "protons": 1}, 480, 274)
    waiting = Machine(ATP.recipe, ATP.shapes, {"protons": 1}, 480, 274)
    crowd = len(ATP.recipe.motion("protons").crowd.forward)
    assert len(waiting.particles(3.0)) == crowd and len(waiting.arrows) == 1  # the claim stands: the arrow, the crowd
    assert len(flowing.particles(3.0)) > crowd and len(flowing.particles(0.0)) > crowd  # on their way from frame 0


def test_masks_hold_what_shows_of_each_structure_and_nothing_twice():
    machine = Machine(ATP.recipe, ATP.shapes, {}, media.ART_WIDTH, media.ART_HEIGHT)
    masks = machine.masks()
    assert set(masks) == {"Rotor", "Internal rod", "Catalytic knob", "Stator"}
    total = sum(m.astype(int) for m in masks.values())
    assert total.max() == 1 and all(m.sum() > 5000 for m in masks.values())


def test_a_washed_structure_is_paler_and_the_rest_keep_their_colour():
    machine = Machine(ATP.recipe, ATP.shapes, {}, media.ART_WIDTH, media.ART_HEIGHT)
    knob = machine.masks()["Catalytic knob"]
    rotor = machine.masks()["Rotor"]
    plain = _pixels(machine, 0.0)[..., :3].astype(int)
    washed = _pixels(machine, 0.0, washes={"Catalytic knob": 0.6})[..., :3].astype(int)
    assert washed[knob].mean() > plain[knob].mean() + 30
    assert (washed[rotor] == plain[rotor]).all()


def test_the_made_up_pump_draws_with_no_membrane_and_its_own_compartment(toy):
    machine = Machine(toy.recipe, toy.shapes, {"plug": 1, "ions": -1}, 480, 274)
    assert machine.recipe.membrane is None and machine.moving
    corner = _pixels(machine, 0.5)[2, 2, :3]
    assert tuple(corner) == (0xDC, 0xEB, 0xF7)  # CYTOSOL's colour, the whole page
    assert len(machine.particles(1.2)) >= 1  # moving the other way, with nothing to move with
    assert set(machine.masks()) == {"Barrel", "Plug"}  # the lid is in no structure: drawn, never labelled


def test_the_command_draws_a_still(tmp_path):
    root = library.HERE.parent.parent
    done = subprocess.run([sys.executable, "-m", "studio.schematic3d.draw", "atp_synthase", str(tmp_path),
                           "--still", "2.0", "--motion", "rotor=1"], cwd=root, capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr
    still = tmp_path / "atp_synthase_2.00s.png"
    assert skia.Image.open(str(still)).dimensions() == skia.ISize(media.ART_WIDTH, media.ART_HEIGHT)


# --- the writer's choice -------------------------------------------------------------------------


def test_the_schema_offers_the_writer_only_the_offered_recipes(toy, monkeypatch):
    monkeypatch.delenv(library.UNCHECKED_ENV, raising=False)
    scene = strict_schema(Bundle)["properties"]["scenes"]["items"]["properties"]
    assert list(scene) == ["id", "schematic", "image_prompt", "structures"]
    assert scene["schematic"]["enum"] == ["", "toy_pump"]


def test_the_prompt_lists_the_offered_machines_or_says_nothing_of_schematics(toy, monkeypatch):
    monkeypatch.delenv(library.UNCHECKED_ENV, raising=False)
    text = bundle_step.prompt()
    assert '`"toy_pump"`' in text and "Its structures: Barrel (also 'drum')" in text and "atp_synthase" not in text
    assert "{library}" not in text and "<!--" not in text
    assert "The Plug turn only if a fact says which way, seen from the top or from the bottom" in text
    monkeypatch.setattr(library, "ROOTS", [library.HERE])
    library.clear_cache()
    assert "schematic" not in bundle_step.prompt().lower()  # nothing checked: no schematic part at all


def test_the_writers_menu_never_describes_a_machine_by_how_it_is_drawn(toy, monkeypatch):
    # A real job taught "the rotor is a ring of fourteen capsules": the drawing's shapes became a fact.
    monkeypatch.setenv(library.UNCHECKED_ENV, "1")
    menu = library.writer_section().lower()
    assert "atp_synthase" in menu and not any(word in menu for word in ("capsule", "tube", "lobe", "drawn as"))


def test_a_bundle_from_before_the_library_reads_as_illustrations_with_the_same_art_key():
    data = bundle_data()
    bundle = Bundle.model_validate(data)
    assert all(scene.schematic == "" for scene in bundle.scenes)
    scene = bundle.scenes[0]
    old = json.dumps([bundle.topic, {k: v for k, v in scene.model_dump().items() if k != "schematic"}], sort_keys=True)
    import hashlib
    assert art.key(bundle, scene) == hashlib.sha256(old.encode()).hexdigest()[:16]  # its art is not drawn again


def test_a_schematic_scene_gets_its_recipes_structures_and_their_colour_everywhere():
    data = bundle_data()
    hook = data["scenes"][0]
    hook["schematic"] = "atp_synthase"
    hook["structures"] = [{"name": "knob", "aliases": ["F1 head"], "sam_phrase": "x", "color_hex": "#123456"},
                          {"name": "ATP synthase", "aliases": [], "sam_phrase": "x", "color_hex": "#3366CC"}]
    data["scenes"][2]["structures"].append({"name": "Rotor", "aliases": [], "sam_phrase": "x", "color_hex": "#654321"})
    bundle, report = checks.review(Bundle.model_validate(data))
    hook = bundle.scenes[0]
    assert [s.name for s in hook.structures] == ["Rotor", "Internal rod", "Catalytic knob", "Stator"]
    knob = hook.structures[2]
    assert knob.aliases == ["knob", "F1 head"] and knob.color_hex == "#C7AEEA"
    assert next(s for s in bundle.scenes[2].structures if s.name == "Rotor").color_hex == "#9AA3E8"
    details = [p.detail for p in report.problems if p.rule == "schematic"]
    assert any("dropped ATP synthase" in d for d in details) and any("Rotor takes" in d for d in details)
    again, report = checks.review(bundle)
    assert again == bundle and not [p for p in report.problems if p.rule == "schematic"]


def test_a_scene_naming_no_recipe_in_the_library_is_refused():
    data = bundle_data()
    data["scenes"][0]["schematic"] = "ribosome"
    with pytest.raises(ValueError, match="not a recipe in the schematic library"):
        Bundle.model_validate(data)


# --- the fact-check -----------------------------------------------------------------------------


def test_the_fact_check_judges_the_motion_claims_and_never_corrects_or_fails_on_them():
    bundle = sample_schematic.bundle()
    lines = factcheck.lines_of(bundle)
    motion = [line for line in lines if line.kind == "motion"]
    assert [line.id for line in motion] == [i for i, _ in library.probes(ATP.recipe)]

    def ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        sent = json.loads(prompt)["lines"]
        return {"checks": [{"id": line["id"], "verdict": "UNSUPPORTED" if line["id"].startswith("motion:") else
                            "SUPPORTED", "source": "" if line["id"].startswith("motion:") else "f1",
                            "problem": "no fact says so" if line["id"].startswith("motion:") else "",
                            "corrected": "Something else." if line["id"].startswith("motion:") else ""}
                           for line in sent]}

    report, checked = factcheck.check_bundle(bundle, "", ask)
    assert report.status == "passed" and report.rounds == 1 and not report.corrections and checked == bundle
    assert {line.verdict for line in report.lines if line.kind == "motion"} == {"UNSUPPORTED"}
    assert library.resolve(ATP.recipe, {line.id: line.verdict for line in report.lines if line.kind == "motion"}) == {}


# --- the art step and the renderer ------------------------------------------------------------


def test_the_art_step_draws_a_schematic_scene_without_the_brain(job):
    located = jobs.read(job / media.LOCATIONS, Locations).scene("hook")
    assert located.schematic == media.schematic_path("hook") == "art/hook.schematic.json"
    assert {s.found_by for s in located.structures} == {"schematic3d"}
    assert [s.name for s in located.structures] == ["Rotor", "Internal rod", "Catalytic knob", "Stator"]
    snapshot = jobs.read(job / located.schematic, library.Snapshot)
    assert snapshot.motions == {"rotor": -1, "protons": 1}
    assert len(snapshot.claims) == 3 and all("clockwise" in c or "Protons" in c for c in snapshot.claims)
    art_png = skia.Image.open(str(job / located.art))
    assert art_png.dimensions() == skia.ISize(media.ART_WIDTH, media.ART_HEIGHT)
    for s in located.structures:  # the label points where the recipe says, inside the part
        assert s.box.holds(s.label_anchor)


def test_a_schematic_is_drawn_again_when_the_fact_check_changes_its_motions(job, tmp_path):
    import shutil

    copy = tmp_path / "job"
    shutil.copytree(job, copy)
    assert art._schematic_current(copy, checks.load(copy / "bundle.json"), sample_schematic.bundle().scenes[0])
    report = jobs.read(copy / "factcheck.json", FactCheckReport)
    lines = [line.model_copy(update={"verdict": "UNSUPPORTED"}) if line.kind == "motion" else line for line in report.lines]
    jobs.write(copy / "factcheck.json", report.model_copy(update={"lines": lines}))
    hook = checks.load(copy / "bundle.json").scenes[0]
    assert not art._schematic_current(copy, checks.load(copy / "bundle.json"), hook)
    located = art.draw_schematic(copy, checks.load(copy / "bundle.json"), hook)
    assert jobs.read(copy / located.schematic, library.Snapshot).motions == {}


def test_the_timeline_reveals_the_recipes_structures_by_the_words_sung(job):
    song = jobs.read(job / media.timeline_path("song"), Timeline)
    first, second = song.beats[0], song.beats[1]
    assert first.scene == second.scene == "hook"
    assert [r.name for r in first.reveal] == ["Rotor"]  # "rotor ring"
    assert [r.name for r in second.reveal] == ["Internal rod", "Catalytic knob"]  # "rod", "knob"
    assert second.spotlight == "Internal rod" and second.camera.target == "Rotor"


def test_the_renderer_draws_the_machine_live_and_moving_from_the_first_frame(job):
    from studio.render.renderer import Renderer, SchematicArt

    song = jobs.read(job / media.timeline_path("song"), Timeline)
    renderer = Renderer(job, song)
    hook = renderer.scene("hook")
    assert isinstance(hook, SchematicArt) and hook.moving
    first = renderer.frame(0.0).copy()
    x, y = renderer.art_to_frame(0.0, *hook.machine.project([(0, -2.5, 0.0)])[:2])  # the ring's front
    x, y = int(np.asarray(x).item()), int(np.asarray(y).item())
    later = renderer.frame(0.2)
    assert (first[y - 15:y + 15, x - 15:x + 15] != later[y - 15:y + 15, x - 15:x + 15]).any()
    assert (renderer.frame(0.2) == later).all()  # a frame depends only on its time
    # A label rides along with its part: the internal rod's dot moves as it turns.
    assert hook.anchor("Internal rod", 0.0) != hook.anchor("Internal rod", 1.0)
    assert hook.anchor("Rotor", 0.0) == hook.anchor("Rotor", 1.0)  # a fixed point on the ring


def test_on_the_exam_question_the_schematic_stays_in_its_panel(job):
    from studio.render.layouts import H
    from studio.render.renderer import Renderer

    song = jobs.read(job / media.timeline_path("song"), Timeline)
    assert song.frq.scene == "hook"
    renderer = Renderer(job, song)
    pixels = renderer.frame(song.frq.t1 - 0.5)
    x0, y0, x1, y1 = (int(v) for v in H.frq_view)
    assert (pixels[y1 + 10:, x0:x1, :3] == 255).all()  # white below the panel, where the band was
    inside = pixels[y0 + 5:y1 - 5, x0 + 5:x1 - 5, :3]
    assert (inside != 255).any(axis=2).mean() > 0.9  # the panel is the page's colour, not white


def test_fitting_refuses_a_recipe_whose_entity_names_are_not_the_files(tmp_path):
    pytest.importorskip("gemmi")
    from studio.schematic3d.fit import entity_names, entity_problems

    cif = tmp_path / "toy.cif"
    cif.write_text("data_TOY\nloop_\n_entity.id\n_entity.pdbx_description\n1 'ATP synthase subunit c'\n"
                   "2 \"ATP synthase subunit b', chloroplastic\"\nloop_\n_entity_poly.entity_id\n"
                   "_entity_poly.pdbx_strand_id\n1 G,H\n2 p\n")
    assert entity_names(cif) == {"G": "ATP synthase subunit c", "H": "ATP synthase subunit c",
                                 "p": "ATP synthase subunit b', chloroplastic"}
    recipe = {"structure": {"entities": {"G": "ATP synthase subunit c", "p": "ATP synthase subunit b"}}}
    assert entity_problems(recipe, cif) == [
        "chain p is \"ATP synthase subunit b', chloroplastic\" in the file, not 'ATP synthase subunit b'"]
