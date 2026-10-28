"""Step 2: the fact-check, the gate before anything is posted or sung.

A fresh brain call sees only the facts (and the study guide, if one was given) and
the lines: every lyric line, every narration sentence and every sentence of the
lesson, in one call. Code applies the checker's corrections and re-checks only the lines that
changed, for at most three rounds. If any line still fails, the job fails.

The same call checks each video's hashtags, which are public and what the Droplets tab
is searched by: a tag the checker does not pass is dropped from both videos, never
rewritten, and never fails the job. A tag that gets no verdict in three rounds is dropped
too, so only checked tags are posted.

It checks the marked motions the same way: what a line's particles carry and the route
they take ("H⁺ travels from Stroma to Cytochrome b6f to Thylakoid lumen"), since the video
shows it as a claim. A motion that fails is left out of the video (no particles on its
lines), never rewritten, and never fails the job; one shown on several lines is checked
once. Unmarked particles (an analogy's water, a signal) are not sent.

Writes factcheck.json. When the check passes after corrections or dropped tags,
bundle.json is rewritten with them and the writer's version is kept as bundle.draft.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from studio import brain, checks
from studio.jobs import read, write
from studio.schemas import (Bundle, Check, CheckedLine, CheckReport, Correction, FactCheck,
                            FactCheckReport, Request)
from studio.sentences import replace_sentences, split_sentences

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "factcheck.md").read_text(encoding="utf-8")
MAX_ROUNDS = 3
KINDS = ("lyric", "narration", "lesson")  # the lines a correction rewrites
TAG = "tag"  # a video's hashtag: passed, or dropped
MOTION = "motion"  # what a line's particles show moving: passed, or left out of the video
KEPT_OR_DROPPED = (TAG, MOTION)  # never corrected, and never failing the check
PASSING = ("SUPPORTED", "FILLER")


@dataclass(frozen=True)
class Line:
    kind: str  # lyric, narration, lesson or tag
    id: str  # the bundle's id; for the lesson, the sentence's number; for a tag, its video and place ("song-3")
    text: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


def run(job: Path, ask=brain.ask, max_rounds: int = MAX_ROUNDS) -> FactCheckReport:
    bundle = checks.load(job / "bundle.json")
    request = read(job / "request.json", Request)
    report, checked = check_bundle(bundle, request.study_guide, ask, max_rounds)
    if report.status == "passed" and checked != bundle:
        draft = job / "bundle.draft.json"
        if not draft.exists():
            draft.write_text((job / "bundle.json").read_text(encoding="utf-8"), encoding="utf-8")
        # A corrected line can say different names, so the code checks run again; what
        # they find joins what the bundle step found.
        checked, problems = checks.review(checked)
        if (job / "checks.json").exists():
            problems = checks.merge(read(job / "checks.json", CheckReport), problems)
        write(job / "bundle.json", checked)
        write(job / "checks.json", problems)
    write(job / "factcheck.json", report)
    return report


def lines_of(bundle: Bundle) -> list[Line]:
    lines = [Line("lyric", lyric.id, lyric.text) for lyric in bundle.lyrics]
    lines += [Line("narration", n.id, n.text) for n in bundle.narration]
    lines += [Line("lesson", str(i), s.text) for i, s in enumerate(split_sentences(bundle.lesson_markdown), 1)]
    return lines + tag_lines(bundle) + motion_lines(bundle)


def motion_text(carries: str, particles: list[str]) -> str:
    """A marked motion as the checker reads it: "H⁺ travels from Stroma to Cytochrome b6f to Thylakoid lumen"."""
    return f"{carries} travels from " + " to ".join(particles)


def motion_lines(bundle: Bundle) -> list[Line]:
    """What each line's particles show moving, when they are marked with what they carry."""
    return [Line(MOTION, d.line_id, motion_text(d.carries, d.particles))
            for d in bundle.direction if d.carries and len(d.particles) >= 2]


def tag_lines(bundle: Bundle) -> list[Line]:
    """Each video's hashtags, written "#tag". One both videos carry is checked once, like a chorus line."""
    return [Line(TAG, f"{video}-{i}", f"#{tag}")
            for video, tags in (("song", bundle.tags.song), ("lesson", bundle.tags.lesson))
            for i, tag in enumerate(tags, 1)]


