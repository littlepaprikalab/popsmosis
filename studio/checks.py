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
ALSO_MAX = 1  # second flows on one line: two things moving at once is as much as a learner can follow
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
    _check_key_lines(data, problems)
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
    ends = {canon.get(fold(f["particles"][-1])) for d in data["direction"] for f in flows_of(d)}
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
# A line that narrates the picture or the song instead of being sung ("Zoom inside to the chloroplast"). "Next up"
# announces a section ("next up, the bridge"); before anything else it is the link to the next topic that the second
# pre-chorus sings ("Next up, the power rule", "Next up: estar plus an -ando"), as two droplets did on Oct 29.
SECTION_NAMES = r"(?:intro|verse|pre-chorus|chorus|bridge|hook|outro)"
DIRECTION_LINE = re.compile(r"^\W*(?:now\s+)?(?:let'?s\s+)?(?:zoom|look|see|watch|here\s+(?:we|comes)"
                            rf"|next\s+up\W+(?:(?:is|comes|the|a)\s+)*{SECTION_NAMES}\b)\b", re.I)


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


SONG_MIN_S, SONG_MAX_S = 120, 160  # the prompt asks for 2 to 2½ minutes; a song outside this is only recorded


def song_length_problems(bundle: Bundle) -> list[Problem]:
    """How long the sung lines come to at the song's tempo (one bar a line below 130 bpm). Recorded, not repaired:
    only a better bundle fixes it. Not part of review(): the bundle step adds it."""
    from studio.steps.audio import line_ms

    seconds = len(bundle.lyrics) * line_ms(bundle) / 1000
    if SONG_MIN_S <= seconds <= SONG_MAX_S:
        return []
    return [Problem(line_id="music", rule="lyric-style", repaired=False,
                    detail=f"{len(bundle.lyrics)} lines at {bundle.music.bpm} bpm sing for about "
                           f"{seconds:.0f} s, outside {SONG_MIN_S} to {SONG_MAX_S}")]


CHORUSES = 3  # the chorus comes back after each pre-chorus and once more at the end
# A bridge line is sung already when this share of its words is in the verses; a bridge with half its lines sung
# already retells them. Of six droplets on Oct 29, the two that retold their verses (a play's plot, the steps to a
# revolution) had 4 and 6 such lines of 8; the four that took a new case (a poison, a corner, a law) had 0 or 1.
RETOLD_SHARE, RETOLD_LINES = 2 / 3, 1 / 2
ATTITUDE_MAX = 2  # claim-free tags in the whole song ("for real", "no doubt"); more are rhymes filled with nothing
ATTITUDE = re.compile(r"\b(?:for real|no doubt|it'?s true|you see|you know|believe (?:it|me)|no lie|that'?s the trick"
                      r"|that'?s the rule|that'?s a fact|word up|check it)\b", re.I)
PRONOUNS = {"it", "its", "them", "they", "their", "this", "that", "these", "those", "he", "she", "him", "her"}
PLAIN = PRONOUNS | {"the", "a", "an", "and", "or", "but", "so", "then", "now", "in", "on", "out", "up", "down", "to",
                    "of", "for", "from", "with", "into", "is", "are", "was", "be", "we", "you", "your", "our", "all",
                    "go", "goes", "get", "got", "make", "makes", "just", "like", "what", "when", "how", "let", "lets"}


def _stems(text: str) -> set[str]:
    """The content words of a text, cut to their first five letters so "pumps" meets "pumping"."""
    return {w[:5] for w in re.findall(r"[a-z0-9]+", fold(text).replace("'", "")) if w not in PLAIN and len(w) > 2}


