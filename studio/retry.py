"""What the studio does about a failure an outage explains, so a droplet gets through it (docs/operator-failures.md).

Three things, each keyed on the fixed code a failure carries (studio/run.py, failure_code):

    in the run      A step whose failure an outage explains (OUTAGE: the API, a provider or the brain did not answer,
                    or failed on its side) is tried again in the same run after WAITS_S, before the run fails, so an
                    outage of a few minutes is waited out rather than costing the operator its one retry. A song that
                    drifted from its lyrics, or a narration that skipped its lines (AGAIN), is made again once.
    after the run   `python -m studio fail` gives a topic, or its videos, up only when the API says no run or
                    attempt is left, or the failure is one no later try would meet (DEFERRABLE says which may be):
                    otherwise it leaves them to a later sweep, on another machine, and says DEFERRED.
    a step's time   No step may run past STEP_MINUTES (watch(), in the run's own process): one that does is ended
                    and recorded as failed, "timed out", so the operator gets an outcome within its hour and a hang is
                    retried like any other failure. Time the run spends waiting for the operator's session to answer
                    (studio/session.py) does not count.

STUDIO_RETRY_SCALE multiplies every wait and STUDIO_STEP_MINUTES sets the limit, for the simulation (tools/sim).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from studio import knobs, session

# Failures an outage explains: tried again in the run, after a wait, and left to a later sweep when the run fails.
OUTAGE = {
    "the API did not answer", "the API failed", "the model call failed", "the session did not answer",
    "Gemini failed", "Meta's API failed", "the audio provider failed",
}
# Made again at once, once: what the step deletes when it fails, so a second try makes it afresh.
AGAIN = {"the song did not sing its lyrics", "the narration did not read its lines"}
# What a later sweep, on another machine, may still get past: besides those, a run that was cut off or timed out.
DEFERRABLE = OUTAGE | AGAIN | {"interrupted", "timed out", "the background run ended without an outcome"}
WAITS_S = (30.0, 120.0, 300.0)  # before the second, third and fourth try of a step an outage failed
STEP_MINUTES = 40.0  # well under the operator's hour of RUNNING (docs/popsmosis-agent-skill.md)
TICK_S = 5.0


def outage(code: str | None) -> bool:
    return bool(code) and (code in OUTAGE or _busy(code))


def deferrable(code: str | None) -> bool:
    return bool(code) and (code in DEFERRABLE or _busy(code))


def _busy(code: str) -> bool:
    """"the API answered 503": a status the API gives when it is busy or failing on its side."""
    words = code.split()
    return code.startswith("the API answered ") and words[-1].isdigit() and (
        int(words[-1]) in (408, 425, 429) or int(words[-1]) >= 500)


def _scale() -> float:
    try:
        return max(0.0, float(os.environ.get("STUDIO_RETRY_SCALE") or 1))
    except ValueError:
        return 1.0


def waits(code: str | None) -> tuple[float, ...]:
    """How long to wait before each further try of a step that failed with this code, in the same run."""
    if outage(code):
        return tuple(w * _scale() for w in WAITS_S)
    if code in AGAIN:
        return (0.0,)
    return ()


def step_minutes() -> float:
    """The most a step may run, in minutes (STUDIO_STEP_MINUTES, on /admin/studio or here; 0: no limit)."""
    try:
        return max(0.0, float(knobs.env("STUDIO_STEP_MINUTES") or STEP_MINUTES))
    except ValueError:
        return STEP_MINUTES


# --- the time limit --------------------------------------------------------------------------------


def watch(job: Path, running: Callable[[], dict[str, float]], end: Callable[[str, str], None]) -> Callable[[], None]:
    """Watch the steps running in this process (running(): step -> the time.monotonic() it started), and when one
    has run past step_minutes(), not counting the time calls of it waited for the session, call end(step, reason),
    which records the run and ends the process. Returns the function that stops the watch."""
    limit = step_minutes() * 60
    stop = threading.Event()
    if not limit:
        return stop.set
    paused: dict[tuple[str, float], float] = {}  # (step, its start) -> seconds spent waiting on the session

    def loop() -> None:
        while not stop.wait(TICK_S * min(1.0, _scale() or 1.0)):
            now, asking = time.monotonic(), bool(session.pending(job))
            for step, since in list(running().items()):
                key = (step, since)
                if asking:
                    paused[key] = paused.get(key, 0.0) + TICK_S * min(1.0, _scale() or 1.0)
                if now - since - paused.get(key, 0.0) > limit:
                    end(step, f"the {step} step ran past its {limit / 60:g}-minute limit (STUDIO_STEP_MINUTES), so "
                              "the run was ended there")
                    return

    threading.Thread(target=loop, name="step time limit", daemon=True).start()
    return stop.set


def end_process(job: Path, step: str, reason: str) -> None:
    """Record the run in run.current.json as failed at this step, "timed out", print its outcome line (the outcome
    `wait` reads of a lesson run with --background), and end the process with every one it started."""
    current = job / "run.current.json"
    try:
        record = json.loads(current.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "steps": []}
    record.pop("since", None)
    record.update(result="FAILED", step=step, code="timed out", reason=reason, seconds=None)
    record.setdefault("steps", []).append({"step": step, "seconds": None, "result": "FAILED", "code": "timed out"})
    with (job / "runs.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    current.unlink(missing_ok=True)
    print(f"{step}: {reason}", file=sys.stderr)
    print(f"FAILED {step}: {reason}", flush=True)
    sys.stderr.flush()
    if os.getpgid(0) == os.getpid():  # a run of its own (--background): its ffmpeg and workers go with it
        os.killpg(0, signal.SIGKILL)
    os._exit(1)
