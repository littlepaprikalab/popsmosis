"""What the studio's checks found in a droplet, sent to the API for an admin's review under the lesson on /learn
(GET /api/answers/:id/review, api/src/lib/review.ts); never shown to the learner:

    code_checks()   checks.json, the code checks on the bundle, sent with the lesson (studio/run.py)
    art_checks()    the art checker's look at each scene's picture, every try, sent with READY (studio/steps/upload.py),
                    with where the kept picture went in the private bucket, so the page can show it beside them

The fact-check, the third part of the review, already goes with the lesson. Like the bill (ledger.try_breakdown()),
neither is ever the reason a post or an upload fails: one that cannot be read, or that is more than the API takes, is
left out and the review says it has none.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from studio import media

# What the API takes (api/src/lib/review.ts).
CHECKS_MAX_BYTES = 64 * 1024
ART_CHECKS_MAX_BYTES = 128 * 1024
ART_TRIES_MAX = 10


def _fits(report: dict, limit: int, what: str) -> dict | None:
    # Strict JSON, as the API parses it.
    size = len(json.dumps(report, allow_nan=False).encode("utf-8"))
    if size > limit:
        print(f"{what} left out: {size} bytes, more than the API takes ({limit})", file=sys.stderr)
        return None
    return report


def code_checks(job: Path) -> dict | None:
    """checks.json as the bundle step wrote it, or None."""
    try:
        report = json.loads((job / "checks.json").read_text(encoding="utf-8"))
        if not isinstance(report, dict) or not isinstance(report.get("problems"), list):
            raise ValueError("no problems list")
        return _fits(report, CHECKS_MAX_BYTES, "checks")
    except (OSError, ValueError) as e:
        print(f"checks left out: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def art_checks(job: Path, uploaded: dict[str, str]) -> dict | None:
    """Each scene's art check (art.review()), with `file`, where its picture went in the private bucket under the
    answer's folder (`uploaded`: a job path to its remote path), or None when it was not uploaded."""
    from studio.steps import art  # its drawing libraries, which the lesson's runner (code_checks()) never needs

    try:
        scenes = art.review(job)
        for scene in scenes:
            scene["file"] = uploaded.get(media.art_path(scene["scene"]))
            scene["tries"] = scene["tries"][-ART_TRIES_MAX:]
        return _fits({"scenes": scenes}, ART_CHECKS_MAX_BYTES, "artChecks")
    except Exception as e:  # a record from an older format: the videos go READY without it
        print(f"artChecks left out: {type(e).__name__}: {e}", file=sys.stderr)
        return None
