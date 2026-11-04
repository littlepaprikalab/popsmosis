"""python -m studio run <question-id>: one topic, from the queue to a posted lesson.

The operator (the routine, following docs/popsmosis-agent-skill.md) takes the topic
from the queue and runs this on its id. The steps, each done once its file exists
(docs/PLAN.md, section 5):

    request    request.json, attachments/   the topic and the files sent with it
    bundle     bundle.json, checks.json
    audit      audit.json                   the facts judged; done once it has passed the facts bundle.json has
    factcheck  factcheck.json               done only once it has passed
    post       posted.json                  the lesson, bundle and fact-check, saved on the API

`run <id> --note "..."` hands the brain an observation from the operator, from what
it has seen in the work (a picture that keeps coming back with text in it): it ends the
bundle prompt, and the fact-check never sees it (NOTE_MAX characters, an observation that
changes no rule; docs/popsmosis-agent-skill.md says when to pass one).

`run` starts at the first step that is not done, so running it again after a
failure retries the step that failed. Before each step but the post it claims the
topic again (POST /api/questions/:id/claim), which renews the queue's lease, and
again every RENEW_S while the step runs, so a slow step (a bundle can take 20
minutes or more, longer than the lease) or a retry keeps it out of an overlapping
run's queue, while a run that dies lets the lease lapse.

A lesson takes 20 minutes or more, and on the routine one command may take 10 at
most: `run <id> --background` starts the run detached from the command and returns
at once, and `python -m studio wait <id>` waits up to 9 minutes for its outcome line
(the same line `run` prints), or says RUNNING when it is still going.

The brain (studio/brain.py) is the one picked for the topic on /learn, else the
default brain set on /admin/studio, both as the claim says; never the environment, which
holds only the keys. Before it claims anything, `run` reads the settings with the agent
key and checks the default brain can run here: if not (its key missing, say), it stops,
as every topic would fail the same way. A topic's own brain that cannot run here fails
the step with the reason, so the topic is given up with it rather than holding up the
queue. The brain is kept in brain.json, so `videos`, `step` and a retry stay on it.

Every run appends what it did to runs.jsonl (each step with the line it ended on, so
`summary` can say what went wrong inside a step that passed, and a run that did not end
DONE with a fixed `code` for why: failure_code) and prints one line, its outcome:

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
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from studio import brain, checks, jobs, knobs, ledger, retry, review, session
from studio.api import Api, ApiError
from studio.ledger import call_row  # noqa: F401  (python -m studio imports it from here)
from studio.media import StepFailed  # the media steps raise it too
from studio.schemas import AuditReport, Bundle, FactCheckReport, Request
from studio.steps import audit as audit_step
from studio.steps import bundle as bundle_step
from studio.steps import factcheck as factcheck_step

STEPS = ledger.LESSON_STEPS  # ("request", "bundle", "audit", "factcheck", "post")
DONE, FAILED, STOPPED = "DONE", "FAILED", "STOPPED"
DEFERRED = "DEFERRED"  # `fail` left the topic, or its videos, to a later sweep (studio/retry.py)
EXIT_CODES = {DONE: 0, FAILED: 1, STOPPED: 3, DEFERRED: 0}
LEASE = "lease.json"  # {runs, maxRuns}: how many runs the API has counted for this topic, and how many it allows
RUN_ID = "run.id"  # this machine's run of the topic, sent on its every claim: the API counts it once
# A lesson the fact audit or the fact-check refused is set aside (bundle.rejected-<n>.json), so the retry writes a
# new one rather than checking the same one again.
REJECTED = ("the fact audit failed", "the fact-check failed")
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
REASON_MAX = 400  # POST /api/questions/:id/fail takes 500, with the step in front
CYCLE_LOG_MAX = 1000  # what an Inkmory cycle log holds (docs/PLAN.md, section 11)
NOTE_MAX = 500  # the operator's note to the brain: an observation or two, never a brief
# The media steps whose line says what went wrong inside them even when they passed: the pictures refused,
# the structures left out, the song lines the aligner was unsure of or that drifted.
NOTED_STEPS = ("art", "locate", "timing")
DETAIL_MAX = 300  # of each step's line in the summary's second paragraph
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
# Anthropic refuses an image over 5 MB of base64; popsmosis takes 4 MB files, so a large photo
# is left out rather than failing the bundle on every try.
IMAGE_MAX_BYTES = 5 * 1024 * 1024 * 3 // 4
# The text files a learner sends become the study guide, sent whole with the bundle and every fact-check round.
# A message may carry 8 MB of text: past this many characters (about 12,000 tokens) the rest is left out, so one
# topic's files cannot make every brain call of its runs cost many times a lesson.
STUDY_GUIDE_MAX_CHARS = 50_000
# A line of the question that starts "Style:" (or "Song style:") is the style the learner wants the song in.
STYLE_LINE = re.compile(r"^[ \t]*(?:song[ \t]+)?style[ \t]*:(.*)$", re.IGNORECASE | re.MULTILINE)
BRAIN_FILE = "brain.json"  # {"brain": a key of brain.CHOICES, "effort": Claude's}: the brain the claim said
# How often a step claims its topic again while it runs: well inside the API's lease (QUESTION_CLAIM_MINUTES, 20 by
# default), which a bundle of 20 minutes or more outlasts when the claim before each step is all that renews it.
RENEW_S = 300.0
BACKGROUND = "run.background.json"  # {pid, runs}: the detached run of `run --background`, and runs.jsonl's length then
OUTCOME = "run.outcome"  # its outcome line, as `run` prints it
LOG = "run.log"  # what it says as it goes
OUTCOME_LINE = re.compile(r"(DONE|FAILED|STOPPED) (\S+): (.*)")


class Stop(Exception):
    """Nothing a retry can fix: the topic was deleted, answered, given up or taken, or the key was refused."""


def failure_code(e: BaseException) -> str:
    """Why a step did not succeed, in a few fixed words of the studio's own. The message stays with the run (the
    operator's log, `show`, the reason a topic is given up with); this is what `summary` says, since the summary goes
    on Inkmory, which is public, and a message can quote a rejected line, a picture's writing or a provider's answer,
    all made from what a learner sent."""
    if isinstance(e, StepFailed):  # the spend ceiling, the fact-check, a provider, a media step: each names its own
        return e.code
    if isinstance(e, session.NoAnswer):
        return "the session did not answer"
    if isinstance(e, brain.Refused):
        return "the model declined"
    if isinstance(e, brain.Truncated):
        return "the answer was cut off"
    if isinstance(e, brain.InvalidOutput):
        return "the answer did not fit its schema"
    if isinstance(e, brain.BrainError):
        return "the model call failed"
    if isinstance(e, ApiError):
        if e.status == 0:
            return "the API did not answer"
        if e.status == 404:
            return "the topic was deleted"
        if e.status == 409:
            return "the topic was answered, given up or taken"
        if e.status in (401, 403):
            return "the API refused the key"
        if e.code == "UNAVAILABLE" and not e.details.get("retryable"):
            # The API's own 503: a service it needs is not set up (its file storage), which no wait or later sweep
            # fixes, unlike a proxy's 503 when it is down (upload.py's _retrying reads it the same way).
            return "the API is not set up for it"
        return f"the API answered {e.status}" if 100 <= e.status <= 599 else "the API failed"
    if isinstance(e, Stop):
        return "stopped"
    return "a studio error"


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
        elif step == "audit":
            return audit_step.is_done(job)
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
    last = read_jsonl(job / "runs.jsonl")[-1:] if (job / "runs.jsonl").exists() else []
    if last and last[0].get("started") == record.get("started") and last[0].get("part") == record.get("part"):
        current.unlink(missing_ok=True)  # recorded already (the time limit ended it), then written again in passing
        return
    record.update(result=FAILED, step=step, code="interrupted",
                  reason=f"{step} was interrupted: the run stopped before the step finished (a time limit or a kill)")
    record.setdefault("steps", []).append({"step": step, "seconds": None, "result": FAILED})
    with (job / "runs.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    current.unlink()


def run(question_id: str, api: Api | None = None, ask=None, note: str = "",
        session_answers: bool = False) -> tuple[str, str, str]:
    """Run the steps that are not done yet; return (DONE | FAILED | STOPPED, step, reason). note: the
    operator's observation for the brain, given to the bundle if this run writes one. session_answers: an operator
    waits on this run (`run --background`), so a model call the API fails is answered by its session while the
    admin's knob allows it (studio/session.py), and a brain whose key is missing here is no reason to stop."""
    note = clean_note(note)
    keys = True  # whether the brain's key must be here
    if ask is None:  # the real brain: the default brain is checked before the topic is claimed
        retired = brain.retired_problem()
        if retired:
            return STOPPED, "run", f"the studio's settings are wrong: {retired}"
        try:
            api = api or Api.from_env()
            studio = api.studio_settings()
        except ApiError as e:
            result, reason = api_outcome(e)
            # Down or failing, the API is no reason to fail this topic: nothing about it was tried.
            return STOPPED, "run", reason if result == STOPPED else f"the studio's settings could not be read: {reason}"
        knobs.use(studio)
        keys = not session_answers  # no session waits on the run: the brain's key must be here, whatever the brain
        with brain.chosen(*default_brain(studio)):  # what the session answers depends on the brain (session.chosen())
            needed = keys or not session.answers()
            problem = brain.settings_problem(keys=needed)
            missing = brain.settings_problem() if not needed and not problem else None
        if problem:
            return STOPPED, "run", f"the studio's settings are wrong: {problem}"
        if missing:
            print(f"brain: {missing}; its calls go to the operator's session (studio/session.py)", file=sys.stderr)
    job = job_dir(question_id)
    job.mkdir(parents=True, exist_ok=True)
    with only_run(job) as mine:
        if not mine:
            return STOPPED, "run", "a run of this topic started earlier is still going; it posts the lesson when it finishes"
        record_interrupted(job)
        with job_chosen(job):
            return _run(job, question_id, api, ask or logged_ask(job, session_answers=session_answers),
                        check_brain=ask is None, note=note, keys=keys)


