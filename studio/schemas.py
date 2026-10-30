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
import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

FILLER = "filler"
Section = Literal["intro", "verse", "pre-chorus", "chorus", "bridge", "outro"]
SECTION_ORDER = ["intro", "verse", "pre-chorus", "chorus", "verse", "pre-chorus", "chorus", "bridge", "chorus", "outro"]
Verdict = Literal["SUPPORTED", "UNSUPPORTED", "FILLER"]
LESSON_MAX_CHARS = 5000  # what POST /api/questions/:id/answer accepts
BPM_RANGE = (80, 160)  # the tempos the song may take; a sung line lasts 1.9 s to 3.7 s (audio.line_ms)
DEFAULT_BPM = 112  # a song whose styles name no tempo: every song before the styles chose one
BPM_TAG = re.compile(r"\b(\d{2,3}(?:\.\d+)?)\s*bpm\b", re.IGNORECASE)
TAGS_PER_VIDEO = 10  # hashtags a video keeps; the API holds them to the same rules (api/src/lib/tags.ts)
TAG_MAX_CHARS = 40  # a longer one is dropped, since cutting it short would make a word nobody searches for


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


class Structure(Strict):
    name: str
    aliases: list[str]
    sam_phrase: str
    color_hex: str = Field(description='"#RRGGBB"')

    @field_validator("color_hex")
    @classmethod
    def _hex(cls, value: str) -> str:
        return _hex_color(value)


class Axes(Strict):
    x: str = Field(description='What the horizontal axis measures, as a textbook prints it ("Quantity"); "" for none')
    y: str = Field(description='What the vertical axis measures ("Price"); "" for none')

    def __bool__(self) -> bool:
        return bool(self.x or self.y)


def _no_axes() -> Axes:
    return Axes(x="", y="")


class Scene(Strict):
    id: str
    image_prompt: str
    structures: list[Structure]
    # Scenes that are stages of one process, in scene order: painted as one subject in one framing (each
    # later stage redrawn from the first), and the video cross-fades between them. "" in a bundle before.
    stage_of: str = Field(default_factory=str, description='The process this scene is one stage of ("mitosis"), its stages '
                                                            'in scene order; "" for any other scene')
    # A graph's axes are read, not taught: the renderer writes their names at their ends from the scene's first
    # frame, since the art has no writing, rather than reveal them as structures when the song happens to sing
    # "quantity". Both "" in a bundle written before Oct 30.
    axes: Axes = Field(default_factory=_no_axes, description='On a graph, what each axis measures, written at its end; '
                                                             '"" for both on any other scene')


def stage_groups(scenes: list[Scene]) -> dict[str, list[Scene]]:
    """Each process's stage scenes, in scene order."""
    groups: dict[str, list[Scene]] = {}
    for scene in scenes:
        if scene.stage_of:
            groups.setdefault(scene.stage_of, []).append(scene)
    return groups


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


class Flow(Strict):
    particles: list[str] = Field(description="Structure names they travel between, in order")
    carries: str = Field(description='What they are, as a textbook prints it ("O₂")')


class Direction(Strict):
    line_id: str = Field(description="The id of a lyric line or a narration sentence")
    scene: str
    camera: Camera
    spotlight: str = Field(description='A structure name, or "" for none')
    particles: list[str] = Field(description="Structure names the particles travel between, in order; [] for none")
    carries: str = Field(description='What the particles are, as a textbook prints it ("H⁺", "e⁻", "O₂", "glucose"); '
                                     '"" for none')
    glow: str = Field(description='A structure that lights up on this line (light excites it, it fires), or ""')
    caption: str = Field(description='Usually ""')
    # A second thing moving on the line at the same time, when it names both: what one structure gives off together
    # (H⁺ and O₂ from Photosystem II), or two ways at once (Na⁺ out, K⁺ in). [] in a bundle written before Oct 29.
    also: list[Flow] = Field(default_factory=list, description="A second thing moving on this line at the same time, "
                                                               "when the line names both; [] for none, at most one")

    def flows(self) -> list[Flow]:
        """Everything moving on the line, the main flow first; a flow with no path is left out."""
        main = [Flow(particles=self.particles, carries=self.carries)] if self.particles else []
        return main + [f for f in self.also if f.particles]


