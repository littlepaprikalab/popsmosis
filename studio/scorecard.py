"""The scorecard: did a prompt change make droplets better overall, not just one?

A fixed set of topics across subjects and levels (studio/scorecards/topics.json) goes through the bundle step
alone, as `python -m studio plan` runs it but with no fact-check, art or audio: the brain writes each bundle and
the code checks run. Each droplet is then scored two ways:

- the code checks (checks.json): the problems left for the prompt, by rule, and how many the code repaired;
- a judge (studio/prompts/rubric.md): one call that reads the droplet against a written rubric and gives each of
  seven criteria (CRITERIA) a score from 1 to 5 with its reason, and lists every error it finds.

    python -m studio scorecard run                              # every topic, WORKERS at a time
    python -m studio scorecard run --topics derivative,p-value  # a few, to try a change cheaply
    python -m studio scorecard run --resume <run>               # finish a run that stopped, on its own topics
    python -m studio scorecard run --resume <run> --rejudge     # judge its droplets again, by the rubric as it is
    python -m studio scorecard compare <run> <run>              # two runs side by side

A run is saved in studio/scorecards/runs/<run>/, named for when it started and the commit it ran on:
scorecard.md (a table per topic, each score's reason, and the summary), scorecard.json (the same, for compare), and
each topic's job folder (request, bundle, checks, judge, calls). The commit is recorded, with the diff
of anything uncommitted under studio/ (prompt.diff), so a prompt tried before it is committed is on record too.

The judge is the same model on every run, whatever the brain (brain.CLAUDE["judge"]), so two runs on different
brains are graded alike; compare says when two runs differ in judge, rubric or topics. One droplet a topic is one
sample: a small change in a mean can be chance, and running the same commit twice shows how much.

A full run spends about $15 to $25 (the bundle call is most of it) and stops starting topics once it has spent
--budget. Like the live tests, run it only when the owner asks (CLAUDE.md).
"""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from studio import brain, checks, ledger, media
from studio.jobs import read, write
from studio.schemas import Bundle, CheckReport, Request, Strict
from studio.steps import bundle as bundle_step

HERE = Path(__file__).resolve().parent
TOPICS = HERE / "scorecards" / "topics.json"
RUNS = HERE / "scorecards" / "runs"
RUBRIC_PATH = HERE / "prompts" / "rubric.md"
RUBRIC = RUBRIC_PATH.read_text(encoding="utf-8")
CRITERIA = {"hook": "Hook", "frame": "Connect", "why_chain": "Why chain", "key_lines": "Key lines",
            "accuracy": "Accuracy", "singability": "Singability", "lesson_clarity": "Lesson"}
WORKERS = 3  # topics at a time: each waits minutes on its bundle call, and more risk the output rate limit
BUDGET_USD = 40.0  # about twice a full run; no topic starts once the run has spent it
FAILED = "failed.json"  # in a topic's folder: the step that failed and why
JUDGE = "judge.json"


class Topic(Strict):
    id: str
    subject: str
    level: str
    topic: str
    course: str = ""
    request: str = ""  # the learner's own words, which can set the audience ("I'm in 6th grade")
    song_style: str = ""
    study_guide: str = ""  # a file beside topics.json

    def as_request(self, folder: Path = TOPICS.parent) -> Request:
        guide = (folder / self.study_guide).read_text(encoding="utf-8") if self.study_guide else ""
        return Request(topic=self.topic, course=self.course, request=self.request, song_style=self.song_style,
                       study_guide=guide)


class Score(Strict):
    reason: str = Field(description="Two to four sentences quoting the droplet's own words: what earned or lost points")
    # An enum, not a checked int: the schema then holds the answer to 1 to 5. As a plain integer, the first run's
    # judge scored three of the derivative droplet's criteria 0, and the answer was refused.
    score: Literal[1, 2, 3, 4, 5] = Field(description="1 to 5, by the rubric's anchors")


class Error(Strict):
    # In parts, with a correction: as one line each, the first run's judge listed claims it then called fine.
    claim: str = Field(description="The words to correct, quoted, and where they are (lyric c1-2, narration n4, "
                                   "the lesson)")
    problem: str = Field(description="What is wrong or misleading, in one sentence")
    correction: str = Field(description="What it should say")


