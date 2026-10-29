"""Step 7: the timeline. Code merges the bundle's direction, the word timings and the
locations into timeline_song.json and timeline_lesson.json (docs/PLAN.md, sections 5 and 6).

No model calls, so it is free to run again:

- A beat per line, from where the line before it ends to where the next one starts. A
  silence of GAP_S or more between lines is a beat of its own (an instrumental gap) that
  holds the scene and the camera.
- A structure is revealed the first time its name or an alias is heard in the video, at
  that word's start, and its label stays on from then: a later scene that draws it shows
  it labelled from that scene's first line (a reveal at the line's start), rather than
  pale and unlabelled until it is named again. A learner keeps seeing what each part is. Words match whatever their case and punctuation, so "Broca's"
  matches "Broca’s area"'s first word, and a name of several words needs them all in
  a row. Longer names win, so "motor cortex" is not also read as "cortex".
- A spotlight or camera target that is not located on its scene, or not revealed there
  yet, falls back to no spotlight and the camera on the whole scene, as checks.py does.
- Both videos open on the hook scene, and the song video on its hook line: a song whose
  singing starts later than INTRO_MAX_S (Lyria now and then plays a 10-second intro
  however it is asked) is played from INTRO_KEEP_S before its first sung word, fading in,
  so the hook is sung within the first two seconds (PLAN section 8).
- The particles are marked with what they carry, as a textbook prints it ("H+" -> "H⁺",
  notation()), each kind in its own colour (marker_colour()), so a proton reads apart
  from an electron and from the ions painted in the picture.
- A space that something builds up in (Bundle.builds_up) fills while particles carrying it
  end there and empties while they start from it: FILL_S seconds of them fill it from
  empty, DRAIN_S would empty it, and it never drops below CROWD_MIN. How full it is runs
  on across scenes, so a close-up of the same space shows it as full as the overview left it.
- A conversion (Bundle.conversions) is written beside its structure, "H₂O → O₂ + H⁺ + e⁻", on the beats where
  that structure is first the subject (its spotlight, glow or camera target, or a stop of the particles), and
  it stays on every later beat of the scene.
- A path particles travel on a scene is drawn as arrows from stop to stop from when they first set off, and
  the arrows stay on every later beat of that scene, so the route stays readable after the particles stop.
  A blocked path (Bundle.blocks) is left out: its arrows would show a flow that does not happen.
- The camera opens each scene on the whole picture, and zooms to the line's target from the scene's second
  line, so the learner sees where a close-up sits before it is shown.
- A part that turns (Bundle.spins) eases up to SPIN_SPEED while particles flow into or
  through it, and eases to a stop when they stop; how far round it is runs on from beat
  to beat, so it never jumps.
- Particles whose last stop is a blocker (Bundle.blocks) are marked blocked: the renderer
  piles them up there. A line's glow, like its spotlight, needs its structure located on
  the scene and named by then.
- At most one caption per song section (one every LESSON_CAPTION_SPACING_S in the
  lesson), held for at least CAPTION_HOLD_S.
- The video ends TAIL_S after the song or the narration does, holding its last picture.
- Beats meet exactly: each starts where the one before it ends, from 0 to the end.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from studio import checks, media
from studio.checks import notation
from studio.jobs import read, write
from studio.media import StepFailed
from studio.schemas import (
    AudioClip,
    Beat,
    Bundle,
    Converting,
    Crowd,
    Locations,
    Particles,
    Reveal,
    Route,
    TimedLine,
    Timeline,
    TimelineCamera,
    Timing,
    Turning,
    Word,
    stage_groups,
    timeline_problems,
    timing_problems,
)
from studio.steps import art

HOOK = "hook"
GAP_S = 1.5  # a silence at least this long between lines gets a beat of its own
LINGER_S = 0.4  # how long a line stays in the lyric band after its last word, before a gap
CAPTION_HOLD_S = 4.0
LESSON_CAPTION_SPACING_S = 20.0  # the narration has no sections, so captions are spaced instead
PARTICLE_COLOR = "#F2C230"  # an unmarked particle's, and a proton's
# Each common kind of particle in its own colour, the same in every lesson; any other by its name.
MARKER_COLOURS = {"H⁺": PARTICLE_COLOR, "e⁻": "#4CC9F0", "O₂": "#E8EEF5", "H₂O": "#7FD1FF", "CO₂": "#B8C2CC",
                  "ATP": "#FF6B6B", "Na⁺": "#B79CFF", "K⁺": "#8EE06B", "Ca²⁺": "#F4E1C1", "Cl⁻": "#5EF2C4"}
OTHER_MARKERS = ("#FF9F68", "#F784C5", "#9AD1FF", "#C3F26B", "#FFD166", "#C9A7FF")
MAX_ZOOM = 3.0
FILL_S = 6.0  # seconds of particles arriving that fill a space from empty
DRAIN_S = 10.0  # seconds of them leaving that would empty it
CROWD_START = 0.15  # how full a space is before anything has arrived: a few, so it reads as a place they gather
CROWD_MIN = 0.15  # it never empties: a gradient runs down, it does not vanish
SPIN_SPEED = 0.45  # turns a second while particles drive a part: fast enough to see, slow enough to follow
TAIL_S = 1.5  # the last picture stays on screen this long after the voice ends
INTRO_MAX_S = 2.0  # the song's first line is sung by then, or its intro is cut
INTRO_KEEP_S = 1.0  # a cut intro keeps this much before the first sung word
INTRO_FADE_S = 0.5


def run(job: Path, ask=None) -> str:
    """Write both timelines; `ask` is unused (no model calls)."""
    media.restart(job, *(media.timeline_path(video) for video in media.VIDEOS))
    bundle = checks.load(job / "bundle.json")
    locations = read(job / media.LOCATIONS, Locations)
    timed = {kind: read(job / media.timing_path(kind), Timing) for kind in media.TIMED}
    expected = {
        "song": [line.id for line in bundle.lyrics],
        "narration": [n.id for n in bundle.narration],
    }
    problems = [f"{kind}: {p}" for kind, ids in expected.items() for p in timing_problems(timed[kind], ids)]
    if problems:
        raise StepFailed("the timings do not match the bundle: " + "; ".join(problems))

    timelines = {}
    notices = art.notices(job, bundle)
    for video in media.VIDEOS:
        try:
            timeline = build(video, bundle, timed, locations)
            shown = {beat.scene for beat in timeline.beats}
            timeline.notices = {scene: notes for scene, notes in notices.items() if scene in shown}
            # Every picture here is AI-made, painted or drawn: each is tagged so, mistakes found or not.
            timeline.ai_pictures = [scene.id for scene in bundle.scenes if scene.id in shown]
        except ValidationError as e:
            raise StepFailed(f"the {video} timeline does not validate: {e}") from e
        problems = timeline_problems(timeline, locations)
        if problems:
            raise StepFailed(f"the {video} timeline: " + "; ".join(problems))
        timelines[video] = timeline
    for video, timeline in timelines.items():
        write(job / media.timeline_path(video), timeline)
    return "; ".join(
        f"{video} {t.duration:.1f} s, {len(t.beats)} beats, {sum(len(b.reveal) for b in t.beats)} reveals"
        for video, t in timelines.items())


def is_done(job: Path) -> bool:
    """Both timelines read, and are newer than everything they were made from."""
    inputs = [job / "bundle.json", job / media.LOCATIONS, *(job / media.timing_path(kind) for kind in media.TIMED),
              *(job / "art").glob("*.json")]  # what each picture's check found, for its notices
    try:
        newest_input = max(path.stat().st_mtime for path in inputs)
        for video in media.VIDEOS:
            path = job / media.timeline_path(video)
            read(path, Timeline)
            if path.stat().st_mtime < newest_input:
                return False
    except (OSError, ValueError):
        return False
    return True


# --- hearing names ----------------------------------------------------------------------


def tokens(text: str) -> list[str]:
    """Words folded for matching: lower case, apostrophes dropped, split at anything else."""
    return re.findall(r"[a-z0-9]+", checks.fold(text).replace("'", ""))


def _same(heard: str, name: str, last: bool) -> bool:
    """A heard token matches a name's token; the last one may be plural ("thylakoids")."""
    return heard == name or (last and heard in (name + "s", name + "es"))