class BuildUp(Strict):
    carries: str = Field(description='What piles up, as `carries` writes it ("H⁺")')
    region: str = Field(description="The structure it piles up in, by its name: a space (a lumen, a compartment)")


class Block(Strict):
    structure: str = Field(description="The structure that stops the particles reaching it (a poison, a drug), by its name")


class Spin(Strict):
    # How it turns on screen is read off its shape in the picture (renderer: SceneArt.spin_disc), which the
    # writer never sees: Claude called a turbine painted face-on "side".
    structure: str = Field(description="The structure that turns while particles flow into or through it, by its name")


class KeyLine(Strict):
    from_line: str = Field(description="The id of the lyric line it appears on; it stays until the next key line's")
    text: str = Field(description='The step being sung, in at most 10 words: "Light splits water → O₂ + H⁺ + e⁻"')
    # Where the lesson video writes the same line. "" in a bundle written before the lesson video showed them.
    from_narration: str = Field(default_factory=str, description="The id of the narration sentence it appears on in the "
                                                                 'lesson video; "" for none')


class Conversion(Strict):
    structure: str = Field(description="The structure where it happens (a complex, an enzyme, a stage), by its name")
    inputs: list[str] = Field(description='What goes in, as a textbook prints it: ["H₂O"], ["NADP⁺", "H⁺", "e⁻"]')
    outputs: list[str] = Field(description='What comes out, as a textbook prints it: ["O₂", "H⁺", "e⁻"]')


def tag_of(value: str) -> str:
    """A hashtag as it is stored and searched: "#AP Bio" -> "apbio".

    Lowercase letters (with their accents), digits and underscores, with no "#" (the page
    adds it back), as TikTok's are: one word that matches however it was typed. "" when
    nothing is left. The API keeps the same characters (tagOf in api/src/lib/tags.ts).
    Normalised again at the end, so a tag cleaned twice reads the same: a spacing accent
    ("A¨") leaves its combining mark beside the letter, which only then composes ("ä").
    """
    kept = "".join(c for c in unicodedata.normalize("NFKC", value).lower()
                   if c == "_" or unicodedata.category(c)[0] in "LMN")
    return unicodedata.normalize("NFKC", kept)


def clean_tags(values: list[str]) -> list[str]:
    """Each tag as tag_of() writes it, the first of each kept, at most TAGS_PER_VIDEO.

    Never a reason to fail a bundle: a tag that does not fit is dropped, not sent back.
    """
    tags: list[str] = []
    for value in values:
        tag = tag_of(value)
        if tag and len(tag) <= TAG_MAX_CHARS and tag not in tags:
            tags.append(tag)
    return tags[:TAGS_PER_VIDEO]


class Tags(Strict):
    """Hashtags for each video, the way a TikTok is tagged, so the Droplets tab can be searched by them."""

    song: Annotated[list[str], AfterValidator(clean_tags)] = Field(
        default_factory=list, description='The song video\'s hashtags, most useful first: "#photosynthesis", "#kpop"')
    lesson: Annotated[list[str], AfterValidator(clean_tags)] = Field(
        default_factory=list, description="The lesson video's hashtags, most useful first")


