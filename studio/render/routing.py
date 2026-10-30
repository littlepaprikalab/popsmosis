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


# --- hand-offs: where a flow passes from one part into the next ------------------------------------------------
#
# Dots travelling the routes above never quite kept to a painted tube (the nephron's coils, a volcano's narrow
# conduit), and a learner watched them wander. A textbook draws an arrow where the flow passes from one part into the
# next instead, and so does the video now: at the parts' meeting, pointing the way the flow goes.

ARROW_CELLS = 7  # half an arrow's length, in cells
NEAR = 8  # cells about a meeting that say which way each part lies
ALONG_RATIO = 6.0  # a meeting this many times longer than wide is a long edge the flow runs along, not across
ALONG_MIN = 8  # ...when it is at least this many cells
ALONG_COS = 0.7  # ...and where the flow goes next lies that way along it: across it (a gas through the membrane it
# shares with the alveolus), the flow crosses the edge


def handoff(a: np.ndarray, b: np.ndarray, toward: Point) -> tuple[Point, Point]:
    """The arrow, tail and tip in art pixels, for a flow passing from part a into part b (their cells), going on
    toward `toward`. Where the two meet across a short join (one tube into the next), it crosses the join from a's
    side to b's; where they meet along a long edge (magma rising in the conduit up through a volcano), it runs along
    that edge, inside a, toward `toward`, when `toward` lies that way; where they do not meet, it spans the gap
    between them."""
    touch = _grown(a, 2) & _grown(b, 2)
    ys, xs = np.nonzero(touch)
    if not len(ys):
        if not a.any() or not b.any():
            return (0.0, 0.0), (0.0, 0.0)  # a part with nothing of it on the grid: no arrow
        (x0, y0), (x1, y1) = _span(a, b)
        if math.hypot(x1 - x0, y1 - y0) >= 2 * ARROW_CELLS * GRID:
            return (x0, y0), (x1, y1)
        # Nearly touching: an arrow as long as any other, across the gap's middle, rather than a stub of a head.
        middle = np.array([(x0 + x1) / 2, (y0 + y1) / 2]) / GRID
        way = np.array([x1 - x0, y1 - y0]) if (x0, y0) != (x1, y1) else np.array([toward[0] - x0, toward[1] - y0])
        return _ends(middle, way)
    if len(ys) >= ALONG_MIN:
        spread = np.cov(np.vstack([xs, ys]))
        values, vectors = np.linalg.eigh(spread)
        axis = vectors[:, 1]  # (x, y), the meeting's long way
        middle = np.array([ys.mean(), xs.mean()])
        way = np.array([toward[0] / GRID - middle[1], toward[1] / GRID - middle[0]])
        lies_along = abs(np.dot(axis, way)) >= ALONG_COS * (np.hypot(*way) or 1.0)
        if (values[0] <= 0 or values[1] / max(values[0], 1e-6) >= ALONG_RATIO) and lies_along:
            at = _near(a, middle)
            if np.dot(axis, way) < 0:
                axis = -axis
            return _ends(np.array([at[1], at[0]]) + 0.5, axis)
    k = int(np.argmin((ys - ys.mean()) ** 2 + (xs - xs.mean()) ** 2))
    meet = np.array([ys[k], xs[k]])
    side_a, side_b = _near(a, meet), _near(b, meet)
    way = np.array([side_b[1] - side_a[1], side_b[0] - side_a[0]])
    return _ends(np.array([meet[1], meet[0]]) + 0.5, way)


def _ends(centre: np.ndarray, way: np.ndarray) -> tuple[Point, Point]:
    unit = way / (np.hypot(*way) or 1.0)
    tail, tip = (centre - unit * ARROW_CELLS) * GRID, (centre + unit * ARROW_CELLS) * GRID
    return (float(tail[0]), float(tail[1])), (float(tip[0]), float(tip[1]))


def _near(cells: np.ndarray, at: np.ndarray) -> np.ndarray:
    """The middle (row, column) of the part's cells within NEAR of `at`; `at` itself when it has none there."""
    y, x = int(at[0]), int(at[1])
    window = cells[max(0, y - NEAR):y + NEAR + 1, max(0, x - NEAR):x + NEAR + 1]
    found = np.argwhere(window)
    return found.mean(0) + [max(0, y - NEAR), max(0, x - NEAR)] if len(found) else at.astype(float)


def _grown(cells: np.ndarray, times: int) -> np.ndarray:
    for _ in range(times):
        cells = joined(cells)
    return cells


def _span(a: np.ndarray, b: np.ndarray) -> tuple[Point, Point]:
    """From a's cell nearest b to b's cell nearest a (each sampled, a few hundred cells), in art pixels."""
    pa, pb = np.argwhere(a), np.argwhere(b)
    if not len(pa) or not len(pb):
        return (0.0, 0.0), (0.0, 0.0)
    pa, pb = pa[::max(1, len(pa) // 400)], pb[::max(1, len(pb) // 400)]
    far = ((pa[:, None, :] - pb[None, :, :]) ** 2).sum(-1)
    i, j = np.unravel_index(int(far.argmin()), far.shape)
    (ya, xa), (yb, xb) = pa[i] + 0.5, pb[j] + 0.5
    return (float(xa * GRID), float(ya * GRID)), (float(xb * GRID), float(yb * GRID))


def meet(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two parts (their cells) touch, or nearly: within the two cells either way that handoff() counts."""
    return bool((_grown(a, 2) & _grown(b, 2)).any())


def inside(points: list[Point], region: np.ndarray) -> float:
    """How far a path (art pixels) runs inside a part (its cells), measured a cell's length at a time, so a long
    straight step that only ends in the part counts only its end."""
    rows, cols = region.shape
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        n = max(1, math.ceil(length / GRID))
        for k in range(n):
            u = (k + 0.5) / n
            r, c = int((y0 + (y1 - y0) * u) // GRID), int((x0 + (x1 - x0) * u) // GRID)
            if 0 <= r < rows and 0 <= c < cols and region[r, c]:
                total += length / n
    return total