class Judgement(Strict):
    """judge.json: the rubric's scores for one droplet, each with its reason."""

    hook: Score
    frame: Score
    why_chain: Score
    key_lines: Score
    errors: list[Error] = Field(description="Each claim to correct; [] when there is none. Never a claim that is fine")
    accuracy: Score
    singability: Score
    lesson_clarity: Score


def load_topics(path: Path = TOPICS) -> list[Topic]:
    return [Topic.model_validate(topic) for topic in json.loads(path.read_text(encoding="utf-8"))]


# --- a run ----------------------------------------------------------------------------------------


def run(topics: list[Topic], folder: Path, ask_for=None, about: dict | None = None, workers: int = WORKERS,
        budget: float = BUDGET_USD, judge_only: bool = False) -> dict:
    """Make and score each topic's droplet in folder/<topic id>/, then write scorecard.json and scorecard.md.

    A topic whose files are there already is not made again: a run that stopped resumes where it left off.
    ask_for(job) is the ask for a topic's calls (default: brain.ask, logged to its calls.jsonl); about is the
    run's provenance (default: provenance()). judge_only (a re-judge) makes no bundle, even for a topic whose
    bundle failed. Returns the scorecard."""
    if ask_for is None:
        from studio.run import logged_ask
        ask_for = logged_ask
    folder.mkdir(parents=True, exist_ok=True)
    card_path = folder / "scorecard.json"
    if card_path.exists():  # a resumed run keeps what it started with
        about = json.loads(card_path.read_text(encoding="utf-8"))["about"]
    else:
        about = dict(about or provenance())
        if about.get("diff"):
            (folder / "prompt.diff").write_text(about["diff"], encoding="utf-8")
        about.pop("diff", None)
        write(card_path, {"about": about, "topics": [], "summary": {}})
    started = time.monotonic()

    def one(topic: Topic) -> None:
        job = folder / topic.id
        if spent(folder) >= budget and not _done(job):
            print(f"{topic.id}: skipped, the run has spent its ${budget:.2f}", file=sys.stderr)
            return
        print(f"{topic.id}: running", file=sys.stderr)
        make(topic, job, ask_for(job), judge_only)
        print(f"{topic.id}: " + ("failed" if (job / FAILED).exists() else "done"), file=sys.stderr)

    ledger.parallel(one, topics, workers=workers)
    rows = [topic_row(topic, folder / topic.id) for topic in topics]
    card = {"about": {**about, "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),  # minutes: every go at it, summed
                      "minutes": round((about.get("minutes") or 0) + (time.monotonic() - started) / 60, 1)},
            "topics": rows, "summary": summarize(rows)}
    write(card_path, card)
    (folder / "scorecard.md").write_text(markdown(card), encoding="utf-8")
    return card


def make(topic: Topic, job: Path, ask, judge_only: bool = False) -> None:
    """The topic's bundle (with its checks), then the judge's scores: whichever is missing.
    A step that fails is recorded in failed.json and the topic goes no further; the run goes on."""
    job.mkdir(parents=True, exist_ok=True)
    if judge_only and not (job / "bundle.json").exists():
        return  # a re-judge spends cents and makes nothing: a topic whose bundle failed stays failed
    (job / FAILED).unlink(missing_ok=True)
    if not (job / "request.json").exists():
        write(job / "request.json", topic.as_request())
    try:
        if not (job / "bundle.json").exists():
            for stale in (JUDGE, "judge.invalid.json"):  # a new bundle is judged afresh, never by an older one's scores
                (job / stale).unlink(missing_ok=True)
            with ledger.at_step("bundle"):
                bundle_step.run(job, ask=ask)
        if not (job / JUDGE).exists():
            with ledger.at_step("judge"):
                judge(job, ask)
    except Exception as e:  # one topic's failure is part of its score, never the end of the run
        step = "judge" if (job / "bundle.json").exists() else "bundle"
        write(job / FAILED, {"step": step, "error": f"{type(e).__name__}: {e}"})


