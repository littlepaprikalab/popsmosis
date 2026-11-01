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
from collections import Counter
from pathlib import Path

from studio.jobs import write
from studio.schemas import BPM_TAG, SONG_SCENES_MAX, Bundle, CheckReport, Problem, lesson_scenes_max

MARKER_MAX = 8  # characters a particle's marker holds ("glucose", "NADPH"); a longer one is dropped
SYMBOL_LETTERS = 3  # what a particle can carry written on it ("Na⁺", "Ca²⁺", "NAD⁺"): anything longer moves as an arrow
SAYS_MAX = 40  # characters of an arrow's one-liner: one short line beside it ("The pump lifts water to the tank")
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
    problems = _no_analogy_scene(data) + _settle_song_scenes(data)
    canon = canonical_names(data["scenes"])
    drawn = {scene["id"]: {s["name"] for s in scene["structures"]} for scene in data["scenes"]}
    directions = {d["line_id"]: d for d in data["direction"]}
    problems += [
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
    _check_side_by_side(data, problems)
    _check_axes(data, problems)
    _check_spins(data, canon, problems)
    _check_blocks(data, canon, problems)
    _check_conversions(data, canon, problems)
    _check_meanings(data, canon, problems)
    _check_lyric_style(data, problems)
    _check_key_lines(data, problems)
    problems += _check_tempo(bundle, data["music"])
    return Bundle.model_validate(data), CheckReport(problems=problems)


ANALOGY = "analogy"  # the id the analogy's picture had (studio/prompts/bundle.md, before Nov 1)
SCENES_MIN = 3  # the fewest scenes a droplet's lesson has (schemas.bundle_problems)


def _no_analogy_scene(data: dict) -> list[Problem]:
    """No picture draws the analogy: it lives in the words, over the real thing (the owner, Nov 1: a water tower,
    then a nightclub, drawn for the sodium-potassium pump, made the learner map two pictures). A scene with the
    analogy's id is left out, and each line directed onto it plays over the scene with the real thing it explains,
    the one after it, whole, with nothing moving or lit, its motion having been the analogy's. A droplet that would
    keep fewer than 3 scenes keeps the scene, with no line on it, never shown. Repaired, and recorded for the
    prompt."""
    ids = [s["id"] for s in data["scenes"]]
    if ANALOGY not in ids:
        return []
    after = [s["id"] for s in data["scenes"][ids.index(ANALOGY) + 1:] if not s.get("song_only")]
    others = [i for i in ids if i not in (ANALOGY, "hook")]
    real = (after or others or [i for i in ids if i != ANALOGY] or [None])[0]
    if real is None:
        return []
    lesson = [s for s in data["scenes"] if not s.get("song_only")]
    dropped = len(lesson) > SCENES_MIN
    problems = [Problem(line_id="scenes", rule="analogy", repaired=True,
                        detail=f'{"left out" if dropped else "showed nothing on"} the analogy\'s picture: its lines '
                               f'play over "{real}"')]
    for d in data["direction"]:
        if d["scene"] == ANALOGY:
            d.update(scene=real, camera={"target": "", "zoom": 1.0}, spotlight="", particles=[], carries="",
                     moves="", says="", glow="", also=[])
    if dropped:
        data["scenes"] = [s for s in data["scenes"] if s["id"] != ANALOGY]
    return problems


def _settle_song_scenes(data: dict) -> list[Problem]:
    """The song's own scenes (Scene.song_only) as the direction plays them. The lesson never plays one: a narration
    sentence over one makes it the lesson's too, while the lesson has room for another scene, or else plays over the
    scene before it. The song keeps at most SONG_SCENES_MAX, the ones it plays longest: a line over another plays
    over the song's scene before it. One no line plays over is left out, a picture painted for nothing (a bundle with
    no song plays none of them). Repairs, not refusals: a bundle refused is a whole bundle call again."""
    problems = []
    narration = {n["id"] for n in data["narration"]}
    lesson = [s for s in data["scenes"] if not s["song_only"]]
    most = lesson_scenes_max(any(s["stage_of"] for s in lesson))
    for scene in data["scenes"]:
        narrated = [d for d in data["direction"] if d["line_id"] in narration and d["scene"] == scene["id"]]
        if scene["song_only"] and narrated and len(lesson) < most:
            scene["song_only"] = False
            lesson.append(scene)
            problems.append(Problem(line_id=f"scene:{scene['id']}", rule="song-scene", repaired=True,
                                    detail=f'scene "{scene["id"]}" is for the song alone, but the narration plays '
                                           "over it; the lesson's too"))
    sung = Counter(d["scene"] for d in data["direction"] if d["line_id"] not in narration)
    song = [s["id"] for s in data["scenes"] if s["song_only"]]
    kept = set(sorted(song, key=lambda scene: -sung[scene])[:SONG_SCENES_MAX])  # stable: ties keep the first
    for scene in song:
        if scene not in kept and sung[scene]:
            problems.append(Problem(line_id=f"scene:{scene}", rule="song-scene", repaired=True,
                                    detail=f'scene "{scene}" is one more than the song\'s {SONG_SCENES_MAX} of its '
                                           "own; its lines play over the scene before them"))
    lesson_ids = [s["id"] for s in lesson]
    for video, allowed in (("lesson", set(lesson_ids)), ("song", set(lesson_ids) | kept)):
        before = lesson_ids[0] if lesson_ids else ""
        for d in data["direction"]:
            if (d["line_id"] in narration) != (video == "lesson"):
                continue
            if d["scene"] in allowed or d["scene"] not in song:
                before = d["scene"]
                continue
            if video == "lesson":
                problems.append(Problem(line_id=d["line_id"], rule="song-scene", repaired=True,
                                        detail=f'plays over "{d["scene"]}", a scene for the song alone; over '
                                               f'"{before}" instead'))
            d["scene"] = before
    played = {d["scene"] for d in data["direction"]}
    for scene in [s for s in data["scenes"] if s["song_only"] and s["id"] not in played and sung[s["id"]] == 0]:
        problems.append(Problem(line_id=f"scene:{scene['id']}", rule="song-scene", repaired=True,
                                detail=f'scene "{scene["id"]}" is for the song alone, but no line plays over it; '
                                       "left out"))
    data["scenes"] = [s for s in data["scenes"] if not s["song_only"] or s["id"] in played]
    return problems


# A picture asking for one thing drawn twice or for two moments side by side ("one pump, drawn twice side by side",
# Nov 1) came back with both and with captions of its own ("State 1", "Conformational change"); the brief has each
# stage a scene of its own, which the video fades between.
SIDE_BY_SIDE = re.compile(r"\b(?:side[- ]by[- ]side|drawn twice|shown twice|two panels|split (?:into|in) two|"
                          r"before and after|left panel|right panel)\b", re.I)


def _check_side_by_side(data: dict, problems: list) -> None:
    """A scene's picture shows one moment: one asking for a thing drawn twice or for two moments side by side is
    recorded, for the prompt to fix (two stages, each a scene)."""
    for scene in data["scenes"]:
        found = SIDE_BY_SIDE.search(scene.get("image_prompt", ""))
        if found:
            problems.append(Problem(line_id=f"scene:{scene['id']}", rule="side-by-side", repaired=False,
                                    detail=f'the picture asks for "{found.group(0)}": draw each stage as a scene of '
                                           "its own"))


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


AXIS_MAX = 28  # characters an axis name holds ("Number of molecules"); a longer one would run across the graph


def _check_axes(data: dict, problems: list) -> None:
    """An axis name is short, as a textbook prints it, and is none of its scene's structures, which are labelled
    already. Any other is left off the screen."""
    for scene in data["scenes"]:
        named = {fold(word) for s in scene["structures"] for word in (s["name"], *s["aliases"])}
        for side in ("x", "y"):
            name = " ".join(scene["axes"][side].split())
            why = (f"runs {len(name)} characters, over {AXIS_MAX}" if len(name) > AXIS_MAX else
                   "names a structure of the scene" if fold(name) in named else "")
            if why:
                problems.append(Problem(line_id=f"scene:{scene['id']}", rule="axes", repaired=True,
                                        detail=f'left the {side} axis name "{name}" off the screen: it {why}'))
                name = ""
            scene["axes"][side] = name


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


def _silent_ed(word: str) -> bool:
    """Whether a word's final "-ed" is silent: "learned", "used", but not after t or d ("wanted", "needed"), nor after
    a consonant and an l or r ("troubled", "hundred"), where the e is sung."""
    if len(word) < 4 or not word.endswith("ed"):
        return False
    before, earlier = word[-3], word[-4]
    return before not in "td" and not (before in "lr" and earlier not in "aeiouy")


def syllables(line: str) -> int:
    """A rough count of a line's syllables, enough to tell a line too full to sing: vowel groups in each word, a
    silent final e and a silent "-ed" aside ("learned", "used", but not "wanted", "hundred" or "troubled"), a
    contraction one word ("it's", "you're"); a term in capitals or with no vowel ("ATP", "b6f") said letter by letter."""
    count = 0
    for word in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", line):  # "it's" one word: a lone "s" is no syllable
        if (len(word) > 1 and word.isupper()) or not re.search(r"[aeiouy]", word, re.IGNORECASE):
            count += len(word)
            continue
        lower = word.lower()
        groups = len(re.findall(r"[aeiouy]+", lower))
        if groups > 1 and lower.endswith("e") and not lower.endswith(("le", "ee")):
            groups -= 1
        elif groups > 1 and _silent_ed(lower):
            groups -= 1
        count += max(1, groups)
    return count


# A sung line is 6 to 9 syllables, so the singer has room to hold a note: round 17's lines of 12 and 13 came back
# at 5 to 6 syllables a second, the speed of speech, sung or rapped. A rapped line, when the learner asks for rap,
# can carry more. A line one over is let through: a singer takes one extra in stride, and the rough count is often
# one high itself. Only a line LYRIC_SLACK + 1 or more over is too full to sing (the owner, Oct 31).
LYRIC_SYLLABLES_MAX, LYRIC_SYLLABLES_RAPPED = 10, 14
LYRIC_SLACK = 1
RAPPED = re.compile(r"\brap(?:ped|ping|s)?\b", re.I)
# A line that narrates the picture or the song instead of being sung ("Zoom inside to the chloroplast"). "Next up"
# and "here comes" announce a section ("next up, the bridge"); before anything else they are sung: the link to the
# next topic the second pre-chorus sings ("Next up, the power rule", "Next up: estar plus an -ando"), or a part
# arriving ("Here comes HIV with a protein like a key"), as three droplets did on Oct 29.
SECTION_NAMES = r"(?:intro|verse|pre-chorus|chorus|bridge|hook|outro)"
DIRECTION_LINE = re.compile(r"^\W*(?:now\s+)?(?:let'?s\s+)?(?:zoom|look|see|watch|here\s+we"
                            rf"|(?:next\s+up|here\s+comes)\W+(?:(?:is|comes|the|a)\s+)*{SECTION_NAMES}\b)\b", re.I)


def _check_lyric_style(data: dict, problems: list) -> None:
    """A sung line is not a stage direction and is not too full to sing: both are only recorded (a better bundle,
    by the prompt, is the fix), so the operator sees which droplets sing them."""
    rapped = RAPPED.search(" ".join((data.get("music") or {}).get("positive_styles") or []))
    most = LYRIC_SYLLABLES_RAPPED if rapped else LYRIC_SYLLABLES_MAX
    for line in data["lyrics"]:
        text = line["text"]
        why = ("narrates the picture instead of being sung" if DIRECTION_LINE.match(text) else
               f"runs {syllables(text)} syllables, over {most}" if syllables(text) > most + LYRIC_SLACK else "")
        if why:
            problems.append(Problem(line_id=line["id"], rule="lyric-style", repaired=False,
                                    detail=f'"{text}" {why}'))


# The singer takes the time the lines need (audio.line_ms), so the song has no set length; one far outside this
# has too few lines or too many, and is only recorded.
SONG_MIN_S, SONG_MAX_S = 90, 240


def song_length_problems(bundle: Bundle) -> list[Problem]:
    """How long the sung lines take to sing (audio.line_ms). Recorded, not repaired: only a better bundle fixes
    it. Not part of review(): the bundle step adds it."""
    from studio.steps.audio import line_ms

    if not bundle.sung:  # the lesson video alone (STUDIO_VIDEOS=lesson): no song to time
        return []
    seconds = sum(line_ms(line.text) for line in bundle.lyrics) / 1000
    if SONG_MIN_S <= seconds <= SONG_MAX_S:
        return []
    return [Problem(line_id="music", rule="lyric-style", repaired=False,
                    detail=f"{len(bundle.lyrics)} lines sing for about {seconds:.0f} s, outside {SONG_MIN_S} to "
                           f"{SONG_MAX_S}")]


CHORUSES = 3  # the chorus comes back after each pre-chorus and once more at the end
VERSE_LINES_MAX = 8  # at any tempo: the song fills its length with the chorus coming back, not longer verses
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
    to hold the hook to, and one for the lesson video alone has no song."""
    if not bundle.why_chain or not bundle.sung:
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
    for section, lines in runs:
        if section == "verse" and len(lines) > VERSE_LINES_MAX:
            problems.append(Problem(line_id=lines[0].id, rule="lyric-style", repaired=False,
                                    detail=f"a verse of {len(lines)} lines, over {VERSE_LINES_MAX}"))
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


def style_problems(bundle: Bundle, asked: str) -> list[Problem]:
    """The song's style against the bundle prompt's rule: one free-form description, not tags, that keeps what
    the learner asked for. Code can count only the tempo of that: the writer builds on the rest in its own words.
    Recorded, not repaired, as the song's shape is. A bundle with no song has no style to hold to it."""
    if not bundle.sung:
        return []
    styles = bundle.music.positive_styles
    problems = []
    if len(styles) > 1:
        problems.append(Problem(line_id="music", rule="song-style", repaired=False,
                                detail=f"the style is {len(styles)} tags, not one free-form description"))
    if (found := BPM_TAG.search(asked)) and round(float(found.group(1))) != bundle.music.bpm:
        problems.append(Problem(line_id="music", rule="song-style", repaired=False,
                                detail=f"the song is at {bundle.music.bpm} bpm, not the {found.group(1)} bpm the "
                                       f"learner asked for"))
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
            problems.append(Problem(line_id=key.from_line or key.from_narration, rule="key-line", repaired=False,
                                    detail=f'the key line "{key.text}" runs {words} words, over {KEY_LINE_WORDS_MAX}'))
    return problems + misplaced_key_lines(bundle)


def _check_key_lines(data: dict, problems: list) -> None:
    """Key lines, one to a lyric line: one on a line the song does not have, or a second on the same line, is left
    out of the song video (and dropped, with no narration sentence either), since the screen could not show it."""
    if not data["lyrics"]:  # the lesson video alone: they are placed on the narration
        _check_narrated_key_lines(data, problems)
        return
    order = {line["id"]: i for i, line in enumerate(data["lyrics"])}
    spoken = {n["id"] for n in data["narration"]}
    kept, seen = [], set()
    for key in data["key_lines"]:
        if not key["text"].strip():
            problems.append(Problem(line_id=key["from_line"] or "key_lines", rule="key-line", repaired=True,
                                    detail="dropped an empty key line"))
            continue
        if key["from_line"] not in order or key["from_line"] in seen:
            if key.get("from_narration", "") not in spoken:
                problems.append(Problem(line_id=key["from_line"] or "key_lines", rule="key-line", repaired=True,
                                        detail=f'dropped the key line "{key["text"]}": its lyric line is unknown or '
                                               f"taken, and it has no narration sentence"))
                continue
            if key["from_line"]:  # "" is a step the song never sings: the lesson video's only, as the brief asks
                problems.append(Problem(line_id=key["from_line"], rule="key-line", repaired=True,
                                        detail=f'left the key line "{key["text"]}" out of the song video: its lyric '
                                               f"line is unknown or taken"))
            key["from_line"] = ""
        else:
            seen.add(key["from_line"])
        kept.append(key)
    # In the lesson video, one on a sentence the narration does not have, or a second on the same sentence, is
    # left out of that video only.
    taken = set()
    for key in kept:
        at = key.get("from_narration", "")
        if at and (at not in spoken or at in taken):
            problems.append(Problem(line_id=at, rule="key-line", repaired=True,
                                    detail=f'left the key line "{key["text"]}" out of the lesson video: its narration '
                                           f"sentence is unknown or taken"))
            key["from_narration"] = ""
        taken.add(key["from_narration"])
    data["key_lines"] = kept  # in the brief's order, the chain's: each video places them by its own lines


# A key line's words and a lyric's, to tell whether the lyric sings its step: round 22's pump song showed
# "2 K⁺ binding drops phosphate → pump snaps back inward" over "Potassium leaks out, takes its plus charge down the
# hill", a verse after the line that sang it ("Potassium hops on, the piece drops, the pump flips back in"): the
# writer kept the chain's order where the song had its own (the owner, Nov 1).
STEP_SAID = 2  # words a lyric shares with a key line to sing its step
STEP_CLEARER = 2  # flagged only beside a lyric that shares this many more words than its own...
STEP_OWN = 1  # ...and only on one that shares at most this many
STEP_COMMON = 0.2  # a word in more than this share of the song's lines ("pump", in a pump song) says nothing
STEP_SECTIONS = ("verse", "pre-chorus")  # where a step is sung: not the chorus (the core idea) nor the bridge (the case)
STEP_STOP = frozenset("""a an the and or but so as of to in on at by for with from into onto out up down off over
    under it its it's is are was were be been being this that these those then than there here they them their we
    you your our his her he she i me my not no yes all each every one two three what which who how why when where
    just only still too very can will would could should do does did get gets got let lets make makes""".split())
STEP_SYMBOLS = {"na": "sodium", "k": "potassium", "h": "hydrogen", "ca": "calcium", "cl": "chloride",
                "h2o": "water", "o2": "oxygen", "co2": "carbon", "e": "electron", "mg": "magnesium"}


def step_words(text: str) -> set[str]:
    """The words a line says, folded, cut to their stems ("drops", "dropped" -> "drop") and with a symbol read as its
    name ("Na⁺" -> "sodium"), leaving out the small words every line has."""
    words = set()
    for word in re.findall(r"[a-z0-9]+", fold(text).translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")).replace("'", "")):
        word = STEP_SYMBOLS.get(word, word)
        for end in ("ing", "ed", "es", "s"):
            if len(word) > len(end) + 2 and word.endswith(end):
                word = word[: -len(end)]
                if end in ("ing", "ed") and len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiouls":
                    word = word[:-1]  # "dropped" -> "drop"
                break
        word = word.rstrip("e") if len(word) > 3 else word
        if word not in STEP_STOP and len(word) > 1:
            words.add(word)
    return words


def misplaced_key_lines(bundle: Bundle) -> list[Problem]:
    """Key lines that may sit on a lyric singing something else: after the first (the question), one whose own lyric
    shares at most STEP_OWN of its words while a verse or pre-chorus line more than one line away, free of other key
    lines, shares STEP_CLEARER more. Recorded, not moved: on the 12 scorecard songs of Oct 31 it found the pump
    song's three, but would have moved four lines that sang their step through a metaphor ("Hit too soft and you
    roll back down" is the catalyst song's activation energy) onto lines that only repeat its words."""
    lyrics = bundle.lyrics
    order = {line.id: i for i, line in enumerate(lyrics)}
    said = {line.id: step_words(line.text) for line in lyrics}
    counts = Counter(word for words in said.values() for word in words)
    common = {word for word, n in counts.items() if n > max(STEP_COMMON * len(lyrics), 3)}
    said = {at: words - common for at, words in said.items()}
    text = {line.id: line.text for line in lyrics}
    placed = [k for k in bundle.key_lines if k.from_line in order]
    first = min(placed, key=lambda k: order[k.from_line], default=None)
    taken = {k.from_line for k in placed}
    problems = []
    for key in placed:
        if key is first:
            continue
        words = step_words(key.text)
        own = len(words & said[key.from_line])
        free = [line.id for line in lyrics[1:] if line.section in STEP_SECTIONS and line.id not in taken
                and abs(order[line.id] - order[key.from_line]) > 1]
        best = max(free, key=lambda at: (len(words & said[at]), -order[at]), default=None)
        if best is None:
            continue
        shared = len(words & said[best])
        if shared >= STEP_SAID and own <= STEP_OWN and shared >= own + STEP_CLEARER:
            problems.append(Problem(line_id=key.from_line, rule="key-line", repaired=False,
                                    detail=f'the key line "{key.text}" is on {key.from_line} ("{text[key.from_line]}"), '
                                           f'which may sing something else: {best} ("{text[best]}") says more of it'))
    return problems


def _check_narrated_key_lines(data: dict, problems: list) -> None:
    """Key lines with no song (STUDIO_VIDEOS=lesson, LessonBundle), in narration order, one to a sentence: one on a
    sentence the narration does not have, or a second on the same sentence, is dropped."""
    order = {n["id"]: i for i, n in enumerate(data["narration"])}
    kept, seen = [], set()
    for key in data["key_lines"]:
        at = key.get("from_narration", "")
        if at not in order or at in seen or not key["text"].strip():
            problems.append(Problem(line_id=at or "key_lines", rule="key-line", repaired=True,
                                    detail=f'dropped the key line "{key["text"]}": its narration sentence is unknown, '
                                           f"taken, or it is empty"))
            continue
        seen.add(at)
        kept.append(key)
    data["key_lines"] = sorted(kept, key=lambda k: order[k["from_narration"]])


def motion_problems(bundle: Bundle) -> list[Problem]:
    """The song's direction against the planning rule a check can count: every chorus directed alike (compared from
    its end, since the last one opens with a summary couplet). Recorded, not repaired; the bundle step adds them.

    How many lines in a row hold still is not counted. The bundle prompt has a line that comments or sums up hold
    still, and the spotlight and the camera carry a scene where nothing travels, so a run of still lines is no
    mistake. Counted until Oct 31, a run of five flagged a chorus summing up the Moon's phases over the overview,
    the spotlight stepping from phase to phase, and only because one earlier line had sent sunlight across it: a
    scene that never moved was never flagged, so the count pushed toward dots for their own sake."""
    directions = {d.line_id: d for d in bundle.direction}
    problems = []
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
            # What moves where, not how it is drawn (Direction.moves): a chorus repeats the motion, whichever way.
            if a and b and [(f.particles, f.carries) for f in a.flows()] != [(f.particles, f.carries) for f in b.flows()]:
                problems.append(Problem(line_id=later_id, rule="motion", repaired=False,
                                        detail=f"chorus line {later_id} moves differently from {first_id}"))
    return problems


CONVERT_MAX = 4  # species on one side of a conversion
MEANING_MAX = 40  # characters of a graph part's meaning: one short line under its name ("lower price → people buy more")


def _check_meanings(data: dict, canon: dict[str, str], problems: list) -> None:
    """Each meaning names a part of a graph (a structure on a scene with axes), as its name, in one short line. One
    part has one meaning; any other is left out, and the video writes only the part's name."""
    graphed = {canon.get(fold(s["name"])) for scene in data["scenes"]
               if any(" ".join(scene["axes"][side].split()) for side in ("x", "y")) for s in scene["structures"]}
    kept, seen = [], set()
    for meaning in data["meanings"]:
        name, text = canon.get(fold(meaning["structure"])), " ".join(meaning["text"].split())
        why = ("names no structure" if not name else "is not a part of a graph" if name not in graphed else
               "repeats a part's meaning" if name in seen else "is empty" if not text else
               f"is over {MEANING_MAX} characters" if len(text) > MEANING_MAX else "")
        if why:
            problems.append(Problem(line_id="meanings", rule="meanings", repaired=True,
                                    detail=f"{meaning['structure']}'s meaning {text!r} {why}; left out"))
            continue
        seen.add(name)
        kept.append({"structure": name, "text": text})
    data["meanings"] = kept


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
    music generator is never asked for one pace and given section lengths for another. A bundle with no song has none."""
    if not bundle.sung:
        return []
    bpm = bundle.music.bpm
    styles = [BPM_TAG.sub(lambda found: found[0] if round(float(found[1])) == bpm else f"{bpm} bpm", style)
              for style in music["positive_styles"]]
    if not any(BPM_TAG.search(style) for style in styles):  # said at the end of the description, not as a tag
        styles = [*styles[:-1], f"{styles[-1].rstrip(' .,;')}, {bpm} bpm"] if styles else [f"{bpm} bpm"]
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
    # A word the line says but does not list is added only when its scene draws that part: the video reveals it
    # there whatever the list says. Elsewhere it is a word, not a pointer: the song carrying its frame ("Charged-up
    # batteries, out they ride" over the overview, where only the analogy draws "Batteries"), or the topic's own
    # word ("rocket") said over a scene that draws the rocket's parts. Added and then flagged as not in the scene,
    # such words were 141 of 142 not-in-scene problems in a 12-topic simulation on Oct 31; the brain had listed the
    # one real slip itself.
    for structure, word in said(line["text"], canon).items():
        if structure not in named and structure in drawn:
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
        also.append({"particles": stops, "carries": marked, "moves": flow.get("moves", ""), "says": flow.get("says", "")})
    direction["also"] = also
    for flow in [direction, *direction["also"]]:
        _check_says(line_id, flow, problems)


def arrow(carries: str, moves: str) -> bool:
    """Whether a flow is drawn as an arrow (renderer: _travels): it travels, or what moves has no short symbol to
    write on a particle (a word, "glucose", or nothing). A crossing ion ("Na⁺") is particles."""
    return moves == "travels" or sum(ch.isalpha() for ch in notation(carries)) > SYMBOL_LETTERS or not carries.strip()


def _check_says(line_id: str, flow: dict, problems: list[Problem]) -> None:
    """An arrow's one-liner (Direction.says) is one short line; one too long is left out. An arrow with none is
    recorded: the video draws it bare, and the learner is left asking what it shows (the owner, Nov 1)."""
    says = " ".join(flow.get("says", "").split())
    if len(says) > SAYS_MAX:
        problems.append(Problem(line_id=line_id, rule="says", repaired=True,
                                detail=f"the arrow's one-liner {says!r} is over {SAYS_MAX} characters; left out"))
        says = ""
    flow["says"] = says
    if flow["particles"] and not says and arrow(flow["carries"], flow.get("moves", "")):
        problems.append(Problem(line_id=line_id, rule="says", repaired=False,
                                detail=f"an arrow ({' → '.join(flow['particles'])}) with no one-liner beside it"))
