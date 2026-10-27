"""python -m studio videos <question-id>: a posted lesson's two videos (docs/PLAN.md, section 12).

The media steps (studio/media.py) run once the lesson is posted, in this order:

    art -> locate          at the same time as          audio -> timing
                then timeline -> render -> upload

Each branch starts at its first step that is not done. A step that runs makes every
later one stale, so once either branch has run a step, the timeline, render and
upload all run again. A branch that fails lets the other finish, so a retry keeps
what it made.

The claim. The videos are claimed like a topic (POST /api/answers/:id/video/claim):
`run` starts the first attempt as soon as it posts the lesson, which marks them
RENDERING on the page, and this run renews it before every step, so the API can tell
a render that is alive from one that died with its machine. videos.json holds the
attempt this machine has. A render that goes quiet past VIDEO_STALE_MINUTES is handed
to a later run by `python -m studio recover`, which rebuilds the job here from what
the API stored.

It ends with one line, like `run`:

    DONE upload: <what the last step did>
    FAILED <step>: <reason>        run again to retry that step
    STOPPED <step>: <reason>       nothing to retry: another run has the videos, they
                                   are made or given up, or this job has no posted lesson

On the routine, where one command may take 10 minutes at most, `videos <id>
--background` claims, starts the run detached from the command and returns at once,
and `python -m studio wait <id>` waits up to 9 minutes for its outcome line, or says
RUNNING when it is still going. The packages and models the steps use are installed
when the session starts (.claude/hooks/session-start.sh), never here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError

from studio import brain, jobs, ledger, media
from studio.api import Api, ApiError
from studio.run import (DONE, FAILED, STOPPED, Stop, api_outcome, job_dir, logged_ask, one_line, only_run,
                        read_jsonl, record_interrupted)
from studio.schemas import Bundle, FactCheckReport, Request

BRANCHES = (("art", "locate"), ("audio", "timing"))
TAIL = ("timeline", "render", "upload")
STARTED, RUNNING, RECOVER, NONE = "STARTED", "RUNNING", "RECOVER", "NONE"
CLAIM = "videos.json"  # {answerId, attempt}: the attempt this machine holds
BACKGROUND = "videos.background.json"  # {pid, runs}: the detached run and where its outcome will be
LOG = "videos.log"
WAIT_MINUTES = 9.0  # under the 10 minutes one tool command may take
REPO = Path(__file__).resolve().parent.parent


def unavailable() -> str | None:
    """Why the videos cannot be made here, or None. Checks the step modules exist without importing them."""
    if os.environ.get("STUDIO_VIDEOS", "").strip().lower() in ("off", "0", "false", "no"):
        return "the videos are turned off here (STUDIO_VIDEOS)"
    missing = [n for n in media.MEDIA_STEPS if importlib.util.find_spec(f"studio.steps.{n}") is None]
    if missing:
        return f"the media steps are not all built yet ({', '.join(missing)})"
    return None


def answer_of(job: Path) -> str | None:
    try:
        return json.loads((job / "posted.json").read_text(encoding="utf-8")).get("id") or None
    except (OSError, ValueError, AttributeError):
        return None


def held_attempt(job: Path) -> int | None:
    try:
        return int(json.loads((job / CLAIM).read_text(encoding="utf-8"))["attempt"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def claim(api: Api, job: Path) -> dict:
    """Start this machine's attempt at the videos, or renew the one it holds; Stop when it may not."""
    answer = answer_of(job)
    if not answer:
        raise Stop("this job has no posted lesson, so there are no videos to make")
    try:
        claimed = api.claim_videos(answer, held_attempt(job))
    except ApiError as e:
        result, reason = api_outcome(e)
        raise (Stop if result == STOPPED else media.StepFailed)(f"claim: {reason}") from e
    jobs.write(job / CLAIM, {"answerId": answer, "attempt": claimed["attempt"]})
    return claimed


