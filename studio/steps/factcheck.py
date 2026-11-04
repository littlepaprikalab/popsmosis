"""Step 2: the fact-check, the gate before anything is posted or sung.

A fresh brain call sees only the facts (and the study guide, if one was given, as context: never
ground truth, never a source, since a learner can send anything) and the lines: every couplet of the song, every narration sentence and every sentence of the
lesson, in one call. Code applies the checker's corrections and re-checks only the lines that
changed, for at most three rounds. If any line still fails, the job fails.

Each kind of line has its rules in RULES: what a SUPPORTED verdict must rest on, and what happens to a line
still wrong (corrected or the check fails; corrected or taken out; or just taken out). The rest says why.

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

What else goes out with the droplet is checked in the same call for whether it is fit to: the public title,
every caption shown on a video (judged as a line, against the facts), the description each picture is painted
from, the song's style and what it must avoid (prompts/factcheck.md, Public text). A title, a caption, a
picture's description or the song's style (often the learner's own words) that fails is corrected like a line; a
thing to avoid that fails is dropped.

It checks the key lines and the core idea, which the song writes on screen, like a lesson sentence: a
correction rewrites one, and one still wrong with no correction is taken off the screen (a key line dropped,
the core idea left ""), never failing the job.

It checks the marked motions the same way: what a line's particles carry and the route
they take ("H⁺ travels from Stroma to Cytochrome b6f to Thylakoid lumen"), since the video
shows it as a claim. A motion that fails is left out of the video (no particles on its
lines), never rewritten, and never fails the job; one shown on several lines is checked
once. Unmarked particles are checked as "Something travels from Romeo to Audience", so a
route nothing travels is left out too; on the hook they are not sent, as there they are the
everyday thing, which the facts do not trace.

The facts it holds every line to have been audited first (studio/steps/audit.py). A fact the audit
corrected is sent as corrected, with what it said before (`was`), and the lyrics and narration sentences
written from it carry `written_from`, so a line that still says what the wrong fact said is caught and
corrected like any other. Without a corrected fact the call is as it was.

Writes factcheck.json, and songwriter.json when the songwriter wrote a correction. When the check
passes after corrections or dropped tags, bundle.json is rewritten with them and the writer's
version is kept as bundle.draft.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from studio import brain, checks
from studio.jobs import read, write
from studio.schemas import (Appeal, AuditReport, Bundle, Check, CheckedLine, CheckReport, Correction, FactCheck,
                            FactCheckReport, Request, untagged)
from studio.sentences import replace_sentences, split_sentences
from studio.steps import songwriter

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "factcheck.md").read_text(encoding="utf-8")
MAX_ROUNDS = 3

# The kinds of line.
LYRIC = "lyric"  # a couplet of the song, its lines joined by "\n"
KEY = "keyline"  # a key line, or the core idea: written on screen in both videos
TITLE = "title"  # "course: topic", the droplet's public title
SCENE = "scene"  # what a picture is painted from
STYLE = "style"  # the song's style, free-form words, often the learner's own
AVOID = "avoid"  # one thing the song must not sound like
TAG = "tag"  # a video's hashtag
MOTION = "motion"  # what a video shows moving, piling up, turning or turned into, and the words beside an arrow

# What a SUPPORTED verdict must name as its source.
FACTS = "facts"  # a fact id, or several
FACTS_OR_COURSE = "facts or course knowledge"  # or what is true at the audience's level (COURSE_KNOWLEDGE)
NOTHING = "nothing"  # judged as fit to go out, against no fact: a SUPPORTED one cites none

# What happens to a line still wrong.
FIX_OR_FAIL = "fix or fail"  # corrected; one with no correction fails the check
FIX_OR_REMOVE = "fix or remove"  # corrected; one with no correction is taken out, never failing the check
REMOVE = "remove"  # taken out where it is (remove()), never corrected, never failing the check


@dataclass(frozen=True)
class Rule:
    rests_on: str  # what a SUPPORTED verdict must cite
    if_wrong: str  # what happens to a line still wrong
    in_order: bool = False  # read as a run of lines: a re-check sends each with the line before it
    about: bool = False  # judged with the course, topic, audience and song style (about())


# How the check treats each kind of line: every kind's rules, in one place.
RULES = {
    LYRIC: Rule(FACTS_OR_COURSE, FIX_OR_FAIL, in_order=True, about=True),  # the songwriter rewrites it first
    "narration": Rule(FACTS, FIX_OR_FAIL, in_order=True),
    "lesson": Rule(FACTS, FIX_OR_FAIL, in_order=True),
    "caption": Rule(FACTS, FIX_OR_FAIL, in_order=True),
    KEY: Rule(FACTS, FIX_OR_REMOVE, in_order=True),
    TITLE: Rule(NOTHING, FIX_OR_FAIL),
    SCENE: Rule(NOTHING, FIX_OR_FAIL),
    STYLE: Rule(NOTHING, FIX_OR_FAIL),
    AVOID: Rule(NOTHING, REMOVE),
    TAG: Rule(FACTS, REMOVE, about=True),
    MOTION: Rule(FACTS, REMOVE),
}
KEPT_OR_DROPPED = tuple(kind for kind, rule in RULES.items() if rule.if_wrong == REMOVE)  # never failing the check
PASSING = ("SUPPORTED", "FILLER")
COURSE_KNOWLEDGE = "course knowledge"  # a lyric's source when it is true but the facts do not say it
SONGWRITER = "songwriter.json"  # the corrections the songwriter wrote (write_songwriter())
EVERYDAY = ("hook",)  # the scene whose unmarked dots are not checked (studio/prompts/bundle.md)


@dataclass(frozen=True)
class Line:
    kind: str  # lyric, narration, lesson or tag
    id: str  # the bundle's id; for the lesson, the sentence's number; for a tag, its video and place ("song-3");
    # for a couplet, its lines' ids ("v1-1,v1-2")
    text: str
    members: tuple[str, ...] = ()  # a couplet's lyric ids, in order; its text has a line for each
    at: tuple = ()  # where a line that is removed when wrong is in the bundle, for remove()

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
    write_songwriter(job, report)
    write(job / "factcheck.json", report)
    return report


def write_songwriter(job: Path, report: FactCheckReport) -> None:
    """songwriter.json: the corrections the songwriter wrote, which factcheck.json leaves unmarked (Correction.by),
    and its appeals, upheld or not; none of either, and the file goes."""
    sung = [c.model_dump(exclude={"kind", "by"}) for c in report.corrections if c.by == "songwriter"]
    appeals = [a.model_dump() for a in report.appeals]
    if sung or appeals:
        write(job / SONGWRITER, {"corrections": sung, **({"appeals": appeals} if appeals else {})})
    else:
        (job / SONGWRITER).unlink(missing_ok=True)


def songwriter_wrote(job: Path) -> set[tuple[int, str]]:
    """The (round, before) of each correction the songwriter wrote, from songwriter.json; none when it has none."""
    try:
        data = json.loads((job / SONGWRITER).read_text(encoding="utf-8"))
        return {(c["round"], c["before"]) for c in data["corrections"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


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
    lines += [Line("caption", d.line_id, d.caption.strip()) for d in bundle.direction if d.caption.strip()]
    return lines + key_lines(bundle) + tag_lines(bundle) + motion_lines(bundle) + public_lines(bundle)


def title_text(bundle: Bundle) -> str:
    """The droplet's public title as the Droplets tab builds it (api/src/lib/droplet.ts, dropletTitle)."""
    course, topic = bundle.course.strip(), bundle.topic.strip()
    return f"{course}: {topic}" if course and topic else topic or course


