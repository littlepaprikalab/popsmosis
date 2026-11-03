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

import colorsys
import json
import re
import zlib
from collections import Counter
from pathlib import Path

from studio.jobs import write
from studio.schemas import (BPM_TAG, LESSON_MAX_CHARS, PART_TAG, SONG_SCENES_MAX, Bundle, CheckReport, Fact, Problem,
                            lesson_scenes_max, with_study_tags)

MARKER_MAX = 8  # characters a particle's marker holds ("glucose", "NADPH"); a longer one is dropped
SYMBOL_LETTERS = 3  # what a particle can carry written on it ("Na⁺", "Ca²⁺", "NAD⁺"): anything longer moves as an arrow
SAYS_MAX = 40  # characters of an arrow's one-liner: one short line beside it ("Glucose rides in with sodium")
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
    problems = _no_analogy_scene(data) + _case_off_the_hook(data) + _settle_song_scenes(data) + _direct_choruses(data)
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
    _check_crowded(data, problems)
    _check_colours(data, problems)
    _check_aliases(data, problems)
    _check_part_tags(data, problems)
    _check_side_by_side(data, problems)
    _check_axes(data, problems)
    _check_spins(data, canon, problems)
    _check_blocks(data, canon, problems)
    _check_conversions(data, canon, problems)
    _check_meanings(data, canon, problems)
    _check_lyric_style(data, problems)
    _check_key_lines(data, problems)
    problems += _check_tempo(bundle, data["music"])
    problems += _check_voice(data["music"])
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


def _case_off_the_hook(data: dict) -> list[Problem]:
    """The lesson comes back to the hook only to close. Sentences that come back to it to talk about parts it does not
    draw, from the first such sentence to the one that says the core idea (or to the last sentence, the close, when
    none does), play over the scene that draws the most of those parts, the song's own picture first on a tie, since
    it was painted for what the lesson has not shown; _settle_song_scenes then makes it the lesson's too. Nov 2: Muse
    Wonder's script put the diabetes case on the hook, so "So the proximal tubule cannot take back all that extra
    glucose" played over a boy holding a water jug, while the bundle had painted the case, glucose piled up in the
    tubule, for the song alone. Repaired, and recorded for the prompt."""
    ids = [s["id"] for s in data["scenes"]]
    if "hook" not in ids:
        return []
    canon = canonical_names(data["scenes"])
    drawn = {s["id"]: {x["name"] for x in s["structures"]} for s in data["scenes"]}
    directions = {d["line_id"]: d for d in data["direction"]}
    lines = [n for n in data["narration"] if n["id"] in directions]
    left = next((i for i, n in enumerate(lines) if directions[n["id"]]["scene"] != "hook"), len(lines))
    core = fold(" ".join(data["core_idea"].split()))
    spans: list[list[dict]] = [[]]  # in each later passage on the hook, the sentences from the first that is away
    for i, n in enumerate(lines[left:], left):
        if directions[n["id"]]["scene"] != "hook":
            if spans[-1]:
                spans.append([])
            continue
        if core and core in fold(" ".join(n["text"].split())) or i == len(lines) - 1:
            break  # the close stays on the hook
        if spans[-1] or set(said(n["text"], canon)) - drawn["hook"]:
            spans[-1].append(n)
    problems = []
    for span in spans:
        parts = {p for n in span for p in said(n["text"], canon)} - drawn["hook"]
        scores = {s["id"]: (len(parts & drawn[s["id"]]), s["song_only"], ids.index(s["id"]))
                  for s in data["scenes"] if s["id"] != "hook"}
        if not parts or not scores or max(scores.values())[0] == 0:
            continue
        target = max(scores, key=scores.get)
        for n in span:
            directions[n["id"]].update(scene=target, camera={"target": "", "zoom": 1.0}, spotlight="", particles=[],
                                       carries="", moves="", says="", glow="", also=[])
            problems.append(Problem(line_id=n["id"], rule="case-on-hook", repaired=True,
                                    detail=f'comes back to the hook for {", ".join(sorted(parts))}, which it does not '
                                           f'draw; over "{target}" instead'))
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
# One thing drawn more than once, or panels: "side by side" alone is no sign, as two markets, two people or a chart's
# bars stand side by side (the scorecard of Nov 1: half the pictures it caught were fine).
SIDE_BY_SIDE = re.compile(r"\b(?:(?:drawn|shown|painted) (?:twice|again|two times|three times|four times|\d+ times)|"
                          r"(?:two|three|four|2|3|4) panels|(?:first|second|third|fourth|left|right|middle) panel|"
                          r"split (?:into|in) two|before and after|state [12]\b)", re.I)


def _check_side_by_side(data: dict, problems: list) -> None:
    """A scene's picture shows one moment: one asking for a thing drawn twice or for two moments side by side is
    recorded, for the prompt to fix (two stages, each a scene)."""
    for scene in data["scenes"]:
        found = SIDE_BY_SIDE.search(scene.get("image_prompt", ""))
        if found:
            problems.append(Problem(line_id=f"scene:{scene['id']}", rule="side-by-side", repaired=False,
                                    detail=f'the picture asks for "{found.group(0)}": draw each stage as a scene of '
                                           "its own"))


