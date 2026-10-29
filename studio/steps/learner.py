"""Part of step 1: the learner test. Does a learner who reads only the lesson come away with its essence?

A helper plays a learner at the droplet's audience level who has never met the topic and reads only
lesson_markdown, then answers three questions in their own words: the big idea, why it works, and what
would happen if a part stopped working. A second helper call, which sees the bundle's core idea and why
chain, grades the answers: did the learner get the core idea, and which links of the why chain. The learner
never sees what the lesson was meant to teach, so the test is of the lesson, not of the plan.

When the learner misses the core idea or most of the why chain, the brain rewrites the lesson once, told
what was missed, and the learner reads it again. The second result is recorded, whatever it is: the test
informs the lesson, it never fails the droplet. Everything is written to learner.json.

Runs only for a bundle with a why chain (written after Oct 29); an older one has nothing to grade against.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field

from studio.jobs import write
from studio.schemas import Bundle, Strict

LEARNER_PROMPT = """You are a {audience} student who has never studied {topic} before. You have just read the \
lesson below, and it is all you know about the topic. Answer in your own words, the way a student would, \
using only what the lesson taught you. If the lesson did not make something clear, say so rather than guess.

1. `big_idea`: in one sentence, what is the big idea of this topic?
2. `why`: explain why it works, step by step: what causes what.
3. `what_if`: pick one part the lesson describes and say what would happen if it stopped working, and why."""

GRADER_PROMPT = """You grade a student's answers about a lesson they just read, against what the lesson was \
meant to teach. Be fair but strict: credit an idea when the student's own words show they understood it, \
even if worded differently; do not credit a term repeated without the cause behind it.

- `core_idea`: true when the student's answers show the core idea below.
- `links`: one true or false for each link of the why chain below, in order: true when the student's answers \
show that link, its cause and its effect.
- `missing`: for each idea the student missed or got wrong, one short sentence saying what it is, for the \
lesson's writer. Empty when nothing was missed."""

REWRITE_PROMPT = """A learner read the lesson below and missed part of what it was meant to teach. Rewrite \
`lesson_markdown` so that a learner meeting the topic for the first time comes away with the core idea and \
every link of the why chain. Keep everything else the lesson rules ask for, keep to the facts (a separate \
fact-checker will read every sentence against them alone), and change only what is needed.

What the learner missed:
{missing}

The bundle, for its facts, core idea, why chain, key terms and misconception:
{bundle}

The lesson as it is:
{lesson}"""

PASS_SHARE = 0.6  # the share of the why chain's links a learner must get, with the core idea, to pass


class LearnerAnswers(Strict):
    big_idea: str
    why: str
    what_if: str


class Grade(Strict):
    core_idea: bool
    links: list[bool] = Field(description="One for each link of the why chain, in order")
    missing: list[str]

    def passed(self, links: int) -> bool:
        return self.core_idea and sum(self.links[:links]) >= math.ceil(PASS_SHARE * links)


class Rewrite(Strict):
    lesson_markdown: str


def test(bundle: Bundle, ask, label: str = "learner") -> tuple[LearnerAnswers, Grade]:
    """A learner reads the lesson and answers; a grader compares the answers with the core idea and why chain."""
    answers = LearnerAnswers(**ask("helper", f"<lesson>\n{bundle.lesson_markdown}\n</lesson>", LearnerAnswers,
                                   system=LEARNER_PROMPT.format(audience=bundle.audience or "high school",
                                                                topic=bundle.topic),
                                   label=label))
    meant = "Core idea: " + bundle.core_idea + "\n\nWhy chain:\n" + "\n".join(
        f"{i}. {link}" for i, link in enumerate(bundle.why_chain, 1))
    grade = Grade(**ask("helper", f"{meant}\n\nThe student's answers:\n{answers.model_dump_json(indent=2)}", Grade,
                        system=GRADER_PROMPT, label=f"{label} grade"))
    return answers, grade


def run(job: Path, bundle: Bundle, ask, system: str) -> Bundle:
    """The bundle, its lesson rewritten once if a learner missed its essence; learner.json records how it went.
    `system` is the bundle prompt, so a rewrite keeps the lesson rules."""
    if not bundle.why_chain or not bundle.lesson_markdown.strip():
        return bundle
    try:
        return _run(job, bundle, ask, system)
    except Exception as e:  # the test informs the lesson; a model that fails it never fails the droplet
        write(job / "learner.json", {"error": f"{type(e).__name__}: {e}"})
        return bundle


def _run(job: Path, bundle: Bundle, ask, system: str) -> Bundle:
    answers, grade = test(bundle, ask)
    record = {"first": {"answers": answers.model_dump(), "grade": grade.model_dump(),
                        "passed": grade.passed(len(bundle.why_chain))}}
    if not record["first"]["passed"]:
        missing = "\n".join(f"- {m}" for m in grade.missing) or "- the core idea"
        plan = bundle.model_dump(include={"topic", "audience", "core_idea", "why_chain", "key_terms", "facts",
                                          "misconception"})
        rewrite = Rewrite(**ask("brain", REWRITE_PROMPT.format(missing=missing, bundle=json.dumps(plan, ensure_ascii=False),
                                                               lesson=bundle.lesson_markdown),
                                Rewrite, system=system, label="lesson rewrite"))
        bundle = bundle.model_copy(update={"lesson_markdown": rewrite.lesson_markdown})
        answers, grade = test(bundle, ask, label="learner again")
        record["rewritten"] = {"answers": answers.model_dump(), "grade": grade.model_dump(),
                               "passed": grade.passed(len(bundle.why_chain))}
    write(job / "learner.json", record)
    return bundle