def marker_colour(label: str) -> str:
    """The colour of particles carrying `label`: its own for a common kind, else one picked by its name."""
    if not label:
        return PARTICLE_COLOR
    return MARKER_COLOURS.get(label) or OTHER_MARKERS[zlib.crc32(label.encode()) % len(OTHER_MARKERS)]


@dataclass
class Names:
    """The structures one scene can reveal (the located ones), each with the words that name it."""

    entries: list[tuple[tuple[str, ...], str]]  # (folded words, structure name), longest first

    @property
    def located(self) -> set[str]:
        return {name for _, name in self.entries}

    @classmethod
    def for_scene(cls, bundle: Bundle, scene_id: str, located: set[str]) -> Names:
        entries: dict[tuple[str, ...], str] = {}
        for scene in bundle.scenes:
            if scene.id != scene_id:
                continue
            for s in scene.structures:
                if s.name not in located:
                    continue
                for word in (s.name, *s.aliases):
                    if key := tuple(tokens(word)):
                        entries.setdefault(key, s.name)  # a shared name reveals the first
        return cls(sorted(entries.items(), key=lambda e: len(e[0]), reverse=True))

    def heard(self, words: list[str]) -> dict[str, int]:
        """Each structure the words name, mapped to the index of the word its name starts on."""
        flat = [(tok, i) for i, word in enumerate(words) for tok in tokens(word)]
        taken = [False] * len(flat)
        found: dict[str, int] = {}
        for key, name in self.entries:
            n = len(key)
            for start in range(len(flat) - n + 1):
                span = range(start, start + n)
                if any(taken[j] for j in span):
                    continue
                if all(_same(flat[start + k][0], key[k], k == n - 1) for k in range(n)):
                    for j in span:
                        taken[j] = True
                    word = flat[start][1]
                    found[name] = min(found.get(name, word), word)
        return found

    def canonical(self, name: str) -> str | None:
        """A direction's name (or alias) as the structure's name, if this scene has it located."""
        key = tuple(tokens(name))
        return next((n for k, n in self.entries if k == key), None) if key else None