def public_lines(bundle: Bundle) -> list[Line]:
    """What goes out with the droplet besides its lines: the title and the headline, each picture's description, the song's style and
    what it must avoid."""
    return ([Line(TITLE, "title", title_text(bundle))]
            + ([Line(TITLE, "headline", bundle.headline)] if bundle.headline.strip() else [])
            + [Line(SCENE, scene.id, scene.described()) for scene in bundle.scenes]
            + [Line(STYLE, str(i), style) for i, style in enumerate(bundle.music.positive_styles, 1)]
            + [Line(AVOID, str(i), style, at=("negative_styles", i - 1))
               for i, style in enumerate(bundle.music.negative_styles, 1)])


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
    # A line's own text on one line, so a couplet has a line for each of its ids, whatever a lyric holds.
    return [Line(LYRIC, ",".join(line.id for line in unit), "\n".join(" ".join(line.text.split()) for line in unit),
                 tuple(line.id for line in unit)) for unit in units]


def _pairs(run: list) -> list[list]:
    pairs = [run[i:i + 2] for i in range(0, len(run), 2)]
    if len(pairs) > 1 and len(pairs[-1]) == 1:
        odd = pairs.pop()
        pairs[-1] += odd
    return pairs


def key_lines(bundle: Bundle) -> list[Line]:
    """What the song writes above its diagram: the key lines, and the core idea over the last chorus. With no song
    (the lesson video alone) a key line is known by its narration sentence instead."""
    lines = [Line(KEY, "core_idea", bundle.core_idea)] if bundle.core_idea.strip() else []
    return lines + [Line(KEY, k.from_line or k.from_narration, k.text) for k in bundle.key_lines if k.text.strip()]


