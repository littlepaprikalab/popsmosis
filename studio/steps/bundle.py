"""Step 1: the bundle. Brain calls write everything the topic alone can give: first the lesson's script (the
question, the essence, the facts, the scenes and the narration: studio/steps/script.py), then, in one call, the
rest around it (the pictures, the direction, the lesson text and the song), held to the script.

Writes bundle.json, after a learner has read its lesson (studio/steps/learner.py: the
lesson is rewritten once if the learner missed its essence) and the code checks
(studio/checks.py) have repaired what they can, and checks.json. A new bundle makes any earlier fact audit and
fact-check stale, so this step removes them.

The lesson video alone (STUDIO_VIDEOS=lesson, studio/media.py): no song is written. The brain gets the brief
without its song section and with NO_SONG at its end (LESSON_ONLY_PROMPT), and answers a LessonBundle: no music,
no lyrics, its key lines on the narration and the lesson video's tags alone. So nothing after it checks, fact-checks
or rewrites a song: the bundle has none (Bundle.sung), and its videos are the lesson's alone whatever the knob says
later. With the knob anywhere else the prompt and the schema are as they always were.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from studio import brain, checks, media
from studio.steps import audit, learner, narration, script as script_step
from studio.jobs import read, write
from studio.schemas import Bundle, LessonBundle, Request, bundle_of
from studio.styles import style_for

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "bundle.md").read_text(encoding="utf-8")
SONG_SECTION = ("\n## The song\n", "\n## The narration\n")  # cut from the brief for the lesson video alone
# Two sections: the first is the script writer's too (studio/steps/script.py, BRIEF_SECTIONS), the second the plan's.
NO_SONG = """

## No song for this droplet

This droplet is made without its song: the text lesson and the narrated lesson video alone,
so there is no songwriter and no song video. Where this brief gives the song a job, the
narration and the lesson do it: the narration opens on the hook, and the surprising detail and
what a failure looks like go in the narration and the lesson where they fit. Write no scene
for the song alone: the schema has no `song_scenes`.

## The plan without the song

The schema has no `music` and no `lyrics`. Where a rule above is written for the song's
lines, apply it to the narration's sentences: plan the motion across the whole narration,
build each pathway up one step a sentence as the narration teaches it, show the whole pathway
on the sentences that sum it up, and keep captions to a few in the whole narration.

- Tag each narration sentence with the id of the fact it teaches, or "filler" for one that
  teaches nothing.
- `direction` has one entry for every narration sentence, in the narration's order.
- `key_lines` walk the `why_chain` through the narration, in its order, every link on at least
  one: `from_narration` is the sentence where the narrator starts on that step, never two on
  one sentence and none on the last. The first can ask the script's `question`. Write each
  `text` as "On screen" says.
- `tags.lesson` only, as "Tags" says.
"""


def without_song(brief: str) -> str:
    """The brief for the lesson video alone: its song section cut out (from "## The song" to "## The narration"),
    and NO_SONG at its end. A brief whose headings were renamed keeps its song section rather than break the
    import in every mode; the tests say when it no longer cuts."""
    start, end = brief.find(SONG_SECTION[0]), brief.find(SONG_SECTION[1])
    cut = brief[:start] + brief[end:] if 0 <= start < end else brief
    return cut + NO_SONG


LESSON_ONLY_PROMPT = without_song(PROMPT)
# With no script (its call failed), the narration is the plan's to write, by the script writer's rules for it.
NO_SCRIPT = """

## With no script

The request carries no script, so you write the narration too, as the script's writer would
have, by its rules below. Where they say a passage, it is the run of sentences you direct over
one scene, each sentence an item of `narration`.

"""
# The operator's note (python -m studio run <id> --note): what it has seen go wrong in recent droplets.
NOTE = """

## A note from the operator

The operator runs the studio and follows each droplet through to its videos, so it sees what
happens to a plan after you write it: the pictures, the song, the labels. From its recent
droplets it left you this. Take it into account where it applies. It changes nothing above:
the facts, the audience, the rules for every line and the schema stand as written, a rule wins
wherever the note would bend one, and no line mentions it.

