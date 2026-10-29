"""Step 2: the fact-check, the gate before anything is posted or sung.

A fresh brain call sees only the facts (and the study guide, if one was given) and
the lines: every couplet of the song, every narration sentence and every sentence of the
lesson, in one call. Code applies the checker's corrections and re-checks only the lines that
changed, for at most three rounds. If any line still fails, the job fails.

The song is checked a couplet at a time (couplets()), since a song makes its claims across its
lines, and by a question of its own: would a student who sings it come away believing something
false? A lyric is not held to the facts' words, and may pass on what is true at the audience's
level where the facts are silent ("course knowledge"); the lesson, the narration and the screen
are held to the facts alone. A couplet that fails is rewritten by the songwriter
(studio/steps/songwriter.py), once, so it stays a song; the checker's own correction is used when
the songwriter has none, or when its rewrite fails too.

The same call checks each video's hashtags, which are public and what the Droplets tab
is searched by: a tag the checker does not pass is dropped from both videos, never
rewritten, and never fails the job. A tag that gets no verdict in three rounds is dropped
too, so only checked tags are posted.

It checks the key lines and the core idea, which the song writes on screen, like a lesson sentence: a
correction rewrites one, and one still wrong with no correction is taken off the screen (a key line dropped,
the core idea left ""), never failing the job.

It checks the marked motions the same way: what a line's particles carry and the route
they take ("H⁺ travels from Stroma to Cytochrome b6f to Thylakoid lumen"), since the video
shows it as a claim. A motion that fails is left out of the video (no particles on its
lines), never rewritten, and never fails the job; one shown on several lines is checked
once. Unmarked particles (an analogy's water, a signal) are not sent.

The facts it holds every line to have been audited first (studio/steps/audit.py). A fact the audit
corrected is sent as corrected, with what it said before (`was`), and the lyrics and narration sentences
written from it carry `written_from`, so a line that still says what the wrong fact said is caught and
corrected like any other. Without a corrected fact the call is as it was.

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
from studio.schemas import (AuditReport, Bundle, Check, CheckedLine, CheckReport, Correction, FactCheck,
                            FactCheckReport, Request)
from studio.sentences import replace_sentences, split_sentences
from studio.steps import songwriter

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "factcheck.md").read_text(encoding="utf-8")
MAX_ROUNDS = 3
KEY = "keyline"  # a key line, or the core idea: written on screen in the song
LYRIC = "lyric"  # a couplet of the song, its lines joined by "\n"
KINDS = (LYRIC, "narration", "lesson", KEY)  # the lines a correction rewrites
TAG = "tag"  # a video's hashtag: passed, or dropped
MOTION = "motion"  # what a line's particles show moving: passed, or left out of the video
KEPT_OR_DROPPED = (TAG, MOTION)  # never corrected, and never failing the check
PASSING = ("SUPPORTED", "FILLER")
COURSE_KNOWLEDGE = "course knowledge"  # a lyric's source when it is true but the facts do not say it


@dataclass(frozen=True)
class Line:
    kind: str  # lyric, narration, lesson or tag
    id: str  # the bundle's id; for the lesson, the sentence's number; for a tag, its video and place ("song-3");
    # for a couplet, its lines' ids ("v1-1,v1-2")
    text: str
    members: tuple[str, ...] = ()  # a couplet's lyric ids, in order; its text has a line for each

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


def run(job: Path, ask=brain.ask, max_rounds: int = MAX_ROUNDS) -> FactCheckReport:
    bundle = checks.load(job / "bundle.json")
    request = read(job / "request.json", Request)
    report, checked = check_bundle(bundle, request.study_guide, ask, max_rounds, was=audit_corrections(job, bundle))
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


def audit_corrections(job: Path, bundle: Bundle) -> dict[str, str]:
    """What each fact the audit corrected said before (studio/steps/audit.py), for the facts that still read as
    it corrected them."""
    try:
        report = read(job / "audit.json", AuditReport)
    except (OSError, ValueError):
        return {}
    now = {fact.id: fact.text for fact in bundle.facts}
    return {c.id: c.before for c in report.corrections if report.status == "passed" and now.get(c.id) == c.after}


def lines_of(bundle: Bundle) -> list[Line]:
    lines = couplets(bundle)
    lines += [Line("narration", n.id, n.text) for n in bundle.narration]
    lines += [Line("lesson", str(i), s.text) for i, s in enumerate(split_sentences(bundle.lesson_markdown), 1)]
    return lines + key_lines(bundle) + tag_lines(bundle) + motion_lines(bundle)


def couplets(bundle: Bundle) -> list[Line]:
    """The song as it rhymes: each section's lines two at a time from its start, a section's odd last line
    joining the pair before it (a chorus of five ends on three), and a section of one line on its own."""
    units, run = [], []
    for lyric in bundle.lyrics:
        if run and lyric.section != run[-1].section:
            units += _pairs(run)
            run = []
        run.append(lyric)
    units += _pairs(run)
    return [Line(LYRIC, ",".join(line.id for line in unit), "\n".join(line.text for line in unit),
                 tuple(line.id for line in unit)) for unit in units]


def _pairs(run: list) -> list[list]:
    pairs = [run[i:i + 2] for i in range(0, len(run), 2)]
    if len(pairs) > 1 and len(pairs[-1]) == 1:
        odd = pairs.pop()
        pairs[-1] += odd
    return pairs


def key_lines(bundle: Bundle) -> list[Line]:
    """What the song writes above its diagram: the key lines, and the core idea over the last chorus."""
    lines = [Line(KEY, "core_idea", bundle.core_idea)] if bundle.core_idea.strip() else []
    return lines + [Line(KEY, k.from_line, k.text) for k in bundle.key_lines if k.text.strip()]


def motion_text(carries: str, particles: list[str], blocked: bool = False) -> str:
    """A marked motion as the checker reads it: "H⁺ travels from Stroma to Cytochrome b6f to Thylakoid lumen", or,
    when its last stop blocks it, "e⁻ flow from Photosystem II is stopped at Herbicide"."""
    if blocked:
        through = f" through {' and '.join(particles[1:-1])}" if len(particles) > 2 else ""
        return f"{carries} flow from {particles[0]}{through} is stopped at {particles[-1]}"
    return f"{carries} travels from " + " to ".join(particles)


def _motion(bundle: Bundle, flow) -> str:
    blockers = {b.structure for b in bundle.blocks}
    return motion_text(flow.carries, flow.particles, flow.particles[-1] in blockers)


def build_up_text(carries: str, region: str) -> str:
    return f"{carries} builds up in {region}"


def spin_text(bundle: Bundle, structure: str) -> str:
    """What the video shows turning, and what drives it: "ATP synthase spins as H⁺ flows through it", or for
    unmarked particles (an analogy's water) "Water wheel spins as particles flow into it from Reservoir"."""
    flows = [f for d in bundle.direction for f in d.flows() if structure in f.particles[1:]]
    marked = sorted({d.carries for d in flows if d.carries})
    if marked:
        return f"{structure} spins as {' and '.join(marked)} flows through it"
    # What flows into it comes from the stop just before it, not where its path began ("water pump -> reservoir
    # -> turbine": the reservoir's water turns the turbine; naming the pump was refused as wrong).
    sources = sorted({d.particles[d.particles.index(structure, 1) - 1] for d in flows})
    return f"{structure} spins as particles flow into it from {' and '.join(sources)}"


def conversion_text(structure: str, inputs: list[str], outputs: list[str]) -> str:
    """What the video writes beside a structure: "At Photosystem II, H₂O is turned into O₂, H⁺ and e⁻"."""
    return f"At {structure}, {' and '.join(inputs)} is turned into {' and '.join(outputs)}"


def motion_lines(bundle: Bundle) -> list[Line]:
    """What each line's particles show moving, when they are marked with what they carry, and what the
    video shows piling up and turning."""
    return [Line(MOTION, d.line_id, _motion(bundle, d)) for d in bundle.direction
            if d.carries and len(d.particles) >= 2] + [
        Line(MOTION, f"{d.line_id}+{k}", _motion(bundle, f)) for d in bundle.direction
        for k, f in enumerate(d.also, 1) if f.carries and len(f.particles) >= 2] + [
        Line(MOTION, f"builds-{i}", build_up_text(b.carries, b.region)) for i, b in enumerate(bundle.builds_up, 1)] + [
        Line(MOTION, f"spins-{i}", spin_text(bundle, s.structure)) for i, s in enumerate(bundle.spins, 1)] + [
        Line(MOTION, f"converts-{i}", conversion_text(c.structure, c.inputs, c.outputs))
        for i, c in enumerate(bundle.conversions, 1)]


def tag_lines(bundle: Bundle) -> list[Line]:
    """Each video's hashtags, written "#tag". One both videos carry is checked once, like a chorus line."""
    return [Line(TAG, f"{video}-{i}", f"#{tag}")
            for video, tags in (("song", bundle.tags.song), ("lesson", bundle.tags.lesson))
            for i, tag in enumerate(tags, 1)]


def about(bundle: Bundle) -> dict[str, str]:
    """What the checker needs beyond the facts to judge a tag ("#apbio" by the course, "#kpop" by the song) and a
    lyric (what is true at the audience's level)."""
    return {"course": bundle.course, "topic": bundle.topic, "audience": bundle.audience,
            "song_style": ", ".join(bundle.music.positive_styles)}


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
    bundle: Bundle, study_guide: str, ask, max_rounds: int = MAX_ROUNDS, was: dict[str, str] | None = None
) -> tuple[FactCheckReport, Bundle]:
    """Check, correct and re-check; return the report and the bundle as corrected. was: what each fact the
    audit corrected said before, by its id.

    Verdicts are remembered by (kind, text), so a line is checked again only when
    its text changes, and a chorus couplet sung three times is checked once.
    """
    facts = {fact.id: fact.text for fact in bundle.facts}
    context = about(bundle)
    was = {i: before for i, before in (was or {}).items() if i in facts}
    # The lines written from a corrected fact, by their key: a correction never changes a line's fact_id.
    fact_of = {line.id: line.fact_id for line in [*bundle.lyrics, *bundle.narration]}
    written_from = {}
    for line in lines_of(bundle):
        if line.kind in (LYRIC, "narration"):
            cited = sorted({fact_of[i] for i in (line.members or (line.id,))} & set(was))
            if cited:
                written_from[line.key] = ", ".join(cited)
    verdicts: dict[tuple[str, str], tuple[Check, int]] = {}
    corrections: list[Correction] = []
    to_songwriter: set[str] = set()  # the couplets sent to the songwriter: each goes once
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
        checks = ask_checker(pending, lines, facts, study_guide, ask, round_no, context, was, written_from)
        for line in pending:
            check = checks.get(line.key)
            # A missing verdict, or SUPPORTED without a real source, is asked again next round. A lyric may rest
            # on what is true at the audience's level; every other line rests on the facts.
            if check and (check.verdict != "SUPPORTED" or cites(check.source, facts, study_guide)
                          or (line.kind == LYRIC and check.source.strip().lower() == COURSE_KNOWLEDGE)):
                verdicts[(line.kind, line.text)] = (check, round_no)
        # A tag or a motion that fails is dropped below, never corrected: only the other lines get fixes.
        wrong = [line for line in pending
                 if line.kind not in KEPT_OR_DROPPED and _verdict(verdicts, line) == "UNSUPPORTED"]
        if not wrong or round_no == max_rounds:
            continue

        # A narration or lesson line with no correction fails the check: found before a songwriter is paid.
        for line in wrong:
            check = verdicts[(line.kind, line.text)][0]
            if line.kind not in (LYRIC, KEY) and not corrected(line, check):
                reason = f"{line.kind} {line.id} is unsupported and has no correction: {check.problem}"
                break
        if reason:
            break
        fresh = [(line, verdicts[(line.kind, line.text)][0]) for line in wrong
                 if line.kind == LYRIC and line.id not in to_songwriter]
        written = songwriter.rewrite(bundle, fresh, ask, round_no) if fresh else {}
        to_songwriter |= {line.id for line, _ in fresh}

        fixes: dict[tuple[str, str], str] = {}
        made: list[Correction] = []
        for line in wrong:
            check = verdicts[(line.kind, line.text)][0]
            after, by = (written[line.id], "songwriter") if line.id in written else (corrected(line, check), "checker")
            if line.kind == KEY and not after:
                after = ""  # taken off the screen: a key line is a help, never worth failing the droplet
            elif not after:
                reason = f"{line.kind} {_shown(line.id)} is unsupported and has no correction: {check.problem}"
                break
            fixes[(line.kind, line.text)] = after
            ids = [i for other in lines if (other.kind, other.text) == (line.kind, line.text)
                   for i in (other.members or (other.id,))]
            made.append(Correction(round=round_no, kind=line.kind, ids=ids, before=line.text,
                                   after=after, problem=check.problem, by=by))
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
            blocked = bool(d["particles"]) and d["particles"][-1] in {b["structure"] for b in data["blocks"]}
            if (d["carries"] and len(d["particles"]) >= 2
                    and motion_text(d["carries"], d["particles"], blocked) not in passed_motions):
                d["particles"], d["carries"] = [], ""
            stoppers = {b["structure"] for b in data["blocks"]}
            d["also"] = [f for f in d["also"] if not f["carries"] or len(f["particles"]) < 2
                         or motion_text(f["carries"], f["particles"], f["particles"][-1] in stoppers) in passed_motions]
        data["builds_up"] = [b for b in data["builds_up"]
                             if build_up_text(b["carries"], b["region"]) in passed_motions]
        data["spins"] = [s for s in data["spins"] if spin_text(bundle, s["structure"]) in passed_motions]
        data["conversions"] = [c for c in data["conversions"]
                               if conversion_text(c["structure"], c["inputs"], c["outputs"]) in passed_motions]
        bundle = Bundle.model_validate(data)
    # A key line still not passed after the last round is taken off the screen, as one with no correction is.
    unpassed = {(KEY, c.text): "" for c in checked if c.kind == KEY and c.verdict not in PASSING}
    if unpassed and not reason:
        bundle = apply_fixes(bundle, unpassed)  # the report keeps them, with their verdicts
    failing = [c for c in checked if c.kind not in KEPT_OR_DROPPED + (KEY,) and c.verdict not in PASSING]
    if failing and not reason:
        reason = f"after {rounds} round(s), {len(failing)} line(s) still fail: " + "; ".join(
            _describe(c, verdicts) for c in failing[:3]
        ) + (f"; and {len(failing) - 3} more" if len(failing) > 3 else "")
    status = "failed" if reason else "passed"
    return FactCheckReport(status=status, reason=reason, rounds=rounds, lines=checked,
                           corrections=corrections), bundle


