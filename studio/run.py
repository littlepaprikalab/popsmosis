"""python -m studio run <question-id>: one topic, from the queue to a posted lesson.

The operator (the routine, following docs/popsmosis-agent-skill.md) takes the topic
from the queue and runs this on its id. The steps, each done once its file exists
(docs/PLAN.md, section 5):

    request    request.json, attachments/   the topic and the files sent with it
    bundle     bundle.json, checks.json
    factcheck  factcheck.json               done only once it has passed
    post       posted.json                  the lesson, bundle and fact-check, saved on the API

`run` starts at the first step that is not done, so running it again after a
failure retries the step that failed. Before each step but the post it claims the
topic again (POST /api/questions/:id/claim), which renews the queue's lease, so a
slow step or a retry keeps it out of an overlapping run's queue.

Every run appends what it did to runs.jsonl and prints one line, its outcome:

    DONE post: lesson posted as answer <id>
    FAILED <step>: <reason>        run again to retry that step
    STOPPED <step>: <reason>       nothing to retry: the topic is gone, taken or done

`python -m studio fail <id>` gives the topic up with the last run's reason, and
`python -m studio summary <id>` says what the runs did, for the operator's record.
"""

from __future__ import annotations

import json
import re
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from studio import brain, jobs
from studio.api import Api, ApiError
from studio.schemas import Bundle, FactCheckReport, Request
from studio.steps import bundle as bundle_step
from studio.steps import factcheck as factcheck_step

STEPS = ("request", "bundle", "factcheck", "post")
DONE, FAILED, STOPPED = "DONE", "FAILED", "STOPPED"
EXIT_CODES = {DONE: 0, FAILED: 1, STOPPED: 3}
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
REASON_MAX = 400  # POST /api/questions/:id/fail takes 500, with the step in front
CYCLE_LOG_MAX = 1000  # what an Inkmory cycle log holds (docs/PLAN.md, section 11)
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
# Anthropic refuses an image over 5 MB of base64; popsmosis takes 4 MB files, so a large photo
# is left out rather than failing the bundle on every try.
IMAGE_MAX_BYTES = 5 * 1024 * 1024 * 3 // 4


class Stop(Exception):
    """Nothing a retry can fix: the topic was deleted, answered, given up or taken, or the key was refused."""


class StepFailed(Exception):
    """The step ran and did not succeed; running again retries it."""


def job_dir(question_id: str) -> Path:
    if not ID_RE.fullmatch(question_id):
        raise ValueError(f"{question_id!r} is not a question id")
    return jobs.ROOT / question_id


def is_done(job: Path, step: str) -> bool:
    if step == "factcheck":
        path = job / "factcheck.json"
        return path.exists() and jobs.read(path, FactCheckReport).status == "passed"
    return (job / {"request": "request.json", "bundle": "bundle.json", "post": "posted.json"}[step]).exists()