# --- the merge --------------------------------------------------------------------------


def hook_scene(bundle: Bundle) -> str:
    ids = [s.id for s in bundle.scenes]
    return HOOK if HOOK in ids else ids[0]


def build(video: str, bundle: Bundle, timed: dict[str, Timing], locations: Locations) -> Timeline:
    """One video's timeline. Raises ValidationError if the result does not hold together."""
    lines = bundle.lyrics if video == "song" else bundle.narration
    voice = timed["song" if video == "song" else "narration"]
    skip = intro_skip(voice) if video == "song" else 0.0
    voice = shifted(voice, skip)
    texts = {line.id: line.text for line in lines}
    sections = _section_runs(bundle) if video == "song" else {}
    directions = {d.line_id: d for d in bundle.direction}
    palette = {s.name: s.color_hex for scene in bundle.scenes for s in scene.structures}
    located = {s.scene: {loc.name for loc in s.structures} for s in locations.scenes}
    names = {scene.id: Names.for_scene(bundle, scene.id, located.get(scene.id, set())) for scene in bundle.scenes}
    hook = hook_scene(bundle)
    blockers = {b.structure for b in bundle.blocks}

    beats: list[dict] = []
    revealed: dict[str, list[str]] = {}  # per scene, in order
    heard: list[str] = []  # every structure named in this video so far, in order
    captions: list[str | None] = []
    opened: set[str] = set()  # scenes whose first line has played

    def gap(t0: float, t1: float, after: dict | None) -> None:
        scene = after["scene"] if after else hook
        camera = after["camera"] if after else TimelineCamera(target=None, zoom=1.0)
        beats.append({"t0": t0, "t1": t1, "scene": scene, "camera": camera, "reveal": [], "spotlight": None,
                      "labels": list(revealed.get(scene, [])), "particles": [], "caption": None, "line_id": None,
                      "line": "", "words": []})
        captions.append(None)

    first = voice.lines[0].start if voice.lines else voice.duration
    cursor = 0.0
    if first >= GAP_S:
        gap(0.0, first, None)
        cursor = first
    for i, timed_line in enumerate(voice.lines):
        following = voice.lines[i + 1].start if i + 1 < len(voice.lines) else voice.duration
        silent = following - timed_line.end >= GAP_S
        t1 = round(timed_line.end + LINGER_S, 3) if silent else following
        t1 = max(t1, cursor + 1 / media.FPS)

        direction = directions.get(timed_line.line_id)
        scene = direction.scene if direction else (beats[-1]["scene"] if beats else hook)
        if not any(b["line_id"] for b in beats):
            scene = hook  # both videos open on the hook
        if scene not in located:
            scene = beats[-1]["scene"] if beats else hook
        scene_names = names.get(scene) or Names([])
        labels = revealed.setdefault(scene, [])

        words = timed_line.words or [Word(word=w, start=timed_line.start, end=timed_line.end)
                                     for w in texts.get(timed_line.line_id, "").split()]
        # A beat lasts at least a frame, so lines the aligner put at one instant push the
        # beats after them a little late: keep each word inside its own beat.
        words = [Word(word=w.word, start=min(max(w.start, cursor), t1), end=min(max(w.end, cursor), t1))
                 for w in words]
        # What was named on an earlier scene, and is drawn on this one, is labelled from the start.
        reveals = [Reveal(name=name, t=cursor) for name in heard
                   if name in located.get(scene, set()) and name not in labels]
        labels += [r.name for r in reveals]
        for name, index in sorted(scene_names.heard([w.word for w in words]).items(), key=lambda e: e[1]):
            if name not in labels:
                labels.append(name)
                reveals.append(Reveal(name=name, t=min(max(words[index].start, cursor), t1)))
            if name not in heard:
                heard.append(name)

        spotlight = _shown(direction.spotlight, scene_names, labels) if direction else None
        target = _shown(direction.camera.target, scene_names, labels) if direction else None
        zoom = min(max(direction.camera.zoom, 1.0), MAX_ZOOM) if target and direction else 1.0
        stops = [s for s in (scene_names.canonical(p) for p in (direction.particles if direction else [])) if s]
        stops = [s for j, s in enumerate(stops) if j == 0 or s != stops[j - 1]]
        label = notation(direction.carries) if direction else ""
        particles = [Particles(path=stops, color=marker_colour(label), trail=True, label=label,
                               blocked=stops[-1] in blockers)] if len(stops) >= 2 else []
        glow = _shown(direction.glow, scene_names, labels) if direction else None
        if scene not in opened and scene != hook:  # the first line of a scene shows the whole picture
            target, zoom = None, 1.0
        opened.add(scene)

        beats.append({"t0": cursor, "t1": t1, "scene": scene, "camera": TimelineCamera(target=target, zoom=zoom),
                      "reveal": reveals, "spotlight": spotlight, "glow": glow, "labels": list(labels), "particles": particles,
                      "caption": None, "line_id": timed_line.line_id, "line": texts.get(timed_line.line_id, ""),
                      "words": words})
        captions.append((direction.caption.strip() or None) if direction else None)
        cursor = t1
        if silent and following > t1:
            gap(t1, following, beats[-1])
            cursor = following
    if not beats:
        gap(0.0, max(voice.duration, 1 / media.FPS), None)
        cursor = beats[-1]["t1"]

    _place_captions(beats, captions, sections, video)
    # The last beat holds its picture to the end, TAIL_S after the voice.
    duration = round(max(cursor, voice.duration) + TAIL_S, 3)
    beats[-1]["t1"] = duration
    _fill_crowds(beats, bundle, located)
    _turn(beats, bundle, located)
    _convert(beats, bundle, located)
    _route(beats)
    audio = [AudioClip(path=voice.audio, at=0, start=skip, fade_in=INTRO_FADE_S) if skip
             else AudioClip(path=voice.audio, at=0)]
    stages = [[scene.id for scene in group] for group in stage_groups(bundle.scenes).values() if len(group) > 1]
    return Timeline(video=video, duration=duration, fps=media.FPS, audio=audio, palette=palette,
                    beats=[Beat(**b) for b in beats], stages=stages)


