"""python -m studio videos <question-id>: a posted lesson's two videos (docs/PLAN.md, section 12).

STUDIO_VIDEOS, an admin's knob on /admin/studio (studio/knobs.py): `on` (the default) makes both, `lesson` the
lesson video alone, with no song and no hook cut (studio/media.py, lesson_only()), and `off` none: the droplet
stays text. A bundle written while it was `lesson` has no song (studio/steps/bundle.py), so its videos are the
lesson's alone whatever it says later.

The media steps (studio/media.py) run once the lesson is posted, in this order:

    art -> locate          at the same time as          audio -> timing
                then timeline -> arrows -> render -> upload

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

The videos run on the topic's brain (brain.json, which `run` keeps from the claim, and
`recover` from the API), as the lesson did. Who paints the art is the admin's setting for
that brain's provider (settings.json, kept from the claim that starts each run): Gemini
3.1 Flash Image or Muse Image, with SAM 3.1 finding the structures, or Claude's own SVG.

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

from studio import brain, jobs, knobs, ledger, media, retry, session
from studio.api import Api, ApiError
from studio.run import (BRAIN_FILE, DEFERRED, DONE, FAILED, STOPPED, Stop, api_outcome, default_brain, failure_code,
                        job_chosen, job_dir, keep_brain, lesson_model, logged_ask, one_line, only_run, read_jsonl,
                        record_interrupted)
from studio.schemas import Bundle, FactCheckReport, Request

BRANCHES = (("art", "locate"), ("audio", "timing"))
TAIL = ("timeline", "arrows", "render", "upload")
LOCAL_TAIL = ("timeline", "arrows", "render")  # a local job has no posted lesson to upload the videos to
STARTED, RUNNING, RECOVER, NONE = "STARTED", "RUNNING", "RECOVER", "NONE"
UNAVAILABLE = "the videos cannot be made here"  # the code of a run that stopped on unavailable()
CLAIM = "videos.json"  # {answerId, attempt, maxAttempts}: the attempt this machine holds, and how many the API allows
BACKGROUND = "videos.background.json"  # {pid, runs}: the detached run and where its outcome will be
LOG = "videos.log"
WAIT_MINUTES = 9.0  # under the 10 minutes one tool command may take
REPO = Path(__file__).resolve().parent.parent


def unavailable(job: Path | None = None, studio: dict | None = None, session_answers: bool = False) -> str | None:
    """Why the videos cannot be made here, or None. Checks the step modules exist without importing them, and the
    keys the brain in force and the art's painter need: the job's painter, or without a job, the one the studio
    settings name (the defaults without them). session_answers: an operator waits on the run, so its session answers
    the model calls the API cannot while the admin's knob allows it (studio/session.py), and the brain's key is not
    needed here; a painter's and the song's still are, since no session paints or sings."""
    if knobs.env("STUDIO_VIDEOS").strip().lower() in ("off", "0", "false", "no"):
        return f"the videos are turned off here ({knobs.named('STUDIO_VIDEOS')})"
    missing = [n for n in media.MEDIA_STEPS if importlib.util.find_spec(f"studio.steps.{n}") is None]
    if missing:
        return f"the media steps are not all built yet ({', '.join(missing)})"
    settings = jobs.studio_settings(job) if job else studio or {}
    keys = not (session_answers and session.allowed())
    problem = brain.retired_problem() or brain.settings_problem(keys=keys) or brain.art_problem(settings.get("art"))
    song = settings.get("song") if isinstance(settings.get("song"), dict) else {}
    lyria = not media.lesson_only(job) and (song.get("provider") or media.DEFAULT_SONG_PROVIDER) == "gemini"  # the lesson alone has no song
    if not problem and lyria and not os.environ.get("GEMINI_API_KEY"):
        problem = ("the song needs GEMINI_API_KEY: Lyria sings it (an admin can pick ElevenLabs on /admin/studio)")
    if problem:
        return f"the studio's settings are wrong: {problem}"
    return None


def refresh_settings(job: Path, api: Api | None) -> Api | None:
    """Keep the studio's settings as they are now with the job, before its keys are checked: the painter the
    lesson's last claim named may have been changed since, because this machine lacked its key. Best effort: the
    claim after it says the same, or fails with the reason. Without them, the knobs the job's last claim carried
    are in force, never this machine's environment alone (a detached run starts with none). Returns the Api, made
    from the environment if need be."""
    try:
        api = api or Api.from_env()
        studio = api.studio_settings()
    except ApiError:
        studio = {}
    if studio:  # {} from an API too old to have them, or one that did not answer: keep what the job has
        jobs.keep_studio_settings(job, studio)
    else:
        knobs.use(jobs.studio_settings(job))
    return api


def answer_of(job: Path) -> str | None:
    try:
        return json.loads((job / "posted.json").read_text(encoding="utf-8")).get("id") or None
    except (OSError, ValueError, AttributeError):
        return None


def attempts_left(job: Path) -> int:
    """The attempts at the videos the API still allows after the one held (0 when it did not say)."""
    try:
        claimed = json.loads((job / CLAIM).read_text(encoding="utf-8"))
        return max(0, int(claimed["maxAttempts"]) - int(claimed["attempt"]))
    except (OSError, ValueError, KeyError, TypeError):
        return 0


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
    jobs.write(job / CLAIM, {"answerId": answer, "attempt": claimed["attempt"],
                             **({"maxAttempts": claimed["maxAttempts"]} if isinstance(claimed.get("maxAttempts"), int) else {})})
    return claimed


def start_after_post(job: Path, api: Api) -> str:
    """`run` calls this once the lesson is posted: claim the first attempt, so the page says the videos
    are coming and a routine that dies before `videos` starts is still recovered later. Best effort."""
    why = unavailable(job, session_answers=True)  # `videos --background` is what makes them
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
        self.running: dict[str, float] = {}  # step -> the time.monotonic() it started at
        self.failed: set[str] = set()  # steps whose last try in this run failed
        self.lock = threading.Lock()
        self.reporting = threading.Lock()  # one report at a time, so an older one never lands after a newer
        self.reporter = ledger.Reporter()

    def _save(self) -> None:
        step = min(self.running, key=media.MEDIA_STEPS.index) if self.running else "videos"
        jobs.write(self.job / "run.current.json",
                   {"started": self.started, "part": "videos", "step": step, "steps": self.steps})

    def begin(self, step: str) -> None:
        print(f"{step}: running", file=sys.stderr)
        with self.lock:
            self.running[step] = time.monotonic()
            self.failed.discard(step)
            self._save()

    def end(self, step: str, seconds: float, result: str, reason: str = "", code: str = "") -> None:
        """reason: the line the step ended on. A done step's is kept with the run too (an art step names the
        pictures it refused), so `summary` can tell the operator what went wrong inside a step that passed. code: a
        step that did not succeed says why in fixed words (studio/run.py, failure_code)."""
        print(f"{step}: {result.lower()} in {seconds:.0f}s" + (f": {reason}" if reason else ""), file=sys.stderr)
        with self.lock:
            del self.running[step]
            if result == FAILED:
                self.failed.add(step)
            self.steps.append({"step": step, "seconds": seconds, "result": result,
                               **({"detail": one_line(reason)} if reason and result == DONE else {}),
                               **({"code": code} if code and result != DONE else {})})
            self._save()

    def report(self, api: Api) -> None:
        """Tell the page where the render is: the steps running now, the failed ones and the videos' bill so
        far (studio/ledger.py's progress()). Best effort: a report that does not get through is never a step's
        outcome, and one from a run that was taken over is refused by the API."""
        answer = answer_of(self.job)
        if not answer:
            return
        with self.reporting:
            with self.lock:
                running, failed = dict(self.running), tuple(self.failed)
            self.reporter.send(lambda: api.report_video_progress(
                answer, held_attempt(self.job), ledger.progress(self.job, "videos", running, failed)))

    def finish(self, result: str, step: str, reason: str, code: str = "") -> tuple[str, str, str]:
        """code: why the run did not end DONE, when no step of it says (a claim refused); else the failed step's."""
        reason = one_line(reason)
        if result != DONE and not code:
            code = next((s.get("code", "") for s in reversed(self.steps) if s["step"] == step and s["result"] != DONE), "")
        record = {"started": self.started, "part": "videos", "seconds": round(time.monotonic() - self.clock, 1),
                  "result": result, "step": step, "reason": reason, "steps": self.steps,
                  **({"code": code} if code and result != DONE else {})}
        with (self.job / "runs.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
        (self.job / "run.current.json").unlink(missing_ok=True)
        return result, step, reason


def videos(question_id: str, api: Api | None = None, ask=None, session_answers: bool = False) -> tuple[str, str, str]:
    """Run the media steps that are not done; return (DONE | FAILED | STOPPED, step, reason). session_answers: an
    operator waits on this run (`videos --background`), so its session answers a model call the API fails, while the
    admin's knob allows it (studio/session.py)."""
    job = job_dir(question_id)
    if not (job / "posted.json").exists():
        return STOPPED, "videos", ("no lesson for this topic was posted from this machine; "
                                   "`python -m studio recover` rebuilds one the API hands back")
    with only_run(job) as mine:
        if not mine:
            return STOPPED, "videos", "a run of this topic is still going"
        record_interrupted(job)
        with job_chosen(job):
            return _videos(job, api, ask or logged_ask(job, session_answers=session_answers),
                           session_answers=session_answers)


def _videos(job: Path, api: Api | None, ask, session_answers: bool = False) -> tuple[str, str, str]:
    record = Record(job)
    api = refresh_settings(job, api)
    why = unavailable(job, session_answers=session_answers)
    if why:
        return record.finish(STOPPED, "videos", why, code=UNAVAILABLE)
    try:
        api = api or Api.from_env()
        claimed = claim(api, job)
    except Stop as e:
        return record.finish(STOPPED, "claim", str(e), code=failure_code(e.__cause__ or e))
    except media.StepFailed as e:
        return record.finish(FAILED, "claim", str(e), code=failure_code(e.__cause__ or e))
    except ApiError as e:  # not configured
        return record.finish(*_outcome(e, "claim"), code=failure_code(e))
    # Who paints is the admin's setting as of this claim, for the whole run: the heartbeats do not change it.
    jobs.keep_studio_settings(job, claimed.get("studio"))
    if not (job / BRAIN_FILE).exists():  # a job from before the brain was kept: the one its lesson was written on
        default, effort = default_brain(claimed.get("studio"))
        name = claimed.get("brain") or brain.choice_for_model(lesson_model(job) or claimed.get("model")) or default
        keep_brain(job, name, effort)
        brain.choose(name, effort)
    why = unavailable(job, session_answers=session_answers)  # a painter the setting just changed to may need a key
    if why:  # the render goes quiet and `recover` hands it on once the setting or the key is fixed
        return record.finish(STOPPED, "videos", why, code=UNAVAILABLE)

    # No step runs past its time limit: one that does ends the run, recorded as failed there (studio/retry.py).
    stop_watch = retry.watch(job, lambda: dict(record.running), lambda step, why: retry.end_process(job, step, why))
    try:
        return record.finish(*_make(job, api, ask, record, TAIL))
    finally:
        stop_watch()


def _make(job: Path, api: Api | None, ask, record: Record, tail: tuple[str, ...]) -> tuple[str, str, str]:
    """The branches side by side, then the tail; (DONE | FAILED | STOPPED, step, reason)."""
    with ThreadPoolExecutor(len(BRANCHES)) as pool:
        branches = list(pool.map(lambda steps: _run_steps(job, steps, api, ask, record), BRANCHES))
    ran = any(branch[3] for branch in branches)
    unfinished = [branch for branch in branches if branch[0] != DONE]
    if unfinished:
        # Nothing to retry beats something to retry; then the earliest step.
        result, step, reason, _ = min(unfinished, key=lambda b: (b[0] != STOPPED, media.MEDIA_STEPS.index(b[1])))
        return result, step, reason
    result, step, reason, tail_ran = _run_steps(job, tail, api, ask, record, fresh=ran)
    if result == DONE and not (ran or tail_ran):
        reason = "the videos were already made"
    return result, step, reason


def local(job: Path, ask=None) -> tuple[str, str, str]:
    """`python -m studio make <job>`: a local job's videos, made as `videos` makes them but with no API
    (no claim, no upload): art and locate alongside audio and timing, then the timeline and the
    render. For trying the pipeline on a job from `plan` (docs/test-rounds.md). Each branch resumes
    at its first step not done, and the run is kept in runs.jsonl, as for `videos`."""
    with only_run(job) as mine:
        if not mine:
            return STOPPED, "videos", "a run of this job is still going"
        record_interrupted(job)
        record = Record(job)
        knobs.use(jobs.studio_settings(job))  # as its last claim said, if it had one
        with job_chosen(job):  # the brain the job was made on (`plan --brain`), else the default
            why = unavailable(job)  # that brain's keys, as `videos` checks them: a Muse job needs no Claude key
            if why:
                return record.finish(STOPPED, "videos", why, code=UNAVAILABLE)
            return record.finish(*_make(job, None, ask or logged_ask(job), record, LOCAL_TAIL))


def _step_done(job: Path, name: str) -> bool:
    try:
        return bool(media.step_module(name).is_done(job))
    except Exception:  # a missing package or a file from an older schema: run it, and let it say why
        return False


def _run_steps(job: Path, steps: tuple[str, ...], api: Api | None, ask, record: Record,
               fresh: bool = False) -> tuple[str, str, str, bool]:
    """Run steps in order from the first not done (all of them when fresh); (result, step, reason, ran any). A step
    whose failure an outage explains is tried again after a wait, and a song or narration the timing step deleted is
    made again once (studio/retry.py), from the first step of these that is then not done."""
    i = 0 if fresh else next((i for i, name in enumerate(steps) if not _step_done(job, name)), len(steps))
    result, step, reason, ran = DONE, steps[-1], "already done", False
    tried: dict[str, int] = {}  # step -> further tries made in this run
    while i < len(steps):
        step = steps[i]
        record.begin(step)
        clock = time.monotonic()
        code = ""
        try:
            with ledger.at_step(step):
                if api is not None:  # a local job (`make`) has no claim to renew, and no page to tell
                    claim(api, job)  # the heartbeat: a render that stops renewing is recovered by a later run
                    record.report(api)
                reason = media.step_module(step).run(job, ask=ask) or f"{step} done"
            result = DONE
        except Stop as e:
            result, reason, code = STOPPED, str(e), failure_code(e.__cause__ or e)
        except (media.StepFailed, brain.BrainError) as e:
            # A claim that failed is the API's failure, not the step's: its cause says which.
            result, reason, code = FAILED, str(e), failure_code(e.__cause__ if isinstance(e.__cause__, ApiError) else e)
        except ApiError as e:
            (result, reason), code = api_outcome(e), failure_code(e)
        except Exception as e:  # a bug, or a step not built: the run still ends with its one line
            traceback.print_exc(file=sys.stderr)
            result, reason, code = FAILED, f"{type(e).__name__}: {e}", failure_code(e)
        ran = True
        record.end(step, round(time.monotonic() - clock, 1), result, one_line(reason), code)
        # Done or failed, the page hears it now: the other branch may run on for minutes. The upload's READY
        # already carried the last bill, and a run that was stopped has nothing to report.
        if api is not None and (result == FAILED or (result == DONE and step != "upload")):
            record.report(api)
        if result == DONE:
            i += 1
            continue
        waits = retry.waits(code) if result == FAILED else ()
        if tried.get(step, 0) >= len(waits):
            break
        wait = waits[tried.get(step, 0)]
        tried[step] = tried.get(step, 0) + 1
        print(f"{step}: {code}, so it is tried again in {wait:.0f}s: {one_line(reason)}", file=sys.stderr)
        time.sleep(wait)
        # From the failed step, or an earlier one it undid (the timing step deletes a song that drifted).
        i = min(i, next((j for j, name in enumerate(steps) if not _step_done(job, name)), i))
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
    api = refresh_settings(job, api)
    with job_chosen(job):  # the detached run reads brain.json itself
        why = unavailable(job, session_answers=True)
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
    # --session: the operator waits on this run, so its session answers a model call the API fails (studio/session.py).
    command = [sys.executable, "-m", "studio", "videos", question_id, "--session"]
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
    """The background run's outcome line once it ends, ASK while model calls wait for the operator's session
    (studio/session.py), or RUNNING after `minutes`."""
    job = job_dir(question_id)
    try:
        started = json.loads((job / BACKGROUND).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return STOPPED, "videos", "no videos were started in the background for this topic here"
    deadline, clock = time.monotonic() + minutes * 60, time.monotonic()
    asking = session.Asking(job)
    while True:
        alive = _alive(started.get("pid"))
        if alive and asking.settled():
            return session.ASK, _step_now(job), asking.reason(question_id)
        if not alive:
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
                                              "reason": reason, "code": "the background run ended without an outcome",
                                              "steps": []}) + "\n")
                    return FAILED, "videos", reason
        if time.monotonic() >= deadline:
            return RUNNING, _step_now(job), (f"still going after {(time.monotonic() - clock) / 60:.0f} min; "
                                             f"run `python -m studio wait {question_id}` again")
        time.sleep(min(poll_s, session.SETTLE_S))


