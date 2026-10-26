"""Run the studio locally.

    python -m studio plan --topic "brain structures" --course "AP Psych"
    python -m studio step factcheck <job-id>     # re-run one step: bundle, check, factcheck
    python -m studio show <job-id> [--brief]     # facts, scenes, lyrics, checks, fact-check, calls

Jobs live in jobs/<id>/ (STUDIO_JOBS_DIR overrides it). Every brain call is logged
to the job's calls.jsonl with its tokens, cost and time.

To try a prompt draft cheaply, run the brain on Haiku first:
STUDIO_BRAIN_MODEL=claude-haiku-5-5 python -m studio plan ... --brief
(STUDIO_BRAIN_EFFORT overrides the brain's effort the same way.)
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from dataclasses import asdict
from pathlib import Path

from studio import brain, checks, jobs
from studio.schemas import Bundle, CheckReport, FactCheckReport, Request
from studio.steps import bundle as bundle_step
from studio.steps import factcheck as factcheck_step

STEPS = {"bundle": bundle_step.run, "check": checks.run, "factcheck": factcheck_step.run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m studio", description="Run the popsmosis studio locally.")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="start a job: the bundle, then the fact-check")
    plan.add_argument("--topic", required=True)
    plan.add_argument("--course", default="")
    plan.add_argument("--request", default="", help="the learner's request in their own words")
    plan.add_argument("--study-guide", type=Path, help="a text file of course material")
    plan.add_argument("--image", type=Path, action="append", default=[], help="a photo of notes; repeatable")
    plan.add_argument("--job", help="the job id (default: made from the topic and the time)")
    step = commands.add_parser("step", help="re-run one step of an existing job")
    step.add_argument("name", choices=list(STEPS))
    step.add_argument("job")
    show = commands.add_parser("show", help="print a job's facts, scenes, lyrics, checks, fact-check and calls")
    show.add_argument("job")
    for command in (plan, step, show):
        command.add_argument("--brief", action="store_true", help="leave out the line-by-line fact-check table")
    args = parser.parse_args(argv)

    if args.command == "plan":
        job = jobs.ROOT / (args.job or jobs.new_id(args.topic))
        job.mkdir(parents=True, exist_ok=False)
        request = Request(
            topic=args.topic,
            course=args.course,
            request=args.request,
            study_guide=args.study_guide.read_text(encoding="utf-8") if args.study_guide else "",
            images=[str(path.resolve()) for path in args.image],
        )
        jobs.write(job / "request.json", request)
        print(f"job {job.name}", file=sys.stderr)
        ok = run_steps(job, ["bundle", "factcheck"])
    else:
        job = jobs.ROOT / args.job
        if not job.is_dir():
            parser.error(f"no job folder {job}")
        ok = run_steps(job, [args.name]) if args.command == "step" else True
    print_job(job, brief=args.brief)
    return 0 if ok else 1


def run_steps(job: Path, names: list[str]) -> bool:
    for name in names:
        started = time.monotonic()
        print(f"{name}: running", file=sys.stderr)
        try:
            result = STEPS[name](job) if name == "check" else STEPS[name](job, ask=_logged_ask(job))
        except brain.BrainError as e:
            print(f"{name}: failed: {e}", file=sys.stderr)
            return False
        took = time.monotonic() - started
        if isinstance(result, FactCheckReport) and result.status == "failed":
            print(f"{name}: failed after {took:.0f}s: {result.reason}", file=sys.stderr)
            return False
        print(f"{name}: done in {took:.0f}s", file=sys.stderr)
    return True


def _logged_ask(job: Path):
    """brain.ask, logging each call to the job's calls.jsonl and stderr as it ends."""

    def ask(*args, **kwargs):
        before = len(brain.calls)
        try:
            return brain.ask(*args, **kwargs)
        finally:
            with (job / "calls.jsonl").open("a", encoding="utf-8") as log:
                for call in brain.calls[before:]:
                    log.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **asdict(call)}) + "\n")
                    print(f"  {_call_row(call)}", file=sys.stderr)

    return ask