def judge(job: Path, ask) -> Judgement:
    """The judge reads the droplet as it was written, against the rubric."""
    bundle = checks.load(job / "bundle.json")
    request = read(job / "request.json", Request)
    try:
        answer = ask("judge", droplet_text(bundle, request), Judgement, system=RUBRIC, label="judge")
    except brain.InvalidOutput as e:
        (job / "judge.invalid.json").write_text(e.raw, encoding="utf-8")  # kept for a look
        raise
    (job / "judge.invalid.json").unlink(missing_ok=True)
    judgement = Judgement.model_validate(answer)
    write(job / JUDGE, judgement)
    return judgement


def droplet_text(bundle: Bundle, request: Request) -> str:
    """The droplet as the judge reads it: what it is for, its essence, and the lesson, song and narration whole."""
    lines = {line.id: line.text for line in [*bundle.lyrics, *bundle.narration]}
    parts = [f"Topic: {bundle.topic}", f"Course: {bundle.course}", f"Audience: {bundle.audience}"]
    if request.request:
        parts.append(f"The learner's request, in their words: {request.request}")
    parts += ["", "## Core idea", bundle.core_idea, "", "## Why chain",
              *[f"{i}. {link}" for i, link in enumerate(bundle.why_chain, 1)], "",
              "## Key terms", ", ".join(bundle.key_terms) or "(none)", "",
              "## Misconception", bundle.misconception, "", "## Facts",
              *[f"{fact.id}: {fact.text}" for fact in bundle.facts], "",
              "## Lesson (the text the learner reads)", bundle.lesson_markdown.strip(), "",
              f"## Song: {', '.join(bundle.music.positive_styles)}; {bundle.music.bpm} bpm"]
    section = None
    for lyric in bundle.lyrics:
        if lyric.section != section:
            section = lyric.section
            parts.append(f"[{section}]")
        parts.append(f"{lyric.id}: {lyric.text}")
    parts += ["", "## Key lines (on screen, in the why chain's order, each from the line it starts on)"]
    for key in bundle.key_lines:
        where = f'from lyric {key.from_line} "{lines.get(key.from_line, "?")}"' if key.from_line else "not in the song"
        if key.from_narration:
            where += f'; from narration {key.from_narration} "{lines.get(key.from_narration, "?")}"'
        parts.append(f'- "{key.text}" ({where})')
    if not bundle.key_lines:
        parts.append("(none)")
    if bundle.meanings:  # a droplet with no graph has none, and prints as it did before them
        parts += ["", "## Meanings (each written under a graph part's name once it is named)",
                  *[f"- {m.structure}: {m.text}" for m in bundle.meanings]]
    parts += ["", "## Narration (the lesson video's voice-over)", *[f"{n.id}: {n.text}" for n in bundle.narration]]
    if request.study_guide:
        parts += ["", "## Study guide the learner gave", request.study_guide.strip()]
    return "<droplet>\n" + "\n".join(parts) + "\n</droplet>"


def spent(folder: Path) -> float:
    """What the run's calls have cost so far, in dollars."""
    return sum(row.get("cost_usd") or 0 for job in folder.iterdir() if job.is_dir() for row in ledger.read(job))


def _done(job: Path) -> bool:
    return (job / JUDGE).exists()


def provenance() -> dict:
    """What the run ran on: the commit, anything uncommitted under studio/ (the run's own folder aside), the
    prompt's, rubric's and topics' hashes, the brain and the judge."""
    root = HERE.parent
    scope = ["--", "studio", ":(exclude)studio/scorecards/runs"]

    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return ""

    commit = git("rev-parse", "HEAD").strip()
    dirty = bool(git("status", "--porcelain", *scope).strip())
    if brain.provider() == "claude":
        model, effort = brain.claude_brain()
    else:
        model, effort = brain.brain_model(), ""
    return {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "commit": commit, "dirty": dirty,
            "diff": git("diff", "HEAD", *scope) if dirty else "",
            "prompt": digest(bundle_step.PROMPT), "rubric": digest(RUBRIC),
            "topics": digest("".join(path.read_text(encoding="utf-8")
                                     for path in [TOPICS, *sorted((TOPICS.parent / "guides").iterdir())])),
            "brain": brain.current()["name"], "model": model, "effort": effort,
            "judge": f"{brain.CLAUDE['judge']['model']} at {brain.CLAUDE['judge']['effort']}"}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def run_name(about: dict) -> str:
    return time.strftime("%Y%m%d-%H%M") + "-" + (about["commit"][:7] or "nogit") + ("-dirty" if about["dirty"] else "")