def _step_now(job: Path) -> str:
    try:
        return json.loads((job / "run.current.json").read_text(encoding="utf-8")).get("step", "videos")
    except (OSError, ValueError, AttributeError):
        return "videos"


# --- a render that died with its machine ------------------------------------------------------


def recover(api: Api | None = None) -> tuple[str, str, str]:
    """Take one droplet whose render went quiet from the API's queue and rebuild its job here."""
    try:
        api = api or Api.from_env()
        studio = api.studio_settings()
    except ApiError as e:
        return _outcome(e, "recover")
    knobs.use(studio)
    with brain.chosen(*default_brain(studio)):
        why = unavailable(studio=studio, session_answers=True)  # `videos --background` makes what it takes
    if why:  # taking one would spend an attempt this machine cannot use
        return NONE, "recover", why
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
    if stored.exists() and _read_as_now(stored) != bundle.model_dump(mode="json"):
        # Files made from another bundle are stale for this one: keep them aside rather than mix them in.
        aside = job.with_name(f"{job.name}.before-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(str(job), str(aside))
        job.mkdir()
    if not (job / "request.json").exists():
        jobs.write(job / "request.json", Request(topic=bundle.topic, course=bundle.course))
    jobs.write(job / "bundle.json", bundle)
    jobs.write(job / "factcheck.json", factcheck)
    jobs.write(job / "posted.json", {"id": item["answerId"], "questionId": item["questionId"], "recovered": True})
    # The brain the lesson was written on: the topic's own, else the one its answer records, else today's default.
    default, effort = default_brain(item.get("studio"))
    keep_brain(job, item.get("brain") or brain.choice_for_model(item.get("model")) or default, effort)
    jobs.keep_studio_settings(job, item.get("studio"))  # who paints its art
    jobs.write(job / CLAIM, {"answerId": item["answerId"], "attempt": item["attempt"],
                             **({"maxAttempts": item["maxAttempts"]} if isinstance(item.get("maxAttempts"), int) else {})})


def _read_as_now(path: Path) -> dict | None:
    """A job's bundle.json as the schema now reads it, so one written before a field existed (Scene.axes) is still
    the same lesson, with that field's default; None when it does not read."""
    try:
        return Bundle.model_validate_json(path.read_text(encoding="utf-8")).model_dump(mode="json")
    except (OSError, ValueError):
        return None


# --- giving up -----------------------------------------------------------------------------------


def give_up(job: Path, runs: list[dict], api: Api | None = None) -> tuple[str, str]:
    """After a second failure: give the videos up with the last run's reason. The lesson stays."""
    last = runs[-1] if runs else {}
    if last.get("part") != "videos" or last.get("result") != FAILED:
        return STOPPED, "the lesson is posted and the last run of its videos did not fail"
    answer, attempt = answer_of(job), held_attempt(job)
    if not answer or attempt is None:
        return STOPPED, "this job holds no attempt at the videos"
    left = attempts_left(job)
    if retry.deferrable(last.get("code")) and left:
        # An outage, a kill or a hang: the render is left RENDERING, goes quiet, and once it is stale the video
        # queue hands it to a later sweep as its next attempt (`recover`), until VIDEO_MAX_ATTEMPTS.
        return DEFERRED, (f"not given up: {last['step']} failed ({last['code']}), which a later try may get past, "
                          f"and {left} attempt(s) are left; a later sweep takes the videos up once they go stale")
    reason = one_line(f"{last['step']} failed: {last['reason']}")
    try:
        # With what the failed tries took and cost, so the page's bill counts them too.
        (api or Api.from_env()).give_up_videos(answer, reason, attempt, breakdown=ledger.try_breakdown(job, "videos"))
    except ApiError as e:
        return api_outcome(e)
    return DONE, f"videos given up: {reason}"