def about(bundle: Bundle) -> dict[str, str]:
    """What the checker needs to judge a tag beyond the facts: "#apbio" by the course, "#kpop" by the song."""
    return {"course": bundle.course, "topic": bundle.topic, "song_style": ", ".join(bundle.music.positive_styles)}


def dropped_tags(report: FactCheckReport) -> list[CheckedLine]:
    """The tags the check did not pass, each once, as the report has them."""
    return _dropped(report, TAG)


def dropped_motions(report: FactCheckReport) -> list[CheckedLine]:
    """The marked motions the check did not pass, each once: they are left out of the video."""
    return _dropped(report, MOTION)


def _dropped(report: FactCheckReport, kind: str) -> list[CheckedLine]:
    seen: dict[str, CheckedLine] = {}
    for line in report.lines:
        if line.kind == kind and line.verdict not in PASSING:
            seen.setdefault(line.text, line)
    return list(seen.values())


def check_bundle(
    bundle: Bundle, study_guide: str, ask, max_rounds: int = MAX_ROUNDS
) -> tuple[FactCheckReport, Bundle]:
    """Check, correct and re-check; return the report and the bundle as corrected.

    Verdicts are remembered by (kind, text), so a line is checked again only when
    its text changes, and a chorus line sung three times is checked once.
    """
    facts = {fact.id: fact.text for fact in bundle.facts}
    context = about(bundle)
    verdicts: dict[tuple[str, str], tuple[Check, int]] = {}
    corrections: list[Correction] = []
    reason = ""
    rounds = 0
    for round_no in range(1, max_rounds + 1):
        lines = lines_of(bundle)
        pending: list[Line] = []
        seen = set(verdicts)
        for line in lines:
            if (line.kind, line.text) not in seen:
                seen.add((line.kind, line.text))
                pending.append(line)
        if not pending:
            break
        rounds = round_no
        checks = ask_checker(pending, lines, facts, study_guide, ask, round_no, context)
        for line in pending:
            check = checks.get(line.key)
            # A missing verdict, or SUPPORTED without a real source, is asked again next round.
            if check and (check.verdict != "SUPPORTED" or cites(check.source, facts, study_guide)):
                verdicts[(line.kind, line.text)] = (check, round_no)
        # A tag or a motion that fails is dropped below, never corrected: only the other lines get fixes.
        wrong = [line for line in pending
                 if line.kind not in KEPT_OR_DROPPED and _verdict(verdicts, line) == "UNSUPPORTED"]
        if not wrong or round_no == max_rounds:
            continue

        fixes: dict[tuple[str, str], str] = {}
        made: list[Correction] = []
        for line in wrong:
            check = verdicts[(line.kind, line.text)][0]
            after = " ".join(check.corrected.split())
            if not after or after == line.text:
                reason = f"{line.kind} {line.id} is unsupported and has no correction: {check.problem}"
                break
            fixes[(line.kind, line.text)] = after
            ids = [other.id for other in lines if (other.kind, other.text) == (line.kind, line.text)]
            made.append(Correction(round=round_no, kind=line.kind, ids=ids, before=line.text,
                                   after=after, problem=check.problem))
        if reason:
            break
        try:
            bundle = apply_fixes(bundle, fixes)
        except ValidationError as e:
            reason = f"the corrected bundle is no longer valid: {e}"
            break
        corrections += made

    checked = []
    for line in lines_of(bundle):
        check, round_no = verdicts.get((line.kind, line.text), (None, 0))
        checked.append(CheckedLine(kind=line.kind, id=line.id, text=line.text,
                                   verdict=check.verdict if check else None,
                                   source=check.source if check else "", round=round_no,
                                   problem=check.problem if check and check.verdict == "UNSUPPORTED" else ""))
    # Only checked tags are posted: one that failed, or never got a verdict, leaves both videos.
    passed_tags = {c.text.removeprefix("#") for c in checked if c.kind == TAG and c.verdict in PASSING}
    tags = bundle.tags
    if any(t not in passed_tags for t in [*tags.song, *tags.lesson]):
        bundle = bundle.model_copy(update={"tags": tags.model_copy(update={
            "song": [t for t in tags.song if t in passed_tags],
            "lesson": [t for t in tags.lesson if t in passed_tags]})})
    # Only checked motions are shown: one that failed, or never got a verdict, has its particles taken out.
    passed_motions = {c.text for c in checked if c.kind == MOTION and c.verdict in PASSING}
    if any(line.text not in passed_motions for line in motion_lines(bundle)):
        data = bundle.model_dump()
        for d in data["direction"]:
            if d["carries"] and len(d["particles"]) >= 2 and motion_text(d["carries"], d["particles"]) not in passed_motions:
                d["particles"], d["carries"] = [], ""
        bundle = Bundle.model_validate(data)
    failing = [c for c in checked if c.kind not in KEPT_OR_DROPPED and c.verdict not in PASSING]
    if failing and not reason:
        reason = f"after {rounds} round(s), {len(failing)} line(s) still fail: " + "; ".join(
            _describe(c, verdicts) for c in failing[:3]
        ) + (f"; and {len(failing) - 3} more" if len(failing) > 3 else "")
    status = "failed" if reason else "passed"
    return FactCheckReport(status=status, reason=reason, rounds=rounds, lines=checked,
                           corrections=corrections), bundle