def default_brain(studio: dict | None) -> tuple[str | None, str | None]:
    """The default brain and Claude's effort an admin set on /admin/studio, as the API said ({} from an older API:
    the studio's own defaults)."""
    settings = (studio or {}).get("brain") if isinstance(studio, dict) else None
    settings = settings if isinstance(settings, dict) else {}
    return settings.get("default") or None, settings.get("effort") or None


def default_only(studio: dict | None) -> bool:
    """Whether an admin has the default brain make every topic, whatever brain it was sent to (/admin/studio): the
    switch for when a brain's account is over its quota."""
    settings = (studio or {}).get("brain") if isinstance(studio, dict) else None
    return isinstance(settings, dict) and settings.get("defaultOnly") is True


def _brain_file(job: Path) -> dict:
    try:
        data = json.loads((job / BRAIN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def job_brain(job: Path) -> str | None:
    """The brain the job is made on, as its claim said (brain.json); None: the studio's default."""
    return _brain_file(job).get("brain") or None


def job_effort(job: Path) -> str | None:
    """Claude's effort for the job, as its claim said; None: the default."""
    return _brain_file(job).get("effort") or None


def job_chosen(job: Path):
    """brain.chosen() for the job's brain and effort."""
    return brain.chosen(job_brain(job), job_effort(job))


def keep_brain(job: Path, name: str | None, effort: str | None = None) -> None:
    """Save the brain the job is made on with it, for `videos`, `step` and a retry."""
    record = {"brain": name or None, "effort": effort or None}
    if not (job / BRAIN_FILE).exists() or _brain_file(job) != record:
        jobs.write(job / BRAIN_FILE, record)


def use_brain(job: Path, question: dict, check: bool, keys: bool = True) -> None:
    """Make the rest of this run on the brain the claim says: the topic's own, else the default brain, except that
    a lesson already begun (its bundle written) stays on the brain that began it, so its fact-check and its videos
    are that brain's whatever the default has become since. With check, a brain that cannot run here stops the run
    when it is the default, since every topic would fail the same way, and fails the step when it is the topic's
    own, so the topic is given up with the reason and the queue moves on. A lesson whose brain can no longer run
    here goes on with the default. While an admin has the default make every topic (default_only()), it makes this
    one from here on, whatever brain the topic was sent to or its lesson was begun on."""
    studio = question.get("studio")
    default, effort = default_brain(studio)
    only = default_only(studio)
    own = None if only else question.get("brain") or None
    if own and own not in brain.CHOICES:  # an API newer than this studio: not this topic's fault
        raise Stop(f"the studio's settings are wrong: this topic asks for the brain {own!r}, which this studio does "
                   f"not have ({', '.join(brain.CHOICES)}): update the studio")
    begun = (job_brain(job) if not own and not only and (job / BRAIN_FILE).exists() and is_done(job, "bundle")
             else None)
    _put_brain(job, own or begun or default, effort, "picked for this topic" if own else
               "the brain that began this lesson" if begun else
               "the default, which makes every topic for now (/admin/studio)" if only else "the default")
    jobs.keep_studio_settings(job, studio)  # who paints, for `videos`
    if not check:
        return
    # keys=False: a session waits on the run; whether it answers this brain's calls is known only with the brain in
    # force (session.chosen() sends a Claude topic there, never a Muse one), so it is asked of each brain put in force.
    problem = brain.settings_problem(keys=keys or not session.answers())
    if problem and begun and begun != (default or brain.DEFAULT_BRAIN):
        print(f"brain: {problem}; the lesson goes on with the default", file=sys.stderr)
        _put_brain(job, default, effort, "the default")
        problem = brain.settings_problem(keys=keys or not session.answers())
    if problem and own:
        raise StepFailed(f"the studio cannot run this topic's brain here: {problem}", code="brain cannot run here")
    if problem:
        raise Stop(f"the studio's settings are wrong: {problem}")


def _put_brain(job: Path, name: str | None, effort: str | None, why: str) -> None:
    """Keep the brain with the job and put it in force, saying so when the job first has it or it changes."""
    if not (job / BRAIN_FILE).exists() or (name or brain.DEFAULT_BRAIN) != (job_brain(job) or brain.DEFAULT_BRAIN):
        label = brain.CHOICES.get(name or brain.DEFAULT_BRAIN, {}).get("name", name)
        print(f"brain: {label}, {why}", file=sys.stderr)
    keep_brain(job, name, effort)
    brain.choose(name, effort)


def clean_note(note: str) -> str:
    """The note on one line, at most NOTE_MAX characters: a note too long is cut, never the reason a topic fails."""
    note = " ".join(str(note or "").split())
    if len(note) > NOTE_MAX:
        print(f"note: cut to {NOTE_MAX} characters from {len(note)}", file=sys.stderr)
        note = note[: NOTE_MAX - 1] + "…"
    return note


def _run(job: Path, question_id: str, api: Api | None, ask, check_brain: bool = False,
         note: str = "", keys: bool = True) -> tuple[str, str, str]:
    started, clock = time.strftime("%Y-%m-%dT%H:%M:%S%z"), time.monotonic()
    done_steps: list[dict] = []
    reporter = ledger.Reporter()
    result, step, reason, code = DONE, "post", "the lesson was already posted", ""
    # From the first step that is not done, every step runs: a new bundle makes the fact-check stale. All but the
    # audit, when the bundle step's early audit passed the facts the bundle kept (studio/steps/audit.py): until Nov 2
    # it was billed twice on every topic.
    first = next((i for i, name in enumerate(STEPS) if not is_done(job, name)), len(STEPS))
    running: dict[str, float] = {}  # the step under way and when it started, for the time limit
    stop_watch = retry.watch(job, lambda: dict(running), lambda step, why: retry.end_process(job, step, why))
    for step in STEPS[first:] if not is_done(job, "post") else ():
        if step == "audit" and is_done(job, "audit"):
            print("audit: done beside the bundle", file=sys.stderr)
            continue
        tried, first_try = 0, time.monotonic()
        while True:  # once, and again after a wait while an outage explains the failure (studio/retry.py)
            print(f"{step}: running", file=sys.stderr)
            jobs.write(job / "run.current.json", {"started": started, "step": step, "steps": done_steps})
            step_clock = time.monotonic()
            running[step] = first_try  # the time limit is the step's, its tries and the waits between them together
            try:
                api = api or Api.from_env()
                with ledger.at_step(step):
                    question = claim(api, question_id, job) if step != "post" else None
                    if question is not None:
                        use_brain(job, question, check_brain, keys=keys)
                    if step != "post":  # the post makes the answer, which the videos' reports go on
                        report_progress(reporter, job, api, {step: step_clock})
                    with renewing(api, question_id) if step != "post" else contextlib.nullcontext():
                        if step == "bundle":
                            reason = make_bundle(job, api, question, ask, note=note)
                        else:
                            reason = STEP_RUNNERS[step](job, api, question, ask)
                result, code = DONE, ""
            except Stop as e:
                result, reason, code = STOPPED, str(e), failure_code(e.__cause__ if isinstance(e.__cause__, ApiError) else e)
            except (StepFailed, brain.BrainError) as e:
                # A claim that failed is the API's failure, not the step's: its cause says which.
                result, reason, code = FAILED, str(e), failure_code(e.__cause__ if isinstance(e.__cause__, ApiError) else e)
            except ApiError as e:
                (result, reason), code = api_outcome(e), failure_code(e)
            except Exception as e:  # a bug: the run still ends with its one line
                traceback.print_exc(file=sys.stderr)
                result, reason, code = FAILED, f"{type(e).__name__}: {e}", failure_code(e)
            running.pop(step, None)
            seconds = round(time.monotonic() - step_clock, 1)
            done_steps.append({"step": step, "seconds": seconds, "result": result,
                               **({"detail": one_line(reason)} if result == DONE and reason else {}),
                               **({"code": code} if code and result == FAILED else {})})
            print(f"{step}: {result.lower()} in {seconds:.0f}s", file=sys.stderr)
            if result == FAILED and api is not None:
                # The page says so until the retry starts it again, and bills the try that failed. A failed post
                # too: the topic has no lesson yet, so its card would otherwise show the fact-check still running.
                report_progress(reporter, job, api, {}, failed=(step,), ending=(step, seconds))
            waits = retry.waits(code) if result == FAILED else ()
            if tried >= len(waits):
                break
            print(f"{step}: {code}, so it is tried again in {waits[tried]:.0f}s "
                  f"({tried + 2} of {len(waits) + 1}): {one_line(reason)}", file=sys.stderr)
            time.sleep(waits[tried])
            tried += 1
        if result != DONE:
            break
    stop_watch()
    if result == FAILED and code in REJECTED:
        set_aside(job)
    reason = one_line(reason)
    with retry.finishing():  # the time limit no longer ends the run: it is recorded once
        if done_steps:  # a run with nothing left to do is not one the record should count
            record = {"started": started, "seconds": round(time.monotonic() - clock, 1), "result": result, "step": step,
                      "reason": reason, "steps": done_steps, **({"code": code} if code else {})}
            if note:
                record["note"] = note
            with (job / "runs.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
        (job / "run.current.json").unlink(missing_ok=True)
    return result, step, reason


def claim(api: Api, question_id: str, job: Path | None = None) -> dict:
    """Take or renew the topic's lease; keep with the job how many runs the API has handed it out for and allows
    (LEASE), which `fail` reads before it gives the topic up."""
    try:
        # Every claim of this machine's run carries its id, so the API counts the run once, whatever is sent again
        # (a claim whose answer was lost, the operator's retry here).
        question = api.claim(question_id, run_id=run_id(job)) if job is not None else api.claim(question_id)
    except ApiError as e:
        result, reason = api_outcome(e)
        raise (Stop if result == STOPPED else StepFailed)(f"claim: {reason}") from e
    if job is not None and isinstance(question, dict) and isinstance(question.get("maxRuns"), int):
        lease = {"runs": question.get("runs"), "maxRuns": question["maxRuns"]}
        if _read_json(job / LEASE) != lease:
            jobs.write(job / LEASE, lease)
    return question


def run_id(job: Path) -> str:
    """This machine's run of the topic (RUN_ID), made on its first claim."""
    path = job / RUN_ID
    try:
        found = path.read_text(encoding="utf-8").strip()
        if ID_RE.fullmatch(found) and len(found) >= 8:
            return found
    except OSError:
        pass
    made = uuid.uuid4().hex
    path.write_text(made + "\n", encoding="utf-8")
    return made


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def runs_left(job: Path) -> int:
    """The runs the API still allows this topic after this one (0 when it did not say: an API without the count)."""
    lease = _read_json(job / LEASE)
    runs, most = lease.get("runs"), lease.get("maxRuns")
    return max(0, most - runs) if isinstance(runs, int) and isinstance(most, int) else 0


def set_aside(job: Path) -> None:
    """A lesson the checks refused: its bundle kept as bundle.rejected-<n>.json, and any answers the operator's
    session gave for it with it, so the retry writes the lesson again instead of checking the same one."""
    n = len(list(job.glob("bundle.rejected-*.json"))) + 1
    (job / "bundle.json").replace(job / f"bundle.rejected-{n}.json")
    answers = session.root(job) / "answers"
    if answers.is_dir():
        answers.replace(session.root(job) / f"answers.rejected-{n}")
    print(f"the lesson was refused, so the retry writes it again (kept as bundle.rejected-{n}.json)", file=sys.stderr)


@contextlib.contextmanager
def renewing(api: Api, question_id: str):
    """Claim the topic again every RENEW_S while the block runs, on a thread of its own, so a step longer than the
    API's lease keeps the topic out of an overlapping run's queue. Only the lease: the settings and the brain a
    claim carries are taken before each step, never in the middle of one. Best effort: a renewal that fails is said
    once and tried again at the next; one the API refuses for good (the topic gone, answered, given up or taken)
    ends them, and the next step's claim stops the run."""
    stop, every = threading.Event(), RENEW_S

    def renew() -> None:
        said = False
        while not stop.wait(every):
            try:
                api.claim(question_id)
            except ApiError as e:
                result, reason = api_outcome(e)
                if result == STOPPED:
                    print(f"claim: no longer renewed during the step: {reason}", file=sys.stderr)
                    return
                if not said:
                    print(f"claim: a renewal during the step failed, tried again every {every / 60:g} min: {e}",
                          file=sys.stderr)
                    said = True

    thread = threading.Thread(target=renew, name=f"renew {question_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def report_progress(reporter: ledger.Reporter, job: Path, api: Api, running: dict[str, float],
                    failed: tuple[str, ...] = (), ending: tuple[str, float] | None = None) -> None:
    """Tell the page where the lesson is (studio/ledger.py's progress()), and whether the videos come after it.
    Best effort: a report that does not get through is never a step's outcome (ledger.Reporter)."""
    from studio import videos  # it imports this module

    def send():
        report = ledger.progress(job, "lesson", running, failed, ending)
        report["videos"] = videos.unavailable(job) is None
        api.report_progress(job.name, report)

    reporter.send(send)


def api_outcome(e: ApiError) -> tuple[str, str]:
    if e.status == 404:
        return STOPPED, "the topic was deleted"
    if e.status == 409:  # answered, given up, or claimed by another agent
        return STOPPED, e.message
    if e.status in (401, 403) or e.code == "NOT_CONFIGURED":
        return STOPPED, f"the API refused the key: {e}"
    return FAILED, str(e)


# --- in the background, for a routine ------------------------------------------------------


def background(question_id: str, note: str = "") -> tuple[str, str, str]:
    """Start `run` detached from this command, and return at once; `wait` gives its outcome line."""
    from studio.videos import REPO, STARTED, _alive  # it imports this module

    job = job_dir(question_id)
    job.mkdir(parents=True, exist_ok=True)
    if _alive(_background_pid(job)):
        return STOPPED, "run", "this topic's lesson is already being made in the background; wait for it"
    with only_run(job) as mine:
        if not mine:
            return STOPPED, "run", "a run of this topic started earlier is still going; it posts the lesson when it finishes"
    # --session: the operator waits on this run, so its session answers a model call the API fails (studio/session.py).
    command = [sys.executable, "-m", "studio", "run", question_id, "--session", *(["--note", note] if note else [])]
    with (job / OUTCOME).open("w", encoding="utf-8") as outcome, (job / LOG).open("a", encoding="utf-8") as log:
        # A session of its own, so the tool that started it can end its command without ending the run.
        process = subprocess.Popen(command, cwd=REPO, stdin=subprocess.DEVNULL, stdout=outcome, stderr=log,
                                   start_new_session=True)
    jobs.write(job / BACKGROUND, {"pid": process.pid, "runs": len(read_jsonl(job / "runs.jsonl"))})
    return STARTED, "run", (f"the lesson, in the background; `python -m studio wait {question_id}` gives its outcome "
                            f"(log: {job / LOG})")


def _background_pid(job: Path) -> int | None:
    try:
        return int(json.loads((job / BACKGROUND).read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def waits_on_lesson(question_id: str) -> bool:
    """Whether `wait` is for the lesson: its run went to the background after the videos' did, or theirs never."""
    from studio.videos import BACKGROUND as VIDEOS  # it imports this module

    job = job_dir(question_id)
    lesson, videos = job / BACKGROUND, job / VIDEOS
    return lesson.exists() and (not videos.exists() or lesson.stat().st_mtime_ns >= videos.stat().st_mtime_ns)


def wait(question_id: str, minutes: float | None = None, poll_s: float = 5.0) -> tuple[str, str, str]:
    """The background lesson's outcome line once its run ends, ASK while model calls wait for the operator's session
    (studio/session.py), or RUNNING after `minutes` (the videos' WAIT_MINUTES, under the 10 minutes one tool command
    may take)."""
    from studio.videos import RUNNING, WAIT_MINUTES, _alive  # it imports this module

    job = job_dir(question_id)
    try:
        started = json.loads((job / BACKGROUND).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return STOPPED, "run", "no lesson was started in the background for this topic here"
    clock = time.monotonic()
    deadline = clock + (WAIT_MINUTES if minutes is None else minutes) * 60
    asking = session.Asking(job)
    while True:
        alive = _alive(started.get("pid"))
        if not alive:
            with only_run(job) as mine:
                if mine:  # else a run started by hand holds the job: wait for it too
                    return _ended(job, started)
        if alive and asking.settled():
            return session.ASK, current_step(job, "run"), asking.reason(question_id)
        if time.monotonic() >= deadline:
            return RUNNING, current_step(job, "run"), (f"still going after {(time.monotonic() - clock) / 60:.0f} min; "
                                                       f"run `python -m studio wait {question_id}` again")
        time.sleep(min(poll_s, session.SETTLE_S))


def current_step(job: Path, default: str) -> str:
    """The step a run of the job is on now, as run.current.json says."""
    try:
        return json.loads((job / "run.current.json").read_text(encoding="utf-8")).get("step", default)
    except (OSError, ValueError, AttributeError):
        return default


def _ended(job: Path, started: dict) -> tuple[str, str, str]:
    """How a background run that has ended went: the line it printed, else what runs.jsonl has of it."""
    try:
        lines = (job / OUTCOME).read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        lines = []
    found = OUTCOME_LINE.fullmatch(lines[-1]) if lines else None
    if found:  # every outcome, a stop before the topic was claimed included, which runs.jsonl never has
        return found.group(1), found.group(2), found.group(3)
    record_interrupted(job)  # killed in a step: a failed run of that step
    ours = [r for r in read_jsonl(job / "runs.jsonl")[started.get("runs", 0):] if r.get("part") != "videos"]
    if ours:
        return ours[-1]["result"], ours[-1]["step"], ours[-1]["reason"]
    # It died before it could say anything (an import that failed, say): record it, so the retry and `fail`
    # treat it like any other failed run.
    reason = f"the background run ended without an outcome; see {job / LOG}"
    with (job / "runs.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": None, "result": FAILED,
                              "step": "run", "reason": reason, "code": "the background run ended without an outcome",
                              "steps": []}) + "\n")
    return FAILED, "run", reason


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
        # The path is built from the id checked above, never from a URL the API handed back.
        data = download(api, f"/api/attachments/{attachment['id']}")
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
    study_guide = "\n\n".join(guides)
    if len(study_guide) > STUDY_GUIDE_MAX_CHARS:
        print(f"request: the text files come to {len(study_guide):,} characters; the study guide keeps the first "
              f"{STUDY_GUIDE_MAX_CHARS:,}", file=sys.stderr)
        study_guide = study_guide[:STUDY_GUIDE_MAX_CHARS]
    topic, style = split_style(question["content"])
    # The style field on /learn, when the learner filled it in, is what they asked for; a "Style:" line typed into
    # the topic is the fallback, for a request sent without the field (or before it existed).
    asked = " ".join(str(question.get("songStyle") or "").split())
    request = Request(topic=topic, song_style=asked or style, study_guide=study_guide, images=images)
    jobs.write(job / "request.json", request)
    return f"{len(images)} image(s), {len(guides)} text file(s)" + (", a song style" if asked or style else "")


def split_style(content: str) -> tuple[str, str]:
    """The question's topic, and the song style its "Style:" lines ask for ("" when none does).

    A question that is only a style line keeps it all as its topic, since the rest would be empty.
    """
    style = " ".join(" ".join(found.split()) for found in STYLE_LINE.findall(content)).strip()
    topic = " ".join(STYLE_LINE.sub("", content).split())
    if not style or not topic:
        return " ".join(content.split()), ""
    return topic, style


DOWNLOAD_WAITS_S = (2.0, 6.0)  # before the second and third try of a file the API did not hand over


def download(api: Api, url: str) -> bytes | None:
    """An attachment's bytes, or None when they cannot be had: past the retention (410), deleted (404), or still not
    handed over after DOWNLOAD_WAITS_S. The topic is still worth a lesson without the file; only a refused key
    stops the run."""
    for wait in (*DOWNLOAD_WAITS_S, None):
        try:
            return api.download(url)
        except ApiError as e:
            if e.status in (401, 403):
                raise
            if e.status in (404, 410) or wait is None:
                if e.status not in (404, 410):
                    print(f"request: a file could not be downloaded ({e}); the lesson is made without it", file=sys.stderr)
                return None
            time.sleep(wait * retry._scale())
    return None


def make_bundle(job: Path, api: Api, question: dict, ask, note: str = "") -> str:
    bundle = bundle_step.run(job, ask=ask, note=note)
    return f"{bundle.course} / {bundle.topic}"


def make_audit(job: Path, api: Api, question: dict, ask) -> str:
    report = audit_step.run(job, ask=ask)
    if report.status == "failed":
        raise StepFailed(report.reason, code="the fact audit failed")
    return audit_step.describe(report)


def make_factcheck(job: Path, api: Api, question: dict, ask) -> str:
    report = factcheck_step.run(job, ask=ask)
    if report.status == "failed":
        raise StepFailed(report.reason, code="the fact-check failed")
    dropped = len(factcheck_step.dropped_tags(report))
    still = len(factcheck_step.dropped_motions(report))
    sung = sum(c.by == "songwriter" for c in report.corrections)
    return f"passed in {report.rounds} round(s), {len(report.corrections)} correction(s)" + (
        f", {sung} by the songwriter" if sung else "") + (
        f", {dropped} tag(s) dropped" if dropped else "") + (f", {still} motion(s) left out" if still else "")


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
    # How long each step took and what it cost, shown under the droplet (studio/ledger.py).
    breakdown = ledger.try_breakdown(job, "lesson")
    if breakdown:
        body["breakdown"] = breakdown
    # What the code checks found in the bundle, for an admin's review of the droplet (studio/review.py).
    found = review.code_checks(job)
    if found is not None:
        body["checks"] = found
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


STEP_RUNNERS = {"request": make_request, "bundle": make_bundle, "audit": make_audit, "factcheck": make_factcheck,
                "post": post_lesson}


def lesson_model(job: Path) -> str | None:
    """The model that wrote the bundle: after a fallback, the one that answered."""
    models = [call["model"] for call in ledger.read(job) if call.get("label") == "bundle"]
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
    left = runs_left(job)
    if retry.deferrable(runs[-1].get("code")) and left:
        # An outage, a kill or a hang: another machine, later, may well get through. The lease lapses, the queue
        # hands the topic out again, and the API gives it up itself once its runs are used (QUESTION_MAX_RUNS).
        # A later run here, on a machine that outlives the heartbeat, counts as a new one too.
        (job / LEASE).unlink(missing_ok=True)
        (job / RUN_ID).unlink(missing_ok=True)
        return DEFERRED, (f"not given up: {runs[-1]['step']} failed ({runs[-1]['code']}), which a later try may get "
                          f"past, and the topic has {left} run(s) left; a later sweep takes it up again")
    reason = f"{runs[-1]['step']} failed: {runs[-1]['reason']}"
    try:
        (api or Api.from_env()).fail(question_id, reason)
    except ApiError as e:
        result, why = api_outcome(e)
        return result, why
    return DONE, reason


def summary(question_id: str) -> str:
    """What the runs did, for the operator's record: a paragraph of at most 1,000 characters for its
    cycle log, then, once a media step has run, a second one with what the art, locate and timing
    steps said last (inside_steps()), for its journal: what went wrong inside a step that passed.

    Inkmory is public, so it holds only what is public already or the studio's own words. It names
    the topic by the bundle's course and topic, never by request.json, which holds the learner's own
    words; a run that failed by its step and fixed code (failure_code), never its reason, which can
    quote a rejected line; and what a step said with anything in quotes taken out.
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
        told = [f"run {i} {r['result']} at {r['step']}" + (f" ({r['code']})" if r["result"] != DONE and r.get("code") else "")
                for i, r in enumerate(runs, 1)]
        parts.append(f"{len(runs)} run(s): " + "; ".join(told) + ".")
        took = {}
        for r in runs:
            for s in r["steps"]:
                if s["result"] == DONE:
                    took[s["step"]] = s["seconds"]
        timed = [f"{name} {took[name] / 60:.1f} min"
                 for name in ("bundle", "audit", "factcheck", "install", "art", "audio", "timing", "render", "upload")
                 if name in took]
        if timed:
            parts.append(", ".join(timed) + ".")
    if (job / "audit.json").exists():
        audit = jobs.read(job / "audit.json", AuditReport)
        parts.append(f"Fact audit {audit.status}: " + (audit_step.describe(audit) if audit.status == "passed"
                                                       else _short(audit.reason, 160)) + ".")
    if (job / "factcheck.json").exists():
        report = jobs.read(job / "factcheck.json", FactCheckReport)
        dropped = len(factcheck_step.dropped_tags(report))
        parts.append(f"Fact-check {report.status} in {report.rounds} round(s), {len(report.corrections)} correction(s)"
                     + (f", {dropped} tag(s) dropped." if dropped else "."))
    posted = (job / "posted.json").exists()
    parts.append("Lesson posted." if posted else "A run is still going." if running else "No lesson posted.")
    video_runs = [r for r in runs if r.get("part") == "videos"]
    if posted and running:
        parts.append("Videos still being made.")
    elif video_runs:
        last = video_runs[-1]
        parts.append({DONE: "Videos made.", FAILED: f"Videos failed at {last['step']}.",
                      STOPPED: f"Videos stopped at {last['step']}."}[last["result"]])
    calls = [c for c in ledger.read(job) if c.get("role") != "local"]  # Demucs and MMS run here, unbilled
    if calls:
        parts.append(f"{len(calls)} model call(s), {ledger.cost_text(calls)}.")
        answered = session.summary(calls)  # the calls the operator's session answered after the API failed
        if answered:
            parts.append(answered)
    inside = inside_steps(runs)
    return _short(" ".join(parts), CYCLE_LOG_MAX) + (f"\n\n{inside}" if inside else "")


def inside_steps(runs: list[dict]) -> str:
    """What the media steps said as they ended, the last time each passed: a picture refused for a part out of
    place, a label the helper saw wrong, a structure left out, a song line the aligner was unsure of or that drifted. Their failures are in
    the first paragraph; these are the problems a step lived with."""
    said: dict[str, str] = {}
    for r in runs:
        for s in r.get("steps", []):
            if s.get("step") in NOTED_STEPS and s.get("result") == DONE and s.get("detail"):
                said[s["step"]] = s["detail"]
    if not said:
        return ""
    return _short("Inside the steps, as each last said: " + " ".join(
        f"{step}: {_short(unquoted(said[step]), DETAIL_MAX).rstrip('.')}." for step in NOTED_STEPS if step in said),
        CYCLE_LOG_MAX)


# A quotation: in single quotes (never an apostrophe, which has a letter before it), double or curly quotes, or a
# parenthesis holding one ("writing in it ('ATP', 'Ben')"); and one a cut left open, which runs to the end of the line.
QUOTED = re.compile(r"\([^()]*(?<!\w)'[^()]*(?:\)|$)|(?<!\w)'[^']*(?:'(?!\w)|$)|\"[^\"]*(?:\"|$)|“[^”]*(?:”|$)|‘[^’]*(?:’|$)")
# What a provider said when it would not make a picture: its words, which may echo the description it was given.
PROVIDER_WORDS = re.compile(r"(declined the picture|refused the picture|refused the song|blocked the song's prompt):[^;]*")


def unquoted(text: str) -> str:
    """A step's line without what it quotes (a label found wrong in a picture) or a provider's own words, for Inkmory:
    each is cut short on the way, so a quote can arrive with no end, and runs to the end of the line then."""
    return " ".join(QUOTED.sub("…", PROVIDER_WORDS.sub(r"\1", text)).split())


# --- shared with python -m studio ---------------------------------------------------


def logged_ask(job: Path, session_answers: bool = False):
    """brain.ask, logging each call to the job's calls.jsonl (tagged with its step) and stderr as it ends. Each call
    first checks the topic is under its spend ceiling (ledger.check_ceiling). session_answers: an operator waits on
    this run, so a call the API fails (brain.BrainError) is asked of its session instead, while the admin's knob
    allows it (studio/session.py), and logged with the session's bill."""

    def ask(*args, **kwargs):
        ledger.check_ceiling(job)
        # Only this thread's calls: another thread may be asking at the same time.
        with brain.collect() as mine:
            try:
                role = kwargs.get("role", args[0] if args else "")
                if session_answers and session.first(role):  # the admin's choice: Claude in the session
                    return session.ask(job, session.Chosen(f"{session.VIA} is session"), *args, **kwargs)
                try:
                    return brain.ask(*args, **kwargs)
                except brain.BrainError as e:
                    if not (session_answers and session.allowed()):
                        raise
                    return session.ask(job, e, *args, **kwargs)
            finally:
                ledger.log(job, mine)

    return ask


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines() if row.strip()]


def one_line(text: str) -> str:
    return _short(" ".join(str(text).split()), REASON_MAX)


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
