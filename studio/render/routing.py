"""Paths through the parts of a picture, for the particles that travel them.

A flow's particles used to go from one part's stop to the next in a straight line or a gentle arc, cutting across
the picture: round 17's nephron sent its filtrate from the capsule to the collecting duct straight across the page,
past the whole loop, under arrows that stayed drawn. Now each leg of a flow, from where it stops in one part to where
it stops in the next, runs through the parts themselves where their outlines (the masks the locate step made) join:
along the middle of a tube and round its bends, out of one part and into the next where they meet. Where they do not
join (two parts apart, an outline in pieces), the leg is left to the old hop.

Found on a grid of GRID art pixels a cell: the cheapest path by Dijkstra, a cell costing more the nearer it lies to
the outline's edge, so the path keeps to the middle; then smoothed. numpy only.
"""

from __future__ import annotations

import heapq
import math

import numpy as np

Point = tuple[float, float]

GRID = 6  # art pixels a cell
DEPTH_MAX = 8  # cells from the edge: deeper than this counts as the middle
EDGE_COST = 6.0  # a cell on the edge costs this much more to cross than one in the middle
SNAP = 8  # cells a leg's end may be moved to reach the outline (a label point just outside a thin part)
SMOOTH = 3  # cells each side averaged into each point of the path
DETOUR_MAX = 8.0  # a path this many times longer than the straight hop has found a way round, not through


def cells(mask: np.ndarray) -> np.ndarray:
    """A part's outline (a boolean mask, art pixels) as grid cells: a cell is in when any of its pixels is."""
    h, w = mask.shape
    rows, cols = -(-h // GRID), -(-w // GRID)
    padded = np.zeros((rows * GRID, cols * GRID), bool)
    padded[:h, :w] = mask
    return padded.reshape(rows, GRID, cols, GRID).any(axis=(1, 3))


def joined(*parts: np.ndarray) -> np.ndarray:
    """The parts' cells together, grown by a cell so two parts drawn edge to edge (a hairline between their
    outlines) join."""
    region = np.logical_or.reduce(parts)
    grown = region.copy()
    grown[1:] |= region[:-1]
    grown[:-1] |= region[1:]
    grown[:, 1:] |= region[:, :-1]
    grown[:, :-1] |= region[:, 1:]
    return grown


def depth(region: np.ndarray) -> np.ndarray:
    """How many cells each cell lies inside the region's edge, up to DEPTH_MAX; 0 outside."""
    out = np.zeros(region.shape, np.int16)
    inside = region.copy()
    for k in range(1, DEPTH_MAX + 1):
        out[inside] = k
        shrunk = inside.copy()
        shrunk[1:] &= inside[:-1]
        shrunk[:-1] &= inside[1:]
        shrunk[:, 1:] &= inside[:, :-1]
        shrunk[:, :-1] &= inside[:, 1:]
        shrunk[0], shrunk[-1], shrunk[:, 0], shrunk[:, -1] = False, False, False, False
        inside = shrunk
        if not inside.any():
            break
    return out


def _snap(region: np.ndarray, at: Point) -> tuple[int, int] | None:
    """The region's cell nearest the point, within SNAP cells of it; None when there is none."""
    rows, cols = region.shape
    y, x = int(at[1] // GRID), int(at[0] // GRID)
    y0, y1, x0, x1 = max(0, y - SNAP), min(rows, y + SNAP + 1), max(0, x - SNAP), min(cols, x + SNAP + 1)
    ys, xs = np.nonzero(region[y0:y1, x0:x1])
    if not len(ys):
        return None
    k = int(np.argmin((ys + y0 - y) ** 2 + (xs + x0 - x) ** 2))
    return int(ys[k] + y0), int(xs[k] + x0)


STEPS = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
         (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]


def route(region: np.ndarray, a: Point, b: Point) -> list[Point] | None:
    """The path from a to b (art pixels) through the region (cells), keeping to its middle, from a to b exactly;
    None when the two are not joined inside it, or only the long way round."""
    start, end = _snap(region, a), _snap(region, b)
    if start is None or end is None:
        return None
    cost = 1.0 + EDGE_COST / np.maximum(depth(region), 1)
    rows, cols = region.shape
    best = {start: 0.0}
    came: dict[tuple[int, int], tuple[int, int]] = {}
    heap = [(0.0, start)]
    while heap:
        spent, cell = heapq.heappop(heap)
        if cell == end:
            break
        if spent > best.get(cell, math.inf):
            continue
        y, x = cell
        for dy, dx, step in STEPS:
            ny, nx = y + dy, x + dx
            if 0 <= ny < rows and 0 <= nx < cols and region[ny, nx]:
                total = spent + step * cost[ny, nx]
                if total < best.get((ny, nx), math.inf):
                    best[(ny, nx)] = total
                    came[(ny, nx)] = cell
                    heapq.heappush(heap, (total, (ny, nx)))
    if end not in best:
        return None
    path = [end]
    while path[-1] != start:
        path.append(came[path[-1]])
    path.reverse()
    points = _smooth([((x + 0.5) * GRID, (y + 0.5) * GRID) for y, x in path])
    points = [a, *points[1:-1], b]
    if length(points) > DETOUR_MAX * max(math.dist(a, b), 4 * GRID):
        return None
    return points


def _smooth(points: list[Point]) -> list[Point]:
    """Each point the average of SMOOTH points either side (fewer near the ends, which stay put), every other one
    kept: the grid's steps become a curve."""
    if len(points) < 3:
        return points
    out = []
    for i in range(len(points)):
        near = points[max(0, i - SMOOTH):i + SMOOTH + 1]
        out.append((sum(q[0] for q in near) / len(near), sum(q[1] for q in near) / len(near)))
    kept = out[::2]
    if kept[-1] != out[-1]:
        kept.append(out[-1])
    kept[0] = points[0]
    kept[-1] = points[-1]
    return kept


def length(points: list[Point]) -> float:
    return sum(math.dist(p, q) for p, q in zip(points, points[1:]))