# --- the scores ------------------------------------------------------------------------------------


def topic_row(topic: Topic, job: Path) -> dict:
    """One topic's scores, from the files its job wrote."""
    row: dict = {"id": topic.id, "subject": topic.subject, "level": topic.level, "topic": topic.topic,
                 "course": topic.course, "made": (job / "bundle.json").exists(), "failed": None, "checks": None,
                 "rubric": None, "errors": None}
    if (job / FAILED).exists():
        row["failed"] = json.loads((job / FAILED).read_text(encoding="utf-8"))
    elif not row["made"]:
        row["failed"] = {"step": "bundle", "error": "not run: the run spent its budget first"}
    try:
        bundle = checks.load(job / "bundle.json") if row["made"] else None
    except ValueError as e:  # written by an older studio: the run's other topics are still scored
        row.update(made=False, failed={"step": "bundle", "error": f"bundle.json does not read: {e}"})
        bundle = None
    if bundle is not None:
        choruses = [line for line in bundle.lyrics if line.section == "chorus"]
        row.update(audience=bundle.audience, core_idea=bundle.core_idea, why_chain=bundle.why_chain,
                   title_line=(choruses or bundle.lyrics)[0].text, bpm=bundle.music.bpm)
        try:
            problems = read(job / "checks.json", CheckReport).problems if (job / "checks.json").exists() else None
        except ValueError:  # a rule an older studio had: the checks go unscored, the rest of the row stands
            problems = None
        if problems is not None:
            left = [p for p in problems if not p.repaired]
            row["checks"] = {"left": len(left), "repaired": len(problems) - len(left),
                             "by_rule": dict(Counter(p.rule for p in left)),
                             "problems": [f"{p.line_id} {p.rule}: {p.detail}" for p in left]}
    if (job / JUDGE).exists():
        try:
            judgement = read(job / JUDGE, Judgement)
        except ValueError:  # an older judgement's shape: --rejudge gives it this one's
            row["failed"] = {"step": "judge", "error": "judge.json is in an older shape: judge it again (--rejudge)"}
        else:
            row["rubric"] = {key: getattr(judgement, key).model_dump() for key in CRITERIA}
            row["errors"] = [error.model_dump() for error in judgement.errors]
    calls = ledger.read(job)
    row["cost"] = {step: round(sum(r.get("cost_usd") or 0 for r in calls if ledger.step_of(r) == step), 4)
                   for step in ("bundle", "judge")}
    row["cost"]["unpriced"] = any(r.get("cost_usd") is None for r in calls)
    row["seconds"] = round(sum(r.get("seconds") or 0 for r in calls), 1)
    return row


def summarize(rows: list[dict]) -> dict:
    judged = [r for r in rows if r["rubric"]]
    checked = [r for r in rows if r["checks"]]
    rubric = {}
    for key in CRITERIA:
        scores = [r["rubric"][key]["score"] for r in judged]
        rubric[key] = ({"mean": round(statistics.mean(scores), 2), "lowest": min(scores),
                        "low": sum(s <= 2 for s in scores)} if scores else None)
    rules = Counter()
    for r in checked:
        rules.update(r["checks"]["by_rule"])
    return {
        "topics": len(rows), "made": sum(r["made"] for r in rows), "judged": len(judged),
        "failed": {r["id"]: r["failed"]["step"] for r in rows if r["failed"]},
        "rubric": rubric,
        "checks": {"left": sum(r["checks"]["left"] for r in checked),
                   "repaired": sum(r["checks"]["repaired"] for r in checked),
                   "per_droplet": round(statistics.mean(r["checks"]["left"] for r in checked), 1) if checked else None,
                   "by_rule": dict(rules.most_common())},
        "errors": sum(len(r["errors"]) for r in judged),
        "cost": {"bundle": round(sum(r["cost"]["bundle"] for r in rows), 2),
                 "judge": round(sum(r["cost"]["judge"] for r in rows), 2),
                 "unpriced": any(r["cost"]["unpriced"] for r in rows)},
    }


