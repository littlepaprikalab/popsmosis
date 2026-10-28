"""Pydantic models for the studio's files (docs/PLAN.md, sections 4 and 5).

Bundle and FactCheck are what the brain writes. Their field order is the order the
model writes in, so later parts build on what it has already written. They export
only what both providers accept (brain.strict_schema); counts, formats and
cross-references are checked by validators, which never reach the schema.
"""

from __future__ import annotations

import bisect
import functools
import math
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from studio.schematic3d import library as schematics

FILLER = "filler"
Section = Literal["verse", "chorus", "bridge"]
SECTION_ORDER = ["verse", "chorus", "verse", "chorus", "bridge", "chorus"]
Verdict = Literal["SUPPORTED", "UNSUPPORTED", "FILLER"]
LESSON_MAX_CHARS = 5000  # what POST /api/questions/:id/answer accepts
BPM_RANGE = (80, 160)  # the tempos the song may take; a line is two bars, so 6.0 s down to 3.0 s
DEFAULT_BPM = 112  # a song whose styles name no tempo: every song before the styles chose one
BPM_TAG = re.compile(r"\b(\d{2,3}(?:\.\d+)?)\s*bpm\b", re.IGNORECASE)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _hex_color(value: str) -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"{value!r} is not #RRGGBB")
    return value.upper()


# --- Step 1: the bundle -------------------------------------------------------


class Fact(Strict):
    id: str = Field(description='"f1", "f2", ...')
    text: str
    source_quote: str = Field(description='Verbatim from the study guide; "" when there is none')


class Frq(Strict):
    prompt: str
    predict_question: str
    model_answer_steps: list[str]
    twist: str


class Structure(Strict):
    name: str
    aliases: list[str]
    sam_phrase: str
    color_hex: str = Field(description='"#RRGGBB"')

    @field_validator("color_hex")
    @classmethod
    def _hex(cls, value: str) -> str:
        return _hex_color(value)


def _schematic_names(schema: dict) -> None:
    # The writer may choose only the recipes a person has checked; a stored bundle may name any.
    schema["enum"] = ["", *schematics.offered()]


class Scene(Strict):
    id: str
    schematic: str = Field(description='A machine from the schematic library that this scene draws, by name; '
                                       '"" for an illustration', json_schema_extra=_schematic_names)
    image_prompt: str
    structures: list[Structure]

    @model_validator(mode="before")
    @classmethod
    def _older_scenes(cls, data):
        # Every scene written before the schematic library was an illustration.
        if isinstance(data, dict) and "schematic" not in data:
            return {**data, "schematic": ""}
        return data

    @field_validator("schematic")
    @classmethod
    def _known(cls, value: str) -> str:
        if value and value not in schematics.all_names():
            raise ValueError(f"{value!r} is not a recipe in the schematic library")
        return value


class Music(Strict):
    positive_styles: list[str]
    negative_styles: list[str]
    sections: list[Section]

    @property
    def bpm(self) -> int:
        """The first tempo the styles name ("98 bpm"), held to BPM_RANGE; DEFAULT_BPM when they name none."""
        for style in self.positive_styles:
            if found := BPM_TAG.search(style):
                return min(BPM_RANGE[1], max(BPM_RANGE[0], round(float(found.group(1)))))
        return DEFAULT_BPM


class Lyric(Strict):
    id: str
    section: Section
    text: str
    names: list[str] = Field(description="Every structure name or alias the line says, as written; [] for none")
    fact_id: str = Field(description='The id of the fact this line teaches, or "filler"')


class Narration(Strict):
    id: str
    text: str
    names: list[str] = Field(description="Every structure name or alias the sentence says, as written; [] for none")
    fact_id: str = Field(description='The id of the fact this sentence teaches, or "filler"')


class Camera(Strict):
    target: str = Field(description='A structure name, or "" for the whole scene')
    zoom: float


class Direction(Strict):
    line_id: str = Field(description="The id of a lyric line or a narration sentence")
    scene: str
    camera: Camera
    spotlight: str = Field(description='A structure name, or "" for none')
    particles: list[str] = Field(description="Structure names the particles travel between, in order; [] for none")
    caption: str = Field(description='Usually ""')