def _check_part_tags(data: dict, problems: list) -> None:
    """Each part's tag in its scene's image_prompt (schemas.PART_TAG), where the painter is told its colour and label
    (art.inline()). One naming the part by an alias or in another case is given its name, and one naming no part of
    the scene, or a part tagged before, is dropped: repaired, and recorded. A part left untagged in a scene that tags
    others is recorded: the painter is told its colour and label after the picture's words, as before tags, where it
    can put them on the wrong thing."""
    for scene in data["scenes"]:
        named = {name.casefold(): s["name"] for s in scene["structures"] for name in (s["name"], *s["aliases"])}
        tagged: list[str] = []
        fixed: list[str] = []

        def one(found: re.Match, named: dict = named, tagged: list = tagged, fixed: list = fixed) -> str:
            raw = found.group(1).strip()
            name = named.get(raw.casefold())
            if name is None or name in tagged:
                fixed.append(f"[[{raw}]] dropped: " + ("tagged before" if name else "no part of the scene"))
                return ""
            tagged.append(name)
            if raw != name:
                fixed.append(f"[[{raw}]] as [[{name}]]")
            return found.group(0)[:found.start(1) - found.start(0)] + name + "]]"

        scene["image_prompt"] = PART_TAG.sub(one, scene["image_prompt"])
        if fixed:
            problems.append(Problem(line_id=f"scene:{scene['id']}", rule="part-tags", repaired=True,
                                    detail="; ".join(fixed)))
        untagged = [s["name"] for s in scene["structures"] if s["name"] not in tagged]
        if untagged and tagged:  # a scene with no tags at all is painted as before tags, and was clean then
            problems.append(Problem(line_id=f"scene:{scene['id']}", rule="part-tags", repaired=False,
                                    detail=f"no tag for {', '.join(untagged)}: coloured and labelled after the "
                                           "picture's words instead, where the painter can put them on the wrong thing"))


def tag_parts(text: str, structures: list[dict]) -> str:
    """A picture's words with each part's tag (schemas.PART_TAG) right after the first words that name it, by its name
    or an alias, a plural too, the longest first ("thylakoid lumen" before "thylakoid"), as the brief asks the writer to
    place them; a part they never name, or one tagged already, is left as it is. For words written again without tags:
    the fact-check judges a picture's words untagged, and its correction came back with none (factcheck.apply_fixes),
    so the painter put those parts' colours and labels from the lists after the words, as before tags."""
    have = {m.group(1).strip().casefold() for m in PART_TAG.finditer(text)}
    words = sorted(((alias, s["name"]) for s in structures for alias in (s["name"], *s["aliases"]) if alias.strip()),
                   key=lambda pair: -len(pair[0]))
    taken: list[tuple[int, int]] = []  # what a longer name already holds
    at: dict[str, int] = {}
    for alias, name in words:
        if name in at or name.casefold() in have:
            continue
        for found in re.finditer(rf"(?<![\w\[]){re.escape(alias)}(?:e?s)?\b(?!['’]s)", text, re.I):
            if not any(a < found.end() and found.start() < b for a, b in taken):
                taken.append((found.start(), found.end()))
                at[name] = found.end()
                break
    for name, end in sorted(at.items(), key=lambda pair: -pair[1]):
        text = f"{text[:end]} [[{name}]]{text[end:]}"
    return text


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


# A scene's structures, past which it is only recorded: each is one more part to name, find and follow. The light
# reactions' overview drew 15 on Nov 1: it stayed pale for 30 s or more until each was named, SAM never found 7 of the
# droplet's 43 parts, and code's colour words ran out (three "yellow", two "teal"). The brief asks a younger audience
# for two or three; one over passes, as the Moon's phases need the Sun, Earth, the Moon and its orbit.
STRUCTURES_MAX, YOUNGER_STRUCTURES_MAX = 8, 4
YOUNGER = re.compile(r"\bages?\s+(\d{1,2})\b|\b(\d{1,2})[\s-]+years?[\s-]+olds?\b|\b(\d{1,2})(?:st|nd|rd|th)[\s-]+grade"
                     r"|\bgrade\s+(\d{1,2})\b|\b(?:elementary|primary|middle school|kids?|child(?:ren)?)\b", re.I)


def younger(audience: str) -> bool:
    """Whether the droplet is for a younger audience than high school, as the brief has it written: "about age 10",
    "6th grade", "middle school"; not "high school", "college" or "9th grade"."""
    found = YOUNGER.search(audience)
    if not found:
        return False
    age, years, grade, grade_after = found.groups()
    if age or years:
        return int(age or years) <= 13
    if grade or grade_after:
        return int(grade or grade_after) <= 8
    return True


def _check_crowded(data: dict, problems: list) -> None:
    """A scene with more structures than a learner can follow is recorded, for the prompt: which parts to leave out
    is the writer's to say."""
    most = YOUNGER_STRUCTURES_MAX if younger(data["audience"]) else STRUCTURES_MAX
    for scene in data["scenes"]:
        if len(scene["structures"]) > most:
            problems.append(Problem(line_id=f"scene:{scene['id']}", rule="crowded", repaired=False,
                                    detail=f"draws {len(scene['structures'])} structures, over {most}"))