def ask_checker(pending: list[Line], lines: list[Line], facts: dict[str, str], study_guide: str, ask,
                round_no: int, context: dict[str, str] | None = None, was: dict[str, str] | None = None,
                written_from: dict[str, str] | None = None) -> dict[str, Check]:
    previous: dict[str, str] = {}
    if round_no > 1:
        # A re-check sends lines without their neighbours, so each brings the one before it.
        for kind in KINDS:
            same = [line for line in lines if line.kind == kind]
            for before, line in zip(same, same[1:]):
                previous.setdefault(line.key, before.text)
    was, written_from = was or {}, written_from or {}
    payload: dict = {"facts": [{"id": i, "text": text, **({"was": was[i]} if i in was else {})}
                               for i, text in facts.items()]}
    if study_guide:
        payload["study_guide"] = study_guide
    if context and any(line.kind in (TAG, LYRIC) for line in pending):
        payload["about"] = context  # for the tags and the lyrics: every other line is judged by the facts alone
    payload["lines"] = [
        {"id": line.key, "text": line.text, **({"previous": previous[line.key]} if line.key in previous else {}),
         **({"written_from": written_from[line.key]} if line.key in written_from else {})}
        for line in pending
    ]
    answer = ask("brain", json.dumps(payload, ensure_ascii=False, indent=1), FactCheck, system=PROMPT,
                 label=f"factcheck round {round_no}")
    return {check.id: check for check in FactCheck.model_validate(answer).checks}