# --- the tables ------------------------------------------------------------------------------------


def markdown(card: dict) -> str:
    about, rows, s = card["about"], card["topics"], card["summary"]
    cost = s["cost"]["bundle"] + s["cost"]["judge"]
    out = [f"# Scorecard {card['about'].get('name', '')}".rstrip(), "",
           f"Commit `{about['commit'][:7]}`" + (" with uncommitted changes (prompt.diff)" if about["dirty"] else "")
           + "".join(f"; resumed on `{c}`" for c in dict.fromkeys(  # the re-judge, a resume too, is said below
               r["commit"][:7] for r in about.get("resumed", []) if not r.get("rejudge")))
           + (f"; judged again on `{about['rejudged']['commit'][:7]}`" if about.get("rejudged") else "")
           + f"; brain {about['brain']} ({about['model']}" + (f" at {about['effort']}" if about["effort"] else "")
           + f"); judge {about['judge']}; bundle prompt `{about['prompt']}`, rubric `{about['rubric']}`, topics "
           f"`{about['topics']}`. {s['topics']} topics, ${cost:.2f}" + ("+" if s["cost"]["unpriced"] else "")
           + (f", {about['minutes']} min" if about.get("minutes") is not None else "") + ".", "",
           "## Summary", "", "| | |", "|---|---|",
           f"| Droplets made | {s['made']} of {s['topics']}"
           + (f"; failed: {', '.join(f'{k} ({v})' for k, v in s['failed'].items())}" if s["failed"] else "") + " |"]
    c = s["checks"]
    if c["per_droplet"] is not None:
        out.append(f"| Checks left for the prompt | {c['left']} ({c['per_droplet']} a droplet); "
                   f"{c['repaired']} repaired by code |")
        out.append("| ... by rule | " + (", ".join(f"{rule} {n}" for rule, n in c["by_rule"].items()) or "none") + " |")
    out.append(f"| Errors the judge found | {s['errors']} in {s['judged']} droplets |")
    out.append(f"| Cost | bundle step ${s['cost']['bundle']:.2f}, judge ${s['cost']['judge']:.2f} |")
    out += ["", "| Criterion | Mean | Lowest | At 2 or below |", "|---|---|---|---|"]
    for key, name in CRITERIA.items():
        r = s["rubric"][key]
        out.append(f"| {name} | {r['mean']:.2f} | {r['lowest']} | {r['low']} |" if r else f"| {name} | - | - | - |")
    out += ["", "## By topic", "",
            "| Topic | Level | Checks | " + " | ".join(CRITERIA.values()) + " | Cost |",
            "|---|---|---|" + "---|" * len(CRITERIA) + "---|"]
    for r in rows:
        scores = [str(r["rubric"][key]["score"]) if r["rubric"] else "-" for key in CRITERIA]
        out.append(f"| {r['id']} | {r['level']} | {checks_cell(r)} | " + " | ".join(scores)
                   + f" | ${r['cost']['bundle'] + r['cost']['judge']:.2f} |")
    out += ["", "## Why each score", ""]
    for r in rows:
        out += topic_section(r)
    return "\n".join(out).rstrip() + "\n"


def checks_cell(row: dict) -> str:
    if not row["checks"]:
        return "-"
    return f"{row['checks']['left']} ({row['checks']['repaired']} fixed)"


def topic_section(r: dict) -> list[str]:
    out = [f"### {r['id']}: {r['topic']} ({r['course']}, {r['level']})", ""]
    if r["failed"]:
        out += [f"Failed at the {r['failed']['step']}: {r['failed']['error']}", ""]
    if r["made"]:
        out += [f"Audience {r['audience']}; {r['bpm']} bpm. Core idea: {r['core_idea']}",
                f'Title line: "{r["title_line"]}"', ""]
    if r["rubric"]:
        for key, name in CRITERIA.items():
            out.append(f"- **{name} {r['rubric'][key]['score']}.** {r['rubric'][key]['reason']}")
        out += [f"- Error: {e['claim']}: {e['problem']} Correction: {e['correction']}" for e in r["errors"]]
    if r["checks"] and r["checks"]["problems"]:
        out.append("- Checks left for the prompt:")
        out += [f"  - {p}" for p in r["checks"]["problems"]]
    return out + [""]


