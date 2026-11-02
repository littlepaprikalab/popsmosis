"""The scorecard run in the session (CLAUDE.md, "The scorecard runs in the session"): the code's part of it.

No model is called here. Subagents answer each call from the prompts and schemas this writes: a writer per topic
(SCORE_WRITER.md) and a judge per topic (SCORE_JUDGE.md). This script builds their prompts, checks their answers
and runs the bundle step's code checks. The learner test and the narration edit are skipped.

    python tools/insession/score.py prep                    every topic's prompts and schemas
    python tools/insession/score.py script-check <topic>    script.answer.json against Script
    python tools/insession/score.py bundle-prompt <topic>   the bundle call's user prompt, from the script held to the guide
    python tools/insession/score.py bundle-check <topic>    bundle.answer.json against Bundle
    python tools/insession/score.py checks <topic>          the code checks, in bundle.run's order: bundle.json, checks.json
    python tools/insession/score.py judge-prep <topic>      the droplet and the baseline's, blind as X and Y, beside the rubric
    python tools/insession/score.py judge-check <topic>     judge.answer.json: {"X": Judgement, "Y": Judgement, "prefer", "why"}
    python tools/insession/score.py summary                 scores, preferences and errors, against the baseline

SCORE_RUN is the run's folder (default jobs/_scorecard, kept out of git with jobs/), a folder per topic.
SCORE_BASELINE is the run judged beside it (default: the latest run in studio/scorecards/runs not ending -random).
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from studio import brain, checks  # noqa: E402
from studio.jobs import read, write  # noqa: E402
from studio.schemas import Bundle, Problem, Request  # noqa: E402
from studio.scorecard import RUBRIC, Judgement, Score, droplet_text, load_topics  # noqa: E402
from studio.steps import bundle as bundle_step, script as script_step  # noqa: E402
from studio.styles import style_for  # noqa: E402

RUN = Path(os.environ.get("SCORE_RUN") or REPO / "jobs" / "_scorecard")
RUNS = REPO / "studio" / "scorecards" / "runs"
CRITERIA = [name for name, field in Judgement.model_fields.items() if field.annotation is Score]


def baseline() -> Path:
    if os.environ.get("SCORE_BASELINE"):
        return Path(os.environ["SCORE_BASELINE"])
    return max(p for p in RUNS.iterdir() if p.is_dir() and not p.name.endswith("-random"))


def folder(topic_id: str) -> Path:
    return RUN / topic_id


def prep() -> None:
    for topic in load_topics():
        d = folder(topic.id)
        d.mkdir(parents=True, exist_ok=True)
        request = topic.as_request()
        write(d / "request.json", request)
        (d / "user.md").write_text(bundle_step.user_prompt(request, style_for(topic.id)), encoding="utf-8")
        (d / "script.system.md").write_text(script_step.system_prompt(bundle_step.PROMPT), encoding="utf-8")
        (d / "script.schema.json").write_text(json.dumps(brain.strict_schema(script_step.Script), indent=1),
                                              encoding="utf-8")
        (d / "bundle.system.md").write_text(bundle_step.PROMPT, encoding="utf-8")
        (d / "bundle.schema.json").write_text(json.dumps(brain.strict_schema(Bundle), indent=1), encoding="utf-8")
        print(topic.id, "images:", len(request.images), "guide:", bool(request.study_guide))
    print(f"in {RUN}; judged beside {baseline().name}")


def _script(d: Path, name: str) -> script_step.Script:
    """The script as the bundle step keeps it: its facts numbered f1, f2, ... unless they are (script.numbered())."""
    return script_step.numbered(script_step.Script.model_validate(json.loads((d / name).read_text(encoding="utf-8"))))


def script_check(t: str) -> None:
    topic = read(folder(t) / "request.json", Request).topic
    problem = script_step._problem(_script(folder(t), "script.answer.json"), topic)
    print("NOT VALID: " + problem if problem else "OK")


def bundle_prompt(t: str) -> None:
    d = folder(t)
    request = read(d / "request.json", Request)
    script = _script(d, "script.answer.json")
    facts, unquoted = checks.held_to_guide(script.facts, request.study_guide)
    script = script.model_copy(update={"facts": facts})
    write(d / "script.held.json", script)
    write(d / "unquoted.json", {"problems": [p.model_dump() for p in unquoted]})
    (d / "bundle.user.md").write_text((d / "user.md").read_text(encoding="utf-8") + "\n\n"
                                      + script_step.as_prompt(script), encoding="utf-8")
    print("written", d / "bundle.user.md")


def bundle_check(t: str) -> None:
    bundle = Bundle.model_validate(json.loads((folder(t) / "bundle.answer.json").read_text(encoding="utf-8")))
    print("OK" if bundle.sung else "NOT VALID: lyrics and narration must not be empty")


def run_checks(t: str) -> None:
    """bundle.run's checks after the bundle call, with no learner test or narration edit: keep in step with it."""
    d = folder(t)
    request = read(d / "request.json", Request)
    script = _script(d, "script.held.json")
    unquoted = [Problem.model_validate(p) for p in json.loads((d / "unquoted.json").read_text())["problems"]]
    bundle = Bundle.model_validate(json.loads((d / "bundle.answer.json").read_text(encoding="utf-8")))
    bundle = script_step.follow(bundle, script, None)
    bundle, report = checks.review(bundle)
    settled, repaired = checks.repair_once(bundle, request.study_guide)
    if settled is not bundle:
        bundle, again = checks.review(settled)
        report.problems += [p for p in again.problems if p not in report.problems]
    report.problems += (unquoted + repaired + checks.song_length_problems(bundle) + checks.motion_problems(bundle)
                        + checks.essence_problems(bundle) + checks.key_line_problems(bundle)
                        + checks.song_shape_problems(bundle) + checks.style_problems(bundle, request.song_style))
    write(d / "bundle.json", bundle)
    write(d / "checks.json", report)
    left = [p for p in report.problems if not p.repaired]
    print(t, "problems:", len(report.problems), "left:", len(left))


