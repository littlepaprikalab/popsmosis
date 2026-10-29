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
from studio.schemas import BPM_TAG, Bundle, CheckReport, Problem

MARKER_MAX = 8  # characters a particle's marker holds ("glucose", "NADPH"); a longer one is dropped
SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def run(job: Path) -> CheckReport:
    """Re-check a job's bundle.json in place."""
    bundle, report = review(load(job / "bundle.json"))
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
    problems += [
        Problem(line_id="structures", rule="shared-name", repaired=False,
                detail=f'"{word}" names {" and ".join(owners)}, but only reveals {canon[word]}')
        for word, owners in shared_names(data["scenes"]).items()
    ]
    # The song and the lesson video are separate videos, so each reveals from scratch.
    # A structure named once stays labelled on every later scene that draws it (studio/steps/timeline.py).
    for stream in (data["lyrics"], data["narration"]):
        heard: set[str] = set()
        for line in stream:
            direction = directions[line["id"]]
            scene = direction["scene"]
            heard |= _check_names(line, canon, scene, drawn[scene], problems) & drawn[scene]  # heard where drawn
            _check_direction(line["id"], direction, canon, scene, drawn[scene], heard & drawn[scene], problems)
    _check_builds_up(data, canon, problems)
    _check_stages(data, problems)
    _check_spins(data, canon, problems)
    _check_blocks(data, canon, problems)
    _check_conversions(data, canon, problems)
    _check_lyric_style(data, problems)
    problems += _check_tempo(bundle, data["music"])
    return Bundle.model_validate(data), CheckReport(problems=problems)


def _check_stages(data: dict, problems: list) -> None:
    """A process needs two stages or more: a lone one is an ordinary scene."""
    counts: dict[str, int] = {}
    for scene in data["scenes"]:
        if scene.get("stage_of"):
            counts[scene["stage_of"]] = counts.get(scene["stage_of"], 0) + 1
    for scene in data["scenes"]:
        if scene.get("stage_of") and counts[scene["stage_of"]] < 2:
            problems.append(Problem(line_id=f"scene:{scene['id']}", rule="stages", repaired=True,
                                    detail=f"the only stage of {scene['stage_of']}; an ordinary scene instead"))
            scene["stage_of"] = ""


def _check_blocks(data: dict, canon: dict[str, str], problems: list) -> None:
    """Each block names a structure, as its name, that particles end at: they pile up there in the video.
    Any other is left out."""
    ends = {canon.get(fold(d["particles"][-1])) for d in data["direction"] if d["particles"]}
    kept = []
    for block in data["blocks"]:
        name = canon.get(fold(block["structure"]))
        why = "names no structure" if not name else "is not where any particles end" if name not in ends else ""
        if why:
            problems.append(Problem(line_id="blocks", rule="blocks", repaired=True,
                                    detail=f"{block['structure']} blocking {why}; left out"))
            continue
        kept.append({"structure": name})
    data["blocks"] = kept


LYRIC_WORDS_MAX = 12
# A line that narrates the picture or the song instead of being sung ("Zoom inside to the chloroplast").
DIRECTION_LINE = re.compile(r"^\W*(?:now\s+)?(?:let'?s\s+)?(?:zoom|look|see|watch|here\s+(?:we|comes)|next\s+up)\b", re.I)


def _check_lyric_style(data: dict, problems: list) -> None:
    """A sung line is not a stage direction and is not long: both are only recorded (a better bundle, by the
    prompt, is the fix), so the operator sees which droplets sing them."""
    for line in data["lyrics"]:
        text = line["text"]
        why = ("narrates the picture instead of being sung" if DIRECTION_LINE.match(text) else
               f"runs {len(text.split())} words, over {LYRIC_WORDS_MAX}" if len(text.split()) > LYRIC_WORDS_MAX else "")
        if why:
            problems.append(Problem(line_id=line["id"], rule="lyric-style", repaired=False,
                                    detail=f'"{text}" {why}'))


CONVERT_MAX = 4  # species on one side of a conversion