# --- two runs side by side -------------------------------------------------------------------------


def compare(a: dict, b: dict) -> str:
    """Two scorecards side by side, on the topics both scored. Says first when the two are not comparable."""
    ids = [r["id"] for r in a["topics"] if r["id"] in {x["id"] for x in b["topics"]}]
    rows_a = {r["id"]: r for r in a["topics"]}
    rows_b = {r["id"]: r for r in b["topics"]}
    sa = summarize([rows_a[i] for i in ids])
    sb = summarize([rows_b[i] for i in ids])
    aa, ab = a["about"], b["about"]
    out = [f"# Scorecards: {aa.get('name', 'A')} vs {ab.get('name', 'B')}", "", "| | A | B |", "|---|---|---|"]
    for key, name in (("commit", "Commit"), ("prompt", "Bundle prompt"), ("brain", "Brain"), ("effort", "Effort"),
                      ("judge", "Judge"), ("rubric", "Rubric"), ("topics", "Topics")):
        va, vb = str(aa.get(key, "")), str(ab.get(key, ""))
        if key == "commit":
            va, vb = va[:7] + (" (dirty)" if aa["dirty"] else ""), vb[:7] + (" (dirty)" if ab["dirty"] else "")
        out.append(f"| {name} | {va} | {vb}" + (" (changed)" if va != vb else "") + " |")
    differ = [name for key, name in (("judge", "judge"), ("rubric", "rubric")) if aa.get(key) != ab.get(key)]
    if differ:
        out += ["", f"**Not comparable: the {' and '.join(differ)} changed, so a score moves for that reason too.**"]
    out += ["", f"## Summary, on the {len(ids)} topics both ran", "", "| | A | B | Change |", "|---|---|---|---|"]
    for key, name in CRITERIA.items():
        ra, rb = sa["rubric"][key], sb["rubric"][key]
        if ra and rb:
            out.append(f"| {name} (mean) | {ra['mean']:.2f} | {rb['mean']:.2f} | {rb['mean'] - ra['mean']:+.2f} |")
    for label, va, vb in (
        ("Droplets made", sa["made"], sb["made"]),
        ("Checks left a droplet", sa["checks"]["per_droplet"], sb["checks"]["per_droplet"]),
        ("Errors the judge found", sa["errors"], sb["errors"]),
        ("Cost ($)", round(sa["cost"]["bundle"] + sa["cost"]["judge"], 2),
         round(sb["cost"]["bundle"] + sb["cost"]["judge"], 2)),
    ):
        change = f"{vb - va:+g}" if va is not None and vb is not None else ""
        out.append(f"| {label} | {va} | {vb} | {change} |")
    rules = sorted(set(sa["checks"]["by_rule"]) | set(sb["checks"]["by_rule"]))
    if rules:
        out.append("| Checks by rule | " + ", ".join(f"{r} {sa['checks']['by_rule'].get(r, 0)}" for r in rules)
                   + " | " + ", ".join(f"{r} {sb['checks']['by_rule'].get(r, 0)}" for r in rules) + " | |")
    out += ["", "## By topic (A → B)", "",
            "| Topic | Checks | " + " | ".join(CRITERIA.values()) + " |",
            "|---|---|" + "---|" * len(CRITERIA)]
    for i in ids:
        ra, rb = rows_a[i], rows_b[i]
        cells = [_pair(checks_cell(ra), checks_cell(rb))]
        cells += [_pair(str(ra["rubric"][k]["score"]) if ra["rubric"] else "-",
                        str(rb["rubric"][k]["score"]) if rb["rubric"] else "-") for k in CRITERIA]
        out.append(f"| {i} | " + " | ".join(cells) + " |")
    out += ["", "One droplet a topic is one sample: a mean that moves by less than about half a point, or one topic "
                "by one point, can be chance. Two runs of the same commit show how much."]
    return "\n".join(out) + "\n"


def _pair(a: str, b: str) -> str:
    return a if a == b else f"{a} → {b}"