class Bundle(Strict):
    course: str
    topic: str
    audience: str = Field(description='Who the droplet is written for: "high school" unless the request names someone else')
    core_idea: str
    # Why the core idea holds, a link a line, and the only terms the learner must come away with. A bundle written
    # before Oct 29 has neither.
    why_chain: list[str] = Field(default_factory=list,
                                 description="3 to 5 cause-and-effect links that explain the core idea, in order")
    key_terms: list[str] = Field(default_factory=list,
                                 description="The terms the learner must know, at most 8; every other name is optional")
    facts: list[Fact]
    misconception: str
    scenes: list[Scene]
    lesson_markdown: str
    music: Music
    lyrics: list[Lyric]
    narration: list[Narration]
    direction: list[Direction]
    # What piles up where as the lesson goes (a gradient), filled and emptied by the marked particles.
    # A bundle written before Oct 28 has none.
    builds_up: list[BuildUp] = Field(default_factory=list, description="What piles up in which space; [] for none")
    # What turns while particles drive it. A bundle written before Oct 28 has none.
    spins: list[Spin] = Field(default_factory=list, description="What turns while particles flow through it; [] for none")
    # What stops the particles that reach it. A bundle written before Oct 28 has none.
    blocks: list[Block] = Field(default_factory=list, description="What stops the particles reaching it; [] for none")
    # What each structure turns into what, drawn beside it as "H₂O → O₂ + H⁺ + e⁻" while it is the subject of a line.
    # A bundle written before Oct 29 has none.
    conversions: list[Conversion] = Field(default_factory=list,
                                          description="What turns into what, and where; [] for none")
    # What both videos write above their diagram, held while the lines go by, so a learner who cannot keep up
    # with them reads the step being taught. A bundle written before Oct 29 has none.
    key_lines: list[KeyLine] = Field(default_factory=list,
                                     description="The why chain written on screen in both videos, in song order")
    # Last, so they are written knowing the whole lesson and the song's style. A bundle
    # written before there were tags has none.
    tags: Tags = Field(default_factory=Tags)

    @model_validator(mode="before")
    @classmethod
    def _older_bundles(cls, data):
        if not isinstance(data, dict):
            return data
        # A bundle written before `audience` existed was pitched to high school.
        if "audience" not in data:
            data = {**data, "audience": "high school"}
        # The exam question (`frq`) left both videos on Oct 28; a bundle written before still has one.
        data = {k: v for k, v in data.items() if k != "frq"}
        # What the particles carry, and what lights up, were added on Oct 28: before, nothing did.
        if isinstance(data.get("direction"), list):
            data["direction"] = [{"carries": "", "glow": "", **d} if isinstance(d, dict) else d
                                 for d in data["direction"]]
        return data

    @model_validator(mode="after")
    def _consistent(self) -> Bundle:
        # The song is timed and sung from the lyrics' own sections (studio/steps/audio.py); music.sections
        # only names them, so it follows the lyrics. A writer may sing the chorus once more than the
        # prompt's order has it, as songs do: on Oct 28 Claude wrote one, and the whole bundle was refused.
        self.music.sections = sung_sections(self.lyrics)
        problems = bundle_problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def sung_sections(lyrics: list[Lyric]) -> list[str]:
    """The sections as the lyrics run: a section each time the next line's differs."""
    return [line.section for i, line in enumerate(lyrics) if i == 0 or line.section != lyrics[i - 1].section]


def bundle_problems(bundle: Bundle) -> list[str]:
    """What downstream steps rely on but the schema cannot say."""
    problems = []
    if not 3 <= len(bundle.facts) <= 4:
        problems.append(f"{len(bundle.facts)} facts, not 3-4")
    most = 8 if any(s.stage_of for s in bundle.scenes) else 6  # a process's stages take a scene each
    if not 3 <= len(bundle.scenes) <= most:
        problems.append(f"{len(bundle.scenes)} scenes, not 3-{most}")
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
    if not bundle.lesson_markdown.strip():
        problems.append("lesson_markdown is empty")
    elif len(bundle.lesson_markdown) > LESSON_MAX_CHARS:
        problems.append(f"lesson_markdown is {len(bundle.lesson_markdown)} characters, over {LESSON_MAX_CHARS}")
    return problems


class Problem(Strict):
    line_id: str  # or "scene:<id>" for a whole scene, "structures" for the structure list, "music" for the styles
    rule: Literal["empty-scene", "shared-name", "unknown-name", "not-in-scene", "name-not-said", "unlisted-name",
                  "spotlight", "camera", "particles", "carries", "builds_up", "spins", "blocks", "conversions", "lyric-style", "motion", "essence", "key-line", "glow", "stages",
                  "tempo", "song-style", "axes"]
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
    # A tag or a motion (what a line's particles show moving) is dropped when it fails, never corrected.
    # "frq" is a line of the exam question, in a fact-check made before it left the videos on Oct 28.
    kind: Literal["lyric", "narration", "lesson", "frq", "tag", "motion", "keyline"]
    id: str
    text: str
    verdict: Verdict | None  # None: never got a verdict
    source: str
    round: int  # the round that gave the verdict; 0 if none did
    problem: str = ""  # UNSUPPORTED: what the checker found wrong; for a tag, why it was dropped