def _check_conversions(data: dict, canon: dict[str, str], problems: list) -> None:
    """Each conversion names a structure, as its name, with something going in and something coming out, each as a
    textbook prints it. One structure shows one conversion; any other is left out."""
    kept, seen = [], set()
    for conv in data["conversions"]:
        name = canon.get(fold(conv["structure"]))
        sides = [[notation(x) for x in conv[side] if notation(x)] for side in ("inputs", "outputs")]
        why = ("names no structure" if not name else "repeats a structure's conversion" if name in seen else
               "has nothing going in or coming out" if not all(sides) else
               f"has more than {CONVERT_MAX} on one side" if any(len(side) > CONVERT_MAX for side in sides) else
               "has a name too long to print" if any(len(x) > MARKER_MAX for side in sides for x in side) else "")
        if why:
            problems.append(Problem(line_id="conversions", rule="conversions", repaired=True,
                                    detail=f"{conv['structure']} converting {why}; left out"))
            continue
        seen.add(name)
        kept.append({"structure": name, "inputs": sides[0], "outputs": sides[1]})
    data["conversions"] = kept


def _check_spins(data: dict, canon: dict[str, str], problems: list) -> None:
    """Each spin names a structure, as its name, that particles flow into or through: they are what turn it
    in the video. Any other is left out."""
    driven = {canon.get(fold(stop)) for d in data["direction"] for stop in d["particles"][1:]}
    kept = []
    for spin in data["spins"]:
        name = canon.get(fold(spin["structure"]))
        why = "names no structure" if not name else "is not where any particles flow" if name not in driven else ""
        if why:
            problems.append(Problem(line_id="spins", rule="spins", repaired=True,
                                    detail=f"{spin['structure']} turning {why}; left out"))
            continue
        kept.append({"structure": name})
    data["spins"] = kept


def _check_builds_up(data: dict, canon: dict[str, str], problems: list) -> None:
    """Each build-up names a structure, as its name, that particles carrying the same thing start or end in:
    they are what fill it and empty it in the video. Any other is left out."""
    kept = []
    for build in data["builds_up"]:
        carries, region = notation(build["carries"]), canon.get(fold(build["region"]))
        ends = {canon.get(fold(d["particles"][k])) for d in data["direction"]
                if d["particles"] and notation(d["carries"]) == carries for k in (0, -1)}
        why = ("is not marked" if not carries or len(carries) > MARKER_MAX else
               "names no structure" if not region else
               f"is not where any particles carrying {carries} start or end" if region not in ends else "")
        if why:
            problems.append(Problem(line_id="builds_up", rule="builds_up", repaired=True,
                                    detail=f"{build['carries']} building up in {build['region']} {why}; left out"))
            continue
        kept.append({"carries": build["carries"], "region": region})
    data["builds_up"] = kept


def _check_tempo(bundle: Bundle, music: dict) -> list[Problem]:
    """The styles name one tempo, the one the song's sections are timed by (Music.bpm), so the
    music generator is never asked for one pace and given section lengths for another."""
    bpm = bundle.music.bpm
    styles = [BPM_TAG.sub(lambda found: found[0] if round(float(found[1])) == bpm else f"{bpm} bpm", style)
              for style in music["positive_styles"]]
    if not any(BPM_TAG.search(style) for style in styles):
        styles.append(f"{bpm} bpm")
    if styles == music["positive_styles"]:
        return []
    music["positive_styles"] = styles
    return [Problem(line_id="music", rule="tempo", repaired=True,
                    detail=f"the styles now name only {bpm} bpm, the tempo the song's sections are timed by")]


# Curly quotes and long dashes read as plain ones, so "Broca’s area" is "Broca's area".
def notation(carries: str) -> str:
    """What the particles carry, as a textbook prints it. The bundle asks for it so ("H⁺", "Ca²⁺");
    plain text is turned into it: a charge at the end is raised, with the digit just before it
    ("H+" -> "H⁺", "Ca2+" -> "Ca²⁺"), and any other digit after a letter lowered ("H2O" -> "H₂O")."""
    text = " ".join(carries.split()).replace("\u2212", "-")  # a minus sign, as a hyphen
    text = re.sub(r"\s+(?=\d?[+-]$)", "", text)  # "H +" -> "H+"
    charge = ""
    if found := re.fullmatch(r"(.*[A-Za-z)\]])(\d?[+-])", text):
        text, charge = found.groups()
    return re.sub(r"(?<=[A-Za-z)\]])\d+", lambda d: d.group().translate(SUBSCRIPT), text) + charge.translate(SUPERSCRIPT)


# Each swaps one character for one, so positions in the folded text match the original.
_PLAIN = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"})


def fold(text: str) -> str:
    return text.translate(_PLAIN).lower()


