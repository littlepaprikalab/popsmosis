"""What each step of a droplet took and cost: calls.jsonl, and the breakdown shown under the videos.

Every model call is a brain.Call appended to jobs/<id>/calls.jsonl as it ends, tagged with
the step that made it: the brain's (studio/brain.py), the song and the voice (step 5, with
the credits ElevenLabs charged), and the models the timing step runs on this machine
(Demucs, MMS: no bill, so a cost of 0). The step runners (studio/run.py, studio/videos.py,
`python -m studio step`) name the step with at_step(); how long each step took is in
runs.jsonl.

breakdown(job, part) puts the two together for the API, which keeps it with the droplet
(`breakdown` on POST /api/questions/:id/answer and PATCH /api/answers/:id/video): each
step's time and tries, what it cost, the models that ran it, and the other software it ran
on this machine (TOOLS) at the version installed here. progress() sends the same bill so
far at the start and the end of every step, with the steps running then, so the page shows
it filling in while the droplet is made (api/src/lib/progress.ts).

    {"steps": [{"step": "bundle", "seconds": 186.2, "tries": 1, "costUsd": 0.5213,
                "models": [{"model": "claude-opus-5-5", "calls": 1, "costUsd": 0.5213, "credits": null}],
                "tools": []},
               {"step": "render", "seconds": 157.0, "tries": 1, "costUsd": 0, "models": [],
                "tools": [{"name": "skia-python", "version": "144.1"}, {"name": "ffmpeg", "version": "7.1.1"}]},
               ...],
     "seconds": 312.4}

A model's costUsd is null when one of its calls has no known price (an ElevenLabs call
without ELEVENLABS_USD_PER_1K_CREDITS); its credits, when the provider said them, still are.
"""

from __future__ import annotations

import contextlib
import functools
import importlib.metadata
import json
import subprocess
import sys
import threading
import time
from dataclasses import asdict, fields
from pathlib import Path

from studio import brain, media

CALLS = "calls.jsonl"
MAX_BYTES = 16 * 1024  # what the API takes (api/src/lib/breakdown.ts); a bigger one is left out, never refused
LESSON_STEPS = ("request", "bundle", "factcheck", "post")  # studio/run.py's STEPS
PARTS = {"lesson": LESSON_STEPS, "videos": media.MEDIA_STEPS}
# The software each media step runs on this machine besides its models, by the name it is
# installed under. The bill lists each with the version installed here (version()); the API
# adds who made it. Steps that are the studio's own code alone have none.
TOOLS = {
    "art": ("skia-python",),  # draws the SVG art (studio/svg.py), or fits Muse Image's picture to the art
    "audio": ("ffmpeg",),  # ffprobe reads each file's length; ffmpeg re-encodes a Lyria song
    "timing": ("demucs", "torchaudio", "torch", "ffmpeg"),  # Demucs, the MMS aligner, ffmpeg decoding the song
    "render": ("skia-python", "ffmpeg"),  # draws the frames, encodes the videos (studio/render/)
}
# How a call logged before calls were tagged with their step is told apart: by its label.
LABEL_STEPS = {"bundle": "bundle", "factcheck": "factcheck", "art": "art",
               "song": "audio", "narration": "audio", "frq": "audio"}

_local = threading.local()
_lock = threading.Lock()


@contextlib.contextmanager
def at_step(name: str):
    """The calls this thread logs inside the block belong to this step."""
    outer = getattr(_local, "step", None)
    _local.step = name
    try:
        yield
    finally:
        _local.step = outer


def current_step() -> str | None:
    return getattr(_local, "step", None)


def log(job: Path, calls: list[brain.Call]) -> None:
    """Append the calls to the job's calls.jsonl, tagged with this thread's step, and print each one.

    Best effort: a log that cannot be written (a full disk) is reported and the step goes on, since
    the call it records has been paid for and what it made must still be kept.
    """
    if not calls:
        return
    step = current_step()
    for call in calls:
        print(f"  {call_row(call)}", file=sys.stderr)
    try:
        rows = "".join(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "step": step, **asdict(call)},
                                  allow_nan=False) + "\n" for call in calls)
        with _lock, (job / CALLS).open("a", encoding="utf-8") as out:
            out.write(rows)  # one write, so a kill leaves whole lines or, at worst, one cut short
    except (OSError, ValueError) as e:
        print(f"  calls not logged ({type(e).__name__}: {e}); the bill will be short", file=sys.stderr)