class Correction(Strict):
    round: int
    kind: Literal["lyric", "narration", "lesson", "frq", "keyline"]  # "frq": as for CheckedLine
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


# --- The planner's record on Inkmory (docs/PLAN.md, section 11) -------------------


class PlannerRecord(Strict):
    """What the planner writes about its beat; the studio posts each part to its Inkmory record
    (studio/inkmory.py). A part it has nothing to say in is "". The limits are Inkmory's: the
    prompt states them, and code cuts a part that runs over rather than lose it to a refusal."""

    cycle_log: str = Field(description='The plain record of this beat, up to 1,000 characters, starting "job <id>: <course> / <topic>"')
    journal: str = Field(description="A reflection in your own voice, up to 3,000 characters")
    life_story: str = Field(description='Your whole life story, up to 2,000 words, when it is due; otherwise ""')
    bio: str = Field(description='A new bio, up to 1,000 characters, only when yours no longer fits; otherwise ""')
    vibe: str = Field(description='A new vibe, up to 200 characters, only when yours no longer fits; otherwise ""')
    inner_thought: str = Field(description='With a new bio or vibe, the thought behind the change, up to 1,500 characters; otherwise ""')


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
    found_by: Literal["svg", "sam", "sam-box", "helper"]


class SceneLocations(Strict):
    scene: str
    art: JobPath  # media.art_path(scene)
    width: int
    height: int
    # A structure that could not be found is left out: the timeline then gives it no
    # spotlight and no camera move, and the renderer never draws its label.
    structures: list[Location]

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
    line_id: str  # a lyric id or a narration id
    start: float
    end: float
    words: list[Word]  # every word of the line, in order
    # False: its section of the song has no singing (Eleven Music left the chunk instrumental), so it is timed at
    # no length where that section ends and never shown (step 6)
    sung: bool = True


class Timing(Strict):
    """timing/<kind>_words.json: when each line, and each word in it, is heard in one audio file (step 6)."""

    audio: JobPath  # the file it times: audio/song.mp3 or audio/narration.mp3
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
    fade_in: float = 0.0  # seconds it fades in over, for a clip that starts partway into music
    fade_out: float = 0.0  # seconds it fades out over at its end, for a clip cut out of a song (the hook cut)


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
    label: str = ""  # what they are, written on the lead particle ("H⁺"); "" for an unmarked dot
    blocked: bool = False  # the last stop stops them (Bundle.blocks): they pile up there, not go round again

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        return _hex_color(value)


class Crowd(Strict):
    """A space filling up with what the particles carry (Bundle.builds_up), as one beat shows it."""

    region: str  # the space, a structure located on the beat's scene
    label: str  # what fills it ("H⁺"), written on some of its dots
    color: str
    start: float  # how full it is when the beat starts, 0-1
    end: float  # and when it ends; it changes evenly in between

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        return _hex_color(value)


SPIN_EASE_S = 0.8  # a part speeds up to its turning speed, or slows to a stop, over this long


class Turning(Strict):
    """A part that turns (Bundle.spins), as one beat shows it: how far round it is, and how fast it goes."""

    structure: str  # located on the beat's scene
    turns: float  # how far round it has turned when the beat starts, in turns
    from_speed: float  # turns a second when the beat starts
    speed: float  # turns a second it eases to over SPIN_EASE_S, and keeps

    def at(self, dt: float) -> tuple[float, float]:
        """How far round it is and how fast it turns, dt seconds into the beat."""
        dt = max(dt, 0.0)
        ease = min(dt, SPIN_EASE_S)
        # The speed changes evenly over the ease, then holds: turns are its integral.
        turned = self.from_speed * ease + (self.speed - self.from_speed) * ease * ease / (2 * SPIN_EASE_S)
        turned += self.speed * (dt - ease)
        return self.turns + turned, self.from_speed + (self.speed - self.from_speed) * ease / SPIN_EASE_S


class Route(Strict):
    """A path particles have travelled on this scene, drawn as arrows from stop to stop, staying on."""

    path: list[str]  # the structures, in order, each located on the beat's scene
    color: str
    label: str = ""  # what travels it ("e⁻"); "" for unmarked particles
    since: float = 0.0  # when its particles first set off on this scene: the arrows fade in then

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        return _hex_color(value)


