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
    STOPPED <step>: <reason>       nothing to retry: the topic is gone, taken or done,
                                   or a run of it started earlier is still going

Only one run of a job goes at a time (a lock on run.lock). A run that is stopped
before it finishes, by a tool's time limit or a kill, leaves run.current.json
behind; the next run, `fail` or `summary` records it as a failed run of that step,
so a topic that is killed every time is still retried once and then given up.

`python -m studio fail <id>` gives the topic up with the last run's reason, and
`python -m studio summary <id>` says what the runs did, for the operator's record.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import re
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from studio import brain, checks, jobs
from studio.api import Api, ApiError
from studio.media import StepFailed  # the media steps raise it too
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


def job_dir(question_id: str) -> Path:
    if not ID_RE.fullmatch(question_id):
        raise ValueError(f"{question_id!r} is not a question id")
    return jobs.ROOT / question_id


def is_done(job: Path, step: str) -> bool:
    """Whether the step's file is there and reads; one that does not is a step still to run."""
    try:
        if step == "request":
            jobs.read(job / "request.json", Request)
        elif step == "bundle":
            checks.load(job / "bundle.json")
        elif step == "factcheck":
            return jobs.read(job / "factcheck.json", FactCheckReport).status == "passed"
        else:
            return bool(json.loads((job / "posted.json").read_text(encoding="utf-8")).get("id"))
        return True
    except (OSError, ValueError, KeyError):  # missing, cut short, or from an older schema
        return False