def song_shape_problems(bundle: Bundle) -> list[Problem]:
    """The song against the rules a check can count, for any subject: the chorus three times; a title line that
    names real things from the droplet's own idea, not pronouns; a chorus that says the idea; verses no longer
    than the tempo asks for; a bridge that sings something new, not the verses again; few claim-free tags.
    Recorded, not repaired; the bundle step adds them. A bundle written before the why chain existed has nothing
    to hold the hook to."""
    if not bundle.why_chain:
        return []
    problems: list[Problem] = []
    runs: list[tuple[str, list]] = []
    for line in bundle.lyrics:
        if runs and runs[-1][0] == line.section:
            runs[-1][1].append(line)
        else:
            runs.append((line.section, [line]))
    choruses = [lines for section, lines in runs if section == "chorus"]
    if len(choruses) < CHORUSES:
        problems.append(Problem(line_id="music", rule="lyric-style", repaired=False,
                                detail=f"the chorus is sung {len(choruses)} times, not {CHORUSES}"))
    idea = _stems(" ".join([bundle.core_idea, *bundle.why_chain, *bundle.key_terms]))
    if choruses:
        title = choruses[0][0]
        words = re.findall(r"[a-z']+", fold(title.text))
        if sum(w in PRONOUNS for w in words) >= 2 or not _stems(title.text) & idea:
            problems.append(Problem(line_id=title.id, rule="lyric-style", repaired=False,
                                    detail=f'the title line "{title.text}" names nothing from the core idea, why '
                                           f"chain or key terms, or leans on pronouns"))
        named = _stems(" ".join(line.text for line in choruses[0])) & _stems(bundle.core_idea)
        if len(named) < 2:
            problems.append(Problem(line_id=choruses[0][0].id, rule="lyric-style", repaired=False,
                                    detail="the chorus says too little of the core idea: at most one of its words"))
    most = 8 if bundle.music.bpm < 100 or bundle.music.bpm >= 130 else 12
    for section, lines in runs:
        if section == "verse" and len(lines) > most:
            problems.append(Problem(line_id=lines[0].id, rule="lyric-style", repaired=False,
                                    detail=f"a verse of {len(lines)} lines, over {most} at {bundle.music.bpm} bpm"))
    verses = _stems(" ".join(line.text for section, lines in runs if section == "verse" for line in lines))
    bridge = [line for section, lines in runs if section == "bridge" for line in lines]
    retold = [line for line in bridge if (words := _stems(line.text)) and len(words & verses) >= RETOLD_SHARE * len(words)]
    if bridge and len(retold) >= RETOLD_LINES * len(bridge):
        problems.append(Problem(line_id=bridge[0].id, rule="lyric-style", repaired=False,
                                detail=f"the bridge retells the verses: {len(retold)} of its {len(bridge)} lines sing "
                                       f"what the verses sang ({', '.join(line.id for line in retold)})"))
    tagged = sorted({line.text for line in bundle.lyrics if ATTITUDE.search(line.text)})
    if len(tagged) > ATTITUDE_MAX:
        problems.append(Problem(line_id="lyrics", rule="lyric-style", repaired=False,
                                detail=f"{len(tagged)} lines end on a claim-free tag, over {ATTITUDE_MAX}: "
                                       + "; ".join(f'"{t}"' for t in tagged)))
    return problems


ESSENCE_WORDS_MAX = 20
WHY_LINKS = (3, 5)
KEY_TERMS_MAX = 8


def essence_problems(bundle: Bundle) -> list[Problem]:
    """The essence fields against their limits: a core idea short enough to remember, a why chain of 3 to 5 links,
    at most KEY_TERMS_MAX key terms. Recorded, not repaired; the bundle step adds them. A bundle written before
    the why chain existed has none of these."""
    if not bundle.why_chain:
        return []
    problems = []
    words = len(bundle.core_idea.split())
    if words > ESSENCE_WORDS_MAX:
        problems.append(Problem(line_id="core_idea", rule="essence", repaired=False,
                                detail=f"the core idea runs {words} words, over {ESSENCE_WORDS_MAX}"))
    if not WHY_LINKS[0] <= len(bundle.why_chain) <= WHY_LINKS[1]:
        problems.append(Problem(line_id="why_chain", rule="essence", repaired=False,
                                detail=f"the why chain has {len(bundle.why_chain)} links, not {WHY_LINKS[0]} to {WHY_LINKS[1]}"))
    if len(bundle.key_terms) > KEY_TERMS_MAX:
        problems.append(Problem(line_id="key_terms", rule="essence", repaired=False,
                                detail=f"{len(bundle.key_terms)} key terms, over {KEY_TERMS_MAX}"))
    return problems


KEY_LINE_WORDS_MAX = 10
KEY_LINES = (3, 10)  # the why chain's 3 to 5 links, a link or two lines each


def key_line_words(text: str) -> int:
    """The words in a key line: the arrows and signs the prompt asks for ("→", "=") are not words."""
    return sum(1 for token in text.split() if re.search(r"\w", token))


def key_line_problems(bundle: Bundle) -> list[Problem]:
    """The key lines against their limits: some to show, each at most KEY_LINE_WORDS_MAX words. Recorded, not
    repaired (the renderer fits a long one onto two rows); the bundle step adds them. A bundle written before the
    why chain existed has none."""
    if not bundle.why_chain:
        return []
    problems = []
    if not KEY_LINES[0] <= len(bundle.key_lines) <= KEY_LINES[1]:
        problems.append(Problem(line_id="key_lines", rule="key-line", repaired=False,
                                detail=f"{len(bundle.key_lines)} key lines, not {KEY_LINES[0]} to {KEY_LINES[1]}"))
    if bundle.key_lines and not any(k.from_narration for k in bundle.key_lines):
        problems.append(Problem(line_id="key_lines", rule="key-line", repaired=False,
                                detail="no key line is placed in the narration, so the lesson video shows none"))
    for key in bundle.key_lines:
        words = key_line_words(key.text)
        if words > KEY_LINE_WORDS_MAX:
            problems.append(Problem(line_id=key.from_line, rule="key-line", repaired=False,
                                    detail=f'the key line "{key.text}" runs {words} words, over {KEY_LINE_WORDS_MAX}'))
    return problems


