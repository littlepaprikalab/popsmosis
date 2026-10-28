"""Run the studio.

    python -m studio run <question-id>           # a topic from the queue, to a posted lesson
    python -m studio fail <question-id>          # give it up with its last run's reason
    python -m studio summary <question-id>       # what its runs did, for the operator's record
    python -m studio videos <question-id>        # a posted lesson's videos; --background on a routine
    python -m studio wait <question-id>          # the background videos' outcome, waiting up to 9 minutes
    python -m studio recover                     # take a render that died with its machine, rebuild its job

    python -m studio plan --topic "brain structures" --course "AP Psych"   # a local job, never posted
    python -m studio step factcheck <job-id>     # re-run one step: bundle, check, factcheck
    python -m studio step render <job-id>        # or a media step, once it is built (studio/media.py)
    python -m studio show <job-id> [--brief]     # facts, scenes, lyrics, checks, fact-check, timing, runs, calls
    python -m studio fallback <job-id>           # the ffmpeg karaoke video, once the song is timed

`run`, `fail`, `summary`, `videos`, `wait` and `recover` are the operator's
(studio/run.py, studio/videos.py); they need POPSMOSIS_API_URL and POPSMOSIS_API_KEY,
and use the question id as the job id.

Jobs live in jobs/<id>/ (STUDIO_JOBS_DIR overrides it). Every model call is logged
to the job's calls.jsonl with its step, tokens, cost and time (studio/ledger.py), and
`show` ends with each step's time, cost and models, as the droplet's page shows them.

The brain is set by an admin, never by the environment, which holds only the keys: the
default brain and Claude's effort on /admin/studio, or a topic's own on /learn. `run` reads
them from the API and keeps the topic's in the job's brain.json, and `videos` and `step` use
it (studio/run.py). A local job takes `plan --brain claude|muse-wonder|muse-spark` and
`--effort` (default Claude Opus 5.5 at high); muse-spark vets a prompt change for about a
quarter of Muse Wonder's price.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

from studio import ass_fallback, brain, checks, jobs, ledger, media
from studio import run as operator_run
from studio import videos as operator_videos
from studio.ledger import call_row
from studio.run import logged_ask
from studio.schemas import CheckReport, FactCheckReport, Request, Timing
from studio.steps import bundle as bundle_step
from studio.steps import factcheck as factcheck_step
from studio.steps import timing as timing_step

STEPS = {"bundle": bundle_step.run, "check": checks.run, "factcheck": factcheck_step.run}
# STARTED: the videos went to the background; RUNNING: `wait` ran out of time; NONE: nothing to recover.
EXIT_CODES = {**operator_run.EXIT_CODES, operator_videos.STARTED: 0, operator_videos.RECOVER: 0,
              operator_videos.NONE: 0, operator_videos.RUNNING: 4}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m studio", description="Run the popsmosis studio.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("run", "a topic from the queue, to a posted lesson; resumes at the first missing step"),
                            ("fail", "give a topic up with its last run's reason"),
                            ("summary", "what a topic's runs did, in one paragraph")):
        commands.add_parser(name, help=help_text).add_argument("question")
    make = commands.add_parser("videos", help="a posted lesson's videos; resumes at the first media step not done")
    make.add_argument("question")
    make.add_argument("--background", action="store_true", help="claim, start the run detached, and return at once")
    waiting = commands.add_parser("wait", help="the background videos' outcome line, or RUNNING after --minutes")
    waiting.add_argument("question")
    waiting.add_argument("--minutes", type=float, default=operator_videos.WAIT_MINUTES)
    commands.add_parser("recover", help="take one render that died with its machine and rebuild its job here")
    plan = commands.add_parser("plan", help="start a job: the bundle, then the fact-check")
    plan.add_argument("--topic", required=True)
    plan.add_argument("--course", default="")
    plan.add_argument("--request", default="", help="the learner's request in their own words")
    plan.add_argument("--style", default="", help="the song style the learner asked for, in their words")
    plan.add_argument("--study-guide", type=Path, help="a text file of course material")
    plan.add_argument("--image", type=Path, action="append", default=[], help="a photo of notes; repeatable")
    plan.add_argument("--job", help="the job id (default: made from the topic and the time)")
    plan.add_argument("--brain", choices=list(brain.CHOICES),
                      help=f"the brain, as an admin picks it on /learn (default: {brain.DEFAULT_BRAIN})")
    plan.add_argument("--effort", choices=list(brain.EFFORTS), help=f"Claude's effort (default: {brain.DEFAULT_EFFORT})")
    step = commands.add_parser("step", help="re-run one step of an existing job")
    step.add_argument("name", choices=[*STEPS, *media.MEDIA_STEPS])
    step.add_argument("job")
    show = commands.add_parser("show", help="print a job's facts, scenes, lyrics, checks, fact-check, runs and calls")
    show.add_argument("job")
    fallback = commands.add_parser("fallback", help="make the karaoke fallback video from the art, the song and its timing")
    fallback.add_argument("job")
    for command in (plan, step, show):
        command.add_argument("--brief", action="store_true", help="leave out the line-by-line fact-check table")
    args = parser.parse_args(argv)
    retired = brain.retired_note()
    if retired and args.command in ("run", "videos", "recover", "plan", "step"):
        print(retired, file=sys.stderr)

    if args.command == "recover":
        result, question, reason = operator_videos.recover()
        print(f"{result} {question}: {reason}")
        return EXIT_CODES[result]
    if args.command in ("videos", "wait"):
        if not operator_run.ID_RE.fullmatch(args.question):
            parser.error(f"{args.question!r} is not a question id")
        if args.command == "wait":
            result, step, reason = operator_videos.wait(args.question, minutes=args.minutes)
        elif args.background:
            result, step, reason = operator_videos.background(args.question)
        else:
            result, step, reason = operator_videos.videos(args.question)
        print(f"{result} {step}: {reason}")
        return EXIT_CODES[result]
    if args.command in ("run", "fail", "summary"):
        if not operator_run.ID_RE.fullmatch(args.question):
            parser.error(f"{args.question!r} is not a question id")
        if args.command == "summary":
            print(operator_run.summary(args.question))
            return 0
        if args.command == "fail":
            result, reason = operator_run.mark_failed(args.question)
            print(f"{result} fail: {reason}")
            return EXIT_CODES[result]
        result, step, reason = operator_run.run(args.question)
        print(f"{result} {step}: {reason}")
        return EXIT_CODES[result]

    if args.command == "fallback":
        job = jobs.ROOT / args.job
        if not job.is_dir():
            parser.error(f"no job folder {job}")
        try:
            print(ass_fallback.make(job))
        except (OSError, ValueError, media.StepFailed) as e:
            print(f"fallback: failed: {e}", file=sys.stderr)
            return 1
        return 0

    if args.command == "plan":
        job = jobs.ROOT / (args.job or jobs.new_id(args.topic))
        job.mkdir(parents=True, exist_ok=False)
        request = Request(
            topic=args.topic,
            course=args.course,
            request=args.request,
            song_style=args.style,
            study_guide=args.study_guide.read_text(encoding="utf-8") if args.study_guide else "",
            images=[str(path.resolve()) for path in args.image],
        )
        jobs.write(job / "request.json", request)
        if args.brain or args.effort:
            operator_run.keep_brain(job, args.brain, args.effort)
        print(f"job {job.name}", file=sys.stderr)
        with brain.chosen(args.brain, args.effort):
            ok = run_steps(job, ["bundle", "factcheck"])
    else:
        job = jobs.ROOT / args.job
        if not job.is_dir():
            parser.error(f"no job folder {job}")
        with operator_run.job_chosen(job):  # the brain the job was made on
            ok = run_steps(job, [args.name]) if args.command == "step" else True
    print_job(job, brief=args.brief)
    return 0 if ok else 1


def run_steps(job: Path, names: list[str]) -> bool:
    for name in names:
        started = time.monotonic()
        print(f"{name}: running", file=sys.stderr)
        try:
            step = media.step_module(name).run if name in media.MEDIA_STEPS else STEPS[name]
        except LookupError as e:  # not built yet
            print(f"{name}: {e}", file=sys.stderr)
            return False
        try:
            with ledger.at_step(name):
                result = step(job) if name == "check" else step(job, ask=logged_ask(job))
        except (brain.BrainError, media.StepFailed) as e:
            print(f"{name}: failed: {e}", file=sys.stderr)
            return False
        took = time.monotonic() - started
        if isinstance(result, str):
            print(f"{name}: {result}", file=sys.stderr)
        if isinstance(result, FactCheckReport) and result.status == "failed":
            print(f"{name}: failed after {took:.0f}s: {result.reason}", file=sys.stderr)
            return False
        print(f"{name}: done in {took:.0f}s", file=sys.stderr)
    return True


def print_job(job: Path, brief: bool = False) -> None:
    request = jobs.read(job / "request.json", Request)
    print(f"\n# {request.topic}" + (f" ({request.course})" if request.course else "") + f"  [{job.name}]")
    if (job / "bundle.json").exists():
        bundle = checks.load(job / "bundle.json")
        print(f"\nAudience: {bundle.audience}\nCore idea: {bundle.core_idea}\n\n## Facts\n")
        for fact in bundle.facts:
            print(_wrap(f"{fact.id}  {fact.text}", indent=4) + "\n")
        print("## Scenes\n")
        for scene in bundle.scenes:
            print(_wrap(f"{scene.id}: " + ", ".join(
                s.name + (f" ({'/'.join(s.aliases)})" if s.aliases else "") for s in scene.structures), indent=4))
        print("\n## Lyrics\n")
        print(_wrap("Style: " + ", ".join(bundle.music.positive_styles), indent=7) + "\n")
        section = None
        for lyric in bundle.lyrics:
            if lyric.section != section:
                section = lyric.section
                print(f"[{section}]")
            names = f"  [{', '.join(lyric.names)}]" if lyric.names else ""
            print(f"  {lyric.id:<6} {lyric.fact_id:<7} {lyric.text}{names}")
        print("\n## Tags\n")
        for video, tags in (("song", bundle.tags.song), ("lesson", bundle.tags.lesson)):
            print(_wrap(f"{video:<7}" + (" ".join(f"#{t}" for t in tags) or "none"), indent=7))
    if (job / "checks.json").exists():
        problems = jobs.read(job / "checks.json", CheckReport).problems
        left = [p for p in problems if not p.repaired]
        print(f"\n## Checks: {len(problems)} problem(s), {len(problems) - len(left)} repaired, "
              f"{len(left)} for the prompt\n")
        for p in sorted(problems, key=lambda p: p.repaired):
            print(_wrap(f"{'  ' if p.repaired else '! '}{p.line_id:<6} {p.rule:<13} {p.detail}", indent=23))
    if (job / "factcheck.json").exists():
        report = jobs.read(job / "factcheck.json", FactCheckReport)
        said = [line for line in report.lines if line.kind != "tag"]
        counts = {v: sum(line.verdict == v for line in said) for v in ("SUPPORTED", "FILLER", "UNSUPPORTED")}
        print(f"\n## Fact-check: {report.status} after {report.rounds} round(s); "
              + ", ".join(f"{n} {v.lower()}" for v, n in counts.items()) + "\n")
        if report.reason:
            print(_wrap(f"Reason: {report.reason}") + "\n")
        dropped = factcheck_step.dropped_tags(report)
        if dropped:
            print(_wrap("Tags dropped: " + "; ".join(
                f"{t.text} ({t.problem or 'no verdict'})" for t in dropped), indent=4) + "\n")
        if not brief:
            print(f"  {'line':<15} {'verdict':<11} {'source':<9} {'rnd':<3} text")
            for line in report.lines:
                key = f"{line.kind}:{line.id}"
                text = textwrap.shorten(line.text, 80, placeholder="...")
                print(f"  {key:<15} {line.verdict or '-':<11} {textwrap.shorten(line.source, 9, placeholder='~'):<9} "
                      f"{line.round:<3} {text}")
        if report.corrections:
            print("\n## Corrections\n")
            for c in report.corrections:
                print(_wrap(f"round {c.round}, {c.kind} {', '.join(c.ids)}: {c.problem}", indent=2))
                print(_wrap(f"- {c.before}", indent=4, first=4))
                print(_wrap(f"+ {c.after}", indent=4, first=4) + "\n")
    timed = [kind for kind in media.TIMED if (job / media.timing_path(kind)).exists()]
    if timed:
        print("\n## Timing\n")
        for kind in timed:
            timing = jobs.read(job / media.timing_path(kind), Timing)
            print(f"  {kind:<10} {timing.duration:6.1f}s  {len(timing.lines)} lines")
        if (job / timing_step.REPORT).exists():
            report = json.loads((job / timing_step.REPORT).read_text(encoding="utf-8"))
            print(_wrap(f"  song: {timing_step.song_notes(report)}", indent=4))
    runs = operator_run.read_jsonl(job / "runs.jsonl")
    if runs:
        print("\n## Runs\n")
        for i, r in enumerate(runs, 1):
            steps = ", ".join(f"{s['step']} " + (f"{s['seconds']:.0f}s" if s["seconds"] is not None else "cut off")
                              for s in r["steps"]) or "nothing to do"
            print(_wrap(f"{i}. {r['started']}  {r['result']} {r['step']}: {r['reason']}  [{steps}]", indent=4))
    rows = ledger.read(job)
    if rows:
        calls = [ledger.as_call(row) for row in rows]
        print(f"\n## Calls\n\n  {'call':<20} {'model':<17} {'in':>7} {'out':>7} {'cost':>8} {'time':>7}")
        for call in calls:
            print(f"  {call_row(call)}")
        print(f"  {'total':<20} {'':<17} {sum(c.input_tokens for c in calls):>7} "
              f"{sum(c.output_tokens for c in calls):>7} {ledger.cost_text(rows):>8} {sum(c.seconds for c in calls):>6.1f}s")
    for part in ledger.PARTS:
        report = ledger.try_breakdown(job, part)
        if not report or not report["steps"]:
            continue
        took = f"{report['seconds'] / 60:.1f} min" if report["seconds"] is not None else "time n/a"
        print(f"\n## Breakdown: the {part}, {took}\n")
        for row in report["steps"]:
            seconds = f"{row['seconds']:.0f}s" if row["seconds"] is not None else "cut off"
            tries = f" ({row['tries']} tries)" if row["tries"] > 1 else ""
            models = ", ".join(f"{m['model']} x{m['calls']}" for m in row["models"]) or "code"
            cost = f"${row['costUsd']:.4f}" + ("+" if any(m["costUsd"] is None for m in row["models"]) else "")
            tools = ", ".join(f"{t['name']} {t['version'] or '(version n/a)'}" for t in row["tools"])
            print(f"  {row['step']:<10} {seconds:>6}{tries:<10} {cost:<10} {models}" + (f"; with {tools}" if tools else ""))


def _wrap(text: str, indent: int = 0, first: int = 0) -> str:
    return textwrap.fill(text, 100, initial_indent=" " * first, subsequent_indent=" " * indent)


if __name__ == "__main__":
    sys.exit(main())