# --- the command -----------------------------------------------------------------------------------


def main(args) -> int:
    """python -m studio scorecard run|compare (studio/__main__.py)."""
    if args.action == "compare":
        missing = [name for name in args.runs if not (_run_folder(name) / "scorecard.json").exists()]
        if missing:
            print(f"scorecard: no run {', '.join(missing)} in {RUNS}", file=sys.stderr)
            return 2
        cards = [json.loads((_run_folder(name) / "scorecard.json").read_text(encoding="utf-8")) for name in args.runs]
        text = compare(*cards)
        print(text)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        return 0
    if media.lesson_only():
        # A bundle for the lesson video alone has no song, and the rubric judges one: refused before anything is paid.
        print("scorecard: STUDIO_VIDEOS is lesson, which writes no song, and the rubric judges the song; run it with "
              "STUDIO_VIDEOS unset or on", file=sys.stderr)
        return 2
    topics = load_topics()
    if args.resume and args.topics:
        print("scorecard: a resumed run keeps the topics it started with", file=sys.stderr)
        return 2
    if args.rejudge and not args.resume:
        print("scorecard: --rejudge judges a run again: name it with --resume", file=sys.stderr)
        return 2
    if args.topics:
        wanted = [t.strip() for t in args.topics.split(",") if t.strip()]
        unknown = sorted(set(wanted) - {t.id for t in topics})
        if unknown:
            print(f"no such topic: {', '.join(unknown)} (studio/scorecards/topics.json)", file=sys.stderr)
            return 2
        topics = [t for t in topics if t.id in wanted]
    with brain.chosen(args.brain, args.effort):
        problem = brain.settings_problem() or (None if brain.claude_key() else
                                               f"the judge needs {' or '.join(brain.CLAUDE_KEY_VARS)}")
        if problem:
            print(f"scorecard: {problem}", file=sys.stderr)
            return 2
        if args.resume:
            folder = _run_folder(args.resume)
            if not (folder / "scorecard.json").exists():
                print(f"scorecard: no run {args.resume} in {RUNS}", file=sys.stderr)
                return 2
            about = json.loads((folder / "scorecard.json").read_text(encoding="utf-8"))["about"]
            now = provenance()
            keys = ("prompt", "topics", "brain", "model", "effort") + (() if args.rejudge else ("rubric", "judge"))
            changed = [key for key in keys if about[key] != now[key]]
            if changed:
                print(f"scorecard: {', '.join(changed)} changed since {args.resume} started; start a new run",
                      file=sys.stderr)
                return 2
            topics = [t for t in topics if t.id in about["topic_ids"]]
            # Each go at the run on record: a bundle made after a restart was made on the commit checked out then.
            went = {"commit": now["commit"], "dirty": now["dirty"], "at": now["started"]}
            went.update({"rejudge": True} if args.rejudge else {})
            about["resumed"] = [*about.get("resumed", []), went]
            if args.rejudge:  # every droplet judged again, by the rubric and judge in force now
                for t in topics:
                    if (folder / t.id / JUDGE).exists():
                        (folder / t.id / JUDGE).replace(folder / t.id / "judge.previous.json")
                about.update(rubric=now["rubric"], judge=now["judge"], rejudged=went)
            card = json.loads((folder / "scorecard.json").read_text(encoding="utf-8"))
            write(folder / "scorecard.json", {**card, "about": about})
        else:
            about = provenance()
            about.update(name=run_name(about), topic_ids=[t.id for t in topics])
            folder = RUNS / about["name"]
            if folder.exists():  # another run started this minute on this commit: never two in one folder
                print(f"scorecard: {folder} is another run's; start this one in a minute, or --resume that one",
                      file=sys.stderr)
                return 2
        print(f"scorecard {folder.name}: {len(topics)} topic(s), up to ${args.budget:.2f}", file=sys.stderr)
        card = run(topics, folder, about=about, workers=args.workers, budget=args.budget, judge_only=args.rejudge)
    print(f"{folder / 'scorecard.md'}\n")
    print(markdown(card))
    return 0 if not card["summary"]["failed"] else 1


def _run_folder(name: str) -> Path:
    path = Path(name)
    return path if path.is_dir() else RUNS / name