class Converting(Strict):
    """A conversion (Bundle.conversions) as one beat shows it: written beside the structure where it happens."""

    structure: str  # located on the beat's scene
    text: str  # "H₂O → O₂ + H⁺ + e⁻"
    since: float = 0.0  # when it was first written on this scene: it fades in then, and stays on every later beat


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
    crowds: list[Crowd] = []  # the spaces on this scene filling up or emptying; [] in a timeline made before
    turning: list[Turning] = []  # the parts on this scene that turn; [] in a timeline made before
    conversions: list[Converting] = []  # the conversions written on this beat; [] in a timeline made before
    routes: list[Route] = []  # the paths drawn as arrows on this beat; [] in a timeline made before
    glow: str | None = None  # a structure lit up on this line (Direction.glow); None in a timeline made before
    key_line: str = ""  # written above the diagram (Bundle.key_lines; the core idea on the last chorus); "" for none
    caption: str | None
    line_id: str | None  # None: an instrumental gap
    line: str  # the line as heard, for the lyric band; "" in a gap
    words: list[Word]  # its words, in video time


class Timeline(Strict):
    """timeline_song.json, timeline_lesson.json: everything the renderer draws, and when (step 7).

    Every frame depends only on its time. Frame n is at n / fps, for n below
    frame_count; beat_at(t) is the beat that holds it (t0 <= t < t1). The beats run
    from 0 to the end of the video.
    """

    video: Literal["song", "lesson", "hook"]
    duration: float
    fps: int
    audio: list[AudioClip]  # mixed into one track
    palette: dict[str, str]  # structure name -> #RRGGBB, the same in both videos
    beats: list[Beat]
    title: str = ""  # written across the top by a layout with a title band (the hook cut's): the topic
    key_terms: list[str] = []  # Bundle.key_terms: picked out in colour where a key line says one
    stages: list[list[str]] = []  # each process's stage scenes, in order: the video cross-fades between them
    # What the art check found still wrong in a scene's AI-made picture, a sentence each for the student,
    # by scene: shown beside the picture, so a flawed picture is disclosed rather than passed off as right.
    notices: dict[str, list[str]] = {}
    # The scenes whose picture a model made, each tagged as AI-generated: every scene, as the studio makes its
    # art (Muse Image paints it, or the brain draws it as SVG); a picture from anywhere else would be left out.
    ai_pictures: list[str] = []
    # Each graph's axis names, by scene (Scene.axes): written at the ends of the axes found in its picture.
    axes: dict[str, Axes] = {}

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

    def beat_at(self, t: float) -> Beat:
        """The beat on screen at t."""
        return self.beats[max(0, bisect.bisect_right(self._starts, t) - 1)]


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= EXACT_S


def _times(timeline: Timeline) -> list[float]:
    times = [timeline.duration]
    for beat in timeline.beats:
        times += [beat.t0, beat.t1, beat.camera.zoom] + [r.t for r in beat.reveal]
        times += [t for w in beat.words for t in (w.start, w.end)]
    for clip in timeline.audio:
        times += [clip.at, clip.start] + ([clip.end] if clip.end is not None else [])
    return times


def timeline_shape_problems(timeline: Timeline) -> list[str]:
    """What a timeline must hold on its own: beats that cover the whole video, in order."""
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
        named = [beat.spotlight, beat.camera.target] + [n for p in beat.particles for n in p.path] + [
            c.region for c in beat.crowds] + [s.structure for s in beat.turning] + [beat.glow] + [
            n for r in beat.routes for n in r.path]
        for crowd in beat.crowds:
            if not (0 <= crowd.start <= 1 and 0 <= crowd.end <= 1):
                problems.append(f"{where} fills {crowd.region} to {crowd.start}-{crowd.end}, outside 0-1")
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

    if not _same(timeline.beats[-1].t1, timeline.duration):
        problems.append(f"the beats end at {timeline.beats[-1].t1}, not at the end of the video ({timeline.duration})")
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
    for where, scene_id, names in shown:
        scene = locations.scene(scene_id)
        if scene is None:
            problems.append(f"{where} is on scene {scene_id}, which has no locations")
            continue
        for name in sorted({n for n in names if n}):
            if scene.get(name) is None:
                problems.append(f"{where} names {name}, which is not located on {scene_id}")
    return problems
