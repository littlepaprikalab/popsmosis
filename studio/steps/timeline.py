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
- A beat that sums up (the whole scene, nothing spotlit) runs every flow its scene has shown so far, beside its
  own, up to RUN_MAX: the system running, as a textbook's summary figure draws it. Each was taught, and
  fact-checked, on an earlier line, so nothing new is claimed; the spaces fill only from the beat's own.
- At most one caption per song section (one every LESSON_CAPTION_SPACING_S in the
  lesson), held for at least CAPTION_HOLD_S.
- A graph's axis names (Scene.axes) go to the renderer, which writes them at its axes' ends.
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
RUN_MAX = 2  # flows moving at once on a beat that sums up: round 17's rock cycle moved 4 at once, and it read as a swarm
SCENE_MIN_S = 3.5  # a picture on screen for less than this (one sung line) gives way to the one before it
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
    hook = hook_cut(bundle, timelines["song"])
    if hook is not None:
        write(job / media.timeline_path(media.HOOK), hook)
    else:
        (job / media.timeline_path(media.HOOK)).unlink(missing_ok=True)
    return "; ".join(
        f"{video} {t.duration:.1f} s, {len(t.beats)} beats, {sum(len(b.reveal) for b in t.beats)} reveals"
        for video, t in timelines.items()) + (f"; hook cut {hook.duration:.1f} s" if hook else "")


HOOK_LEAD_S = 0.3  # the hook cut starts this long before its first sung word
HOOK_TAIL_S = 0.8  # and runs this long past its last
HOOK_FADE_IN_S, HOOK_FADE_OUT_S = 0.25, 0.6
HOOK_MIN_S = 25.0  # shorter than this, the cut takes in the end of the verse before it...
HOOK_VERSE_LINES_MAX = 4  # ...up to this many of its lines