# Hue bands in degrees, for colour_word. Purples run past 290 (#9C27B0 is 291) and pinks to 345 (#F06292 is
# 340): with the bands at 290 and 335, round 17 asked for a purple Roman Empire as "light pink" and its pink
# civil wars as "red", and the label "Civil wars" went on the Empire. Cyan is its own since Nov 1: the light
# reactions' water (#00ACC1) was "teal" like the carbon dioxide beside it (#00897B), and the picture drew two alike
# teal balls. Green runs to 170 since Nov 2: the beetles' leaves (#43AA8B, 162), "light green leaves" to the writer,
# were asked for and painted teal while the song sang "leaves so green"; teal itself is 180 (#008080).
HUES = ((10, "red"), (40, "orange"), (70, "yellow"), (170, "green"), (182, "teal"), (197, "cyan"), (250, "blue"),
        (310, "purple"), (345, "pink"), (360, "red"))
# A mid-tone orange this dark is brown, as everyone names it: ATP synthase's #A0703C, the Bastille's #A86B32 and a
# friar's robe were "orange" to the illustrator while the writer called them brown (Nov 1).
BROWN_VALUE = 0.72


def colour_word(color_hex: str) -> str:
    """A colour as everyday words, which image and segmentation models know: "#90BE6D" -> "light green". The
    illustrator's only word for a structure's colour (art.paint_prompt), and SAM's (_recolour_sam_phrases)."""
    r, g, b = (int(color_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    if saturation < 0.15:
        return "white" if value > 0.85 else "black" if value < 0.2 else "grey"
    name = next(word for limit, word in HUES if hue * 360 < limit)
    if (name == "orange" and value < BROWN_VALUE) or (name == "red" and value < 0.55):
        return "brown"
    if value < 0.4:
        return f"dark {name}"
    if saturation < 0.5 and value > 0.6:
        return "pink" if name == "red" else f"light {name}"  # light red is what everyone calls pink (#FFC0CB)
    return name


# A colour as a sam_phrase names it ("orange lobe", "man in red", "golden yellow flashlight", "pale green fluid"): an
# everyday colour word, maybe with a shade before it or a second hue after it.
_HUE_WORDS = "red|orange|brown|yellow|green|blue|purple|pink|grey|gray"
_COLOUR_WORDS = (_HUE_WORDS + "|gold|golden|amber|lime|olive|teal|cyan|turquoise|aqua|navy|indigo|violet|lavender"
                 "|magenta|coral|salmon|crimson|scarlet|maroon|silver|white|black|tan|beige")
COLOUR_NAME = re.compile(rf"\b(?:(?:light|dark|pale|bright|deep|soft|vivid)[\s-]+)?(?:{_COLOUR_WORDS})"
                         rf"(?:[\s-]+(?:{_HUE_WORDS}))?\b", re.I)


def _listed(names: list[str]) -> str:
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)


def _recolour_sam_phrases(data: dict, problems: list) -> None:
    """Each sam_phrase names its structure's colour in the word the illustrator is told (colour_word), the writer's
    noun kept: since Nov 1 the picture's description names no colour, so the painting has the colour code names, and
    SAM asked for the writer's "lime ball" looked for a colour the picture was never asked for ("NADPH yellow"). A
    phrase with no colour word is left as it is. Repaired, and recorded."""
    changed = []
    for scene in data["scenes"]:
        for structure in scene["structures"]:
            phrase, word = structure["sam_phrase"], colour_word(structure["color_hex"])
            found = COLOUR_NAME.search(phrase)
            if found and re.sub(r"[\s-]+", " ", found.group(0).lower()) != word:
                structure["sam_phrase"] = phrase[:found.start()] + word + phrase[found.end():]
                changed.append(f'"{phrase}" as "{structure["sam_phrase"]}"')
    if changed:
        problems.append(Problem(line_id="structures", rule="colours", repaired=True,
                                detail="SAM asks for each colour as the illustrator is told it: "
                                       + "; ".join(dict.fromkeys(changed))))


def _recolour_image_prompts(data: dict, problems: list) -> None:
    """A colour the picture's words give a part, right before its name ("a white potassium iodide"), is the one the
    Colours line gives it (art.paint_prompt): the writer was asked to name no colour there and named one in 61 of 62
    scenes (Nov 1), a quarter of them not the part's own. The longest name wins ("thylakoid lumen" over "thylakoid").
    Repaired, and recorded."""
    changed = []
    for scene in data["scenes"]:
        colours = {fold(name): s["color_hex"] for s in scene["structures"] for name in (s["name"], *s["aliases"])}
        if not colours:
            continue
        names = "|".join(re.escape(n) for n in sorted(colours, key=len, reverse=True))
        pattern = re.compile(rf"({COLOUR_NAME.pattern})(\s+)({names})\b", re.I)

        def own(found: re.Match) -> str:
            word = colour_word(colours[fold(found.group(3))])
            if re.sub(r"[\s-]+", " ", found.group(1).lower()) != word:
                changed.append(f'"{found.group(0)}" as "{word} {found.group(3)}"')
            return word + found.group(2) + found.group(3)

        scene["image_prompt"] = pattern.sub(own, scene["image_prompt"])
    if changed:
        problems.append(Problem(line_id="scenes", rule="colours", repaired=True,
                                detail="the picture's words give each part the colour the illustrator is told: "
                                       + "; ".join(dict.fromkeys(changed))))


def _check_colours(data: dict, problems: list) -> None:
    """Code finds each structure by its colour and names it to the illustrator (colour_word), the picture's only word
    for it: two structures of one scene in one colour word are painted alike. Two structures anywhere drawn alike, in
    one colour word as one thing ("king in blue"), read as one (Nov 1: Louis XVI and Charles I, and the bridge sang
    "Same broke king"); one colour for two different things (an orange tower and an orange chest) does not. The
    sam_phrases are made to name their colours so first. The rest is recorded, for the prompt: which part should
    change is the writer's to say."""
    _colour_for_ions(data, problems)
    _recolour_sam_phrases(data, problems)
    _recolour_image_prompts(data, problems)
    looks: dict[tuple[str, str], list[str]] = {}
    for scene in data["scenes"]:
        by_word: dict[str, list[str]] = {}
        for structure in scene["structures"]:
            word = colour_word(structure["color_hex"])
            by_word.setdefault(word, []).append(structure["name"])
            alike = looks.setdefault((word, " ".join(fold(structure["sam_phrase"]).split())), [])
            if structure["name"] not in alike:
                alike.append(structure["name"])
        for word, names in by_word.items():
            if len(names) > 1:
                problems.append(Problem(line_id=f"scene:{scene['id']}", rule="colours", repaired=False,
                                        detail=f'{_listed(names)} are {"both" if len(names) == 2 else "all"} '
                                               f'"{word}" to the illustrator'))
    for (_, phrase), names in looks.items():
        if len(names) > 1:
            problems.append(Problem(line_id="structures", rule="colours", repaired=False,
                                    detail=f'{_listed(names)} are drawn alike, each a "{phrase}", so they read as one'))


def _names_kinds(name: str, kinds) -> list[str]:
    """The kinds a structure's name is named for, by symbol or by word: "Sodium channel" and "Na⁺ channel", Na⁺."""
    return [kind for kind in kinds if any(re.search(rf"(?<!\w){re.escape(word)}s?(?!\w)", fold(name))
                                          for word in (kind.casefold(), *CARRIED_WORDS.get(kind, ())))]


def is_kind(name: str, kind: str) -> bool:
    """A structure that is the carried thing itself ("Oxygen", "Water molecules", "ATP"), not one named for it."""
    said = " ".join(re.sub(r"\b(ions?|molecules?|particles?)\b", " ", fold(name)).split())
    return said.removesuffix("s") in {kind.casefold(), *CARRIED_WORDS.get(kind, ())} or said == kind.casefold()


def _colour_for_ions(data: dict, problems: list) -> None:
    """A part named for one ion takes that ion's colour, the colour of its dots in every lesson (MARKER_COLOURS): the
    sodium channel the purple of Na⁺. On Nov 1 the pump's sodium channel was green, as the K⁺ dots were, and its
    potassium channel purple, as Na⁺, so the colours taught the swap. A part named for two (the sodium-potassium
    pump) keeps its own. A part this lands on, the same hue on one of its scenes, moves to the free hue nearest its
    own (Nov 2: the potassium channel's green met the writer's green membrane, and K⁺'s the green phosphate). Every
    other part keeps the writer's colour: moved off the hue of any ion on its scene, the light reactions' sunlight
    went grey, and their overview, fifteen parts, had no hue left. Repaired, and recorded."""
    changed, ionic = [], {}
    for scene in data["scenes"]:
        for structure in scene["structures"]:
            ions = _names_kinds(structure["name"], IONS)
            if len(ions) == 1:
                ionic[structure["name"]] = ions[0]
                if (new := MARKER_COLOURS[ions[0]]) != structure["color_hex"]:
                    changed.append(f'{structure["name"]} {colour_word(structure["color_hex"])} as {colour_word(new)}')
                    structure["color_hex"] = new
    scenes_of: dict[str, list[dict]] = {}
    for scene in data["scenes"]:
        for structure in scene["structures"]:
            scenes_of.setdefault(structure["name"], []).append(scene)
    moved: dict[str, str] = {}
    for name, scenes in scenes_of.items():
        if name in ionic:
            continue
        own = next(s["color_hex"] for s in scenes[0]["structures"] if s["name"] == name)
        hit = {_hue(MARKER_COLOURS[ionic[s["name"]]]) for scene in scenes for s in scene["structures"]
               if s["name"] in ionic}
        if _hue(own) not in hit:
            continue
        taken = {_hue(moved.get(s["name"], s["color_hex"])) for scene in scenes for s in scene["structures"]
                 if s["name"] != name}
        free = sorted((c for c in FREE_COLOURS if _hue(c) not in taken), key=lambda c: _hue_gap(c, own))
        if free:
            moved[name] = free[0]
            changed.append(f"{name} {colour_word(own)} as {colour_word(free[0])}, clear of the ion's")
    for scene in data["scenes"]:
        for structure in scene["structures"]:
            structure["color_hex"] = moved.get(structure["name"], structure["color_hex"])
    if changed:
        problems.append(Problem(line_id="structures", rule="colours", repaired=True,
                                detail="a part named for an ion is painted in its colour: "
                                       + "; ".join(dict.fromkeys(changed))))


# What a part an ion's colour lands on is painted instead: the free one nearest its own hue (_colour_for_ions).
FREE_COLOURS = ("#F57C1F", "#8B5CF6", "#E0559A", "#3B82F6", "#22A355", "#A0522D", "#DC3545", "#E6B422", "#1FA6A0",
                "#4CC9F0", "#9AA5B1")


def _hue(color_hex: str) -> str:
    """A colour's hue as the illustrator is told it, the shade dropped: "light purple" and "purple" read alike."""
    return colour_word(color_hex).split()[-1]


def _hue_gap(a: str, b: str) -> float:
    """How far apart two colours' hues are, in degrees round the wheel; a grey is far from every hue."""
    (ha, sa, _), (hb, sb, _) = (colorsys.rgb_to_hsv(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))) for c in (a, b))
    if sa < 0.15 or sb < 0.15:
        return 360.0
    gap = abs(ha - hb) * 360
    return min(gap, 360 - gap)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", fold(text)))