def _fill_crowds(beats: list[dict], bundle: Bundle, located: dict[str, set[str]]) -> None:
    """How full each build-up's space is on each beat: rising while particles carrying it end there,
    falling while they start from it, carried on from beat to beat whatever the scene. A beat shows
    the spaces located on its scene."""
    builds = [(notation(b.carries), b.region) for b in bundle.builds_up]
    level = dict.fromkeys(builds, CROWD_START)
    for beat in beats:
        crowds = []
        for build in builds:
            label, region = build
            flows = [f for f in beat["particles"] if f.label == label]
            rate = ((1 / FILL_S if any(f.path[-1] == region for f in flows) else 0)
                    - (1 / DRAIN_S if any(f.path[0] == region for f in flows) else 0))
            start = level[build]
            level[build] = end = min(max(start + rate * (beat["t1"] - beat["t0"]), CROWD_MIN), 1.0)
            if region in located.get(beat["scene"], set()):
                crowds.append(Crowd(region=region, label=label, color=marker_colour(label),
                                    start=round(start, 4), end=round(end, 4)))
        beat["crowds"] = crowds


def _convert(beats: list[dict], bundle: Bundle, located: dict[str, set[str]]) -> None:
    """The conversions each beat writes. One is written from the first beat on its scene that is about its
    structure (spotlight, glow, camera target, or a stop of its particles, with the structure labelled by then),
    and stays on every later beat of that scene, as a label does."""
    shown: dict[str, dict[str, float]] = {}  # per scene: structure -> when it was first written
    for beat in beats:
        scene = shown.setdefault(beat["scene"], {})
        subjects = {beat["spotlight"], beat.get("glow"), beat["camera"].target}
        subjects |= {stop for f in beat["particles"] for stop in f.path}
        for c in bundle.conversions:
            if (c.structure in subjects and c.structure in beat["labels"] and c.structure not in scene
                    and c.structure in located.get(beat["scene"], set())):
                scene[c.structure] = beat["t0"]
        beat["conversions"] = [
            Converting(structure=c.structure, text=f"{' + '.join(c.inputs)} → {' + '.join(c.outputs)}",
                       since=scene[c.structure])
            for c in bundle.conversions if c.structure in scene]


