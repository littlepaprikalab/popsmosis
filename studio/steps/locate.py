"""Step 4: locate each structure in the art (docs/PLAN.md).

With the interim SVG art the art step already wrote the masks and locations.json, so
there is nothing to do. Locating structures in generated images (Muse Image) needs SAM
3.1, which comes with the Meta key; until then this step only says so.
"""

from __future__ import annotations

from pathlib import Path

from studio import brain, media
from studio.steps import art


def run(job: Path, ask=brain.ask) -> str:
    if is_done(job):
        return "nothing to do: the art step located every structure"
    raise media.StepFailed("no locations.json for this bundle; run the art step first (its SVG art locates "
                           "every structure; locating structures with SAM 3.1 needs the Meta key)")


def is_done(job: Path) -> bool:
    return art.located(job) is not None