def start_after_post(job: Path, api: Api) -> str:
    """`run` calls this once the lesson is posted: claim the first attempt, so the page says the videos
    are coming and a routine that dies before `videos` starts is still recovered later. Best effort."""
    why = unavailable()
    if why:
        return f"no videos: {why}"
    try:
        return f"videos claimed as attempt {claim(api, job)['attempt']}"
    except (Stop, media.StepFailed) as e:
        return f"videos not claimed: {e}"


# --- one run of the videos ------------------------------------------------------------


class Record:
    """What this run did, kept in run.current.json while it works and appended to runs.jsonl at the end."""

    def __init__(self, job: Path):
        self.job = job
        self.started, self.clock = time.strftime("%Y-%m-%dT%H:%M:%S%z"), time.monotonic()
        self.steps: list[dict] = []
        self.running: list[str] = []
        self.lock = threading.Lock()

    def _save(self) -> None:
        step = min(self.running, key=media.MEDIA_STEPS.index) if self.running else "videos"
        jobs.write(self.job / "run.current.json",
                   {"started": self.started, "part": "videos", "step": step, "steps": self.steps})

    def begin(self, step: str) -> None:
        print(f"{step}: running", file=sys.stderr)
        with self.lock:
            self.running.append(step)
            self._save()

    def end(self, step: str, seconds: float, result: str) -> None:
        print(f"{step}: {result.lower()} in {seconds:.0f}s", file=sys.stderr)
        with self.lock:
            self.running.remove(step)
            self.steps.append({"step": step, "seconds": seconds, "result": result})
            self._save()

    def finish(self, result: str, step: str, reason: str) -> tuple[str, str, str]:
        reason = one_line(reason)
        record = {"started": self.started, "part": "videos", "seconds": round(time.monotonic() - self.clock, 1),
                  "result": result, "step": step, "reason": reason, "steps": self.steps}
        with (self.job / "runs.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
        (self.job / "run.current.json").unlink(missing_ok=True)
        return result, step, reason


def videos(question_id: str, api: Api | None = None, ask=None) -> tuple[str, str, str]:
    """Run the media steps that are not done; return (DONE | FAILED | STOPPED, step, reason)."""
    job = job_dir(question_id)
    if not (job / "posted.json").exists():
        return STOPPED, "videos", ("no lesson for this topic was posted from this machine; "
                                   "`python -m studio recover` rebuilds one the API hands back")
    with only_run(job) as mine:
        if not mine:
            return STOPPED, "videos", "a run of this topic is still going"
        record_interrupted(job)
        return _videos(job, api, ask or logged_ask(job))


def _videos(job: Path, api: Api | None, ask) -> tuple[str, str, str]:
    record = Record(job)
    why = unavailable()
    if why:
        return record.finish(STOPPED, "videos", why)
    try:
        api = api or Api.from_env()
        claim(api, job)
    except Stop as e:
        return record.finish(STOPPED, "claim", str(e))
    except media.StepFailed as e:
        return record.finish(FAILED, "claim", str(e))
    except ApiError as e:  # not configured
        return record.finish(*_outcome(e, "claim"))

    with ThreadPoolExecutor(len(BRANCHES)) as pool:
        branches = list(pool.map(lambda steps: _run_steps(job, steps, api, ask, record), BRANCHES))
    ran = any(branch[3] for branch in branches)
    unfinished = [branch for branch in branches if branch[0] != DONE]
    if unfinished:
        # Nothing to retry beats something to retry; then the earliest step.
        result, step, reason, _ = min(unfinished, key=lambda b: (b[0] != STOPPED, media.MEDIA_STEPS.index(b[1])))
        return record.finish(result, step, reason)
    result, step, reason, tail_ran = _run_steps(job, TAIL, api, ask, record, fresh=ran)
    if result == DONE and not (ran or tail_ran):
        reason = "the videos were already made"
    return record.finish(result, step, reason)


def _step_done(job: Path, name: str) -> bool:
    try:
        return bool(media.step_module(name).is_done(job))
    except Exception:  # a missing package or a file from an older schema: run it, and let it say why
        return False


def _run_steps(job: Path, steps: tuple[str, ...], api: Api, ask, record: Record,
               fresh: bool = False) -> tuple[str, str, str, bool]:
    """Run steps in order from the first not done (all of them when fresh); (result, step, reason, ran any)."""
    first = 0 if fresh else next((i for i, name in enumerate(steps) if not _step_done(job, name)), len(steps))
    result, step, reason, ran = DONE, steps[-1], "already done", False
    for step in steps[first:]:
        record.begin(step)
        clock = time.monotonic()
        try:
            with ledger.at_step(step):
                claim(api, job)  # the heartbeat: a render that stops renewing is recovered by a later run
                reason = media.step_module(step).run(job, ask=ask) or f"{step} done"
            result = DONE
        except Stop as e:
            result, reason = STOPPED, str(e)
        except (media.StepFailed, brain.BrainError) as e:
            result, reason = FAILED, str(e)
        except ApiError as e:
            result, reason = api_outcome(e)
        except Exception as e:  # a bug, or a step not built: the run still ends with its one line
            traceback.print_exc(file=sys.stderr)
            result, reason = FAILED, f"{type(e).__name__}: {e}"
        ran = True
        record.end(step, round(time.monotonic() - clock, 1), result)
        if result != DONE:
            break
    return result, step, reason, ran


def _outcome(e: ApiError, step: str) -> tuple[str, str, str]:
    result, reason = api_outcome(e)
    return result, step, reason


# --- in the background, for a routine ------------------------------------------------------


def background(question_id: str, api: Api | None = None) -> tuple[str, str, str]:
    """Claim the videos, start `videos` detached from this command, and return at once."""
    job = job_dir(question_id)
    if not (job / "posted.json").exists():
        return STOPPED, "videos", "no lesson for this topic was posted from this machine"
    why = unavailable()
    if why:
        return STOPPED, "videos", why
    if _alive(_background_pid(job)):
        return STOPPED, "videos", "these videos are already being made in the background; wait for them"
    try:
        claimed = claim(api or Api.from_env(), job)
    except Stop as e:
        return STOPPED, "claim", str(e)
    except media.StepFailed as e:
        return FAILED, "claim", str(e)
    except ApiError as e:
        return _outcome(e, "claim")
    command = [sys.executable, "-m", "studio", "videos", question_id]
    with (job / LOG).open("a", encoding="utf-8") as log:
        # A session of its own, so the tool that started it can end its command without ending the run.
        process = subprocess.Popen(command, cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    jobs.write(job / BACKGROUND, {"pid": process.pid, "runs": len(read_jsonl(job / "runs.jsonl"))})
    return STARTED, "videos", (f"attempt {claimed['attempt']}, in the background; "
                               f"`python -m studio wait {question_id}` gives its outcome (log: {job / LOG})")


def _background_pid(job: Path) -> int | None:
    try:
        return int(json.loads((job / BACKGROUND).read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:  # a finished child nobody has reaped is a zombie: it still answers kill(pid, 0)
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait(question_id: str, minutes: float = WAIT_MINUTES, poll_s: float = 5.0) -> tuple[str, str, str]:
    """The background run's outcome line once it ends, or RUNNING after `minutes`."""
    job = job_dir(question_id)
    try:
        started = json.loads((job / BACKGROUND).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return STOPPED, "videos", "no videos were started in the background for this topic here"
    deadline, clock = time.monotonic() + minutes * 60, time.monotonic()
    while True:
        if not _alive(started.get("pid")):
            with only_run(job) as mine:
                if mine:  # else a run started by hand holds the job: wait for it too
                    record_interrupted(job)
                    ours = [r for r in read_jsonl(job / "runs.jsonl")[started.get("runs", 0):] if r.get("part") == "videos"]
                    if ours:
                        return ours[-1]["result"], ours[-1]["step"], ours[-1]["reason"]
                    # It died before writing one (an import that failed, say): record it, so the
                    # retry and `fail` treat it like any other failed run of the videos.
                    reason = f"the background run ended without an outcome; see {job / LOG}"
                    with (job / "runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "part": "videos",
                                              "seconds": None, "result": FAILED, "step": "videos",
                                              "reason": reason, "steps": []}) + "\n")
                    return FAILED, "videos", reason
        if time.monotonic() >= deadline:
            try:
                step = json.loads((job / "run.current.json").read_text(encoding="utf-8")).get("step", "videos")
            except (OSError, ValueError):
                step = "videos"
            return RUNNING, step, (f"still going after {(time.monotonic() - clock) / 60:.0f} min; "
                                   f"run `python -m studio wait {question_id}` again")
        time.sleep(poll_s)


# --- a render that died with its machine ------------------------------------------------------


def recover(api: Api | None = None) -> tuple[str, str, str]:
    """Take one droplet whose render went quiet from the API's queue and rebuild its job here."""
    why = unavailable()
    if why:  # taking one would spend an attempt this machine cannot use
        return NONE, "recover", why
    api = api or Api.from_env()
    try:
        handed = api.videos_queue(1)
    except ApiError as e:
        return _outcome(e, "recover")
    if not handed:
        return NONE, "recover", "no videos to recover"
    item = handed[0]
    question_id = item["questionId"]
    job = job_dir(question_id)
    job.mkdir(parents=True, exist_ok=True)
    with only_run(job) as mine:
        if not mine:
            return STOPPED, question_id, "a run of this topic is still going here"
        try:
            rebuild(job, item)
        except (ValidationError, ValueError, KeyError) as e:
            reason = one_line(f"recover failed: the stored lesson cannot be read back: {e}")
            try:
                api.give_up_videos(item["answerId"], reason, item["attempt"])
            except ApiError:
                pass
            return FAILED, question_id, reason
    return RECOVER, question_id, (f"attempt {item['attempt']} at its videos; start it with "
                                  f"`python -m studio videos {question_id} --background`")


def rebuild(job: Path, item: dict) -> None:
    """Write what the media steps read, from what the API stored with the lesson: never the learner's words."""
    bundle = Bundle.model_validate(item["bundle"])
    factcheck = FactCheckReport.model_validate(item["factcheck"])
    stored = job / "bundle.json"
    if stored.exists() and json.loads(stored.read_text(encoding="utf-8")) != bundle.model_dump(mode="json"):
        # Files made from another bundle are stale for this one: keep them aside rather than mix them in.
        aside = job.with_name(f"{job.name}.before-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(str(job), str(aside))
        job.mkdir()
    if not (job / "request.json").exists():
        jobs.write(job / "request.json", Request(topic=bundle.topic, course=bundle.course))
    jobs.write(job / "bundle.json", bundle)
    jobs.write(job / "factcheck.json", factcheck)
    jobs.write(job / "posted.json", {"id": item["answerId"], "questionId": item["questionId"], "recovered": True})
    jobs.write(job / CLAIM, {"answerId": item["answerId"], "attempt": item["attempt"]})


# --- giving up -----------------------------------------------------------------------------------


def give_up(job: Path, runs: list[dict], api: Api | None = None) -> tuple[str, str]:
    """After a second failure: give the videos up with the last run's reason. The lesson stays."""
    last = runs[-1] if runs else {}
    if last.get("part") != "videos" or last.get("result") != FAILED:
        return STOPPED, "the lesson is posted and the last run of its videos did not fail"
    answer, attempt = answer_of(job), held_attempt(job)
    if not answer or attempt is None:
        return STOPPED, "this job holds no attempt at the videos"
    reason = one_line(f"{last['step']} failed: {last['reason']}")
    try:
        (api or Api.from_env()).give_up_videos(answer, reason, attempt)
    except ApiError as e:
        return api_outcome(e)
    return DONE, f"videos given up: {reason}"