def ask_checker(pending: list[Line], lines: list[Line], facts: dict[str, str], study_guide: str, ask,
                round_no: int, context: dict[str, str] | None = None) -> dict[str, Check]:
    previous: dict[str, str] = {}
    if round_no > 1:
        # A re-check sends lines without their neighbours, so each brings the one before it.
        for kind in KINDS:
            same = [line for line in lines if line.kind == kind]
            for before, line in zip(same, same[1:]):
                previous.setdefault(line.key, before.text)
    payload: dict = {"facts": [{"id": i, "text": text} for i, text in facts.items()]}
    if study_guide:
        payload["study_guide"] = study_guide
    if context and any(line.kind == TAG for line in pending):
        payload["about"] = context  # for the tags only: every other line is judged by the facts alone
    payload["lines"] = [
        {"id": line.key, "text": line.text, **({"previous": previous[line.key]} if line.key in previous else {})}
        for line in pending
    ]
    answer = ask("brain", json.dumps(payload, ensure_ascii=False, indent=1), FactCheck, system=PROMPT,
                 label=f"factcheck round {round_no}")
    return {check.id: check for check in FactCheck.model_validate(answer).checks}


def apply_fixes(bundle: Bundle, fixes: dict[tuple[str, str], str]) -> Bundle:
    """Rewrite every line whose (kind, text) has a fix: all repeats of a chorus line alike."""
    data = bundle.model_dump()
    for kind, field in (("lyric", "lyrics"), ("narration", "narration")):
        for line in data[field]:
            line["text"] = fixes.get((kind, line["text"]), line["text"])
    lesson = {before: after for (kind, before), after in fixes.items() if kind == "lesson"}
    if lesson:
        data["lesson_markdown"] = replace_sentences(data["lesson_markdown"], lesson)
    return Bundle.model_validate(data)


def cites(source: str, facts: dict[str, str], study_guide: str) -> bool:
    """A real fact id (or several, comma-separated), or a quote of 3+ words from the study guide."""
    source = source.strip()
    ids = [part.strip() for part in source.split(",")]
    if source and all(i in facts for i in ids):
        return True
    quote = _squash(source.strip("\"'“”‘’"))
    return bool(study_guide) and len(quote.split()) >= 3 and quote in _squash(study_guide)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _verdict(verdicts: dict[tuple[str, str], tuple[Check, int]], line: Line) -> str | None:
    found = verdicts.get((line.kind, line.text))
    return found[0].verdict if found else None


def _describe(line: CheckedLine, verdicts: dict[tuple[str, str], tuple[Check, int]]) -> str:
    found = verdicts.get((line.kind, line.text))
    why = found[0].problem if found and found[0].problem else "no verdict"
    return f'{line.kind} {line.id} "{line.text}": {why}'