def _check_aliases(data: dict, problems: list) -> None:
    """An alias that is a word of the topic reveals its structure every time the topic is said: the light reactions'
    Sunlight, with the alias "light", was revealed by every "light reactions" (Nov 1). Not a word of the structure's own
    name ("pump" for the sodium-potassium pump), which means it. Recorded, not repaired: without the alias, a part the
    lines call by that word alone would never be revealed."""
    topic, seen = fold(data["topic"]), set()
    for scene in data["scenes"]:
        for structure in scene["structures"]:
            for alias in structure["aliases"]:
                word = " ".join(fold(alias).split())
                if not word or (structure["name"], word) in seen or _words(word) <= _words(structure["name"]):
                    continue
                if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", topic):
                    seen.add((structure["name"], word))
                    problems.append(Problem(line_id="structures", rule="alias", repaired=False,
                                            detail=f'"{alias}", an alias of {structure["name"]}, is a word of the '
                                                   f'topic: every line that says "{data["topic"]}" reveals it'))


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
# A style that raps one section says which (a learner's own; every style the studio picks raps both verses, Nov 2):
# only that section's lines run longer. However the
# writer puts it: "a rapped second verse", "raps the second verse", "the bridge is rapped".
RAPPED_SECOND_VERSE = re.compile(r"\brap(?:ped|ping|s)?\s+(?:on\s+)?(?:the\s+|a\s+)?(?:second verse|verse (?:2|two))\b"
                                 r"|\b(?:second verse|verse (?:2|two))\s+(?:is\s+|gets\s+)?rapped\b", re.I)