def motion_text(carries: str, particles: list[str], blocked: bool = False) -> str:
    """A motion as the checker reads it: "H⁺ travels from Stroma to Cytochrome b6f to Thylakoid lumen", or,
    when its last stop blocks it, "e⁻ flow from Photosystem II is stopped at Herbicide". Unmarked, "Something
    travels from ..." and "The flow from ... is stopped at ..."."""
    if blocked:
        through = f" through {' and '.join(particles[1:-1])}" if len(particles) > 2 else ""
        return f"{carries + ' flow' if carries else 'The flow'} from {particles[0]}{through} is stopped at {particles[-1]}"
    return f"{carries or 'Something'} travels from " + " to ".join(particles)


def is_checked(scene: str, carries: str, particles: list[str]) -> bool:
    """Whether a flow goes to the fact-check: every one with a path, but an unmarked one on the hook.
    Unmarked, the brain sent dots from Romeo to the audience and from one point of a graph to another (Oct 29)."""
    return len(particles) >= 2 and (bool(carries) or scene not in EVERYDAY)


def _motion(bundle: Bundle, flow) -> str:
    blockers = {b.structure for b in bundle.blocks}
    return motion_text(flow.carries, flow.particles, flow.particles[-1] in blockers)


def build_up_text(carries: str, region: str) -> str:
    return f"{carries} builds up in {region}"


def spin_text(bundle: Bundle, structure: str) -> str:
    """What the video shows turning, and what drives it: "ATP synthase spins as H⁺ flows through it", or for
    unmarked particles "Turbine spins as particles flow into it from Reservoir"."""
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


def meaning_text(structure: str, text: str) -> str:
    """What the video writes under a graph part's name: "On the graph, Demand curve means: lower price → people buy
    more"."""
    return f"On the graph, {structure} means: {text}"


def says_text(carries: str, particles: list[str], says: str) -> str:
    """What the video writes beside an arrow: "Beside the arrow from Outside of the cell to Inside of the cell,
    carrying glucose: Glucose rides in with sodium"."""
    what = f", carrying {carries}" if carries else ""
    return f"Beside the arrow from {particles[0]} to {particles[-1]}{what}: {says}"


def says_checked(scene: str, says: str, particles: list[str]) -> bool:
    """Whether an arrow's one-liner goes to the fact-check: every one, but one on the hook, which speaks of the
    everyday picture, not of the topic."""
    return bool(says) and len(particles) >= 2 and scene not in EVERYDAY