def hook_cut(bundle: Bundle, song: Timeline) -> Timeline | None:
    """The hook cut: the song's first chorus and the pre-chorus right before it, the part that sings the core idea
    with the line that sets it up (and, when that is shorter than HOOK_MIN_S, the end of the verse before it), as its own timeline for the upright video. Its beats are the song's, moved to
    start at 0, so it shows what the song shows then; its audio is that stretch of the song, faded in and out.
    None when the song has no chorus, or its lines were not timed."""
    sections = [line.section for line in bundle.lyrics]
    if "chorus" not in sections:
        return None
    first = sections.index("chorus")
    last = first
    while last + 1 < len(sections) and sections[last + 1] == "chorus":
        last += 1
    start = first
    while start > 0 and sections[start - 1] == "pre-chorus":
        start -= 1
    by_line = {beat.line_id: beat for beat in song.beats if beat.line_id}
    # A line the song never sings has no beat (step 6): the cut runs from the first line sung to the last.
    while start < last and bundle.lyrics[start].id not in by_line:
        start += 1
    while last > start and bundle.lyrics[last].id not in by_line:
        last -= 1
    # A short cut starts a few lines earlier, at the end of the verse that builds into the pre-chorus.
    for _ in range(HOOK_VERSE_LINES_MAX):
        head, tail = by_line.get(bundle.lyrics[start].id), by_line.get(bundle.lyrics[last].id)
        if (start == 0 or sections[start - 1] != "verse" or head is None or tail is None
                or tail.t1 - head.t0 >= HOOK_MIN_S or bundle.lyrics[start - 1].id not in by_line):
            break
        start -= 1
    opening, closing = by_line.get(bundle.lyrics[start].id), by_line.get(bundle.lyrics[last].id)
    if opening is None or closing is None or not song.audio:
        return None
    t0 = max((opening.words[0].start if opening.words else opening.t0) - HOOK_LEAD_S, 0.0)
    t1 = min((closing.words[-1].end if closing.words else closing.t1) + HOOK_TAIL_S, song.duration)
    beats = []
    for beat in song.beats:
        if beat.t1 <= t0 or beat.t0 >= t1:
            continue
        b0, b1 = max(beat.t0, t0) - t0, min(beat.t1, t1) - t0

        def moved(t: float) -> float:
            return round(min(max(t - t0, b0), b1), 3)

        beats.append(beat.model_copy(update={
            "t0": round(b0, 3), "t1": round(b1, 3),
            "reveal": [r.model_copy(update={"t": moved(r.t)}) for r in beat.reveal],
            "words": [w.model_copy(update={"start": moved(w.start), "end": moved(w.end)}) for w in beat.words
                      if t0 <= w.start < t1],
            "conversions": [c.model_copy(update={"since": max(c.since - t0, 0.0)}) for c in beat.conversions],
            "routes": [r.model_copy(update={"since": max(r.since - t0, 0.0)}) for r in beat.routes],
        }))
    duration = round(t1 - t0, 3)
    beats[-1] = beats[-1].model_copy(update={"t1": duration})
    voice = song.audio[0]
    audio = [AudioClip(path=voice.path, at=0, start=round(voice.start + t0, 3), end=round(voice.start + t1, 3),
                       fade_in=HOOK_FADE_IN_S, fade_out=HOOK_FADE_OUT_S)]
    return song.model_copy(update={"video": media.HOOK, "duration": duration, "audio": audio, "beats": beats,
                                   "title": bundle.topic})


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
    sung = [line for line in voice.lines if line.sung]  # a line its song never sings has no beat (step 6)
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

    scenes = _scenes(sung, directions, set(located), hook, voice.duration)
    first = sung[0].start if sung else voice.duration
    cursor = 0.0
    if first >= GAP_S:
        gap(0.0, first, None)
        cursor = first
    for i, timed_line in enumerate(sung):
        following = sung[i + 1].start if i + 1 < len(sung) else voice.duration
        silent = following - timed_line.end >= GAP_S
        t1 = round(timed_line.end + LINGER_S, 3) if silent else following
        t1 = max(t1, cursor + 1 / media.FPS)

        direction = directions.get(timed_line.line_id)
        scene = scenes[i]
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
        said = scene_names.heard([w.word for w in words])
        for name, index in sorted(said.items(), key=lambda e: e[1]):
            if name not in labels:
                labels.append(name)
                reveals.append(Reveal(name=name, t=min(max(words[index].start, cursor), t1)))
            if name not in heard:
                heard.append(name)

        spotlight = _shown(direction.spotlight, scene_names, labels) if direction else None
        target = _shown(direction.camera.target, scene_names, labels) if direction else None
        zoom = min(max(direction.camera.zoom, 1.0), MAX_ZOOM) if target and direction else 1.0
        particles = []
        for flow in direction.flows() if direction else []:  # the main flow, and one moving with it
            stops = [s for s in (scene_names.canonical(p) for p in flow.particles) if s]
            stops = [s for j, s in enumerate(stops) if j == 0 or s != stops[j - 1]]
            label = notation(flow.carries)
            if len(stops) >= 2:
                particles.append(Particles(path=stops, color=marker_colour(label), trail=True, label=label,
                                           blocked=stops[-1] in blockers))
        glow = _shown(direction.glow, scene_names, labels) if direction else None
        if scene not in opened and scene != hook:  # the first line of a scene shows the whole picture
            target, zoom = None, 1.0
        opened.add(scene)

        beats.append({"t0": cursor, "t1": t1, "scene": scene, "camera": TimelineCamera(target=target, zoom=zoom),
                      "reveal": reveals, "spotlight": spotlight, "glow": glow, "labels": list(labels),
                      "named": sorted(said, key=said.get), "particles": particles,
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
    _convert(beats, bundle, located)
    _run_all(beats, hook)
    _turn(beats, bundle, located)
    _route(beats)
    _key_lines(beats, bundle, video)
    audio = [AudioClip(path=voice.audio, at=0, start=skip, fade_in=INTRO_FADE_S) if skip
             else AudioClip(path=voice.audio, at=0)]
    stages = [[scene.id for scene in group] for group in stage_groups(bundle.scenes).values() if len(group) > 1]
    shown = {b["scene"] for b in beats}
    return Timeline(video=video, duration=duration, fps=media.FPS, audio=audio, palette=palette,
                    beats=[Beat(**b) for b in beats], stages=stages,
                    key_terms=bundle.key_terms, axes={s.id: s.axes for s in bundle.scenes if s.axes and s.id in shown})


def _scenes(sung: list, directions: dict, located: set[str], hook: str, end: float) -> list[str]:
    """Each line's scene: its direction's, or the one before where that is not drawn, the first line's the hook (both
    videos open on it). A run of lines on one scene shorter than SCENE_MIN_S keeps the scene before it: round 18's
    kidney song cut to a picture for a single two-second line six times, and each was gone before the eye found
    anything in it."""
    scenes: list[str] = []
    for line in sung:
        direction = directions.get(line.line_id)
        scene = direction.scene if direction else (scenes[-1] if scenes else hook)
        if not scenes:
            scene = hook
        elif scene not in located:
            scene = scenes[-1]
        scenes.append(scene)
    starts = [line.start for line in sung] + [end]
    k = 0
    while k < len(scenes):
        j = k
        while j + 1 < len(scenes) and scenes[j + 1] == scenes[k]:
            j += 1
        if k > 0 and starts[j + 1] - starts[k] < SCENE_MIN_S:
            scenes[k:j + 1] = [scenes[k - 1]] * (j + 1 - k)
        k = j + 1
    return scenes


CORE_SAID = 0.6  # the share of the core idea's words a narration sentence says to be the one that says it


def _says_core_idea(bundle: Bundle) -> int:
    """The narration sentence that says the core idea, as the prompt has it, in its own words (the last that says
    most of them); the last sentence when none does."""
    idea = set(tokens(bundle.core_idea))
    said = [i for i, n in enumerate(bundle.narration) if idea and len(idea & set(tokens(n.text))) >= CORE_SAID * len(idea)]
    return said[-1] if said else len(bundle.narration) - 1


def _key_lines(beats: list[dict], bundle: Bundle, video: str) -> None:
    """Each beat's key line: the last one to start on or before its line (a lyric in the song, a narration
    sentence in the lesson), held through the gaps after it. From each chorus (the core idea sung), or the
    narration's last sentence that says it, the core idea, the whole why chain in one line, until a key line
    starts later: round 19's Rome song held "Gracchi killed → politics settled by force" over its first chorus."""
    lines = bundle.lyrics if video == "song" else bundle.narration
    order = {line.id: i for i, line in enumerate(lines)}
    starts = sorted((order[at], " ".join(k.text.split())) for k in bundle.key_lines
                    if (at := k.from_line if video == "song" else k.from_narration) in order and k.text.strip())
    if not starts:
        return  # a bundle written before key lines: no band, the picture as it was
    if video == "song":
        choruses = [i for i, line in enumerate(bundle.lyrics) if line.section == "chorus"]
        core_from = [i for i in choruses if i - 1 not in choruses]  # where each chorus starts
    else:
        core_from = [_says_core_idea(bundle)]
    if bundle.core_idea.strip():
        starts += [(at, " ".join(bundle.core_idea.split())) for at in core_from]
    starts.sort(key=lambda s: s[0])  # stable: the core idea comes after a key line on the same line, and wins
    for i, beat in enumerate(beats):
        if beat["line_id"] in order:
            at = order[beat["line_id"]]
            beat["key_line"] = next((text for start, text in reversed(starts) if start <= at), "")
        else:
            beat["key_line"] = beats[i - 1].get("key_line", "") if i else ""


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


def _within(short: Particles, long: Particles) -> bool:
    """Whether one flow runs along part of another (or all of it) carrying the same thing."""
    n = len(short.path)
    return short.label == long.label and any(long.path[k:k + n] == short.path for k in range(len(long.path) - n + 1))


def _run_all(beats: list[dict], hook: str) -> None:
    """On a beat that sums up (the whole scene in view, nothing spotlit), every flow its scene has shown so far
    moves at once beside the beat's own. A flow along part of a longer one carrying the same thing gives way to the
    longer, so the whole pathway runs rather than two dots on one leg; at most RUN_MAX move. Not on the hook, nor on
    a scene once a flow on it has been blocked: after the block, the stillness is the point. Runs after the spaces
    are filled, so a space fills or empties only as the beat's own flows say."""
    taught: dict[str, list[Particles]] = {}  # per scene, each unblocked flow in the order it was first shown
    stopped: set[str] = set()  # scenes where a flow has been blocked
    for beat in beats:
        scene, own = beat["scene"], beat["particles"]
        shown = taught.setdefault(scene, [])
        if any(f.blocked for f in own):
            stopped.add(scene)
        if scene != hook and scene not in stopped and beat["spotlight"] is None and beat["camera"].target is None:
            running = list(own)
            for flow in shown:
                if any(_within(flow, g) for g in running):
                    continue  # already moving, itself or within a longer flow
                shorter = [g for g in running if _within(g, flow)]
                if len(running) - len(shorter) < RUN_MAX:
                    running = [g for g in running if g not in shorter] + [flow]
            beat["particles"] = running
        for flow in own:
            if not flow.blocked and not any(g.path == flow.path and g.label == flow.label for g in shown):
                shown.append(flow)


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
    later than INTRO_MAX_S, all but INTRO_KEEP_S of it; else nothing. A line not sung is not heard."""
    first = next((line.start for line in timing.lines if line.sung), 0.0)
    return round(first - INTRO_KEEP_S, 3) if first > INTRO_MAX_S else 0.0


def shifted(timing: Timing, skip: float) -> Timing:
    """The timing as heard when its audio is played from `skip` seconds in."""
    if not skip:
        return timing

    def at(t: float) -> float:
        return round(max(0.0, t - skip), 3)

    return Timing(audio=timing.audio, duration=at(timing.duration), lines=[
        TimedLine(line_id=line.line_id, start=at(line.start), end=at(line.end), sung=line.sung,
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
