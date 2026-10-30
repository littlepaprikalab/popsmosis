"""Where a graph's axes are in its picture, so the renderer writes their names at their ends (Timeline.axes).

The art has no writing, and an axis is no structure, so nothing locates it. But an axis is the one thing a graph
draws as a long, thin, straight line of its own: its curves are structures (their masks are left out), and a filled
region is thick. So each axis is found in the picture's pixels, as the longest thin straight run, across (the x
axis) or up (the y axis), of pixels well apart from the background and outside every structure. A run may pass
under a structure (a curve crossing the axis), as long as most of it is drawn. Free, and the same every time.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

INK = 60  # how far (the largest RGB channel) a pixel of an axis is from the background
GROW = 6  # art pixels: each structure's mask is grown this far, past its soft edge
SKIP = 6  # art pixels of background a line may skip (a painted line's wobble, a gap in a dashed one)
SHARE = 0.3  # an axis is drawn along at least this share of the picture's width (across) or height (up)
DRAWN = 0.6  # and along at least this share of its own length, the rest under structures
THIN = 20  # art pixels: an axis is at most this thick; a filled region is thicker
LONG_ENOUGH = 0.85  # of the lines this near the longest, the lowest is the x axis and the leftmost the y axis


@dataclass(frozen=True)
class Line:
    at: float  # its row (across) or its column (up), through its middle
    start: float  # where it begins along its length: its left end, or its top
    end: float  # its right end, or its bottom


def grow(mask: np.ndarray, r: int) -> np.ndarray:
    """A boolean mask grown by r pixels every way (a square), by running sums."""
    out = mask
    for axis in (0, 1):
        pad = [(r + 1, r) if a == axis else (0, 0) for a in (0, 1)]
        sums = np.cumsum(np.pad(out, pad).astype(np.int32), axis=axis)
        n = out.shape[axis]
        out = (np.take(sums, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
               - np.take(sums, np.arange(n), axis=axis)) > 0
    return out


def find(pixels: np.ndarray, paper: tuple[int, int, int], covered: np.ndarray) -> tuple[Line | None, Line | None]:
    """The x axis and the y axis of a picture (H x W x 3 or more, in the order of `paper`, its background colour),
    given where its structures are (H x W, True inside one). Either is None when the picture has no such line."""
    under = grow(covered, GROW)
    ink = (np.abs(pixels[..., :3].astype(np.int16) - np.array(paper, np.int16)).max(axis=2) > INK) & ~under
    return _longest(ink, under, lowest=True), _longest(ink.T, under.T, lowest=False)


def _longest(ink: np.ndarray, under: np.ndarray, lowest: bool) -> Line | None:
    """The axis among the rows of `ink` (its columns, transposed): of the thin lines near the longest, the lowest
    row, or the leftmost column."""
    need = SHARE * ink.shape[1]
    runs: dict[int, tuple[int, int]] = {}
    for row in np.flatnonzero(ink.sum(axis=1) >= need):
        if run := _run(ink[row], under[row], need):
            runs[row] = run
    lines: list[Line] = []
    band: list[int] = []
    for row in [*sorted(runs), None]:
        if band and (row is None or row != band[-1] + 1 or not _overlap(runs[row], runs[band[-1]])):
            if len(band) <= THIN:
                lines.append(Line(at=float(band[0] + band[-1]) / 2, start=float(np.median([runs[b][0] for b in band])),
                                  end=float(np.median([runs[b][1] for b in band]))))
            band = []
        if row is not None:
            band.append(row)
    if not lines:
        return None
    longest = max(line.end - line.start for line in lines)
    near = [line for line in lines if line.end - line.start >= LONG_ENOUGH * longest]
    return max(near, key=lambda line: line.at) if lowest else min(near, key=lambda line: line.at)


def _run(ink: np.ndarray, under: np.ndarray, need: float) -> tuple[int, int] | None:
    """The row's longest straight run, from its first drawn pixel to its last, skipping no more than SKIP pixels of
    background and passing under structures; None unless it is drawn along `need` pixels and DRAWN of its length."""
    passable = np.flatnonzero(ink | under)
    breaks = np.flatnonzero(np.diff(passable) > SKIP + 1)
    starts, ends = np.r_[passable[0], passable[breaks + 1]], np.r_[passable[breaks], passable[-1]] + 1
    drawn_to = np.r_[0, np.cumsum(ink)]
    k = int(np.argmax(drawn_to[ends] - drawn_to[starts]))
    drawn = np.flatnonzero(ink[starts[k]:ends[k]]) + starts[k]
    if len(drawn) < need or len(drawn) < DRAWN * (drawn[-1] + 1 - drawn[0]):
        return None
    return int(drawn[0]), int(drawn[-1]) + 1


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two rows' runs are mostly the same stretch: the rows of one line."""
    return min(a[1], b[1]) - max(a[0], b[0]) >= 0.5 * min(a[1] - a[0], b[1] - b[0])
