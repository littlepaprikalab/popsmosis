"""Run the operator through failures at every point of the pipeline, with no paid call (tools/sim/README.md).

    python tools/sim/scenarios.py                 # every scenario; a table, and report.md in the output folder
    python tools/sim/scenarios.py brain-outage    # the scenarios whose names start with any of these
    python tools/sim/scenarios.py --list          # their names and what each breaks
    python tools/sim/scenarios.py --out DIR       # where each scenario's folder goes (default jobs/_sim/<time>)

Each scenario asks one topic of a fresh fake API (tools/sim/fakeapi.py), sets its faults, and runs heartbeats of the
operator (tools/sim/teacher.py) until the topic is a droplet with its videos, is given up, or MAX_HEARTBEATS have
passed. Between two heartbeats the clock moves on BETWEEN_MIN minutes, as the schedule's sweeps are apart, so a
lease lapses and a render that died goes stale. It reports how each ended:

    droplet        the lesson posted and its videos READY
    lesson only    the lesson posted, its videos given up
    given up       the topic failed: no lesson
    stuck          none of those after MAX_HEARTBEATS: nothing will finish it
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_HEARTBEATS = 5
BETWEEN_MIN = 61  # past the topic's lease (20) and a render's stale time (60)
TOPIC = "ATP synthesis for my AP Bio exam"


@dataclass
class Scenario:
    name: str
    point: str  # where in the pipeline it breaks
    what: str  # what goes wrong, in words
    faults: dict = field(default_factory=dict)  # tools/sim/fakes.py
    api_faults: list = field(default_factory=list)  # tools/sim/fakeapi.py Fault arguments
    settings: dict = field(default_factory=dict)  # merged into the fake API's studio settings
    env: dict = field(default_factory=dict)  # for the operator's machine; None removes a variable
    session: str = "answer"  # how the stand-in session answers (teacher.py)
    attachments: bool = False


OFF = {"knobs": {"STUDIO_SESSION_FALLBACK": "off"}}


def brain(label: str, fault: str, times: int) -> dict:
    return {"brain": {label: {"fault": fault, "times": times}}}


def step(name: str, fault: str, times: int, code: str = "") -> dict:
    return {"steps": {name: {"fault": fault, "times": times, **({"code": code} if code else {})}}}


SCENARIOS = [
    Scenario("baseline", "-", "nothing goes wrong"),
    # --- the API -------------------------------------------------------------------------------------
    Scenario("api-settings-down", "run: settings", "GET /api/studio/settings fails 3 times",
             api_faults=[{"match": r"GET /api/studio/settings", "status": 503, "times": 3}]),
    Scenario("api-claim-blip", "lesson: claim", "the claim before a step gets a 503 once",
             api_faults=[{"match": r"POST /api/questions/[^/]+/claim", "status": 503, "times": 1, "skip": 1}]),
    Scenario("api-claim-outage", "lesson: claim", "every claim fails for 4 calls (a few minutes' outage)",
             api_faults=[{"match": r"POST /api/questions/[^/]+/claim", "status": 503, "times": 4, "skip": 1}]),
    Scenario("api-attachment-blip", "lesson: request", "a file sent with the topic fails to download twice",
             api_faults=[{"match": r"GET /api/attachments/", "status": 502, "times": 2}], attachments=True),
    Scenario("api-post-lost-reply", "lesson: post", "the lesson is saved but the reply to the post is lost",
             api_faults=[{"match": r"POST /api/questions/[^/]+/answer", "status": "drop", "times": 1, "commit": True}]),
    Scenario("api-post-down", "lesson: post", "posting the lesson fails 3 times",
             api_faults=[{"match": r"POST /api/questions/[^/]+/answer", "status": 503, "times": 3}]),
    Scenario("api-progress-down", "lesson+videos: progress", "every progress report fails",
             api_faults=[{"match": r"PUT /api/.*/progress", "status": 503, "times": 999}]),
    # --- the brain -----------------------------------------------------------------------------------------
    Scenario("brain-blip", "lesson: bundle", "the bundle call fails once (overloaded)", brain("bundle", "error", 1),
             settings=OFF),
    Scenario("brain-outage-no-session", "lesson: bundle", "the brain fails on 2 calls in a row, no session fallback",
             brain("bundle", "error", 2), settings=OFF),
    Scenario("brain-outage-session", "lesson: bundle", "the brain fails on 2 calls in a row; the session answers",
             brain("bundle", "error", 2)),
    Scenario("brain-down-no-session", "lesson: every call", "the API brain is down for the whole run, no fallback",
             brain("*", "overloaded", 999), settings=OFF),
    Scenario("brain-down-session", "lesson+videos: every call", "the API brain is down; the session answers all",
             brain("*", "overloaded", 999)),
    Scenario("brain-nokey-no-session", "run: settings", "the brain's key is missing here, no fallback",
             env={"POPSMOSIS_ANTHROPIC_API_KEY": None}, settings=OFF),
    Scenario("brain-nokey-session", "run: settings", "the brain's key is missing here; the session answers",
             env={"POPSMOSIS_ANTHROPIC_API_KEY": None}),
    Scenario("brain-invalid-no-session", "lesson: bundle", "the bundle twice does not fit its schema, no fallback",
             brain("bundle", "invalid", 2), settings=OFF),
    Scenario("brain-invalid-session", "lesson: bundle", "the bundle does not fit its schema; the session answers",
             brain("bundle", "invalid", 2), session="invalid-first"),
    Scenario("brain-truncated-session", "lesson: factcheck", "a fact-check round is cut off; the session answers",
             brain("factcheck", "truncated", 1)),
    Scenario("brain-refused-session", "lesson: bundle", "the brain declines the bundle; the session answers",
             brain("bundle", "refused", 1)),
    # --- what the brain writes ------------------------------------------------------------------------
    Scenario("audit-wrong-once", "lesson: audit", "the fact audit finds the facts wrong once", brain("fact audit", "wrong", 1)),
    Scenario("audit-wrong-always", "lesson: audit", "the fact audit finds the facts wrong every time",
             brain("fact audit", "wrong", 999)),
    Scenario("factcheck-fails-once", "lesson: factcheck", "the fact-check refuses the first lesson",
             brain("factcheck", "unsupported", 1)),
    Scenario("factcheck-fails-always", "lesson: factcheck", "every fact-check round finds every line unsupported",
             brain("factcheck", "unsupported", 999)),
    Scenario("spend-ceiling", "lesson: any call", "the topic's spend ceiling is below one bundle",
             settings={"spend": {"topicUsd": 0.4}}),
    # --- the machine ----------------------------------------------------------------------------------
    Scenario("killed-in-bundle-once", "lesson: bundle", "the run dies mid-bundle once (machine restart, OOM)",
             brain("bundle", "kill", 1)),
    Scenario("killed-in-bundle-twice", "lesson: bundle", "the run dies mid-bundle twice", brain("bundle", "kill", 2)),
    Scenario("killed-in-bundle-always", "lesson: bundle", "the run dies mid-bundle every time",
             brain("bundle", "kill", 999)),
    Scenario("lesson-hangs-once", "lesson: bundle", "the bundle call never returns once (past the operator's hour)",
             brain("bundle", "hang", 1)),
    Scenario("lesson-hangs-always", "lesson: bundle", "the bundle call never returns, every time",
             brain("bundle", "hang", 999)),
    # --- the videos ------------------------------------------------------------------------------------------
    Scenario("videos-claim-blip", "videos: claim", "the videos' claim fails at `videos --background` once",
             api_faults=[{"match": r"POST /api/answers/[^/]+/video/claim", "status": 503, "times": 1, "skip": 1}]),
    Scenario("videos-never-claimed", "videos: claim", "both the claim after the post and `videos --background` fail",
             api_faults=[{"match": r"POST /api/answers/[^/]+/video/claim", "status": 503, "times": 2}]),
    Scenario("art-fails-once", "videos: art", "the art step fails once", step("art", "fail", 1)),
    Scenario("audio-fails-twice", "videos: audio", "the song provider fails twice (Lyria outage)",
             step("audio", "fail", 2, "the audio provider failed")),
    Scenario("timing-drifts-twice", "videos: timing", "the song drifts from its lyrics twice",
             step("timing", "fail", 2, "the song did not sing its lyrics")),
    Scenario("drawer-down-no-session", "videos: art", "Claude's drawer fails twice in a row, no fallback",
             brain("art", "error", 8), settings=OFF),
    Scenario("drawer-down-session", "videos: art", "Claude's drawer fails; the session draws",
             brain("art", "error", 8)),
    Scenario("heartbeat-claim-blip", "videos: heartbeat", "the claim before a media step gets a 503 once",
             api_faults=[{"match": r"POST /api/answers/[^/]+/video/claim", "status": 503, "times": 1, "skip": 4}]),
    Scenario("render-killed-once", "videos: render", "the render dies with its machine once", step("render", "kill", 1)),
    Scenario("render-hangs-once", "videos: render", "the render runs past the operator's hour once",
             step("render", "hang", 1)),
    Scenario("render-hangs-twice", "videos: render", "the render runs past the operator's hour twice",
             step("render", "hang", 2)),
    Scenario("storage-blip", "videos: upload", "the storage PUT fails twice (retried inside the step)",
             api_faults=[{"match": r"PUT /r2/", "status": 503, "times": 2}]),
    Scenario("storage-outage", "videos: upload", "the storage PUT fails 6 times",
             api_faults=[{"match": r"PUT /r2/", "status": 503, "times": 6}]),
    Scenario("ready-lost", "videos: upload", "marking the videos READY fails 3 times",
             api_faults=[{"match": r"PATCH /api/answers/[^/]+/video", "status": 503, "times": 3}]),
]


def run_one(s: Scenario, root: Path) -> dict:
    from fakeapi import Fault, State, serve
    from teacher import Teacher

    sim = root / s.name
    sim.mkdir(parents=True, exist_ok=True)
    (sim / "faults.json").write_text(json.dumps(s.faults))
    state = State()
    state.settings.update(s.settings)
    for f in s.api_faults:
        state.faults.append(Fault(**f))
    server = serve(state)
    started = time.monotonic()
    try:
        files = [("notes.png", "image/png", b"\x89PNG sim"), ("guide.txt", "text/plain", b"ATP synthase makes ATP.")]
        qid = state.ask(TOPIC, files if s.attachments else [])
        teacher = Teacher(sim, state.base_url, s.env, s.session)
        beats = 0
        for n in range(1, MAX_HEARTBEATS + 1):
            beats = n
            teacher.heartbeat(n)
            d = state.droplet(qid)
            if d["videoStatus"] in ("READY", "FAILED") or d["question"] == "FAILED":
                break
            state.advance(BETWEEN_MIN)
        d = state.droplet(qid)
    except Exception:
        traceback.print_exc()
        return {"name": s.name, "point": s.point, "what": s.what, "outcome": "harness error", "detail": ""}
    finally:
        server.shutdown()
    outcome = ("droplet" if d["videoStatus"] == "READY" else "lesson only" if d["videoStatus"] == "FAILED"
               else "given up" if d["question"] == "FAILED" else "stuck")
    detail = (d["failedReason"] if d["question"] == "FAILED" else d["videoError"] if d["videoStatus"] == "FAILED"
              else f"topic {d['question']}, videos {d['videoStatus']} after {d['videoAttempts']} attempt(s)"
              if outcome == "stuck" else "")
    rows = [json.loads(line) for f in sim.glob("machine-*/*/calls.jsonl") for line in f.read_text().splitlines()
            if line.strip()]
    session_rows = [r for r in rows if r.get("via") == "session"]
    result = {
        "name": s.name, "point": s.point, "what": s.what, "outcome": outcome, "detail": (detail or "")[:160],
        "heartbeats": beats, "commands": sum(1 for line in teacher.log if line.startswith("$ studio")),
        "api_usd": round(sum(r.get("cost_usd") or 0 for r in rows), 2),
        "session_calls": len(session_rows),
        "session_models": sorted({r["model"] for r in session_rows}),
        "session_api_usd": round(sum(r.get("api_usd") or 0 for r in session_rows), 4),
        "bill_session_lines": [m for part in ("lessonBreakdown", "videoBreakdown") for st in ((d[part] or {}).get("steps") or [])
                               for m in st["models"] if m.get("via") == "session"],
        "seconds": round(time.monotonic() - started, 1),
    }
    (sim / "operator.log").write_text("\n".join(teacher.log) + "\n\nAPI events:\n" + "\n".join(state.events)
                                      + "\n\nAPI requests:\n" + "\n".join(state.log) + "\n")
    (sim / "result.json").write_text(json.dumps(result, indent=1))
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python tools/sim/scenarios.py")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    chosen = [s for s in SCENARIOS if not args.names or any(s.name.startswith(n) for n in args.names)]
    if args.list:
        for s in chosen:
            print(f"{s.name:<28} {s.point:<26} {s.what}")
        return 0
    root = (args.out or REPO / "jobs" / "_sim" / time.strftime("%Y%m%d-%H%M%S")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["STUDIO_SIM_DIR"] = str(root)  # the driver's own copy of the fakes reads it
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda s: run_one(s, root), chosen))
    lines = ["| scenario | where | what goes wrong | outcome | beats | API $ | session calls | detail |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {r['point']} | {r['what']} | **{r['outcome']}** | {r.get('heartbeats', '')} | "
                     f"{r.get('api_usd', '')} | {r.get('session_calls', '')} | {r.get('detail', '')} |")
    table = "\n".join(lines)
    (root / "report.md").write_text(table + "\n")
    (root / "results.json").write_text(json.dumps(results, indent=1))
    print(table)
    print(f"\n{root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