def canonical_names(scenes: list[dict]) -> dict[str, str]:
    """Every structure name and alias, folded, mapped to the structure's name (the first, if shared)."""
    canon: dict[str, str] = {}
    for scene in scenes:
        for structure in scene["structures"]:
            for word in (structure["name"], *structure["aliases"]):
                canon.setdefault(fold(word), structure["name"])
    return canon


def shared_names(scenes: list[dict]) -> dict[str, list[str]]:
    """Names or aliases that belong to more than one structure."""
    owners: dict[str, list[str]] = {}
    for scene in scenes:
        for structure in scene["structures"]:
            for word in (structure["name"], *structure["aliases"]):
                names = owners.setdefault(fold(word), [])
                if structure["name"] not in names:
                    names.append(structure["name"])
    return {word: names for word, names in owners.items() if len(names) > 1}


def said(text: str, canon: dict[str, str]) -> dict[str, str]:
    """The structures a line says, each mapped to the words it uses for it.

    This is how the timeline reveals structures too: by the words heard. Longer names
    win, so "motor cortex" is not also read as "cortex".
    """
    found: dict[str, str] = {}
    taken: list[tuple[int, int]] = []
    folded = fold(text)
    for word in sorted(canon, key=len, reverse=True):
        for m in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", folded):
            if any(start < m.end() and m.start() < end for start, end in taken):
                continue
            taken.append(m.span())
            found.setdefault(canon[word], text[m.start() : m.end()])
    return found


def merge(earlier: CheckReport, later: CheckReport) -> CheckReport:
    """One report for a bundle checked twice (before and after the fact-check), without repeats."""
    problems = {(p.line_id, p.rule, p.detail): p for p in [*earlier.problems, *later.problems]}
    return CheckReport(problems=list(problems.values()))


def load(path: Path) -> Bundle:
    """Read a bundle.json; lines written before `names` existed get it from their text."""
    return Bundle.model_validate(fill_names(json.loads(path.read_text(encoding="utf-8"))))


def fill_names(data: dict) -> dict:
    """Give lines from before the `names` field the names their text says."""
    canon = canonical_names(data["scenes"])
    for line in [*data["lyrics"], *data["narration"]]:
        line.setdefault("names", list(said(line["text"], canon).values()))
    return data


def _check_names(line: dict, canon: dict[str, str], scene: str, drawn: set[str], problems: list[Problem]) -> set[str]:
    kept, named = [], set()
    for name in line["names"]:
        structure = canon.get(fold(name))
        if structure is None:
            problems.append(Problem(line_id=line["id"], rule="unknown-name", repaired=False,
                                    detail=f'names "{name}", which no scene draws'))
            kept.append(name)
        elif not re.search(rf"(?<!\w){re.escape(fold(name))}(?!\w)", fold(line["text"])):
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
        structure = canon.get(fold(name))
        if structure not in drawn:
            return f'is not drawn in scene "{scene}"'
        if structure not in revealed:
            return "has not been named yet"
        return None

    if direction["glow"] and (why := misplaced(direction["glow"])):
        problems.append(Problem(line_id=line_id, rule="glow", repaired=True,
                                detail=f"glow on {direction['glow']} {why}; none instead"))
        direction["glow"] = ""
    if direction["spotlight"] and (why := misplaced(direction["spotlight"])):
        problems.append(Problem(line_id=line_id, rule="spotlight", repaired=True,
                                detail=f"spotlight {direction['spotlight']} {why}; no spotlight instead"))
        direction["spotlight"] = ""
    target = direction["camera"]["target"]
    if target and (why := misplaced(target)):
        problems.append(Problem(line_id=line_id, rule="camera", repaired=True,
                                detail=f"camera on {target} {why}; whole scene instead"))
        direction["camera"] = {"target": "", "zoom": 1.0}
    stops = [p for p in direction["particles"] if canon.get(fold(p)) in drawn]
    if stops != direction["particles"]:
        stops = stops if len(stops) >= 2 else []
        problems.append(Problem(line_id=line_id, rule="particles", repaired=True,
                                detail=f'particles name structures scene "{scene}" does not draw; kept {stops}'))
        direction["particles"] = stops
    carries = direction["carries"]
    if carries and (not direction["particles"] or len(carries) > MARKER_MAX):
        why = "with no particles" if not direction["particles"] else f"is over {MARKER_MAX} characters"
        problems.append(Problem(line_id=line_id, rule="carries", repaired=True,
                                detail=f"carries {carries!r} {why}; unmarked"))
        direction["carries"] = ""