def _check_key_lines(data: dict, problems: list) -> None:
    """Key lines in song order, one to a lyric line: one on a line the song does not have, or a second on the same
    line, is dropped, since the screen could not show it."""
    order = {line["id"]: i for i, line in enumerate(data["lyrics"])}
    kept, seen = [], set()
    for key in data["key_lines"]:
        if key["from_line"] not in order or key["from_line"] in seen or not key["text"].strip():
            problems.append(Problem(line_id=key["from_line"] or "key_lines", rule="key-line", repaired=True,
                                    detail=f'dropped the key line "{key["text"]}": its lyric line is unknown, taken, '
                                           f"or it is empty"))
            continue
        seen.add(key["from_line"])
        kept.append(key)
    # In the lesson video, one on a sentence the narration does not have, or a second on the same sentence, is
    # left out of that video only.
    spoken, taken = {n["id"] for n in data["narration"]}, set()
    for key in kept:
        at = key.get("from_narration", "")
        if at and (at not in spoken or at in taken):
            problems.append(Problem(line_id=at, rule="key-line", repaired=True,
                                    detail=f'left the key line "{key["text"]}" out of the lesson video: its narration '
                                           f"sentence is unknown or taken"))
            key["from_narration"] = ""
        taken.add(key["from_narration"])
    data["key_lines"] = sorted(kept, key=lambda k: order[k["from_line"]])


STILL_LINES_MAX = 2  # lines in a row with nothing moving on a scene where things travel
STILL_SCENES = ("hook", "analogy")  # scenes where nothing needs to move


def motion_problems(bundle: Bundle) -> list[Problem]:
    """The song's direction against the planning rules a check can count: no more than STILL_LINES_MAX lines in a
    row with no particles on a scene where something travels, and every chorus directed alike (compared from its
    end, since the last one opens with a summary couplet). Recorded, not repaired; the bundle step adds them."""
    directions = {d.line_id: d for d in bundle.direction}
    blockers = {b.structure for b in bundle.blocks}
    # A failure scene (one that draws a blocker) holds still after the block on purpose: what stopped stays stopped.
    # So does a scene where nothing travels on any line of either video (a graph, a stage): asked to keep every
    # scene moving, the planner sent unmarked dots from Romeo to the audience and from one point of a graph to
    # another, a journey nothing makes.
    travels = {d.scene for d in bundle.direction if d.flows()}
    still = set(STILL_SCENES) | {s.id for s in bundle.scenes
                                 if s.id not in travels or blockers & {x.name for x in s.structures}}
    problems, run = [], []

    def close_run() -> None:
        if len(run) > STILL_LINES_MAX:
            problems.append(Problem(line_id=run[0], rule="motion", repaired=False,
                                    detail=f"{len(run)} lines in a row from {run[0]} with nothing moving, on a scene "
                                           f"where things travel"))
        run.clear()

    for line in bundle.lyrics:
        d = directions.get(line.id)
        if d and d.scene not in still and not d.flows() and (not run or directions[run[0]].scene == d.scene):
            run.append(line.id)
        else:
            close_run()
            if d and d.scene not in still and not d.flows():
                run.append(line.id)
    close_run()

    choruses, current = [], []
    for i, line in enumerate(bundle.lyrics):
        if line.section == "chorus":
            current.append(line.id)
        if current and (i + 1 == len(bundle.lyrics) or bundle.lyrics[i + 1].section != "chorus"):
            choruses.append(current)
            current = []
    for later in choruses[1:]:
        for first_id, later_id in zip(reversed(choruses[0]), reversed(later)):
            a, b = directions.get(first_id), directions.get(later_id)
            if a and b and a.flows() != b.flows():
                problems.append(Problem(line_id=later_id, rule="motion", repaired=False,
                                        detail=f"chorus line {later_id} moves differently from {first_id}"))
    return problems


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
    driven = {canon.get(fold(stop)) for d in data["direction"] for f in flows_of(d) for stop in f["particles"][1:]}
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
        ends = {canon.get(fold(f["particles"][k])) for d in data["direction"] for f in flows_of(d)
                if notation(f["carries"]) == carries for k in (0, -1)}
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
def flows_of(direction: dict) -> list[dict]:
    """Everything moving on a line, as data: the main flow first (Direction.flows)."""
    main = [{"particles": direction["particles"], "carries": direction["carries"]}] if direction["particles"] else []
    return main + [f for f in direction.get("also", []) if f["particles"]]


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
    # A second flow is held to the same: stops the scene draws, a marker that fits. One that keeps fewer than two
    # stops is left out, and so is any after the first.
    also = []
    for flow in direction.get("also", []):
        stops = [p for p in flow["particles"] if canon.get(fold(p)) in drawn]
        if len(stops) < 2 or len(also) == ALSO_MAX:
            why = "names fewer than two structures the scene draws" if len(stops) < 2 else f"is over {ALSO_MAX} more"
            problems.append(Problem(line_id=line_id, rule="particles", repaired=True,
                                    detail=f"a second flow {why}; left out"))
            continue
        marked = flow["carries"] if len(flow["carries"]) <= MARKER_MAX else ""
        if marked != flow["carries"]:
            problems.append(Problem(line_id=line_id, rule="carries", repaired=True,
                                    detail=f"carries {flow['carries']!r} is over {MARKER_MAX} characters; unmarked"))
        also.append({"particles": stops, "carries": marked})
    direction["also"] = also