def judge_prep(t: str) -> None:
    d, base = folder(t), baseline()
    new = droplet_text(checks.load(d / "bundle.json"), read(d / "request.json", Request))
    old = droplet_text(checks.load(base / t / "bundle.json"), read(base / t / "request.json", Request))
    new_is = random.Random(f"{RUN.name}-{t}").choice("XY")
    x, y = (new, old) if new_is == "X" else (old, new)
    write(d / "judge.key.json", {"new": new_is, "baseline": base.name})
    (d / "judge.system.md").write_text(RUBRIC, encoding="utf-8")
    (d / "judge.user.md").write_text(
        "Two droplets on the same topic, X and Y, each to be judged on its own against the rubric. Then say which "
        "you prefer for a learner meeting the topic for the first time, and why.\n\n"
        f"## Droplet X\n{x}\n\n## Droplet Y\n{y}\n\n"
        'Answer as JSON: {"X": <the rubric\'s judgement of X>, "Y": <of Y>, "prefer": "X" or "Y", "why": "..."}. '
        "Each judgement follows judge.schema.json.", encoding="utf-8")
    (d / "judge.schema.json").write_text(json.dumps(brain.strict_schema(Judgement), indent=1), encoding="utf-8")
    print("written", d / "judge.user.md")


def judge_check(t: str) -> None:
    answer = json.loads((folder(t) / "judge.answer.json").read_text(encoding="utf-8"))
    Judgement.model_validate(answer["X"])
    Judgement.model_validate(answer["Y"])
    if answer.get("prefer") not in ("X", "Y") or not str(answer.get("why", "")).strip():
        print('NOT VALID: "prefer" must be "X" or "Y", and "why" must say why')
        return
    print("OK")


def summary() -> None:
    rows = []
    sums = {"new": dict.fromkeys(CRITERIA, 0.0), "old": dict.fromkeys(CRITERIA, 0.0)}
    prefer_new = errors_new = errors_old = n = 0
    for topic in load_topics():
        d = folder(topic.id)
        if not (d / "judge.answer.json").exists():
            rows.append(f"| {topic.id} | not judged |")
            continue
        answer = json.loads((d / "judge.answer.json").read_text())
        key = json.loads((d / "judge.key.json").read_text())["new"]
        new_j, old_j = answer[key], answer["Y" if key == "X" else "X"]
        n += 1
        prefer_new += answer["prefer"] == key
        errors_new += len(new_j["errors"])
        errors_old += len(old_j["errors"])
        for c in CRITERIA:
            sums["new"][c] += new_j[c]["score"]
            sums["old"][c] += old_j[c]["score"]
        mean_new = sum(new_j[c]["score"] for c in CRITERIA) / len(CRITERIA)
        mean_old = sum(old_j[c]["score"] for c in CRITERIA) / len(CRITERIA)
        left = [p for p in json.loads((d / "checks.json").read_text())["problems"] if not p.get("repaired")]
        rows.append(f"| {topic.id} | {mean_new:.2f} | {mean_old:.2f} | {'new' if answer['prefer'] == key else 'baseline'}"
                    f" | {len(new_j['errors'])} / {len(old_j['errors'])} | {len(left)} |")
    print(f"judged {n} beside {baseline().name}; preferred new {prefer_new} of {n}; "
          f"errors new / baseline {errors_new} / {errors_old}")
    if n:
        all_new = sum(sums["new"].values()) / (n * len(CRITERIA))
        all_old = sum(sums["old"].values()) / (n * len(CRITERIA))
        print(f"mean new {all_new:.2f}, baseline {all_old:.2f}")
        print("by criterion (new / baseline):",
              ", ".join(f"{c} {sums['new'][c] / n:.2f} / {sums['old'][c] / n:.2f}" for c in CRITERIA))
    print("| topic | new | baseline | preferred | errors new / baseline | checks left |\n|---|---|---|---|---|---|")
    print("\n".join(rows))


COMMANDS = {"prep": prep, "script-check": script_check, "bundle-prompt": bundle_prompt, "bundle-check": bundle_check,
            "checks": run_checks, "judge-prep": judge_prep, "judge-check": judge_check, "summary": summary}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](*sys.argv[2:])
