"""Repairs code makes to a Muse Image picture once its structures are located (studio/steps/locate.py),
before anything is painted again: a repair costs at most a helper look or two where a repaint costs a
picture, its check and locating it again, and it does what it says where a repaint is a gamble (Muse Image
drew ATP synthase upside down four times in a row, whatever the request said).

- clear_space: a space something builds up in (Bundle.builds_up) is cleared of small specks painted in
  it that are no structure's: the video fills that space with its own dots, and painted ones look like
  them and never move. Free: no model is asked.
- flip: a structure the art check found out of place, and the helper then says is upside down, is
  turned over in place about its narrowest row (a mushroom-shaped protein's stalk, which stays in its
  membrane), and where it was is filled from beside it: with a copy of the picture just beside it when
  that is clear of other structures and matches what borders the part, else with each row carried across
  from the part's left edge to its right. Both suit a diagram in bands, a membrane in cross-section, where
  what lies beside a part is what lies behind it. The art check looks again, and the flip is kept only if
  fewer structures are out of place and no more labels are wrong (the picture labels its own parts, and a
  label inside the part turns over with it).

Until Oct 31 a third repair, unwrite, painted out the writing in a picture, for the video's own labels; a
picture now labels its own parts (studio/steps/art.py).

What it repairs is added to art/<scene>.json under "repaired".
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
import skia

from studio import media, svg
from studio.schemas import Bundle, Scene, Strict

SPECK_MAX = 2500  # art pixels: a painted dot or sphere, not a structure
BESIDE_LIKE = 25.0  # how close (RGB, on average) the picture beside a part must be to what borders it, to copy
SPECK_APART = 20.0  # how far (Lab, lightness counted half) a speck's colour is from its space's
NEAR_OTHERS = 6  # art pixels kept clear round every other structure: a speck touching one is left alone
EDGE = 3  # art pixels: a speck this near the space's edge is the edge's own shading, left alone
WAIST_BAND = 0.2  # the narrowest row is looked for between this far down the part and as far up from its foot
FLIP_PROMPT = """You look at one illustration for an educational video, where a check found these \
structures out of place. Name, in `upside_down`, each structure below that is drawn upside down: its \
top where its bottom should be (a mushroom-shaped protein with its cap on the wrong side of its \
membrane). Use the names exactly as given. Leave out a structure that is out of place in any other way, \
and leave the list empty when none is upside down."""


class UpsideDown(Strict):
    """The helper's answer: which of the structures found out of place are upside down."""

    upside_down: list[str]


def read_rgb(path: Path) -> np.ndarray:
    return skia.Image.open(str(path)).toarray(colorType=skia.kRGBA_8888_ColorType)[..., :3].copy()


def _lab(rgb: np.ndarray) -> np.ndarray:
    from studio.steps.locate import (
        _lab as lab,  # the locate step's sRGB-to-Lab, one place
    )
    return lab(rgb)