def _route(beats: list[dict]) -> None:
    """The paths each beat draws as arrows: every unblocked path its scene's particles have travelled so far."""
    shown: dict[str, dict[tuple, Route]] = {}  # per scene: (path, label) -> its route, as first drawn
    for beat in beats:
        routes = shown.setdefault(beat["scene"], {})
        for f in beat["particles"]:
            key = (tuple(f.path), f.label)
            if not f.blocked and key not in routes:
                routes[key] = Route(path=list(f.path), color=f.color, label=f.label, since=beat["t0"])
        beat["routes"] = list(routes.values())


def _turn(beats: list[dict], bundle: Bundle, located: dict[str, set[str]]) -> None:
    """How each spinning part turns on each beat: easing up to SPIN_SPEED while particles flow into or through
    it, and to a stop when none do, its turns carried on from beat to beat. A beat shows the parts located
    on its scene."""
    state = {s.structure: (0.0, 0.0) for s in bundle.spins}  # turns, speed
    for beat in beats:
        turning = []
        for spin in bundle.spins:
            driven = any(spin.structure in f.path[1:] for f in beat["particles"])
            turns, speed = state[spin.structure]
            turn = Turning(structure=spin.structure, turns=round(turns % 1, 6), from_speed=speed,
                           speed=SPIN_SPEED if driven else 0.0)
            state[spin.structure] = turn.at(beat["t1"] - beat["t0"])
            if spin.structure in located.get(beat["scene"], set()):
                turning.append(turn)
        beat["turning"] = turning


def intro_skip(timing: Timing) -> float:
    """How much of the start of the audio to leave out: its intro, when the first word is heard
    later than INTRO_MAX_S, all but INTRO_KEEP_S of it; else nothing."""
    first = timing.lines[0].start if timing.lines else 0.0
    return round(first - INTRO_KEEP_S, 3) if first > INTRO_MAX_S else 0.0


def shifted(timing: Timing, skip: float) -> Timing:
    """The timing as heard when its audio is played from `skip` seconds in."""
    if not skip:
        return timing

    def at(t: float) -> float:
        return round(max(0.0, t - skip), 3)

    return Timing(audio=timing.audio, duration=at(timing.duration), lines=[
        TimedLine(line_id=line.line_id, start=at(line.start), end=at(line.end),
                  words=[Word(word=w.word, start=at(w.start), end=at(w.end)) for w in line.words])
        for line in timing.lines])


def _shown(name: str, scene_names: Names, labels: list[str]) -> str | None:
    """A direction's spotlight or target, if it is located on the scene and revealed there by now."""
    structure = scene_names.canonical(name) if name else None
    return structure if structure in labels else None


def _section_runs(bundle: Bundle) -> dict[str, int]:
    """Each lyric's section as sung: verse 1 is 0, the first chorus 1, and so on."""
    runs, n = {}, -1
    for i, line in enumerate(bundle.lyrics):
        if i == 0 or line.section != bundle.lyrics[i - 1].section:
            n += 1
        runs[line.id] = n
    return runs


def _place_captions(beats: list[dict], captions: list[str | None], sections: dict[str, int], video: str) -> None:
    """Keep at most one caption per section (or per spacing), and hold each for CAPTION_HOLD_S."""
    used_sections: set[int] = set()
    last_start: float | None = None
    for i, caption in enumerate(captions):
        if not caption:
            continue
        beat = beats[i]
        if beat["caption"]:
            continue  # the one before is still held
        if video == "song":
            section = sections.get(beat["line_id"])
            if section in used_sections:
                continue
            used_sections.add(section)
        elif last_start is not None and beat["t0"] - last_start < LESSON_CAPTION_SPACING_S:
            continue
        last_start = beat["t0"]
        for later in beats[i:]:
            if later["t0"] >= last_start + CAPTION_HOLD_S and later is not beat:
                break
            later["caption"] = caption