def motion_lines(bundle: Bundle) -> list[Line]:
    """What each line's particles show moving (is_checked), what the video shows piling up, turning and turned
    into, and the words beside an arrow (says_checked). Each keeps where it is (Line.at), for remove()."""
    # Every flow: a line's own (k 0) and its second ones (k 1 on, Direction.also), the own ones first.
    flows = sorted(((i, k, d, f) for i, d in enumerate(bundle.direction) for k, f in enumerate([d, *d.also])),
                   key=lambda flow: flow[1] > 0)

    def named(d, k: int) -> str:
        return f"{d.line_id}+{k}" if k else d.line_id

    moving = [Line(MOTION, named(d, k), _motion(bundle, f), at=("flow", i, k))
              for i, k, d, f in flows if is_checked(d.scene, f.carries, f.particles)]
    shown = ([Line(MOTION, f"builds-{j}", build_up_text(b.carries, b.region), at=("builds_up", j - 1))
              for j, b in enumerate(bundle.builds_up, 1)]
             + [Line(MOTION, f"spins-{j}", spin_text(bundle, s.structure), at=("spins", j - 1))
                for j, s in enumerate(bundle.spins, 1)]
             + [Line(MOTION, f"converts-{j}", conversion_text(c.structure, c.inputs, c.outputs),
                     at=("conversions", j - 1)) for j, c in enumerate(bundle.conversions, 1)]
             + [Line(MOTION, f"means-{j}", meaning_text(m.structure, m.text), at=("meanings", j - 1))
                for j, m in enumerate(bundle.meanings, 1)])
    said = [Line(MOTION, f"{named(d, k)}~says", says_text(f.carries, f.particles, f.says), at=("says", i, k))
            for i, k, d, f in flows if says_checked(d.scene, f.says, f.particles)]
    return moving + shown + said


def tag_lines(bundle: Bundle) -> list[Line]:
    """Each video's hashtags, written "#tag". One both videos carry is checked once, like a chorus line."""
    return [Line(TAG, f"{video}-{i}", f"#{tag}", at=("tags", video, i - 1))
            for video, tags in (("song", bundle.tags.song), ("lesson", bundle.tags.lesson))
            for i, tag in enumerate(tags, 1)]


