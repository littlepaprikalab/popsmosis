"""The textbook-style 3D schematic (studio/schematic3d): fitting shapes to a structure, and drawing them."""

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

skia = pytest.importorskip("skia", reason="skia-python is not installed (or its libEGL is missing)")

from studio.schematic3d import draw  # noqa: E402
from studio.schematic3d.draw import Scene, spin_angle, turned  # noqa: E402

HERE = Path(__file__).resolve().parent.parent / "schematic3d"
RECIPE = json.loads((HERE / "recipes" / "atp_synthase.json").read_text())
FITTED = json.loads((HERE / "shapes" / "atp_synthase.json").read_text())


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


TOY_RECIPE = {"name": "toy", "parts": {"ring": {"chains": list("GHIJKL"), "kind": "capsule"},
                                       "knob": {"chains": ["A"], "kind": "capsule"},
                                       "stalk": {"chains": ["b"], "kind": "tube"}},
              "axle": {"ring": "ring", "down": "knob", "toward_x": "stalk"}, "membrane": {"parts": ["ring"]}}


@pytest.mark.parametrize("axis", [np.array([0, 0, 1.0]), np.array([1.0, 0.4, -0.3])])
def test_fit_finds_the_axle_however_the_structure_lies(axis):
    from studio.schematic3d.fit import fit_structure

    fitted = fit_structure(toy_structure(axis), TOY_RECIPE)
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

    recipe = json.loads(json.dumps(TOY_RECIPE))
    recipe["parts"]["knob"]["chains"] = ["A", "Z"]
    with pytest.raises(ValueError, match="no protein chains Z"):
        fit_structure(toy_structure(np.array([0, 0, 1.0])), recipe)


def test_a_spin_starts_still_eases_in_and_turns_clockwise_seen_from_above():
    assert spin_angle(0.4, turn_s=6, start_s=0.5, ramp_s=1) == 0
    assert spin_angle(1.5, 6, 0.5, 1) == pytest.approx(math.pi / 6)  # half the speed over the ramp: 30 degrees
    assert spin_angle(3.5, 6, 0.5, 1) == pytest.approx(2 * math.pi * 2.5 / 6)
    assert turned(np.array([[1.0, 0, 5]]), math.pi / 2) == pytest.approx(np.array([[0, 1.0, 5]]))  # z stays
    scene = Scene(RECIPE, FITTED, 480, 270)
    assert scene.angle(3.5) < 0  # the recipe's rotor turns clockwise seen from the membrane's top


def test_a_rod_inside_the_knob_is_painted_behind_all_of_it():
    scene = Scene(RECIPE, FITTED, 480, 270)
    for t in (0.0, 2.0, 4.0):
        order = [shape["part"] for *_, shape in scene.paint_order(t)]
        assert max(i for i, part in enumerate(order) if part == "rod") < min(i for i, part in enumerate(order) if part == "knob")


def test_protons_flow_only_once_the_rotor_turns():
    scene = Scene(RECIPE, FITTED, 480, 270)
    assert len(scene.flowing(0.2)) == 0
    one = scene.flowing(0.5 + RECIPE["flow"]["every_s"] / 2)
    assert len(one) >= 1 and np.isfinite(one).all()


def test_a_frame_turns_the_rotor_and_leaves_the_rest_alone():
    scene = Scene(RECIPE, FITTED, 960, 540)
    before = scene.image(0.0, text=False).toarray()
    after = scene.image(3.0, text=False).toarray()
    assert before.shape == (540, 960, 4)
    xs, ys, _ = scene.project([(0, -2.5, 0.0)])  # the front of the ring
    cx, cy = int(xs[0]), int(ys[0])
    assert (before[cy - 20:cy + 20, cx - 20:cx + 20] != after[cy - 20:cy + 20, cx - 20:cx + 20]).any()
    assert (before[:, :120] == after[:, :120]).all()  # the far left: page and membrane, drawn once
    assert scene.page() is scene.page()
    again = scene.image(3.0, text=False).toarray()
    assert (again == after).all()  # a frame depends only on its time


def test_the_command_draws_a_still(tmp_path):
    root = HERE.parent.parent
    done = subprocess.run([sys.executable, "-m", "studio.schematic3d.draw", str(HERE / "recipes" / "atp_synthase.json"),
                           str(HERE / "shapes" / "atp_synthase.json"), str(tmp_path), "--still", "2.0"],
                          cwd=root, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    still = tmp_path / "atp_synthase_2.00s.png"
    assert still.stat().st_size > 10_000
    assert skia.Image.open(str(still)).dimensions() == skia.ISize(draw.LAYOUT.width, draw.LAYOUT.height)