def call_row(call: brain.Call) -> str:
    cost = f"${call.cost_usd:.4f}" if call.cost_usd is not None else "n/a"
    flags = "".join([" fell back" if call.fell_back else "",
                     f" ({call.stop_reason})" if call.stop_reason not in ("end_turn", "stop", "") else "",
                     f" {call.credits:g} credits" if call.credits is not None else ""])
    return (f"{call.label:<20} {call.model:<17} {call.input_tokens:>7} {call.output_tokens:>7} {cost:>8} "
            f"{call.seconds:>6.1f}s{flags}")


def read(job: Path) -> list[dict]:
    """The job's logged calls, each a dict as written. A line cut short by a kill mid-write is skipped:
    the post reads this file (run.lesson_model), and one bad line must not fail it on every retry."""
    try:
        text = (job / CALLS).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


_CALL_FIELDS = {f.name for f in fields(brain.Call)}


def as_call(row: dict) -> brain.Call:
    """A logged row back as a Call, whatever else the row carries."""
    return brain.Call(**{k: v for k, v in row.items() if k in _CALL_FIELDS})


def step_of(row: dict) -> str | None:
    """The step a logged call belongs to: its tag, or for an older row, what its label says."""
    if row.get("step"):
        return row["step"]
    words = str(row.get("label", "")).split()
    return LABEL_STEPS.get(words[0]) if words else None


def cost_text(rows: list[dict]) -> str:
    """What the calls cost in one phrase: "$0.71", "$0.71 + 2914 credits", or "$0.71 + calls with no price"."""
    priced = sum(r["cost_usd"] for r in rows if r.get("cost_usd") is not None)
    unpriced = [r for r in rows if r.get("cost_usd") is None]
    text = f"${priced:.2f}"
    if unpriced:
        credits = [r["credits"] for r in unpriced if r.get("credits") is not None]
        text += f" + {sum(credits):g} credits" if len(credits) == len(unpriced) else " + calls with no price"
    return text


# --- the breakdown -----------------------------------------------------------------------------


def breakdown(job: Path, part: str, running: tuple[str, float] | None = None) -> dict:
    """Each step of one part of the droplet ("lesson" or "videos"), from this job's runs and calls.

    Every run of the part counts, including the tries that failed: they took time and were paid
    for. The run still going counts too (run.current.json), and `running` is the step sending
    this, with its seconds so far.
    """
    order = PARTS[part]
    runs = [r for r in _read_jsonl(job / "runs.jsonl") if _part_of(r) == part]
    current = _current(job, part)
    if current is not None:
        runs.append(current)
    if running is not None:
        if current is None:
            runs.append({"steps": []})
        runs[-1] = {**runs[-1], "steps": [*runs[-1]["steps"], {"step": running[0], "seconds": running[1]}]}

    timed: dict[str, dict] = {}
    for r in runs:
        for s in r.get("steps", []):
            # A run killed between steps is recorded at "videos" or "run" (record_interrupted): not a step.
            if s.get("step") not in order:
                continue
            row = timed.setdefault(s["step"], {"seconds": None, "tries": 0})
            row["tries"] += 1
            if s.get("seconds") is not None:
                row["seconds"] = round((row["seconds"] or 0) + s["seconds"], 1)
    calls: dict[str, list[dict]] = {}
    for row in read(job):
        step = step_of(row)
        if step in order:
            calls.setdefault(step, []).append(row)

    steps = []
    for name in sorted(set(timed) | set(calls), key=order.index):
        models = _models(calls.get(name, []))
        cost = round(sum(m["costUsd"] for m in models if m["costUsd"] is not None), 6)
        steps.append({"step": name, **timed.get(name, {"seconds": None, "tries": 0}),
                      "costUsd": cost, "models": models,
                      "tools": [{"name": tool, "version": version(tool)} for tool in TOOLS.get(name, ())]})
    seconds = [_run_seconds(r) for r in runs]
    return {"steps": steps, "seconds": round(sum(s for s in seconds if s is not None), 1) if runs else None}