def _components(candidate: np.ndarray, limit: int) -> list[np.ndarray]:
    """The 4-connected groups of True pixels, as (n, 2) arrays of (y, x); a group bigger than `limit` is
    dropped as soon as it grows past it."""
    seen = np.zeros_like(candidate)
    h, w = candidate.shape
    groups = []
    for y0, x0 in zip(*np.nonzero(candidate)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        queue, group, big = deque([(y0, x0)]), [], False
        while queue:
            y, x = queue.popleft()
            if not big:
                group.append((y, x))
                big = len(group) > limit
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and candidate[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        if not big:
            groups.append(np.array(group))
    return groups


def _grow(mask: np.ndarray, by: int) -> np.ndarray:
    """The mask grown by `by` pixels (a square step at a time)."""
    out = mask.copy()
    for _ in range(by):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def clear_space(pixels: np.ndarray, space: np.ndarray, others: list[np.ndarray]) -> int:
    """Paint over the small specks in a space with the space's own colour round each; return how many.
    `pixels` (RGB) is changed in place."""
    if not space.any():
        return 0
    lab = _lab(pixels)
    weight = np.array([0.5, 1.0, 1.0], np.float32)
    usual = np.median(lab[space], axis=0)
    apart = np.sqrt((((lab - usual) * weight) ** 2).sum(-1)) > SPECK_APART
    keep_clear = _grow(np.logical_or.reduce(others), NEAR_OTHERS) if others else np.zeros_like(space)
    edge = _grow(~space, EDGE)  # a speck lies wholly inside the space: one at its edge is the edge's own shading
    candidate = space & apart
    cleared = 0
    for group in _components(candidate, SPECK_MAX):
        ys, xs = group[:, 0], group[:, 1]
        if keep_clear[ys, xs].any() or edge[ys, xs].any():
            continue
        speck = np.zeros_like(space)
        speck[ys, xs] = True
        speck = _grow(speck, 2) & space  # its soft edge too
        y0, y1, x0, x1 = max(ys.min() - 12, 0), ys.max() + 13, max(xs.min() - 12, 0), xs.max() + 13
        ring = space[y0:y1, x0:x1] & ~candidate[y0:y1, x0:x1] & ~speck[y0:y1, x0:x1]
        if not ring.any():
            continue
        pixels[speck] = np.median(pixels[y0:y1, x0:x1][ring], axis=0).astype(np.uint8)
        cleared += 1
    return cleared


def waist(mask: np.ndarray) -> int:
    """The middle of the part's narrow stretch, away from its very top and foot: a mushroom's stalk, whose
    middle is where its membrane runs."""
    rows = np.nonzero(mask.any(1))[0]
    top, foot = int(rows.min()), int(rows.max())
    band = int((foot - top) * WAIST_BAND)
    widths = mask[top + band:foot - band + 1].sum(1)
    shown = widths[widths > 0]
    if not len(shown):
        return (top + foot) // 2
    narrow = np.nonzero((widths > 0) & (widths <= 1.3 * shown.min()))[0]
    return top + band + int(np.median(narrow))


def flip(pixels: np.ndarray, mask: np.ndarray, others: list[np.ndarray]) -> np.ndarray:
    """Turn a structure over in place about its narrowest row, filling where it was from beside it.
    Changes `pixels` (RGB) and returns the structure's new mask."""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    pivot = waist(mask)
    new_ys = 2 * pivot - ys
    inside = (new_ys >= 0) & (new_ys < h)
    flipped = np.zeros_like(mask)
    flipped[new_ys[inside], xs[inside]] = True
    part = pixels.copy()
    old = _grow(mask, 2)  # its soft edge too
    rows = np.nonzero(old.any(1) | flipped.any(1))[0]
    r0, r1 = int(rows.min()), int(rows.max()) + 1
    c0, c1 = max(x0 - 2, 0), min(x1 + 2, w)
    width = c1 - c0
    taken = np.logical_or.reduce(others) if others else np.zeros_like(mask)
    gap = old[r0:r1, c0:c1]
    filled = False
    for s0, edge in ((c0 - width - 4, max(c0 - 1, 0)), (c1 + 4, min(c1, w - 1))):  # left, else right
        if s0 < 0 or s0 + width > w or taken[r0:r1, s0:s0 + width].mean() > 0.02:
            continue
        beside = pixels[r0:r1, s0:s0 + width]
        border = pixels[r0:r1, edge].astype(float)
        if np.abs(np.median(beside, axis=1) - border).mean() > BESIDE_LIKE:
            continue  # not what lies behind the part
        region = pixels[r0:r1, c0:c1]
        region[gap] = beside[gap]
        filled = True
        break
    if not filled:  # each row carried across, from the part's left edge to its right
        left = pixels[r0:r1, max(c0 - 1, 0)].astype(float)
        right = pixels[r0:r1, min(c1, w - 1)].astype(float)
        across = np.linspace(0.0, 1.0, width)[None, :, None]
        blend = (left[:, None, :] * (1 - across) + right[:, None, :] * across).astype(np.uint8)
        region = pixels[r0:r1, c0:c1]
        region[gap] = blend[gap]
    pixels[new_ys[inside], xs[inside]] = part[ys[inside], xs[inside]]
    return flipped


def repair(job: Path, bundle: Bundle, scene: Scene, located: dict[str, tuple[np.ndarray, svg.Found]],
           ask, check) -> list[str]:
    """Repair the scene's picture where code can; update its located masks to match. `check(job, bundle, scene)`
    looks at the picture as the art step's check does and returns its ArtCheck. Returns what was repaired."""
    import json

    path = job / media.art_path(scene.id)
    made_path = job / media.check_path(scene.id)
    made = json.loads(made_path.read_text(encoding="utf-8")) if made_path.exists() else {}
    pixels = read_rgb(path)
    done: list[str] = []

    spaces = {b.region for b in bundle.builds_up}
    for name in spaces & set(located):
        others = [m for other, (m, _) in located.items() if other != name]
        if cleared := clear_space(pixels, located[name][0], others):
            done.append(f"cleared {cleared} speck{'s' if cleared != 1 else ''} from {name}")

    misplaced = (made.get("check") or {}).get("misplaced") or []
    if misplaced:
        names = [s.name for s in scene.structures if s.name in located]
        prompt = ("The structures found out of place:\n" + "\n".join(f"- {m}" for m in misplaced)
                  + "\n\nThe structures in the picture:\n" + "\n".join(f"- {n}" for n in names))
        answer = UpsideDown(**ask("helper", prompt, UpsideDown, [path], system=FLIP_PROMPT,
                                  label=f"repair {scene.id}"))
        trial = pixels.copy()
        masks = {}
        for name in answer.upside_down:
            if name in located:
                others = [m for other, (m, _) in located.items() if other != name]
                masks[name] = flip(trial, located[name][0], others)
        if masks:
            before = len(misplaced)
            saved = path.read_bytes()
            path.write_bytes(svg.png(trial))
            try:
                after = check(job, bundle, scene)
            except BaseException:
                # The picture as it was, which its record (written only below) still describes: left flipped, the
                # step's retry would find it out of place by that record's check and could turn it back over.
                path.write_bytes(saved)
                raise
            # A flip that turns a painted label over with its part comes back with that label wrong: no better.
            labels = len((made.get("check") or {}).get("labels") or [])
            if len(after.misplaced) < before and len(after.labels) <= labels:
                pixels = trial
                for name, new in masks.items():
                    found = svg.locate(new)
                    if found is None:
                        del located[name]  # too little of it left in the picture to label
                    else:
                        located[name] = (new, found)
                done += [f"turned {name} over" for name in masks]
                made["check"] = after.model_dump()
            else:
                path.write_bytes(saved)  # no better: as it was
    if done:
        path.write_bytes(svg.png(pixels))
        made["repaired"] = made.get("repaired", []) + done
        made_path.write_text(json.dumps(made, indent=2, ensure_ascii=False), encoding="utf-8")
    return done
