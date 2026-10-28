"""Fit smooth shapes to a structure's chains, in the machine's own frame, with nothing set by hand.

The frame: for an entry OPM has placed in its membrane, OPM's (the membrane's normal is z, its
middle at z = 0, the inner side toward -z), turned about z so the machine shows its widest side
to the camera. For any other entry, its principal axes: z along the longest, x along the next.

Each chain's backbone (alpha carbons; phosphates for RNA and DNA) becomes:

- a tube along its smoothed path, when it is long and thin (a stalk, a long helix);
- one capsule along its long axis, when it is small enough to read as one lobe;
- else a capsule per lobe: its backbone split into compact clusters of about LOBE residues.

Coordinates in the file are Å; everything returned is nm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

ONE_LOBE = 450  # residues: a chain up to this size is one capsule
LOBE = 300  # residues per capsule in a bigger chain
TUBE_RATIO = 4.5  # a chain this many times longer than wide...
TUBE_LENGTH = 7.0  # ...and at least this long (nm) is a tube
TUBE_RADIUS = 0.5


def backbone(structure, chains: set[str]) -> dict[str, np.ndarray]:
    """Each chain's backbone points in nm, in residue order (a gemmi Structure)."""
    out: dict[str, list] = {}
    for chain in structure[0]:
        if chain.name not in chains:
            continue
        for residue in chain:
            atom = residue.find_atom("CA", "*") or residue.find_atom("P", "*")
            if atom is not None and not residue.het_flag == "H":
                out.setdefault(chain.name, []).append([atom.pos.x, atom.pos.y, atom.pos.z])
    return {name: np.array(points) / 10.0 for name, points in out.items() if len(points) >= 4}


def _axes(points: np.ndarray) -> np.ndarray:
    """Rows: the points' principal axes, longest first."""
    return np.linalg.svd(points - points.mean(axis=0), full_matrices=False)[2]


def _skewed(direction: np.ndarray, values: np.ndarray) -> np.ndarray:
    """The direction, turned so the points' long tail lies along it: a fixed choice of sign."""
    return direction if float(np.mean(values ** 3)) >= 0 else -direction


def frame(points: np.ndarray, oriented: bool) -> tuple[np.ndarray, np.ndarray]:
    """The centre and rotation (rows x, y, z) that put the machine in its frame."""
    if oriented:  # keep OPM's z: the membrane is the plane z = 0
        centre = np.array([*points[:, :2].mean(axis=0), 0.0])
        flat = points[:, :2] - centre[:2]
        along = np.linalg.svd(flat, full_matrices=False)[2][0]
        x = np.array([*_skewed(along, flat @ along), 0.0])
        z = np.array([0.0, 0.0, 1.0])
    else:
        centre = points.mean(axis=0)
        axes = _axes(points)
        z = _skewed(axes[0], (points - centre) @ axes[0])
        x = _skewed(axes[1], (points - centre) @ axes[1])
    x = x - np.dot(x, z) * z
    x /= np.linalg.norm(x)
    return centre, np.array([x, np.cross(z, x), z])


def capsule(points: np.ndarray) -> tuple[list[np.ndarray], float]:
    """The capsule along the points' long axis that covers most of them: its cap centres and radius."""
    mid = points.mean(axis=0)
    axis = _axes(points)[0]
    along = (points - mid) @ axis
    lo, hi = np.percentile(along, [4, 96])
    radial = np.linalg.norm((points - mid) - np.outer(along, axis), axis=1)
    radius = float(max(np.percentile(radial, 70), 0.55))
    a, b = mid + axis * lo, mid + axis * hi
    length = float(np.linalg.norm(b - a))
    inset = min(radius, length / 2 - 0.05) if length > 2 * radius else 0.0  # its overall size stays true
    return [a + axis * inset, b - axis * inset], radius


def tube(points: np.ndarray) -> list[np.ndarray]:
    """A path along the chain, smoothed over 9 residues, a point every 6."""
    k = 9
    smooth = np.array([points[max(0, i - k // 2):i + k // 2 + 1].mean(axis=0) for i in range(len(points))])
    return list(smooth[::6]) + ([smooth[-1]] if (len(smooth) - 1) % 6 else [])


def lobes(points: np.ndarray, count: int) -> list[np.ndarray]:
    """The points split into `count` compact clusters (k-means, seeded along the chain, so the same input
    always gives the same lobes)."""
    seeds = points[np.linspace(0, len(points) - 1, count).round().astype(int)]
    for _ in range(12):
        nearest = np.argmin(((points[:, None, :] - seeds[None, :, :]) ** 2).sum(axis=2), axis=1)
        seeds = np.array([points[nearest == i].mean(axis=0) if (nearest == i).any() else seeds[i] for i in range(count)])
    return [points[nearest == i] for i in range(count) if (nearest == i).sum() >= 4]


def chain_shapes(points: np.ndarray) -> list[tuple[str, list[np.ndarray], float]]:
    """(kind, points, radius) for each shape a chain is drawn as."""
    values = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    along = (points - points.mean(axis=0)) @ _axes(points)[0]
    length = float(np.subtract(*np.percentile(along, [96, 4])))
    if len(values) > 1 and values[1] > 0 and values[0] / values[1] > TUBE_RATIO and length > TUBE_LENGTH:
        return [("tube", tube(points), TUBE_RADIUS)]
    if len(points) <= ONE_LOBE:
        cap, radius = capsule(points)
        return [("capsule", cap, radius)]
    out = []
    for lobe in lobes(points, math.ceil(len(points) / LOBE)):
        cap, radius = capsule(lobe)
        out.append(("capsule", cap, radius))
    return out


@dataclass
class Fitted:
    shapes: list[dict]  # {"chain", "structure", "kind", "points", "radius"}, nm, in the machine's frame
    footprint: list[tuple[float, float]]  # where the machine crosses the membrane (x, y), when it sits in one
    sides: dict[str, str]  # each structure: "above the membrane", "in the membrane" or "below the membrane"


def fit(structure, owners: dict[str, str | None], half_thickness: float | None) -> Fitted:
    """Shapes for the chains in `owners` (chain -> the structure it belongs to, or None), in the machine's frame.

    `half_thickness` (nm) is the membrane's, when the coordinates are OPM's; None for any other."""
    chains = backbone(structure, set(owners))
    if not chains:
        raise ValueError("none of the chains has a backbone to draw")
    everything = np.vstack(list(chains.values()))
    centre, rotation = frame(everything, oriented=half_thickness is not None)
    placed = {name: (points - centre) @ rotation.T for name, points in chains.items()}
    shapes = []
    for name, points in placed.items():
        for kind, pts, radius in chain_shapes(points):
            shapes.append({"chain": name, "structure": owners.get(name), "kind": kind,
                           "points": [tuple(round(float(v), 3) for v in p) for p in pts], "radius": round(radius, 3)})
    footprint, sides = [], {}
    if half_thickness is not None:
        inside = np.vstack(list(placed.values()))
        crossing = inside[np.abs(inside[:, 2]) < half_thickness + 0.3]
        footprint = sorted({(round(float(x), 1), round(float(y), 1)) for x, y in crossing[:, :2]})
        for owner in {o for o in owners.values() if o}:
            pts = np.vstack([placed[c] for c, o in owners.items() if o == owner and c in placed] or [np.zeros((0, 3))])
            if len(pts):
                within = float(np.mean(np.abs(pts[:, 2]) < half_thickness))
                sides[owner] = ("in the membrane" if within >= 0.4 else
                                "above the membrane" if pts[:, 2].mean() > 0 else "below the membrane")
    return Fitted(shapes=shapes, footprint=footprint, sides=sides)