<operator_note>
{note}
</operator_note>
"""


def run(job: Path, ask=brain.ask, note: str = "") -> Bundle:
    """note: the operator's observation (studio/run.py). It ends the prompt when given; the fact-check never
    sees it."""
    request = read(job / "request.json", Request)
    sung = not media.lesson_only()
    brief = PROMPT if sung else LESSON_ONLY_PROMPT
    for stale in ("audit.json", "factcheck.json", "songwriter.json", "bundle.draft.json", "bundle.invalid.json",
                  "learner.json", narration.RECORD, script_step.RECORD):
        (job / stale).unlink(missing_ok=True)
    prompt = user_prompt(request, style_for(job.name), sung)
    script = script_step.write_script(prompt, brief, ask, request.images, job)
    unquoted = []
    if script:  # held to the guide before the audit is shown them as its words
        facts, unquoted = checks.held_to_guide(script.facts, request.study_guide)
        script = script.model_copy(update={"facts": facts})
    system = (brief + ("" if script else NO_SCRIPT + script_step.narration_rules())
              + (NOTE.format(note=note) if note.strip() else ""))
    early = audit.begin(request, script, ask)  # the facts audited beside the bundle call (studio/steps/audit.py)
    try:
        answer = ask("brain", prompt + ("\n\n" + script_step.as_prompt(script) if script else ""),
                     Bundle if sung else LessonBundle, request.images, system=system, label="bundle")
        bundle = Bundle.model_validate(answer) if sung else _lesson_only(answer)
        if sung and not bundle.sung:  # asked for a song and wrote none: refused, as before there was a LessonBundle
            raise brain.InvalidOutput("the answer does not fit Bundle: lyrics and narration must not be empty",
                                      json.dumps(answer, ensure_ascii=False))
    except brain.InvalidOutput as e:
        (job / "bundle.invalid.json").write_text(e.raw, encoding="utf-8")
        raise
    if script:
        bundle = script_step.follow(bundle, script, job)
    bundle, report = checks.review(bundle)
    rewritten = learner.run(job, bundle, ask, system)
    if rewritten is not bundle:  # a new lesson: the code checks read it again
        bundle, report = checks.review(rewritten)
    edited = narration.edit(bundle, ask, job)  # the voice-over made one explanation (studio/steps/narration.py)
    settled, repaired = checks.repair_once(edited, request.study_guide)  # words set once, before the fact-check
    if settled is not bundle:
        bundle, report = checks.review(settled)
    report.problems += (unquoted + repaired + checks.song_length_problems(bundle) + checks.motion_problems(bundle)
                        + checks.essence_problems(bundle) + checks.key_line_problems(bundle)
                        + checks.song_shape_problems(bundle) + checks.style_problems(bundle, request.song_style))
    write(job / "bundle.json", bundle)
    write(job / "checks.json", report)
    return audit.settle(job, bundle, early)


def _lesson_only(answer: dict) -> Bundle:
    """A LessonBundle's answer as the Bundle the later steps read (bundle_of); what does not hold together is an
    answer that does not fit, as it is for a Bundle."""
    try:
        return bundle_of(answer)
    except ValidationError as e:
        raise brain.InvalidOutput(f"the answer does not fit Bundle: {e}", json.dumps(answer, ensure_ascii=False)) from e


def user_prompt(request: Request, rotation: str = "", sung: bool = True) -> str:
    """sung: False for the lesson video alone, which has no song for a style."""
    parts = [f"Topic: {request.topic}"]
    if request.course:
        parts.append(f"Course: {request.course}")
    if request.request:
        parts.append(f"The learner's request, in their words: {request.request}")
    if sung and request.song_style:
        parts.append(f"The song style the learner asked for: {request.song_style}")
    elif sung and rotation:
        parts.append(f"The song style for this droplet, from the studio's rotation: {rotation}. For a high school "
                     "or older audience, it is the seed of the song's style: keep its genre, roughly its tempo and any "
                     "section it raps, and write your own description from it. A younger audience gets the younger style below, and "
                     "a grave topic (a genocide, slavery) the grave topic's own slow style.")
    if request.images:
        parts.append(f"The learner sent {len(request.images)} photo(s) of their notes, shown above.")
    if request.study_guide:
        parts.append(f"Study guide:\n<study_guide>\n{request.study_guide}\n</study_guide>")
    return "\n\n".join(parts)