def remove(bundle: Bundle, gone: set[tuple]) -> Bundle:
    """Take out the lines at these places (Line.at): a tag from its video, a thing to avoid from the song's style,
    a motion from the video. A line's own flow loses its particles and a second flow goes; an arrow's one-liner is
    cleared, and its arrow stays unless it failed too."""
    data = bundle.model_dump()
    for video in ("song", "lesson"):
        data["tags"][video] = [t for i, t in enumerate(data["tags"][video]) if ("tags", video, i) not in gone]
    music = data["music"]
    music["negative_styles"] = [s for i, s in enumerate(music["negative_styles"]) if ("negative_styles", i) not in gone]
    for i, d in enumerate(data["direction"]):
        for k, flow in enumerate([d, *d["also"]]):
            if ("says", i, k) in gone:
                flow["says"] = ""
        if ("flow", i, 0) in gone:
            d["particles"], d["carries"] = [], ""
        d["also"] = [f for k, f in enumerate(d["also"], 1) if ("flow", i, k) not in gone]
    for field in ("builds_up", "spins", "conversions", "meanings"):
        data[field] = [x for j, x in enumerate(data[field]) if (field, j) not in gone]
    return Bundle.model_validate(data)


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
    # The couplets sent to the songwriter (each goes once), with the checker's correction of each as it was then:
    # what stands in when the songwriter's rewrite fails and the checker has no usable correction of that.
    to_songwriter: dict[str, str] = {}
    # The couplets the songwriter said the checker was wrong about, by key: its reason, and its rewrite for when the
    # appeal is lost. The next round hears each one: the couplet as written, sent again with the reason.
    appealing: dict[str, tuple[str, str]] = {}
    appeals: list[Appeal] = []
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
        heard = {line.key: appealing[line.key][0] for line in pending if line.key in appealing}
        checks = ask_checker(pending, lines, facts, study_guide, ask, round_no, context, was, written_from, heard)
        for line in pending:
            check = checks.get(line.key)
            # A missing verdict, or SUPPORTED without the source its kind needs (Rule.rests_on), is asked again.
            if check and (check.verdict != "SUPPORTED" or rests_on(RULES[line.kind].rests_on, check.source, facts)):
                verdicts[(line.kind, line.text)] = (check, round_no)
        for line in pending:
            if line.key in heard:
                upheld = _verdict(verdicts, line) in PASSING
                appeals.append(Appeal(round=round_no, ids=list(line.members or (line.id,)), text=line.text,
                                      reason=heard[line.key], upheld=upheld))
                if upheld:
                    del appealing[line.key]  # sung as it was written
        # A line removed when wrong is taken out below, never corrected: only the other lines get fixes.
        wrong = [line for line in pending
                 if RULES[line.kind].if_wrong != REMOVE and _verdict(verdicts, line) == "UNSUPPORTED"]
        if not wrong or round_no == max_rounds:
            continue

        # A line with no correction that fails the check without one fails it now: found before a songwriter is
        # paid. A couplet waits for the songwriter's rewrite.
        for line in wrong:
            check = verdicts[(line.kind, line.text)][0]
            if RULES[line.kind].if_wrong == FIX_OR_FAIL and line.kind != LYRIC and not corrected(line, check):
                reason = f"{line.kind} {line.id} is unsupported and has no correction: {check.problem}"
                break
        if reason:
            break
        fresh = [(line, verdicts[(line.kind, line.text)][0]) for line in wrong
                 if line.kind == LYRIC and line.id not in to_songwriter]
        written, said = songwriter.rewrite(bundle, fresh, ask, round_no) if fresh else ({}, {})
        to_songwriter |= {line.id: corrected(line, check) for line, check in fresh}
        # An appeal is heard next round, and its rewrite, if it is lost, checked the round after: one too late is not.
        late = round_no + 2 > max_rounds

        fixes: dict[tuple[str, str], str] = {}
        made: list[Correction] = []
        for line in wrong:
            check = verdicts[(line.kind, line.text)][0]
            if line.id in said and not late:
                appealing[line.key] = (said[line.id], written.get(line.id, ""))
                del verdicts[(line.kind, line.text)]  # so the next round reads it again, with the reason
                continue
            if line.key in appealing:  # its appeal was heard and lost: the rewrite the songwriter wrote beside it
                rewritten = appealing.pop(line.key)[1]
                after, by = (rewritten, "songwriter") if rewritten else (corrected(line, check), "checker")
            else:
                after, by = ((written[line.id], "songwriter") if line.id in written
                             else (corrected(line, check), "checker"))
            if not after and line.kind == LYRIC and to_songwriter.get(line.id, line.text) != line.text:
                after = to_songwriter[line.id]  # the songwriter's rewrite failed with no fix: the checker's first
            if not after and RULES[line.kind].if_wrong == FIX_OR_REMOVE:
                after = ""  # taken out: a key line is a help, never worth failing the droplet
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

    final = lines_of(bundle)
    checked = []
    for line in final:
        check, round_no = verdicts.get((line.kind, line.text), (None, 0))
        checked.append(CheckedLine(kind=line.kind, id=line.id, text=line.text,
                                   verdict=check.verdict if check else None,
                                   source=check.source if check else "", round=round_no,
                                   problem=check.problem if check and check.verdict == "UNSUPPORTED" else ""))
    # Only what passed goes out: a tag, a thing to avoid or a motion that failed, or never got a verdict, is taken out.
    gone = {line.at for line in final
            if RULES[line.kind].if_wrong == REMOVE and _verdict(verdicts, line) not in PASSING}
    if gone:
        bundle = remove(bundle, gone)
    # A key line still not passed after the last round is taken off the screen, as one with no correction is.
    unpassed = {(c.kind, c.text): "" for c in checked
                if RULES[c.kind].if_wrong == FIX_OR_REMOVE and c.verdict not in PASSING}
    if unpassed and not reason:
        bundle = apply_fixes(bundle, unpassed)  # the report keeps them, with their verdicts
    failing = [c for c in checked if RULES[c.kind].if_wrong == FIX_OR_FAIL and c.verdict not in PASSING]
    if failing and not reason:
        reason = f"after {rounds} round(s), {len(failing)} line(s) still fail: " + "; ".join(
            _describe(c, verdicts) for c in failing[:3]
        ) + (f"; and {len(failing) - 3} more" if len(failing) > 3 else "")
    status = "failed" if reason else "passed"
    return FactCheckReport(status=status, reason=reason, rounds=rounds, lines=checked,
                           corrections=corrections, appeals=appeals), bundle