class Bundle(Strict):
    course: str
    topic: str
    audience: str = Field(description='Who the droplet is written for: "high school" unless the request names someone else')
    core_idea: str
    facts: list[Fact]
    misconception: str
    frq: Frq
    scenes: list[Scene]
    lesson_markdown: str
    music: Music
    lyrics: list[Lyric]
    narration: list[Narration]
    direction: list[Direction]

    @model_validator(mode="before")
    @classmethod
    def _older_bundles(cls, data):
        # A bundle written before `audience` existed was pitched to high school.
        if isinstance(data, dict) and "audience" not in data:
            return {**data, "audience": "high school"}
        return data

    @model_validator(mode="after")
    def _consistent(self) -> Bundle:
        problems = bundle_problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def bundle_problems(bundle: Bundle) -> list[str]:
    """What downstream steps rely on but the schema cannot say."""
    problems = []
    if not 3 <= len(bundle.facts) <= 4:
        problems.append(f"{len(bundle.facts)} facts, not 3-4")
    if not 3 <= len(bundle.scenes) <= 6:
        problems.append(f"{len(bundle.scenes)} scenes, not 3-6")
    for kind, ids in (
        ("fact", [f.id for f in bundle.facts]),
        ("scene", [s.id for s in bundle.scenes]),
        ("line", [line.id for line in bundle.lyrics] + [n.id for n in bundle.narration]),
    ):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            problems.append(f"duplicate {kind} ids: {', '.join(duplicates)}")

    fact_ids = {f.id for f in bundle.facts} | {FILLER}
    for line in [*bundle.lyrics, *bundle.narration]:
        if line.fact_id not in fact_ids:
            problems.append(f"{line.id} teaches unknown fact {line.fact_id!r}")

    colors: dict[str, str] = {}
    for scene in bundle.scenes:
        names = [s.name for s in scene.structures]
        if len(names) != len(set(names)):
            problems.append(f"scene {scene.id} names a structure twice")
        for structure in scene.structures:
            if colors.setdefault(structure.name, structure.color_hex) != structure.color_hex:
                problems.append(f"{structure.name} has two colours")

    sung = [line.section for i, line in enumerate(bundle.lyrics) if i == 0 or line.section != bundle.lyrics[i - 1].section]
    if sung != bundle.music.sections:
        problems.append(f"lyrics run {'/'.join(sung)} but music.sections is {'/'.join(bundle.music.sections)}")

    line_ids = [line.id for line in bundle.lyrics] + [n.id for n in bundle.narration]
    directed = [d.line_id for d in bundle.direction]
    missing = [i for i in line_ids if i not in directed]
    extra = sorted({i for i in directed if i not in line_ids or directed.count(i) > 1})
    if missing:
        problems.append(f"no direction for {', '.join(missing)}")
    if extra:
        problems.append(f"unknown or repeated direction for {', '.join(extra)}")
    scene_ids = {s.id for s in bundle.scenes}
    for d in bundle.direction:
        if d.scene not in scene_ids:
            problems.append(f"direction for {d.line_id} names unknown scene {d.scene!r}")

    if not bundle.lyrics or not bundle.narration:
        problems.append("lyrics and narration must not be empty")
    if not bundle.frq.model_answer_steps:
        problems.append("the FRQ has no model answer steps")
    if not bundle.lesson_markdown.strip():
        problems.append("lesson_markdown is empty")
    elif len(bundle.lesson_markdown) > LESSON_MAX_CHARS:
        problems.append(f"lesson_markdown is {len(bundle.lesson_markdown)} characters, over {LESSON_MAX_CHARS}")
    return problems


class Problem(Strict):
    line_id: str  # or "scene:<id>" for a whole scene, "structures" for the structure list, "music" for the styles
    rule: Literal["empty-scene", "shared-name", "unknown-name", "not-in-scene", "name-not-said", "unlisted-name",
                  "spotlight", "camera", "particles", "tempo", "schematic"]
    detail: str
    repaired: bool  # False: only a better bundle (the prompt) can fix it


class CheckReport(Strict):
    """checks.json: what the code checks found in the bundle's names and direction."""

    problems: list[Problem]


# --- Step 2: the fact-check ---------------------------------------------------


