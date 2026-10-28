"""Fit a smooth shape to each subunit of a structure, as the recipe groups them.

    python -m studio.schematic3d.fit RECIPE STRUCTURE.cif OUT.json

Every chain becomes a capsule along its long axis, or for a long thin chain (`"kind": "tube"`) a
tube along its smoothed path. Coordinates are in nm, in the machine's own frame: the axle is the z
axis through the middle of the ring the recipe names, the part it names `down` is below (-z), and
the part it names `toward_x` is toward +x. The recipe's `membrane.parts` are the ones that sit in
the membrane; their footprint is kept so the drawing can cut the membrane away around them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def frame(z_axis: np.ndarray, toward: np.ndarray) -> np.ndarray:
    """Rows x, y, z of a right-handed frame with z along z_axis and x as close to `toward` as it can be."""
    z = z_axis / np.linalg.norm(z_axis)
    x = toward - np.dot(toward, z) * z
    x /= np.linalg.norm(x)
    return np.array([x, np.cross(z, x), z])


def alpha_carbons(structure) -> dict[str, np.ndarray]:
    """Each protein chain's alpha carbons, in nm (a gemmi Structure)."""
    ca: dict[str, list] = {}
    for chain in structure[0]:
        pts = [[a.pos.x, a.pos.y, a.pos.z] for res in chain for a in res if a.name == "CA"]
        if pts:
            ca.setdefault(chain.name, []).extend(pts)
    return {k: np.array(v) / 10.0 for k, v in ca.items()}


def capsule(pts: np.ndarray, max_radius: float | None = None) -> dict:
    """The capsule along the points' long axis that covers most of them."""
    mid = pts.mean(axis=0)
    axis = np.linalg.svd(pts - mid)[2][0]
    along = (pts - mid) @ axis
    lo, hi = np.percentile(along, [4, 96])
    radial = np.linalg.norm((pts - mid) - np.outer(along, axis), axis=1)
    radius = float(max(np.percentile(radial, 70), 0.55))
    if max_radius:
        radius = min(radius, max_radius)
    a, b = mid + axis * lo, mid + axis * hi
    length = float(np.linalg.norm(b - a))
    # A capsule's length runs between its cap centres: keep its overall size true.
    inset = min(radius, length / 2 - 0.05) if length > 2 * radius else 0.0
    a, b = a + axis * inset, b - axis * inset
    return {"kind": "capsule", "a": a.round(3).tolist(), "b": b.round(3).tolist(), "radius": round(radius, 3)}


def tube(pts: np.ndarray, radius: float) -> dict:
    """A tube along the chain's path, smoothed over 9 residues, a point every 6."""
    k = 9
    smooth = np.array([pts[max(0, i - k // 2):i + k // 2 + 1].mean(axis=0) for i in range(len(pts))])
    return {"kind": "tube", "path": smooth[::6].round(3).tolist(), "radius": radius}


def fit_structure(structure, recipe: dict) -> dict:
    ca = alpha_carbons(structure)
    parts = recipe["parts"]
    chains_of = {name: [c for c in part["chains"] if c in ca] for name, part in parts.items()}
    missing = sorted({c for part in parts.values() for c in part["chains"]} - set(ca))
    if missing:
        raise ValueError(f"the structure has no protein chains {', '.join(missing)}")

    axle = recipe["axle"]
    ring = chains_of[axle["ring"]]
    rings = np.array([ca[c].mean(axis=0) for c in ring])
    centre = rings.mean(axis=0)
    normal = np.linalg.svd(rings - centre)[2][2]
    down = np.vstack([ca[c] for c in chains_of[axle["down"]]]).mean(axis=0)
    if np.dot(down - centre, normal) > 0:
        normal = -normal
    toward = np.vstack([ca[c] for c in chains_of[axle["toward_x"]]]).mean(axis=0) - centre
    rotation = frame(normal, toward)

    def place(points):
        return (np.asarray(points) - centre) @ rotation.T

    shapes = []
    for name, part in parts.items():
        for chain in chains_of[name]:
            pts = place(ca[chain])
            shape = tube(pts, part.get("radius", 0.55)) if part.get("kind") == "tube" else capsule(pts, part.get("max_radius"))
            shapes.append({"chain": chain, "part": name, **shape})

    ring_pts = place(np.vstack([ca[c] for c in ring]))
    in_membrane = place(np.vstack([ca[c] for name in recipe["membrane"]["parts"] for c in chains_of[name]]))
    return {
        "recipe": recipe["name"],
        "shapes": shapes,
        "ring_radius": round(float(np.percentile(np.hypot(ring_pts[:, 0], ring_pts[:, 1]), 95)), 3),
        # Where the parts in the membrane are, seen from above, near the membrane's middle.
        "membrane_footprint": in_membrane[np.abs(in_membrane[:, 2]) < 3.0][:, :2].round(2).tolist(),
    }


def fit_file(recipe_path: Path, structure_path: Path) -> dict:
    import gemmi

    return fit_structure(gemmi.read_structure(str(structure_path)), json.loads(recipe_path.read_text()))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__.split("\n\n")[1])
    fitted = fit_file(Path(sys.argv[1]), Path(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(fitted))
    print(f"{len(fitted['shapes'])} shapes, ring radius {fitted['ring_radius']} nm -> {sys.argv[3]}")