@contextlib.contextmanager
def only_run(job: Path):
    """Yield True while this process holds the job's lock, False if another run holds it.

    The kernel drops the lock when the holder exits, however it exits, so a killed
    run never blocks the next one.
    """
    with (job / "run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def record_interrupted(job: Path) -> None:
    """Turn a run.current.json left by a run that never finished into its failed run."""
    current = job / "run.current.json"
    try:
        record = json.loads(current.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current.unlink(missing_ok=True)
        return
    step = record.get("step", "run")
    record.update(result=FAILED, step=step,
                  reason=f"{step} was interrupted: the run stopped before the step finished (a time limit or a kill)")
    record.setdefault("steps", []).append({"step": step, "seconds": None, "result": FAILED})
    with (job / "runs.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    current.unlink()


def run(question_id: str, api: Api | None = None, ask=None) -> tuple[str, str, str]:
    """Run the steps that are not done yet; return (DONE | FAILED | STOPPED, step, reason)."""
    job = job_dir(question_id)
    job.mkdir(parents=True, exist_ok=True)
    with only_run(job) as mine:
        if not mine:
            return STOPPED, "run", "a run of this topic started earlier is still going; it posts the lesson when it finishes"
        record_interrupted(job)
        return _run(job, question_id, api, ask or logged_ask(job))


def _run(job: Path, question_id: str, api: Api | None, ask) -> tuple[str, str, str]:
    started, clock = time.strftime("%Y-%m-%dT%H:%M:%S%z"), time.monotonic()
    done_steps: list[dict] = []
    result, step, reason = DONE, "post", "the lesson was already posted"
    # From the first step that is not done, every step runs: a new bundle makes the fact-check stale.
    first = next((i for i, name in enumerate(STEPS) if not is_done(job, name)), len(STEPS))
    for step in STEPS[first:] if not is_done(job, "post") else ():
        print(f"{step}: running", file=sys.stderr)
        jobs.write(job / "run.current.json", {"started": started, "step": step, "steps": done_steps})
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
    (job / "run.current.json").unlink(missing_ok=True)
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
        readable = mime in IMAGE_TYPES or mime.startswith("text/")  # not a PDF or anything else, yet
        if attachment.get("expired") or not readable or not ID_RE.fullmatch(str(attachment.get("id", ""))):
            skipped.append(name)
            continue
        data = download(api, attachment["url"])
        if data is None or (mime in IMAGE_TYPES and len(data) > IMAGE_MAX_BYTES):
            skipped.append(name)
        elif mime in IMAGE_TYPES:
            path = job / "attachments" / f"{attachment['id']}{IMAGE_TYPES[mime]}"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
            images.append(str(path.resolve()))
        else:
            guides.append(f"{name}:\n{data.decode('utf-8', 'replace').strip()}")
    if skipped:
        print(f"request: left out {len(skipped)} file(s) the brain cannot read, too big, or expired", file=sys.stderr)
    request = Request(topic=" ".join(question["content"].split()), study_guide="\n\n".join(guides), images=images)
    jobs.write(job / "request.json", request)
    return f"{len(images)} image(s), {len(guides)} text file(s)"


def download(api: Api, url: str) -> bytes | None:
    """An attachment's bytes, or None once they are past the retention (410): the topic is still worth a lesson."""
    try:
        return api.download(url)
    except ApiError as e:
        if e.status == 410:
            return None
        raise


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
    try:
        answer = api.post_answer(job.name, body)
    except ApiError as e:
        # The API names the answer when it is this agent's own: an earlier post went through
        # but its reply was lost. That is this job's lesson, so the post is done.
        if e.status != 409 or not e.details.get("answerId"):
            raise
        jobs.write(job / "posted.json", {"id": e.details["answerId"], "questionId": job.name, "recovered": True})
        _claim_videos(job, api)
        return f"lesson was already posted as answer {e.details['answerId']}"
    jobs.write(job / "posted.json", answer)
    _claim_videos(job, api)
    return f"lesson posted as answer {answer.get('id')}"


def _claim_videos(job: Path, api: Api) -> None:
    """Start the videos' first attempt the moment the lesson is posted (studio/videos.py)."""
    from studio import videos  # it imports this module

    try:
        print(f"post: {videos.start_after_post(job, api)}", file=sys.stderr)
    except Exception as e:  # never the lesson's outcome: `videos` claims again, and `recover` finds it
        print(f"post: videos not claimed: {type(e).__name__}: {e}", file=sys.stderr)


STEP_RUNNERS = {"request": make_request, "bundle": make_bundle, "factcheck": make_factcheck, "post": post_lesson}


def lesson_model(job: Path) -> str | None:
    """The model that wrote the bundle: after a fallback, the one that answered."""
    models = [call["model"] for call in read_jsonl(job / "calls.jsonl") if call.get("label") == "bundle"]
    return models[-1] if models else None


# --- after a run ------------------------------------------------------------------


def mark_failed(question_id: str, api: Api | None = None) -> tuple[str, str]:
    """Give the topic up with its last run's reason (POST /api/questions/:id/fail)."""
    job = job_dir(question_id)
    if not job.is_dir():
        return STOPPED, "there is no job for this topic here"
    with only_run(job) as mine:
        if not mine:
            return STOPPED, "a run of this topic is still going"
        record_interrupted(job)
    runs = read_jsonl(job / "runs.jsonl")
    if (job / "posted.json").exists():
        from studio import videos  # it imports this module

        return videos.give_up(job, runs, api)  # the lesson stays; its videos are what failed
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
    running = False
    if job.is_dir():
        with only_run(job) as mine:
            running = not mine
            if mine:
                record_interrupted(job)
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
        timed = [f"{name} {took[name] / 60:.1f} min"
                 for name in ("bundle", "factcheck", "install", "art", "audio", "timing", "render", "upload") if name in took]
        if timed:
            parts.append(", ".join(timed) + ".")
    if (job / "factcheck.json").exists():
        report = jobs.read(job / "factcheck.json", FactCheckReport)
        parts.append(f"Fact-check {report.status} in {report.rounds} round(s), {len(report.corrections)} correction(s).")
    posted = (job / "posted.json").exists()
    parts.append("Lesson posted." if posted else "A run is still going." if running else "No lesson posted.")
    video_runs = [r for r in runs if r.get("part") == "videos"]
    if posted and running:
        parts.append("Videos still being made.")
    elif video_runs:
        last = video_runs[-1]
        parts.append({DONE: "Videos made.", FAILED: f"Videos failed at {last['step']}.",
                      STOPPED: f"Videos stopped at {last['step']}."}[last["result"]])
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