class Check(Strict):
    id: str
    verdict: Verdict
    source: str = Field(description='SUPPORTED: the fact id or a verbatim study-guide quote. Otherwise ""')
    problem: str = Field(description='UNSUPPORTED: what is wrong, in one sentence. Otherwise ""')
    corrected: str = Field(description='UNSUPPORTED: the line rewritten to say only what the facts support. Otherwise ""')


class FactCheck(Strict):
    checks: list[Check]


class CheckedLine(Strict):
    # "motion": a claim a schematic's motion would make (studio/schematic3d/library.probes); it is
    # never corrected and never fails the check: the motion is drawn only if it is SUPPORTED.
    kind: Literal["lyric", "narration", "lesson", "frq", "motion"]
    id: str
    text: str
    verdict: Verdict | None  # None: never got a verdict
    source: str
    round: int  # the round that gave the verdict; 0 if none did


class Correction(Strict):
    round: int
    kind: Literal["lyric", "narration", "lesson", "frq"]
    ids: list[str]
    before: str
    after: str
    problem: str


class FactCheckReport(Strict):
    """factcheck.json: the verdict on every line of the bundle as it now stands."""

    status: Literal["passed", "failed"]
    reason: str
    rounds: int
    lines: list[CheckedLine]
    corrections: list[Correction]


# --- Job input ----------------------------------------------------------------


class Request(Strict):
    """request.json: what the learner asked for."""

    topic: str
    course: str = ""
    request: str = ""  # their own words, when they said more than the topic
    song_style: str = ""  # the style they asked the song to have, in their words
    study_guide: str = ""
    images: list[str] = []  # paths to photos of their notes


# --- Steps 3-9: the media files ---------------------------------------------------
#
# Code writes these, never a model, so unlike the bundle they may use None and dicts.
# Times are seconds; positions are art pixels, (0, 0) at the top left. Their paths, and
# how a media step is built, are in studio/media.py.

TOLERANCE_S = 0.05  # how far a word or a reveal may stray past its line or beat
EXACT_S = 1e-6  # beats meet exactly (code sets each t0 to the t1 before it), so no frame falls between two
# The API's rule for an upload path (api/src/lib/storage.ts, FILE_RE): relative, and never "." or "..".
SAFE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")


def _job_path(value: str) -> str:
    if not SAFE_PATH.fullmatch(value) or len(value) > 300:
        raise ValueError(f"{value!r} is not a path in the job folder the API would take; "
                         "build it with studio/media.py")
    return value


JobPath = Annotated[str, AfterValidator(_job_path)]  # a file in the job folder, relative to it


class Point(Strict):
    x: float
    y: float


class Box(Strict):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> Box:
        if not (self.x0 < self.x1 and self.y0 < self.y1):
            raise ValueError(f"box ({self.x0}, {self.y0})-({self.x1}, {self.y1}) is empty")
        return self

    def holds(self, point: Point) -> bool:
        return self.x0 <= point.x <= self.x1 and self.y0 <= point.y <= self.y1


class Location(Strict):
    name: str  # the structure's name, as the bundle's scene lists it
    mask: JobPath  # media.mask_path(scene, name)
    box: Box  # around the visible part of the structure
    centroid: Point  # of the visible part; where the camera aims
    label_anchor: Point  # where the label's leader line ends: inside the structure, clear of its neighbours
    found_by: Literal["svg", "sam", "sam-box", "helper", "schematic3d"]


class SceneLocations(Strict):
    scene: str
    art: JobPath  # media.art_path(scene)
    width: int
    height: int
    # A structure that could not be found is left out: the timeline then gives it no
    # spotlight and no camera move, and the renderer never draws its label.
    structures: list[Location]
    # A scene drawn from the schematic library: its snapshot (media.schematic_path), which the
    # renderer draws live, the art being its still. None: the art is the scene.
    schematic: JobPath | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SceneLocations:
        names = [s.name for s in self.structures]
        if len(names) != len(set(names)):
            raise ValueError(f"scene {self.scene} locates a structure twice")
        masks = [s.mask for s in self.structures]
        if len(masks) != len(set(masks)):
            raise ValueError(f"scene {self.scene} gives two structures the same mask")
        frame = Box(x0=0, y0=0, x1=self.width, y1=self.height)
        for s in self.structures:
            corners = (Point(x=s.box.x0, y=s.box.y0), Point(x=s.box.x1, y=s.box.y1))
            if not all(frame.holds(p) for p in corners):
                raise ValueError(f"{s.name} on {self.scene} has a box outside the art")
            for what, point in (("centroid", s.centroid), ("label_anchor", s.label_anchor)):
                if not s.box.holds(point):
                    raise ValueError(f"{s.name} on {self.scene} has its {what} outside its box")
        return self

    def get(self, name: str) -> Location | None:
        return next((s for s in self.structures if s.name == name), None)