RAPPED_BRIDGE = re.compile(r"\brap(?:ped|ping|s)?\s+(?:on\s+)?(?:the\s+|a\s+)?bridge\b"
                           r"|\bbridge\s+(?:is\s+|gets\s+)?rapped\b", re.I)
# A style that raps every verse (Nov 2: all of the rotation's, studio/styles.py), its chorus sung: "rapped verses",
# "rap verses", "rap-sung verses", "raps both verses", "the verses are rapped", "hip-hop verses, rapped in a ... flow".
RAPPED_VERSES = re.compile(r"\brap(?:ped|ping|-sung)?\s+verses\b|\braps?\s+(?:both|all|its|the)\s+verses\b"
                           r"|\bverses,?\s+(?:are\s+)?rapped\b", re.I)


def rapped_lines(data: dict) -> set[str]:
    """The ids of the lines the style raps: every verse when it raps its verses, its second verse or its bridge when
    it names one (none, for a rapped bridge in a song with no bridge), every line when it raps otherwise ("make it
    rap"), none when it does not. A chorus is always sung (the bundle brief)."""
    style = " ".join((data.get("music") or {}).get("positive_styles") or [])
    if not RAPPED.search(style):
        return set()
    lyrics = data["lyrics"]
    runs: list[tuple[str, list[str]]] = []
    for line in lyrics:
        if runs and runs[-1][0] == line["section"]:
            runs[-1][1].append(line["id"])
        else:
            runs.append((line["section"], [line["id"]]))
    picked: set[str] = set()
    named = RAPPED_SECOND_VERSE.search(style) or RAPPED_BRIDGE.search(style) or RAPPED_VERSES.search(style)
    if RAPPED_VERSES.search(style):
        picked |= {i for section, ids in runs if section == "verse" for i in ids}
    elif RAPPED_SECOND_VERSE.search(style):
        verses = [ids for section, ids in runs if section == "verse"]
        picked |= set(verses[1]) if len(verses) > 1 else set()
    if RAPPED_BRIDGE.search(style):
        picked |= {i for section, ids in runs if section == "bridge" for i in ids}
    return picked if named else {line["id"] for line in lyrics}