def try_breakdown(job: Path, part: str, running: tuple[str, float] | None = None) -> dict | None:
    """breakdown(), or None when the job's records cannot be read or it is more than the API takes:
    the bill is never the reason a post or an upload fails."""
    try:
        report = breakdown(job, part, running)
        # Strict JSON, as the API parses it: Python would write a NaN the API refuses, failing the request.
        size = len(json.dumps(report, allow_nan=False).encode("utf-8"))
    except Exception as e:  # a record from an older format, or a number that is not one
        print(f"breakdown left out: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if size > MAX_BYTES:
        print(f"breakdown left out: {size} bytes, more than the API takes ({MAX_BYTES})", file=sys.stderr)
        return None
    return report


def progress(job: Path, part: str, running: dict[str, float], failed: tuple[str, ...] = (),
             ending: tuple[str, float] | None = None) -> dict:
    """Where one part of the droplet is, for the page: the steps running now (each with the time.monotonic()
    it started at), the steps whose last try failed and that are not running again, and the bill so far, with
    `ending`, a step that has just ended and is not in the run's record yet, and its seconds.

    A running step is not a line of the bill until it ends: what its calls have cost so far is."""
    now = time.monotonic()
    report: dict = {"running": [{"step": step, "seconds": round(max(0.0, now - started), 1)}
                                for step, started in sorted(running.items(), key=lambda r: PARTS[part].index(r[0]))],
                    "failed": sorted(set(failed) - set(running), key=PARTS[part].index)}
    bill = try_breakdown(job, part, ending)
    if bill:
        report["breakdown"] = bill
    return report


@functools.cache
def version(tool: str) -> str | None:
    """The version of a tool installed on this machine: a Python package's, or ffmpeg's. None when
    it is not installed or does not say, so the bill shows the tool without one rather than failing."""
    if tool != "ffmpeg":
        try:
            found = importlib.metadata.version(tool)
        except Exception:  # not installed, or an install whose metadata is broken: a version is never worth the bill
            return None
        return found if isinstance(found, str) and found else None
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, errors="replace", timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    words = out.split(maxsplit=3)  # "ffmpeg version 7.1.1-1ubuntu1 Copyright (c) 2000-2025 the FFmpeg developers"
    return words[2] if words[:2] == ["ffmpeg", "version"] and len(words) > 2 else None


def _models(rows: list[dict]) -> list[dict]:
    """The calls grouped by the model that answered, in the order each model first appears."""
    out: dict[str, dict] = {}
    for row in rows:
        m = out.setdefault(row.get("model") or "unknown", {"calls": 0, "costUsd": 0.0, "credits": None})
        m["calls"] += 1
        if m["costUsd"] is not None:
            m["costUsd"] = None if row.get("cost_usd") is None else m["costUsd"] + row["cost_usd"]
        if row.get("credits") is not None:
            m["credits"] = (m["credits"] or 0) + row["credits"]
    return [{"model": name, "calls": m["calls"], "costUsd": None if m["costUsd"] is None else round(m["costUsd"], 6),
             "credits": m["credits"]} for name, m in out.items()]


def _part_of(run: dict) -> str:
    return "videos" if run.get("part") == "videos" else "lesson"


def _current(job: Path, part: str) -> dict | None:
    """The run of this part that is still going, as run.current.json has it."""
    try:
        record = json.loads((job / "run.current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or _part_of(record) != part:
        return None
    return {"steps": [s for s in record.get("steps", []) if isinstance(s, dict) and "step" in s]}


def _run_seconds(run: dict) -> float | None:
    """How long a run took: as recorded once it ended, else from its steps, the art and the audio
    branches side by side (studio/videos.py) and everything else one after another."""
    if run.get("seconds") is not None:
        return run["seconds"]
    from studio.videos import BRANCHES  # it imports this module, through studio.run

    took: dict[str, float] = {}
    for s in run.get("steps", []):
        took[s["step"]] = took.get(s["step"], 0.0) + (s.get("seconds") or 0.0)
    in_branch = {name for branch in BRANCHES for name in branch}
    total = sum(v for k, v in took.items() if k not in in_branch)
    total += max((sum(took.get(name, 0.0) for name in branch) for branch in BRANCHES), default=0.0)
    return round(total, 1) if took else None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines() if row.strip()]