class Locations(Strict):
    """locations.json: where every structure is on every scene's art (steps 3-4)."""

    scenes: list[SceneLocations]

    @model_validator(mode="after")
    def _unique(self) -> Locations:
        ids = [s.scene for s in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("locations.json lists a scene twice")
        return self

    def scene(self, scene_id: str) -> SceneLocations | None:
        return next((s for s in self.scenes if s.scene == scene_id), None)


def locations_problems(locations: Locations, bundle: Bundle) -> list[str]:
    """Locations that do not match the bundle: a scene missing, or a structure its scene does not list."""
    problems = []
    for scene in bundle.scenes:
        located = locations.scene(scene.id)
        if located is None:
            problems.append(f"no locations for scene {scene.id}")
            continue
        listed = {s.name for s in scene.structures}
        for s in located.structures:
            if s.name not in listed:
                problems.append(f"{s.name} is located on {scene.id}, which does not list it")
    extra = {s.scene for s in locations.scenes} - {s.id for s in bundle.scenes}
    problems += [f"locations for unknown scene {s}" for s in sorted(extra)]
    return problems


class Word(Strict):
    word: str  # as the line writes it, without the punctuation around it
    start: float
    end: float


class TimedLine(Strict):
    line_id: str  # a lyric id, a narration id, or an exam-question line (media.frq_lines)
    start: float
    end: float
    words: list[Word]  # every word of the line, in order


class Timing(Strict):
    """timing/<kind>_words.json: when each line, and each word in it, is heard in one audio file (step 6)."""

    audio: JobPath  # the file it times: audio/song.mp3, audio/narration.mp3 or audio/frq.mp3
    duration: float  # that file's length
    lines: list[TimedLine]  # in the order they are heard

    @model_validator(mode="after")
    def _ordered(self) -> Timing:
        problems = []
        previous_end = 0.0
        for line in self.lines:
            if not 0 <= line.start <= line.end <= self.duration + TOLERANCE_S:
                problems.append(f"{line.line_id} runs {line.start}-{line.end} in a {self.duration} s file")
            if line.start < previous_end - TOLERANCE_S:
                problems.append(f"{line.line_id} starts before the line ahead of it ends")
            previous_end = max(previous_end, line.end)
            word_end = line.start
            for word in line.words:
                if not (line.start - TOLERANCE_S <= word.start <= word.end <= line.end + TOLERANCE_S):
                    problems.append(f"{line.line_id}: {word.word!r} at {word.start}-{word.end} is outside its line")
                elif word.start < word_end - TOLERANCE_S:
                    problems.append(f"{line.line_id}: {word.word!r} starts before the word ahead of it ends")
                word_end = max(word_end, word.end)
        ids = [line.line_id for line in self.lines]
        if len(ids) != len(set(ids)):
            problems.append("a line is timed twice")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def line(self, line_id: str) -> TimedLine | None:
        return next((line for line in self.lines if line.line_id == line_id), None)


def timing_problems(timing: Timing, line_ids: list[str]) -> list[str]:
    """A Timing that does not time exactly these lines, in this order."""
    timed = [line.line_id for line in timing.lines]
    missing = [i for i in line_ids if i not in timed]
    extra = [i for i in timed if i not in line_ids]
    problems = [f"{i} is not timed" for i in missing] + [f"{i} is not one of the lines" for i in extra]
    if not problems and timed != line_ids:
        problems.append("the lines are timed out of order")
    return problems


class AudioClip(Strict):
    """A stretch of an audio file, played from `at` in the video."""

    path: JobPath
    at: float
    start: float = 0.0  # where in the file to start
    end: float | None = None  # where in the file to stop; None: its end


class TimelineCamera(Strict):
    target: str | None  # a structure on the beat's scene; None: the whole scene
    zoom: float  # 1.0 shows the whole art
    ease: Literal["inOut", "linear"] = "inOut"


class Reveal(Strict):
    name: str
    t: float  # when its name (or an alias) is heard; the ghost fades to full from here


class Particles(Strict):
    path: list[str]  # the structures they travel between, in order
    color: str
    trail: bool

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        return _hex_color(value)


class Beat(Strict):
    t0: float
    t1: float
    scene: str
    camera: TimelineCamera
    reveal: list[Reveal]  # structures first named on this scene during this beat
    spotlight: str | None  # the others washed pale; None: no spotlight
    # Every structure revealed on this scene so far, this beat's reveals included: a
    # label shows from its reveal's t and stays on every later beat of the scene.
    labels: list[str]
    particles: list[Particles]
    caption: str | None
    line_id: str | None  # None: an instrumental gap
    line: str  # the line as heard, for the lyric band; "" in a gap
    words: list[Word]  # its words, in video time


class FrqStep(Strict):
    t: float  # when the step appears, as its narration starts
    text: str
    spotlight: str | None


class FrqSegment(Strict):
    """The exam question at the end of both videos: a card over the diagram, a predict pause, the steps."""

    t0: float  # the card appears; the beats end here
    t1: float  # the video ends
    scene: str  # the diagram behind the card
    labels: list[str]  # the structures shown revealed and labelled on it; each step's spotlight is one
    prompt: str
    predict_question: str
    predict_t: float  # the countdown starts
    predict_pause_s: float
    steps: list[FrqStep]


class Timeline(Strict):
    """timeline_song.json, timeline_lesson.json: everything the renderer draws, and when (step 7).

    Every frame depends only on its time. Frame n is at n / fps, for n below
    frame_count; beat_at(t) is the beat that holds it (t0 <= t < t1), or None from
    frq.t0 on, where the exam question is drawn.
    """

    video: Literal["song", "lesson"]
    duration: float
    fps: int
    audio: list[AudioClip]  # mixed into one track
    palette: dict[str, str]  # structure name -> #RRGGBB, the same in both videos
    beats: list[Beat]
    frq: FrqSegment

    @field_validator("palette")
    @classmethod
    def _colors(cls, palette: dict[str, str]) -> dict[str, str]:
        return {name: _hex_color(color) for name, color in palette.items()}

    @model_validator(mode="after")
    def _consistent(self) -> Timeline:
        problems = timeline_shape_problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def frame_count(self) -> int:
        return math.ceil(round(self.duration * self.fps, 6))

    @functools.cached_property
    def _starts(self) -> list[float]:
        return [beat.t0 for beat in self.beats]

    def beat_at(self, t: float) -> Beat | None:
        """The beat on screen at t; None from the exam question on."""
        if t >= self.frq.t0:
            return None
        return self.beats[max(0, bisect.bisect_right(self._starts, t) - 1)]


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= EXACT_S


def _times(timeline: Timeline) -> list[float]:
    frq = timeline.frq
    times = [timeline.duration, frq.t0, frq.t1, frq.predict_t, frq.predict_pause_s] + [s.t for s in frq.steps]
    for beat in timeline.beats:
        times += [beat.t0, beat.t1, beat.camera.zoom] + [r.t for r in beat.reveal]
        times += [t for w in beat.words for t in (w.start, w.end)]
    for clip in timeline.audio:
        times += [clip.at, clip.start] + ([clip.end] if clip.end is not None else [])
    return times


def timeline_shape_problems(timeline: Timeline) -> list[str]:
    """What a timeline must hold on its own: beats that cover the video up to the exam question, in order."""
    problems = []
    if not all(math.isfinite(t) for t in _times(timeline)):
        return ["a time is not a finite number"]
    if timeline.duration <= 0 or timeline.fps <= 0:
        problems.append("duration and fps must be positive")
    if not timeline.beats:
        return problems + ["no beats"]
    if not _same(timeline.beats[0].t0, 0):
        problems.append(f"the first beat starts at {timeline.beats[0].t0}, not 0")
    for i, beat in enumerate(timeline.beats):
        where = f"beat {i} ({beat.line_id or 'gap'})"
        if beat.t0 >= beat.t1:
            problems.append(f"{where} runs {beat.t0}-{beat.t1}")
        if beat.camera.zoom < 1:
            problems.append(f"{where} zooms to {beat.camera.zoom}; 1.0 shows the whole art, and less shows past it")
        if i and not _same(timeline.beats[i - 1].t1, beat.t0):
            problems.append(f"{where} starts at {beat.t0}, not where the beat before it ends")
        for reveal in beat.reveal:
            if reveal.name not in beat.labels:
                problems.append(f"{where} reveals {reveal.name} without labelling it")
            if not beat.t0 - TOLERANCE_S <= reveal.t <= beat.t1 + TOLERANCE_S:
                problems.append(f"{where} reveals {reveal.name} at {reveal.t}, outside the beat")
        for word in beat.words:
            if not beat.t0 - TOLERANCE_S <= word.start <= word.end <= beat.t1 + TOLERANCE_S:
                problems.append(f"{where}: {word.word!r} is outside the beat")
        named = [beat.spotlight, beat.camera.target] + [n for p in beat.particles for n in p.path]
        for name in [n for n in named if n] + beat.labels:
            if name not in timeline.palette:
                problems.append(f"{where} names {name}, which has no colour in the palette")
    seen: dict[str, set[str]] = {}
    for i, beat in enumerate(timeline.beats):
        before = seen.setdefault(beat.scene, set())
        if not before <= set(beat.labels):
            dropped = ", ".join(sorted(before - set(beat.labels)))
            problems.append(f"beat {i} drops labels already shown on {beat.scene}: {dropped}")
        before |= set(beat.labels)
        for name in (beat.spotlight, beat.camera.target):
            if name and name not in beat.labels:
                problems.append(f"beat {i} spotlights or aims at {name} before it is revealed on {beat.scene}")

    frq = timeline.frq
    for name in frq.labels:
        if name not in timeline.palette:
            problems.append(f"the exam question names {name}, which has no colour in the palette")
    for step in frq.steps:
        if step.spotlight and step.spotlight not in frq.labels:
            problems.append(f"the exam question spotlights {step.spotlight}, which it does not label")
    if not _same(timeline.beats[-1].t1, frq.t0):
        problems.append(f"the beats end at {timeline.beats[-1].t1}, not where the exam question starts ({frq.t0})")
    if not _same(frq.t1, timeline.duration):
        problems.append(f"the exam question ends at {frq.t1}, not at the end of the video ({timeline.duration})")
    if not frq.t0 <= frq.predict_t:
        problems.append("the predict pause starts before the exam question")
    if not frq.steps:
        problems.append("the exam question has no steps")
    times = [frq.predict_t + frq.predict_pause_s - TOLERANCE_S] + [step.t for step in frq.steps] + [frq.t1]
    if any(a > b for a, b in zip(times, times[1:])):
        problems.append("the exam question's steps must follow the predict pause, in order, before the end")
    for clip in timeline.audio:
        if clip.at < 0 or clip.start < 0 or (clip.end is not None and clip.end <= clip.start):
            problems.append(f"audio clip {clip.path} at {clip.at} plays {clip.start}-{clip.end}")
        elif clip.at >= timeline.duration:
            problems.append(f"audio clip {clip.path} starts after the video ends")
    return problems


def timeline_problems(timeline: Timeline, locations: Locations) -> list[str]:
    """Names in the timeline that its scene's art does not have: every one must be located there."""
    problems = []
    shown = [(f"beat {i}", beat.scene,
              [r.name for r in beat.reveal] + beat.labels + [beat.spotlight, beat.camera.target]
              + [n for p in beat.particles for n in p.path])
             for i, beat in enumerate(timeline.beats)]
    shown.append(("the exam question", timeline.frq.scene, timeline.frq.labels))
    for where, scene_id, names in shown:
        scene = locations.scene(scene_id)
        if scene is None:
            problems.append(f"{where} is on scene {scene_id}, which has no locations")
            continue
        for name in sorted({n for n in names if n}):
            if scene.get(name) is None:
                problems.append(f"{where} names {name}, which is not located on {scene_id}")
    return problems