# A line that narrates the picture or the song instead of being sung ("Zoom inside to the chloroplast"). "Next up"
# and "here comes" announce a section ("next up, the bridge"); before anything else they are sung: the link to the
# next topic the second pre-chorus sings ("Next up, the power rule", "Next up: estar plus an -ando"), or a part
# arriving ("Here comes HIV with a protein like a key"), as three droplets did on Oct 29.
# A bare "watch" or "see" is sung to the listener as often as not, and carries a song's frame: "Watch the replay, you
# know the score", "Watch the needle climbing slow" (Nov 1). They point at the screen only after "now" or "let's",
# and "look" only when it points somewhere ("look at", "look inside"); "zoom" always does.
SECTION_NAMES = r"(?:intro|verse|pre-chorus|chorus|bridge|hook|outro)"
DIRECTION_LINE = re.compile(r"^\W*(?:(?:now\s+)?(?:let'?s\s+)?zoom"
                            r"|(?:now\s+|(?:now\s+)?let'?s\s+)(?:look|see|watch)"
                            r"|(?:now\s+)?(?:let'?s\s+)?look\s+(?:at|inside|into|closer|here)"
                            r"|(?:now\s+)?here\s+we"
                            rf"|(?:now\s+)?(?:next\s+up|here\s+comes)\W+(?:(?:is|comes|the|a)\s+)*{SECTION_NAMES})\b",
                            re.I)


def _check_lyric_style(data: dict, problems: list) -> None:
    """A sung line is not a stage direction and is not too full to sing: both are only recorded (a better bundle,
    by the prompt, is the fix), so the operator sees which droplets sing them."""
    rapped = rapped_lines(data)
    for line in data["lyrics"]:
        text = line["text"]
        most = LYRIC_SYLLABLES_RAPPED if line["id"] in rapped else LYRIC_SYLLABLES_MAX
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


