"""Step 2: the fact-check, the gate before anything is posted or sung.

A fresh brain call sees only the facts (and the study guide, if one was given) and
the lines: every lyric line, every narration sentence, every sentence of the
lesson and every line of the exam question the videos read aloud (media.frq_lines). Code applies the checker's corrections and re-checks only the lines that
changed, for at most three rounds. If any line still fails, the job fails.

A scene drawn from the schematic library adds its machine's motion claims (kind
"motion": each way a part could turn, each way particles could flow; see
studio/schematic3d/library.py). They are judged like any line but never corrected and
never fail the job: the art step draws a motion only in the one way the facts support.

Writes factcheck.json. When the check passes after corrections, bundle.json is
rewritten with them and the writer's version is kept as bundle.draft.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from studio import brain, checks, media
from studio.jobs import read, write
from studio.schematic3d import library
from studio.schemas import (Bundle, Check, CheckedLine, CheckReport, Correction, FactCheck,
                            FactCheckReport, Request)
from studio.sentences import replace_sentences, split_sentences

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "factcheck.md").read_text(encoding="utf-8")
MAX_ROUNDS = 3
KINDS = ("lyric", "narration", "lesson", "frq")  # the lines the job's text is made of; "motion" lines are not


@dataclass(frozen=True)
class Line:
    kind: str  # lyric, narration, lesson or frq
    id: str  # the bundle's id; for the lesson, the sentence's number; for the exam question, media.frq_lines'
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
    lines += [Line("frq", line_id, text) for line_id, text in media.frq_lines(bundle)]
    lines += [Line("motion", probe_id, text) for name in schematics_of(bundle)
              for probe_id, text in library.probes(library.load(name).recipe)]
    return lines


def schematics_of(bundle: Bundle) -> list[str]:
    """The schematic recipes the bundle's scenes draw, each once, in order."""
    return list(dict.fromkeys(scene.schematic for scene in bundle.scenes if scene.schematic))


def motion_verdicts(report: FactCheckReport) -> dict[str, str]:
    """Each motion claim's verdict, by probe id: what library.resolve reads."""
    return {line.id: line.verdict for line in report.lines if line.kind == "motion" and line.verdict}


def check_bundle(
    bundle: Bundle, study_guide: str, ask, max_rounds: int = MAX_ROUNDS
) -> tuple[FactCheckReport, Bundle]:
    """Check, correct and re-check; return the report and the bundle as corrected.

    Verdicts are remembered by (kind, text), so a line is checked again only when
    its text changes, and a chorus line sung three times is checked once.
    """
    facts = {fact.id: fact.text for fact in bundle.facts}
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
        checks = ask_checker(pending, lines, facts, study_guide, ask, round_no)
        for line in pending:
            check = checks.get(line.key)
            # A missing verdict, or SUPPORTED without a real source, is asked again next round.
            if check and (check.verdict != "SUPPORTED" or cites(check.source, facts, study_guide)):
                verdicts[(line.kind, line.text)] = (check, round_no)
        wrong = [line for line in pending if line.kind in KINDS and _verdict(verdicts, line) == "UNSUPPORTED"]
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
                                   source=check.source if check else "", round=round_no))
    failing = [c for c in checked if c.kind in KINDS and c.verdict not in ("SUPPORTED", "FILLER")]
    if failing and not reason:
        reason = f"after {rounds} round(s), {len(failing)} line(s) still fail: " + "; ".join(
            _describe(c, verdicts) for c in failing[:3]
        ) + (f"; and {len(failing) - 3} more" if len(failing) > 3 else "")
    status = "failed" if reason else "passed"
    return FactCheckReport(status=status, reason=reason, rounds=rounds, lines=checked,
                           corrections=corrections), bundle


def ask_checker(pending: list[Line], lines: list[Line], facts: dict[str, str], study_guide: str, ask,
                round_no: int) -> dict[str, Check]:
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
    frq = data["frq"]
    for field in ("prompt", "predict_question"):
        frq[field] = fixes.get(("frq", frq[field]), frq[field])
    frq["model_answer_steps"] = [fixes.get(("frq", step), step) for step in frq["model_answer_steps"]]
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