def ask_checker(pending: list[Line], lines: list[Line], facts: dict[str, str], study_guide: str, ask,
                round_no: int, context: dict[str, str] | None = None, was: dict[str, str] | None = None,
                written_from: dict[str, str] | None = None, appeals: dict[str, str] | None = None) -> dict[str, Check]:
    previous: dict[str, str] = {}
    if round_no > 1:
        # A re-check sends lines without their neighbours, so each brings the one before it.
        for kind in (kind for kind, rule in RULES.items() if rule.in_order):
            same = [line for line in lines if line.kind == kind]
            for before, line in zip(same, same[1:]):
                previous.setdefault(line.key, before.text)
    was, written_from, appeals = was or {}, written_from or {}, appeals or {}
    payload: dict = {"facts": [{"id": i, "text": text, **({"was": was[i]} if i in was else {})}
                               for i, text in facts.items()]}
    if study_guide:
        payload["study_guide"] = study_guide
    if context and any(RULES[line.kind].about for line in pending):
        payload["about"] = context  # for the tags and the lyrics: every other line is judged by the facts alone
    payload["lines"] = [
        {"id": line.key, "text": line.text, **({"previous": previous[line.key]} if line.key in previous else {}),
         **({"written_from": written_from[line.key]} if line.key in written_from else {}),
         **({"appeal": appeals[line.key]} if line.key in appeals else {})}
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
    # A line sung in more than one couplet (the title line, in every chorus, the outro and often the intro; a
    # chorus the last one pairs differently) stays one line: its new words wherever it is sung, the chorus's when two
    # couplets changed it differently. The couplets it changes in are checked again, as any changed line.
    became: dict[str, str] = {}
    for line in sorted(bundle.lyrics, key=lambda line: line.section != "chorus"):
        if new.get(line.id, line.text) != line.text:
            became.setdefault(line.text, new[line.id])
    for line in data["lyrics"]:
        line["text"] = became.get(line["text"], new.get(line["id"], line["text"]))
    for line in data["narration"]:
        line["text"] = fixes.get(("narration", line["text"]), line["text"])
    for d in data["direction"]:
        if d["caption"].strip() and ("caption", d["caption"].strip()) in fixes:
            d["caption"] = fixes[("caption", d["caption"].strip())]
    for scene in data["scenes"]:
        # Checked without its tags (Scene.described()): a corrected picture comes back with none, and has them put
        # back after the words that name its parts (checks.tag_parts).
        fixed = fixes.get((SCENE, untagged(scene["image_prompt"])))
        if fixed is not None:
            scene["image_prompt"] = checks.tag_parts(fixed, scene["structures"])
    data["music"]["positive_styles"] = [fixes.get((STYLE, s), s) for s in data["music"]["positive_styles"]]
    title = fixes.get((TITLE, title_text(bundle)))
    if title:
        course, sep, topic = title.partition(":")
        data["course"], data["topic"] = (course.strip(), topic.strip()) if sep and topic.strip() else ("", title.strip())
    if data["headline"].strip():
        data["headline"] = fixes.get((TITLE, data["headline"]), data["headline"])
    for key in data["key_lines"]:
        key["text"] = fixes.get((KEY, key["text"]), key["text"])
    data["key_lines"] = [key for key in data["key_lines"] if key["text"].strip()]
    data["core_idea"] = fixes.get((KEY, data["core_idea"]), data["core_idea"])
    lesson = {before: after for (kind, before), after in fixes.items() if kind == "lesson"}
    if lesson:
        data["lesson_markdown"] = replace_sentences(data["lesson_markdown"], lesson)
    return Bundle.model_validate(data)


def rests_on(needs: str, source: str, facts: dict[str, str]) -> bool:
    """Whether a SUPPORTED verdict's source is what its kind needs (Rule.rests_on): a real fact id, a lyric's course
    knowledge, or, for what is judged as fit to go out, nothing."""
    return (needs == NOTHING or cites(source, facts)
            or (needs == FACTS_OR_COURSE and COURSE_KNOWLEDGE in source.lower()))


def cites(source: str, facts: dict[str, str]) -> bool:
    """A real fact id, or several, comma-separated. Never the study guide: a learner's material is context, and a line
    that only it supports is not supported."""
    source = source.strip()
    ids = [part.strip() for part in source.split(",")]
    return bool(source) and all(i in facts for i in ids)


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
