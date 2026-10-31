"""Between the bundle and the fact-check: the fact audit. Are the bundle's own facts right?

The fact-check (studio/steps/factcheck.py) holds every line to the bundle's `facts`, which the same planner
call wrote, so a wrong fact passes it and spreads to everything written from it: the lesson, the song, the key
lines, the arrows and the conversions. The audit judges the facts themselves, before the fact-check reads them.

A separate brain call, with a prompt of its own (prompts/audit.md) and none of the planner's conversation, sees
the course, topic and audience, the facts, and the study guide when the learner gave one. It judges each fact on
its own: CORRECT, WRONG with a corrected version, or UNSURE, by established knowledge for the course at the
droplet's level. A study guide is context, never ground truth, since a learner can send anything: a fact it
states that the field does not is WRONG. Where the guide covers a fact and agrees, the verdict quotes it; every
verdict says which (`basis`), and code checks each quote is in the guide.

A fact judged wrong is corrected once, in the first call. A second call, the same prompt, judges each correction
on its own, with any fact the first call left without a verdict. So every fact the fact-check builds on is one
the audit judged CORRECT or UNSURE as it now reads, and the step fails, to be retried by the next run, when that
cannot be:

- a fact judged wrong with no correction;
- a correction the second call judges wrong too: a fact is corrected only once;
- a fact first judged in the second call, and judged wrong: no call is left to judge its correction;
- a fact with no verdict after both calls.

An UNSURE fact is kept and recorded, never failing the droplet: the audit could not tell, and the fact-check
still holds every line to it. A call that comes back with no usable answer fails the step, as for the
fact-check.

The lines written from a corrected fact are rechecked by the fact-check, which runs next: it reads the facts as
corrected, each corrected one with what it said before (`was`), and the lines written from it marked so
(`written_from`).

One call when every fact passes, two when one was corrected or skipped. Writes audit.json. When a fact is
corrected, bundle.json is rewritten with it and the writer's version is kept as bundle.draft.json. Any
factcheck.json is removed: it judged the lines against the facts before this audit.

Begun early. The audit needs only the facts, which the script writes before the bundle call, and the bundle keeps
the script's facts (script.follow). So the bundle step begins it on the script's facts (begin()) beside the bundle
call, about 10 minutes, and settles it once the bundle is written (settle()): audit.json as run() writes it, so this
step finds itself done. The same calls, at no extra cost, a step's wait sooner. The auditor is told the course and
topic as the learner asked them, the bundle not yet written. When the bundle did not keep the script's facts, or the
early audit did not pass or did not finish, it is dropped and this step audits as before (Oct 31).
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from studio import brain, checks, ledger
from studio.jobs import read, write
from studio.schemas import (FILLER, AuditedFact, AuditReport, Bundle, FactAudit, FactCorrection, FactJudgement,
                            Request)
from studio.steps.factcheck import quotes_guide

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "audit.md").read_text(encoding="utf-8")
MAX_CALLS = 2  # the audit, then a look at its corrections
PASSING = ("CORRECT", "UNSURE")


def run(job: Path, ask=brain.ask) -> AuditReport:
    bundle = checks.load(job / "bundle.json")
    request = read(job / "request.json", Request)
    (job / "factcheck.json").unlink(missing_ok=True)
    report, audited = audit_bundle(bundle, request.study_guide, ask)
    # audit.json first: a run stopped before bundle.json takes the corrections finds its facts are not the ones
    # the audit passed (is_done) and audits again, where the other order would pass the corrected facts with no
    # record of what they corrected.
    write(job / "audit.json", report)
    if report.status == "passed" and audited != bundle:
        draft = job / "bundle.draft.json"
        if not draft.exists():
            draft.write_text((job / "bundle.json").read_text(encoding="utf-8"), encoding="utf-8")
        write(job / "bundle.json", audited)
    return report


def is_done(job: Path) -> bool:
    """Whether audit.json passed the facts bundle.json has now: a bundle written again, or facts changed by hand,
    need a new audit."""
    try:
        report = read(job / "audit.json", AuditReport)
        bundle = checks.load(job / "bundle.json")
    except (OSError, ValueError, KeyError):  # missing, cut short, or from an older schema
        return False
    return report.status == "passed" and [(f.id, f.text) for f in report.facts] == [
        (f.id, f.text) for f in bundle.facts]


def audit_bundle(bundle: Bundle, study_guide: str, ask) -> tuple[AuditReport, Bundle]:
    """Judge every fact, correct the wrong ones once and judge the corrections; return the report and the bundle
    with its facts as corrected (as it was, when the audit fails)."""
    audited = audit_facts({"course": bundle.course, "topic": bundle.topic, "audience": bundle.audience},
                          bundle.facts, study_guide, ask)
    return finish(audited, bundle)


@dataclass
class Audited:
    """The facts judged, before they meet the bundle: the report (no lines cite a fact yet), and each fact's text
    and quote as corrected."""

    facts: list  # the facts as they were given
    report: AuditReport
    texts: dict[str, str]
    quotes: dict[str, str]


def audit_facts(about: dict[str, str], given: list, study_guide: str, ask) -> Audited:
    """Judge every fact, correct the wrong ones once and judge the corrections. about: the course, topic and
    audience the auditor is told."""
    texts = {f.id: f.text for f in given}
    quotes = {f.id: f.source_quote for f in given}
    verdicts: dict[str, tuple[FactJudgement, int]] = {}  # the last verdict on each fact's text as it now reads
    corrections: list[FactCorrection] = []
    failures: list[str] = []
    pending, calls = list(texts), 0
    while pending and calls < MAX_CALLS and not failures:
        calls += 1
        answer = ask_auditor(about, {i: texts[i] for i in pending}, quotes, study_guide, ask, calls)
        again = []
        for fact_id in pending:
            judgement = answer.get(fact_id)
            if judgement is None:
                again.append(fact_id)  # asked again in the next call
                continue
            judgement = grounded(judgement, study_guide)
            verdicts[fact_id] = (judgement, calls)
            if judgement.verdict in PASSING:
                continue
            after = " ".join(judgement.corrected.split())
            if any(c.id == fact_id for c in corrections):
                failures.append(f"fact {fact_id} is still wrong after its correction: {judgement.problem}")
            elif calls == MAX_CALLS:
                failures.append(f"fact {fact_id} is wrong, and was first judged in the last call, so no call is left "
                                f"to judge a correction: {judgement.problem}")
            elif not after or after == texts[fact_id]:
                failures.append(f"fact {fact_id} is wrong and the audit gave no correction: {judgement.problem}")
            else:
                corrections.append(FactCorrection(id=fact_id, before=texts[fact_id], after=after,
                                                  problem=judgement.problem, basis=judgement.basis, cited_by=[]))
                texts[fact_id] = after
                # The writer's quote was for the wrong version: the correction keeps only a quote the audit found
                # in the guide for it.
                quotes[fact_id] = judgement.quote if judgement.basis == "study guide" else ""
                del verdicts[fact_id]  # the correction is judged on its own, in the next call
                again.append(fact_id)
        pending = again
    if pending and not failures:
        failures.append(f"no verdict on {', '.join(f'fact {i}' for i in pending)} after {calls} call(s)")

    facts = []
    for fact_id, text in texts.items():
        judgement, call = verdicts.get(fact_id, (None, 0))
        facts.append(AuditedFact(id=fact_id, text=text, verdict=judgement.verdict if judgement else None,
                                 basis=judgement.basis if judgement else "", quote=judgement.quote if judgement else "",
                                 problem=judgement.problem if judgement else "", call=call, cited_by=[]))
    report = AuditReport(status="failed" if failures else "passed", reason="; ".join(failures),
                         judged_against="study guide" if study_guide.strip() else "course knowledge",
                         audience=about["audience"], calls=calls, facts=facts, corrections=corrections)
    return Audited(list(given), report, texts, quotes)


def finish(audited: Audited, bundle: Bundle) -> tuple[AuditReport, Bundle]:
    """The report with the lines that cite each fact (cited_by), and the bundle with its facts as corrected (as it
    was, when the audit failed or corrected none)."""
    cited = cited_by(bundle)
    report = audited.report.model_copy(update={
        "facts": [f.model_copy(update={"cited_by": cited.get(f.id, [])}) for f in audited.report.facts],
        "corrections": [c.model_copy(update={"cited_by": cited.get(c.id, [])}) for c in audited.report.corrections]})
    if report.status == "failed" or not report.corrections:
        return report, bundle
    data = bundle.model_dump()
    for fact in data["facts"]:
        fact["text"], fact["source_quote"] = audited.texts[fact["id"]], audited.quotes[fact["id"]]
    return report, Bundle.model_validate(data)


def begin(request: Request, script, ask) -> Future | None:
    """The audit of the script's facts, begun beside the bundle call, its calls on the bill of the step that began
    it; None with no script."""
    if script is None:
        return None
    step = ledger.current_step()
    about = {"course": request.course, "topic": request.topic, "audience": script.audience}

    def audit() -> Audited:
        with ledger.at_step(step):
            return audit_facts(about, script.facts, request.study_guide, ask)

    pool = ThreadPoolExecutor(1, thread_name_prefix="audit")
    try:
        return pool.submit(audit)
    finally:
        pool.shutdown(wait=False)


def settle(job: Path, bundle: Bundle, early: Future | None) -> Bundle:
    """The early audit kept, when it passed the facts the bundle has: audit.json written and the bundle's facts
    corrected, as run() does; the bundle as it is, and the audit left to its step, otherwise."""
    if early is None:
        return bundle
    try:
        audited = early.result()
    except Exception as e:  # the step audits as before
        print(f"audit: the early audit did not finish, so the audit step runs: {e}", file=sys.stderr)
        return bundle
    if [(f.id, f.text) for f in audited.facts] != [(f.id, f.text) for f in bundle.facts]:
        print("audit: the bundle did not keep the script's facts, so the audit step runs", file=sys.stderr)
        return bundle
    if audited.report.status != "passed":
        return bundle  # the step audits again, as a retry would
    report, corrected = finish(audited, bundle)
    write(job / "audit.json", report)
    if corrected != bundle:
        draft = job / "bundle.draft.json"
        if not draft.exists():
            draft.write_text((job / "bundle.json").read_text(encoding="utf-8"), encoding="utf-8")
        write(job / "bundle.json", corrected)
    return corrected


def ask_auditor(about: dict[str, str], facts: dict[str, str], quotes: dict[str, str], study_guide: str, ask,
                call: int) -> dict[str, FactJudgement]:
    """One call: the facts, judged each on its own. It never sees the lines written from them, the planner's
    conversation, or which of them were corrected. about: the course, topic and audience."""
    payload: dict = {"course": about["course"], "topic": about["topic"], "audience": about["audience"],
                     "facts": [{"id": i, "text": text, **({"source_quote": quotes[i]} if quotes.get(i) else {})}
                               for i, text in facts.items()]}
    if study_guide.strip():
        payload["study_guide"] = study_guide
    answer = ask("brain", json.dumps(payload, ensure_ascii=False, indent=1), FactAudit, system=PROMPT,
                 label="fact audit" if call == 1 else "fact audit again")
    return {judgement.id: judgement for judgement in FactAudit.model_validate(answer).facts}


def grounded(judgement: FactJudgement, study_guide: str) -> FactJudgement:
    """The judgement with its basis as code can stand behind: "study guide" only with a quote of 3+ words copied
    from the guide; otherwise "course knowledge", which is what it was judged by."""
    if judgement.basis == "study guide" and quotes_guide(judgement.quote, study_guide):
        return judgement
    return judgement.model_copy(update={"basis": "course knowledge", "quote": ""})


def cited_by(bundle: Bundle) -> dict[str, list[str]]:
    """Each fact's lyrics and narration sentences, by the fact_id they were written from."""
    lines: dict[str, list[str]] = {}
    for line in [*bundle.lyrics, *bundle.narration]:
        if line.fact_id != FILLER:
            lines.setdefault(line.fact_id, []).append(line.id)
    return lines


def unsure(report: AuditReport) -> list[AuditedFact]:
    """The facts the audit could not confirm: kept, and named in the run's line and the summary."""
    return [f for f in report.facts if f.verdict == "UNSURE"]


def describe(report: AuditReport) -> str:
    """What a passed audit did, for the run's line: "4 fact(s) judged by course knowledge, 1 corrected (f2),
    1 unsure (f4), in 2 call(s)"."""
    by_guide = sum(f.basis == "study guide" for f in report.facts)
    told = [f"{len(report.facts)} fact(s) judged" + (f", {by_guide} by the study guide" if by_guide
                                                       else " by course knowledge")]
    if report.corrections:
        told.append(f"{len(report.corrections)} corrected ({', '.join(c.id for c in report.corrections)})")
    if unsure(report):
        told.append(f"{len(unsure(report))} unsure ({', '.join(f.id for f in unsure(report))})")
    return ", ".join(told) + f", in {report.calls} call(s)"