def run(question_id: str, api: Api | None = None, ask=None) -> tuple[str, str, str]:
    """Run the steps that are not done yet; return (DONE | FAILED | STOPPED, step, reason)."""
    job = job_dir(question_id)
    job.mkdir(parents=True, exist_ok=True)
    ask = ask or logged_ask(job)
    started, clock = time.strftime("%Y-%m-%dT%H:%M:%S%z"), time.monotonic()
    done_steps: list[dict] = []
    result, step, reason = DONE, "post", "the lesson was already posted"
    # From the first step that is not done, every step runs: a new bundle makes the fact-check stale.
    first = next((i for i, name in enumerate(STEPS) if not is_done(job, name)), len(STEPS))
    for step in STEPS[first:] if not is_done(job, "post") else ():
        print(f"{step}: running", file=sys.stderr)
        step_clock = time.monotonic()
        try:
            api = api or Api.from_env()
            question = claim(api, question_id) if step != "post" else None
            reason = STEP_RUNNERS[step](job, api, question, ask)
            result = DONE
        except Stop as e:
            result, reason = STOPPED, str(e)
        except (StepFailed, brain.BrainError) as e:
            result, reason = FAILED, str(e)
        except ApiError as e:
            result, reason = api_outcome(e)
        except Exception as e:  # a bug: the run still ends with its one line
            traceback.print_exc(file=sys.stderr)
            result, reason = FAILED, f"{type(e).__name__}: {e}"
        seconds = round(time.monotonic() - step_clock, 1)
        done_steps.append({"step": step, "seconds": seconds, "result": result})
        print(f"{step}: {result.lower()} in {seconds:.0f}s", file=sys.stderr)
        if result != DONE:
            break
    reason = one_line(reason)
    if done_steps:  # a run with nothing left to do is not one the record should count
        record = {"started": started, "seconds": round(time.monotonic() - clock, 1), "result": result, "step": step,
                  "reason": reason, "steps": done_steps}
        with (job / "runs.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result, step, reason


def claim(api: Api, question_id: str) -> dict:
    try:
        return api.claim(question_id)
    except ApiError as e:
        result, reason = api_outcome(e)
        raise (Stop if result == STOPPED else StepFailed)(f"claim: {reason}") from e


def api_outcome(e: ApiError) -> tuple[str, str]:
    if e.status == 404:
        return STOPPED, "the topic was deleted"
    if e.status == 409:  # answered, given up, or claimed by another agent
        return STOPPED, e.message
    if e.status in (401, 403) or e.code == "NOT_CONFIGURED":
        return STOPPED, f"the API refused the key: {e}"
    return FAILED, str(e)


# --- the steps ------------------------------------------------------------------


def make_request(job: Path, api: Api, question: dict, ask) -> str:
    """request.json from the claimed question: its words, photos of notes, and text files as a study guide."""
    images, guides, skipped = [], [], []
    for attachment in question.get("attachments") or []:
        name, mime = attachment.get("name", "file"), attachment.get("mimeType", "")
        if attachment.get("expired") or not ID_RE.fullmatch(str(attachment.get("id", ""))):
            skipped.append(name)
        elif mime in IMAGE_TYPES:
            data = api.download(attachment["url"])
            if len(data) > IMAGE_MAX_BYTES:
                skipped.append(name)
                continue
            path = job / "attachments" / f"{attachment['id']}{IMAGE_TYPES[mime]}"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
            images.append(str(path.resolve()))
        elif mime.startswith("text/"):
            text = api.download(attachment["url"]).decode("utf-8", "replace").strip()
            guides.append(f"{name}:\n{text}")
        else:
            skipped.append(name)  # a PDF or anything else the brain cannot read yet
    if skipped:
        print(f"request: left out {len(skipped)} file(s) the brain cannot read, too big, or expired", file=sys.stderr)
    request = Request(topic=" ".join(question["content"].split()), study_guide="\n\n".join(guides), images=images)
    jobs.write(job / "request.json", request)
    return f"{len(images)} image(s), {len(guides)} text file(s)"


def make_bundle(job: Path, api: Api, question: dict, ask) -> str:
    bundle = bundle_step.run(job, ask=ask)
    return f"{bundle.course} / {bundle.topic}"


def make_factcheck(job: Path, api: Api, question: dict, ask) -> str:
    report = factcheck_step.run(job, ask=ask)
    if report.status == "failed":
        raise StepFailed(report.reason)
    return f"passed in {report.rounds} round(s), {len(report.corrections)} correction(s)"


def post_lesson(job: Path, api: Api, question: dict | None, ask) -> str:
    bundle = json.loads((job / "bundle.json").read_text(encoding="utf-8"))
    body = {
        "content": Bundle.model_validate(bundle).lesson_markdown,
        "bundle": bundle,
        "factcheck": json.loads((job / "factcheck.json").read_text(encoding="utf-8")),
    }
    model = lesson_model(job)
    if model:
        body["model"] = model
    answer = api.post_answer(job.name, body)
    jobs.write(job / "posted.json", answer)
    return f"lesson posted as answer {answer.get('id')}"


STEP_RUNNERS = {"request": make_request, "bundle": make_bundle, "factcheck": make_factcheck, "post": post_lesson}


def lesson_model(job: Path) -> str | None:
    """The model that wrote the bundle: after a fallback, the one that answered."""
    models = [call["model"] for call in read_jsonl(job / "calls.jsonl") if call.get("label") == "bundle"]
    return models[-1] if models else None


# --- after a run ------------------------------------------------------------------


def mark_failed(question_id: str, api: Api | None = None) -> tuple[str, str]:
    """Give the topic up with its last run's reason (POST /api/questions/:id/fail)."""
    job = job_dir(question_id)
    runs = read_jsonl(job / "runs.jsonl")
    if (job / "posted.json").exists():
        return STOPPED, "the lesson is posted; there is nothing to give up"
    if not runs or runs[-1]["result"] != FAILED:
        return STOPPED, "the last run did not fail"
    reason = f"{runs[-1]['step']} failed: {runs[-1]['reason']}"
    try:
        (api or Api.from_env()).fail(question_id, reason)
    except ApiError as e:
        result, why = api_outcome(e)
        return result, why
    return DONE, reason


def summary(question_id: str) -> str:
    """What the runs did, in at most 1,000 characters, for the operator's cycle log.

    It names the topic by the bundle's course and topic, never by request.json,
    which holds the learner's own words and must never reach Inkmory.
    """
    job = job_dir(question_id)
    parts = []
    if (job / "bundle.json").exists():
        bundle = json.loads((job / "bundle.json").read_text(encoding="utf-8"))
        parts.append(f"job {job.name}: {bundle.get('course')} / {bundle.get('topic')}.")
    else:
        parts.append(f"job {job.name} (no bundle yet).")
    runs = read_jsonl(job / "runs.jsonl")
    if runs:
        told = [f"run {i} {r['result']} at {r['step']}" + (f" ({_short(r['reason'], 160)})" if r["result"] != DONE else "")
                for i, r in enumerate(runs, 1)]
        parts.append(f"{len(runs)} run(s): " + "; ".join(told) + ".")
        took = {}
        for r in runs:
            for s in r["steps"]:
                if s["result"] == DONE:
                    took[s["step"]] = s["seconds"]
        timed = [f"{name} {took[name] / 60:.1f} min" for name in ("bundle", "factcheck") if name in took]
        if timed:
            parts.append(", ".join(timed) + ".")
    if (job / "factcheck.json").exists():
        report = jobs.read(job / "factcheck.json", FactCheckReport)
        parts.append(f"Fact-check {report.status} in {report.rounds} round(s), {len(report.corrections)} correction(s).")
    parts.append("Lesson posted." if (job / "posted.json").exists() else "No lesson posted.")
    calls = read_jsonl(job / "calls.jsonl")
    if calls:
        costs = [c.get("cost_usd") for c in calls]
        cost = f"${sum(costs):.2f}" if None not in costs else "cost n/a"
        parts.append(f"{len(calls)} brain call(s), {cost}.")
    return _short(" ".join(parts), CYCLE_LOG_MAX)


# --- shared with python -m studio ---------------------------------------------------


def logged_ask(job: Path):
    """brain.ask, logging each call to the job's calls.jsonl and stderr as it ends."""

    def ask(*args, **kwargs):
        before = len(brain.calls)
        try:
            return brain.ask(*args, **kwargs)
        finally:
            with (job / "calls.jsonl").open("a", encoding="utf-8") as log:
                for call in brain.calls[before:]:
                    log.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **asdict(call)}) + "\n")
                    print(f"  {call_row(call)}", file=sys.stderr)

    return ask


def call_row(call: brain.Call) -> str:
    cost = f"${call.cost_usd:.4f}" if call.cost_usd is not None else "n/a"
    flags = "".join([" fell back" if call.fell_back else "",
                     f" ({call.stop_reason})" if call.stop_reason not in ("end_turn", "stop") else ""])
    return (f"{call.label:<20} {call.model:<17} {call.input_tokens:>7} {call.output_tokens:>7} {cost:>8} "
            f"{call.seconds:>6.1f}s{flags}")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines() if row.strip()]


def one_line(text: str) -> str:
    return _short(" ".join(str(text).split()), REASON_MAX)


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
