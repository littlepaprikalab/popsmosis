"""Code checks on the bundle's names and direction: free, so they run on every bundle.

A structure is revealed when its name is sung or spoken, and its label then stays on
that diagram (docs/PLAN.md, step 7). Each line lists the structures it names; these
checks hold the lines and their direction to that list. What code can safely repair,
it does: a spotlight on a structure not yet named falls back to none, as the
timeline would. What it cannot, such as a line naming something no scene draws, is
reported for the prompt to fix.

Writes checks.json beside the bundle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from studio.jobs import write
from studio.schemas import Bundle, CheckReport, Problem


def run(job: Path) -> CheckReport:
    """Re-check a job's bundle.json in place. Lines written before `names` existed get it from their text."""
    data = json.loads((job / "bundle.json").read_text(encoding="utf-8"))
    bundle, report = review(Bundle.model_validate(fill_names(data)))
    write(job / "bundle.json", bundle)
    write(job / "checks.json", report)
    return report


def review(bundle: Bundle) -> tuple[Bundle, CheckReport]:
    """Check every line's names and direction; return the repaired bundle and what was found."""
    data = bundle.model_dump()
    canon = canonical_names(data["scenes"])
    drawn = {scene["id"]: {s["name"] for s in scene["structures"]} for scene in data["scenes"]}
    directions = {d["line_id"]: d for d in data["direction"]}
    problems = [
        Problem(line_id=f"scene:{scene}", rule="empty-scene", repaired=False,
                detail=f'scene "{scene}" draws no structures, so nothing on it can be revealed or spotlit')
        for scene, structures in drawn.items() if not structures
    ]
    # The song and the lesson video are separate videos, so each reveals from scratch.
    for stream in (data["lyrics"], data["narration"]):
        revealed: dict[str, set[str]] = {scene: set() for scene in drawn}
        for line in stream:
            direction = directions[line["id"]]
            scene = direction["scene"]
            named = _check_names(line, canon, scene, drawn[scene], problems)
            revealed[scene] |= named & drawn[scene]
            _check_direction(line["id"], direction, canon, scene, drawn[scene], revealed[scene], problems)
    return Bundle.model_validate(data), CheckReport(problems=problems)


def canonical_names(scenes: list[dict]) -> dict[str, str]:
    """Every structure name and alias, lowercased, mapped to the structure's name."""
    canon: dict[str, str] = {}
    for scene in scenes:
        for structure in scene["structures"]:
            for word in (structure["name"], *structure["aliases"]):
                canon.setdefault(word.lower(), structure["name"])
    return canon


def said(text: str, canon: dict[str, str]) -> dict[str, str]:
    """The structures a line says, each mapped to the words it uses for it.

    Longer names win, so "motor cortex" is not also read as "cortex".
    """
    found: dict[str, str] = {}
    taken: list[tuple[int, int]] = []
    for word in sorted(canon, key=len, reverse=True):
        for m in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE):
            if any(start < m.end() and m.start() < end for start, end in taken):
                continue
            taken.append(m.span())
            found.setdefault(canon[word], m.group())
    return found


def fill_names(data: dict) -> dict:
    """Give lines from before the `names` field the names their text says."""
    canon = canonical_names(data["scenes"])
    for line in [*data["lyrics"], *data["narration"]]:
        line.setdefault("names", list(said(line["text"], canon).values()))
    return data


def _check_names(line: dict, canon: dict[str, str], scene: str, drawn: set[str], problems: list[Problem]) -> set[str]:
    kept, named = [], set()
    for name in line["names"]:
        structure = canon.get(name.lower())
        if structure is None:
            problems.append(Problem(line_id=line["id"], rule="unknown-name", repaired=False,
                                    detail=f'names "{name}", which no scene draws'))
            kept.append(name)
        elif not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", line["text"], re.IGNORECASE):
            problems.append(Problem(line_id=line["id"], rule="name-not-said", repaired=True,
                                    detail=f'lists "{name}" but never says it; dropped'))
        else:
            kept.append(name)
            named.add(structure)
    for structure, word in said(line["text"], canon).items():
        if structure not in named:
            problems.append(Problem(line_id=line["id"], rule="unlisted-name", repaired=True,
                                    detail=f'says "{word}" but does not list it; added'))
            kept.append(word)
            named.add(structure)
    line["names"] = kept
    for structure in sorted(named - drawn):
        problems.append(Problem(line_id=line["id"], rule="not-in-scene", repaired=False,
                                detail=f'names {structure}, which scene "{scene}" does not draw'))
    return named


def _check_direction(line_id: str, direction: dict, canon: dict[str, str], scene: str, drawn: set[str],
                     revealed: set[str], problems: list[Problem]) -> None:
    def misplaced(name: str) -> str | None:
        structure = canon.get(name.lower())
        if structure not in drawn:
            return f'is not drawn in scene "{scene}"'
        if structure not in revealed:
            return f'has not been named on scene "{scene}" yet'
        return None

    if direction["spotlight"] and (why := misplaced(direction["spotlight"])):
        problems.append(Problem(line_id=line_id, rule="spotlight", repaired=True,
                                detail=f"spotlight {direction['spotlight']} {why}; no spotlight instead"))
        direction["spotlight"] = ""
    target = direction["camera"]["target"]
    if target and (why := misplaced(target)):
        problems.append(Problem(line_id=line_id, rule="camera", repaired=True,
                                detail=f"camera on {target} {why}; whole scene instead"))
        direction["camera"] = {"target": "", "zoom": 1.0}
    stops = [p for p in direction["particles"] if canon.get(p.lower()) in drawn]
    if stops != direction["particles"]:
        stops = stops if len(stops) >= 2 else []
        problems.append(Problem(line_id=line_id, rule="particles", repaired=True,
                                detail=f'particles name structures scene "{scene}" does not draw; kept {stops}'))
        direction["particles"] = stops
