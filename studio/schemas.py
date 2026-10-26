"""Pydantic models for the studio's files (docs/PLAN.md, sections 4 and 5).

Bundle and FactCheck are what the brain writes. Their field order is the order the
model writes in, so later parts build on what it has already written. They export
only what both providers accept (brain.strict_schema); counts, formats and
cross-references are checked by validators, which never reach the schema.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FILLER = "filler"
Section = Literal["verse", "chorus", "bridge"]
SECTION_ORDER = ["verse", "chorus", "verse", "chorus", "bridge", "chorus"]
Verdict = Literal["SUPPORTED", "UNSUPPORTED", "FILLER"]
LESSON_MAX_CHARS = 5000  # what POST /api/questions/:id/answer accepts


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError(f"{value!r} is not #RRGGBB")
        return value.upper()


class Scene(Strict):
    id: str
    image_prompt: str
    structures: list[Structure]


class Music(Strict):
    positive_styles: list[str]
    negative_styles: list[str]
    sections: list[Section]


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
    line_id: str
    rule: Literal["unknown-name", "not-in-scene", "name-not-said", "unlisted-name", "spotlight", "camera", "particles"]
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
    kind: Literal["lyric", "narration", "lesson"]
    id: str
    text: str
    verdict: Verdict | None  # None: never got a verdict
    source: str
    round: int  # the round that gave the verdict; 0 if none did


class Correction(Strict):
    round: int
    kind: Literal["lyric", "narration", "lesson"]
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
    study_guide: str = ""
    images: list[str] = []  # paths to photos of their notes