def corrected(line: Line, check: Check) -> str:
    """The checker's correction of a line, as it would go in: "" when there is none, it changes nothing, or a
    couplet's comes back with another number of lines."""
    if line.kind == LYRIC:
        after = "\n".join(songwriter.true_version(check, len(line.members)))
    else:
        after = " ".join(check.corrected.split())
    return after if after != line.text else ""


def apply_fixes(bundle: Bundle, fixes: dict[tuple[str, str], str]) -> Bundle:
    """Rewrite every line whose (kind, text) has a fix: all repeats of a chorus couplet alike."""
    data = bundle.model_dump()
    sung = {before: after for (kind, before), after in fixes.items() if kind == LYRIC}
    new = {i: text for unit in couplets(bundle) if unit.text in sung
           for i, text in zip(unit.members, sung[unit.text].split("\n"))}
    for line in data["lyrics"]:
        line["text"] = new.get(line["id"], line["text"])
    for line in data["narration"]:
        line["text"] = fixes.get(("narration", line["text"]), line["text"])
    for key in data["key_lines"]:
        key["text"] = fixes.get((KEY, key["text"]), key["text"])
    data["key_lines"] = [key for key in data["key_lines"] if key["text"].strip()]
    data["core_idea"] = fixes.get((KEY, data["core_idea"]), data["core_idea"])
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
    return quotes_guide(source, study_guide)


def quotes_guide(text: str, study_guide: str) -> bool:
    """Whether the text is a quote of 3+ words copied from the study guide, its quote marks and spacing aside."""
    quote = _squash(text.strip().strip("\"'“”‘’"))
    return bool(study_guide) and len(quote.split()) >= 3 and quote in _squash(study_guide)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _verdict(verdicts: dict[tuple[str, str], tuple[Check, int]], line: Line) -> str | None:
    found = verdicts.get((line.kind, line.text))
    return found[0].verdict if found else None


def shown(text: str) -> str:
    """A couplet on one line, for a reason or a printout: "Light lands on the thylakoid / Every hand-off..."."""
    return text.replace("\n", " / ")


def _shown(line_id: str) -> str:
    return line_id.replace(",", ", ")


def _describe(line: CheckedLine, verdicts: dict[tuple[str, str], tuple[Check, int]]) -> str:
    found = verdicts.get((line.kind, line.text))
    why = found[0].problem if found and found[0].problem else "no verdict"
    return f'{line.kind} {_shown(line.id)} "{shown(line.text)}": {why}'