def chorus_spans(sections: list[str]) -> list[list[int]]:
    """The positions of each chorus's lines, chorus 1 first, from the lyrics' sections in order. A run of chorus lines
    can hold two: in a song with no bridge (a topic that is not a system that fails) the second chorus runs straight
    into the last, which opens on its summary couplet. So a later run holds as many choruses as chorus 1's length
    fits in it, the last aligned with the run's end, after its couplet."""
    runs, start = [], None
    for i, section in enumerate([*sections, ""]):
        if section == "chorus" and start is None:
            start = i
        elif section != "chorus" and start is not None:
            runs.append(list(range(start, i)))
            start = None
    if not runs:
        return []
    size, spans = len(runs[0]), [runs[0]]
    for run in runs[1:]:
        count = max(1, len(run) // size)
        spans += [run[k * size:(k + 1) * size] for k in range(count - 1)] + [run[-size:]]
    return spans


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
    choruses = [[bundle.lyrics[i] for i in span] for span in chorus_spans([line.section for line in bundle.lyrics])]
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


def repair_once(bundle: Bundle, study_guide: str = "") -> tuple[Bundle, list[Problem]]:
    """What the writer is no longer asked to get right, set once by the bundle step before its last code checks: every
    chorus sung as the first, a head count said as a common answer, only quotes the study guide holds, and the tags
    every video carries (schemas.with_study_tags). Not part of review(), which runs again after the fact-check: these
    change words, and a word changed after the check would go out unchecked. The same bundle when nothing changes."""
    data = bundle.model_dump()
    problems = _same_choruses(data)
    for line in data["narration"]:
        if (text := _counted(line["text"])) != line["text"]:
            problems.append(Problem(line_id=line["id"], rule="head-count", repaired=True,
                                    detail=f'"{line["text"]}" counts who makes the mistake; "{text}" instead'))
            line["text"] = text
    lesson = _counted(data["lesson_markdown"])
    if lesson != data["lesson_markdown"] and len(lesson) <= LESSON_MAX_CHARS:
        problems.append(Problem(line_id="lesson_markdown", rule="head-count", repaired=True,
                                detail="a sentence counted who makes the mistake; said as a common answer instead"))
        data["lesson_markdown"] = lesson
    facts, unquoted = held_to_guide(bundle.facts, study_guide)
    data["facts"] = [fact.model_dump() for fact in facts]
    data["tags"] = with_study_tags(bundle.tags, bundle.audience, bundle.sung).model_dump()
    settled = Bundle.model_validate(data)
    return (bundle if settled == bundle else settled), problems + unquoted


def _same_choruses(data: dict) -> list[Problem]:
    """Every later chorus sung word for word as the first, so a learner can sing it back after two hearings: a line
    that differs takes the first chorus's words, names and fact, keeping its own id and direction. Only a chorus as
    long as the first, opening and ending as it does, is held to it: any other may not line up with it."""
    lyrics = data["lyrics"]
    spans = chorus_spans([line["section"] for line in lyrics])
    first = [lyrics[i] for i in spans[0]] if spans else []
    problems = []
    for span in spans[1:]:
        later = [lyrics[i] for i in span]
        if len(later) != len(first) or not (_same(later[0]["text"], first[0]["text"])
                                            and _same(later[-1]["text"], first[-1]["text"])):
            continue
        for want, line in zip(first, later):
            if line["text"] != want["text"]:
                problems.append(Problem(line_id=line["id"], rule="chorus", repaired=True,
                                        detail=f'sang "{line["text"]}" where the first chorus sings "{want["text"]}"; '
                                               "sung as the first"))
                line.update(text=want["text"], names=list(want["names"]), fact_id=want["fact_id"])
    return problems


def _same(a: str, b: str) -> bool:
    return " ".join(fold(a).split()) == " ".join(fold(b).split())


# A head count no fact supports: the facts call a mistake common, they never count who makes it. "Most people say
# yes." went from the script prompt's own example into four droplets on Oct 31, "Many people say yes." into the
# derivative's on Nov 1, and the fact-check rewrote each. At the start of a sentence, said as a common answer, or a
# common guess, instead; any other head count is still the fact-check's.
HEAD_COUNT = re.compile(r"(^|[.!?:]\s+|\n\s*(?:[-*>]\s+|\d+\.\s+)?)(?:most|many|lots of|a lot of)\s+"
                        r"(?:people|students|learners)\s+(?:would\s+)?(?:(?:say|answer|guess)\s+(yes|no)\b"
                        r"|(?:think|believe)\s+that\s+)", re.I)


# What a head count is said as, one picked by the sentence so the same text always reads the same: one form stamped on
# every droplet read as a template (Nov 1, "A common answer is yes" in all twelve).
COMMON_ANSWER = ("A common answer is {}", "The usual guess is {}", "It's tempting to say {}")
COMMON_GUESS = ("A common guess is that ", "It's tempting to think that ", "It's easy to believe that ")


def _counted(text: str) -> str:
    def said(m: re.Match) -> str:
        pick = zlib.crc32(text[max(0, m.start() - 80):m.end() + 80].encode())  # the question before it, too
        if m[2]:
            return m[1] + COMMON_ANSWER[pick % len(COMMON_ANSWER)].format(m[2].lower())
        return m[1] + COMMON_GUESS[pick % len(COMMON_GUESS)]
    return HEAD_COUNT.sub(said, text)


def held_to_guide(facts: list[Fact], study_guide: str) -> tuple[list[Fact], list[Problem]]:
    """Each fact's source_quote only when it is copied from the study guide (3+ words, factcheck.quotes_guide): the
    audit is told it is the guide's words, so one the guide does not hold, or any with no guide, is left out."""
    from studio.steps.factcheck import quotes_guide  # the fact-check imports this module

    kept, problems = [], []
    for fact in facts:
        if fact.source_quote.strip() and not quotes_guide(fact.source_quote, study_guide):
            problems.append(Problem(line_id=fact.id, rule="source-quote", repaired=True,
                                    detail=f'"{fact.source_quote}" is not copied from the study guide; left out'))
            fact = fact.model_copy(update={"source_quote": ""})
        kept.append(fact)
    return kept, problems


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
    return problems


def _place_question(data: dict, problems: list) -> None:
    """The first key line, the question (it ends in "?"), where each video asks it: on the song's first line, and on
    the narration's last question before it leaves the hook. The writer put it on the narration's first sentence, a
    fact two sentences before the narrator asks (the pump's and the derivative's, Nov 1). Not onto a line another
    key line has."""
    if not data["key_lines"] or not data["key_lines"][0]["text"].strip().endswith("?"):
        return
    question, others = data["key_lines"][0], data["key_lines"][1:]
    first = data["lyrics"][0]["id"] if data["lyrics"] else ""
    if first and first != question["from_line"] and first not in {k["from_line"] for k in others}:
        problems.append(Problem(line_id=first, rule="key-line", repaired=True,
                                detail="the question's key line is on the song's first line"))
        question["from_line"] = first
    scenes = {d["line_id"]: d["scene"] for d in data["direction"]}
    ids = [s["id"] for s in data["scenes"]]
    hook = "hook" if "hook" in ids else (ids[0] if ids else "")  # as the timeline has it (timeline.hook_scene)
    opening = data["narration"][:1]  # the lesson opens on the hook, whatever its first line's direction says
    for n in data["narration"][1:]:
        if scenes.get(n["id"]) != hook:
            break
        opening.append(n)
    asked = [n["id"] for n in opening if n["text"].rstrip(" \"'”’").endswith("?")]
    if asked and asked[-1] != question.get("from_narration") \
            and asked[-1] not in {k.get("from_narration", "") for k in others}:
        problems.append(Problem(line_id=asked[-1], rule="key-line", repaired=True,
                                detail=f'the question\'s key line is on {asked[-1]}, where the narrator asks it'))
        question["from_narration"] = asked[-1]


def _check_key_lines(data: dict, problems: list) -> None:
    """Key lines, one to a lyric line: one on a line the song does not have, or a second on the same line, is left
    out of the song video (and dropped, with no narration sentence either), since the screen could not show it."""
    _place_question(data, problems)
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


def _direct_choruses(data: dict) -> list[Problem]:
    """Every chorus directed as the first, so the refrain reads as one: a later chorus's line that sings a line of
    the first word for word (matched from the end, as the last chorus may open with lines of its own) takes its
    direction, all but its caption. The brief asked for it, and the five choruses of Nov 1 did; this makes it sure.
    Before the lines are checked, so each is held to what is named by its own place in the song."""
    lyrics, problems = data["lyrics"], []
    directions = {d["line_id"]: d for d in data["direction"]}
    spans = chorus_spans([line["section"] for line in lyrics])
    for span in spans[1:]:
        for i, j in zip(reversed(spans[0]), reversed(span)):
            first, later = directions.get(lyrics[i]["id"]), directions.get(lyrics[j]["id"])
            sung = {" ".join(fold(lyrics[k]["text"]).split()) for k in (i, j)}
            if not first or not later or len(sung) > 1:
                continue
            copied = {**json.loads(json.dumps(first)), "line_id": later["line_id"], "caption": later["caption"]}
            if copied != later:
                later.clear()
                later.update(copied)
                problems.append(Problem(line_id=later["line_id"], rule="motion", repaired=True,
                                        detail=f"directed as {first['line_id']}, the first chorus's same line"))
    return problems


def motion_problems(bundle: Bundle) -> list[Problem]:
    """The song's direction against the planning rule a check can count: every chorus directed alike (compared from
    its end, since the last one opens with a summary couplet). The review directs a line the first chorus sings word
    for word as the first does (_direct_choruses), so what is left is a line of its own. Recorded, not repaired; the
    bundle step adds them.

    How many lines in a row hold still is not counted. The bundle prompt moves only what travels, and the spotlight
    and the camera carry a scene where nothing does, so a run of still lines is no mistake. Counted until Oct 31, a run of five flagged a chorus summing up the Moon's phases over the overview,
    the spotlight stepping from phase to phase, and only because one earlier line had sent sunlight across it: a
    scene that never moved was never flagged, so the count pushed toward dots for their own sake."""
    directions = {d.line_id: d for d in bundle.direction}
    problems = []
    choruses = [[bundle.lyrics[i].id for i in span] for span in chorus_spans([line.section for line in bundle.lyrics])]
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
    textbook prints it, none of them the structure itself: "rock → magma" beside Magma is a cycle's form turning into
    another, not a reaction there. One structure shows one conversion; any other is left out."""
    kept, seen = [], set()
    for conv in data["conversions"]:
        name = canon.get(fold(conv["structure"]))
        sides = [[notation(x) for x in conv[side] if notation(x)] for side in ("inputs", "outputs")]
        itself = name and any(canon.get(fold(x)) == name for side in ("inputs", "outputs")
                              for x in [*conv[side], *map(notation, conv[side])])
        why = ("names no structure" if not name else "names the structure itself" if itself else
               "repeats a structure's conversion" if name in seen else
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


# No song is shouted (the owner, Nov 2): the writer kept adding shouted gang vocals to its choruses. Each becomes
# backing vocals. And how a sung part goes is "catchy", never "melodic" (the owner, Nov 2).
VOICE_SWAPS = ((re.compile(r"\b(?:shouted\s+)?gang\s+vocals?\b|\bgroup\s+shouts?\b|\bshouts\b", re.I), "backing vocals"),
               (re.compile(r"\bshouted\s+", re.I), ""),
               (re.compile(r"\bmelodic\b", re.I), "catchy"))


def _check_voice(music: dict) -> list[Problem]:
    """The styles without shouting, and "catchy" for "melodic" (VOICE_SWAPS)."""
    styles = []
    for style in music["positive_styles"]:
        for pattern, instead in VOICE_SWAPS:
            style = pattern.sub(instead, style)
        styles.append(style)
    if styles == music["positive_styles"]:
        return []
    music["positive_styles"] = styles
    return [Problem(line_id="music", rule="song-style", repaired=True,
                    detail="the style asks for no shouted vocals, and catchy rather than melodic singing")]


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


# Each common kind of particle in its own colour, the same in every lesson (timeline.marker_colour), and the words a
# line calls it by: the renderer finds a count by them ("three sodium"), and _colour_for_ions a part named for an ion.
MARKER_COLOURS = {"H⁺": "#F2C230", "e⁻": "#4CC9F0", "O₂": "#E8EEF5", "H₂O": "#7FD1FF", "CO₂": "#B8C2CC",
                  "ATP": "#FF6B6B", "Na⁺": "#B79CFF", "K⁺": "#8EE06B", "Ca²⁺": "#F4E1C1", "Cl⁻": "#5EF2C4"}
IONS = {"Na⁺": ("sodium",), "K⁺": ("potassium",), "H⁺": ("hydrogen", "proton"), "Ca²⁺": ("calcium",),
        "Cl⁻": ("chloride",)}
CARRIED_WORDS = {**IONS, "e⁻": ("electron",), "O₂": ("oxygen",), "CO₂": ("carbon dioxide",), "H₂O": ("water",)}


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