def print_job(job: Path, brief: bool = False) -> None:
    request = jobs.read(job / "request.json", Request)
    print(f"\n# {request.topic}" + (f" ({request.course})" if request.course else "") + f"  [{job.name}]")
    if (job / "bundle.json").exists():
        data = json.loads((job / "bundle.json").read_text(encoding="utf-8"))
        bundle = Bundle.model_validate(checks.fill_names(data))
        print(f"\nCore idea: {bundle.core_idea}\n\n## Facts\n")
        for fact in bundle.facts:
            print(_wrap(f"{fact.id}  {fact.text}", indent=4) + "\n")
        print("## Scenes\n")
        for scene in bundle.scenes:
            print(_wrap(f"{scene.id}: " + ", ".join(
                s.name + (f" ({'/'.join(s.aliases)})" if s.aliases else "") for s in scene.structures), indent=4))
        print("\n## Lyrics\n")
        section = None
        for lyric in bundle.lyrics:
            if lyric.section != section:
                section = lyric.section
                print(f"[{section}]")
            names = f"  [{', '.join(lyric.names)}]" if lyric.names else ""
            print(f"  {lyric.id:<6} {lyric.fact_id:<7} {lyric.text}{names}")
    if (job / "checks.json").exists():
        problems = jobs.read(job / "checks.json", CheckReport).problems
        left = [p for p in problems if not p.repaired]
        print(f"\n## Checks: {len(problems)} problem(s), {len(problems) - len(left)} repaired, "
              f"{len(left)} for the prompt\n")
        for p in sorted(problems, key=lambda p: p.repaired):
            print(_wrap(f"{'  ' if p.repaired else '! '}{p.line_id:<6} {p.rule:<13} {p.detail}", indent=23))
    if (job / "factcheck.json").exists():
        report = jobs.read(job / "factcheck.json", FactCheckReport)
        counts = {v: sum(line.verdict == v for line in report.lines) for v in ("SUPPORTED", "FILLER", "UNSUPPORTED")}
        print(f"\n## Fact-check: {report.status} after {report.rounds} round(s); "
              + ", ".join(f"{n} {v.lower()}" for v, n in counts.items()) + "\n")
        if report.reason:
            print(_wrap(f"Reason: {report.reason}") + "\n")
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
    if (job / "calls.jsonl").exists():
        calls = [brain.Call(**{k: v for k, v in json.loads(row).items() if k != "at"})
                 for row in (job / "calls.jsonl").read_text(encoding="utf-8").splitlines() if row.strip()]
        print(f"\n## Calls\n\n  {'call':<20} {'model':<17} {'in':>7} {'out':>7} {'cost':>8} {'time':>7}")
        for call in calls:
            print(f"  {_call_row(call)}")
        costs = [c.cost_usd for c in calls]
        total = f"${sum(costs):.4f}" if None not in costs else "n/a"
        print(f"  {'total':<20} {'':<17} {sum(c.input_tokens for c in calls):>7} "
              f"{sum(c.output_tokens for c in calls):>7} {total:>8} {sum(c.seconds for c in calls):>6.1f}s")


def _call_row(call: brain.Call) -> str:
    cost = f"${call.cost_usd:.4f}" if call.cost_usd is not None else "n/a"
    flags = "".join([" fell back" if call.fell_back else "",
                     f" ({call.stop_reason})" if call.stop_reason not in ("end_turn", "stop") else ""])
    return (f"{call.label:<20} {call.model:<17} {call.input_tokens:>7} {call.output_tokens:>7} {cost:>8} "
            f"{call.seconds:>6.1f}s{flags}")


def _wrap(text: str, indent: int = 0, first: int = 0) -> str:
    return textwrap.fill(text, 100, initial_indent=" " * first, subsequent_indent=" " * indent)


if __name__ == "__main__":
    sys.exit(main())
