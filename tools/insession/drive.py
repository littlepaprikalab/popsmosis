"""Run one topic through the whole pipeline with every Claude call answered in the session.

    python drive.py <job-id>     # jobs/<job-id>/request.json written first (any topic), or a scorecard topic's id

The steps are the studio's own code (studio/steps/*), run in `plan` then `make` order: bundle, audit,
factcheck, then art and locate beside audio and timing, then timeline and render (studio/videos.py, local()).
Each step gets `ask`, which answers a Claude call from jobs/<id>/_insession/answers/<label>-<hash>.json,
the hash taken over the label, system prompt, prompt, schema and images. With no saved answer it writes the
request to _insession/pending/<label>-<hash>/ and waits for a subagent to save one (answer.py save).

Paid calls: Muse Image and SAM 3.1 (Meta) and Gemini's song and voice only, as the owner approved for this
round. Each Muse picture and SAM answer is cached by its request under _insession/paid/, so a step that is
run again never pays twice for the same call. Before every paid call the round's total spend (every job with
an _insession folder) is checked against GUARD_USD. Anthropic and ElevenLabs keys must be unset.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

REPO = Path(os.environ.get("INSESSION_REPO") or Path(__file__).resolve().parents[2])  # a checkout of the studio to run
sys.path.insert(0, str(REPO))

FORBIDDEN = ("ANTHROPIC_API_KEY", "POPSMOSIS_ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "STUDIO_BRAIN_MODEL",
             "STUDIO_BRAIN_EFFORT", "POPSMOSIS_API_URL", "POPSMOSIS_API_KEY", "BRAIN_PROVIDER",
             "SONG_PROVIDER", "ART_PROVIDER")
GUARD_USD = 7.50  # the owner said stop and ask before $8 in all
WAIT_S = 2.0
TIMEOUT_S = 6 * 3600
SETTINGS = {"song": {"provider": "gemini"}, "art": {"claude": "muse"}}

left = [v for v in FORBIDDEN if os.environ.get(v)]
if left:
    sys.exit(f"refusing to run: {', '.join(left)} still set")

from pydantic import ValidationError  # noqa: E402

from studio import brain, gemini, jobs, ledger, media, muse, scorecard  # noqa: E402
from studio import videos as videos_mod  # noqa: E402
from studio import run as run_mod  # noqa: E402
from studio.schemas import AuditReport, FactCheckReport  # noqa: E402
from studio.steps import audio, audit as audit_step, bundle as bundle_step, factcheck as factcheck_step  # noqa: E402


class Pending(BaseException):
    """No answer came in time: the step stops, and a later run picks the saved answers up."""


def _no_claude(*a, **k):
    raise brain.BrainError("no Anthropic API calls in this session: the call should have gone to the session's ask")


brain._ask_claude = _no_claude
brain._ask_meta = _no_claude
brain.settings_problem = lambda: None  # the brain is answered in the session, so it needs no key here


def _blocked(what):
    def fail(*a, **k):
        raise media.StepFailed(f"{what} is not approved for this round", code="not approved")
    return fail


gemini.paint = _blocked("Gemini 3.1 Flash Image")
audio._eleven = _blocked("ElevenLabs")


def round_jobs() -> list[Path]:
    return [p for p in jobs.ROOT.iterdir() if (p / "_insession").is_dir()] if jobs.ROOT.exists() else []


def round_spent() -> float:
    return sum(ledger.spent(j) for j in round_jobs())


_guard = threading.Lock()


def guard(price: float) -> None:
    with _guard:
        total = round_spent()
        if total + price > GUARD_USD:
            raise media.StepFailed(f"the round has spent ${total:.2f}; another ${price:.2f} would pass the "
                                   f"${GUARD_USD:.2f} guard: ask the owner first", code="spend guard")


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _file_digest(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_paint, _segment, _gemini_call = muse.paint, muse.segment, audio._gemini


def cached_paint(job, label, prompt, reference=None):
    key = _digest("paint", label, prompt, _file_digest(reference) if reference is not None else None)
    path = job / "_insession" / "paid" / f"paint-{key[:20]}.png"
    if path.exists():
        print(f"  {label}: Muse Image picture from the cache (not billed again)", file=sys.stderr)
        return path.read_bytes()
    guard(muse.IMAGE_PRICE_USD)
    picture = _paint(job, label, prompt, reference)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    tmp.write_bytes(picture)
    os.replace(tmp, path)
    return picture


def cached_segment(job, label, art, *, phrase=None, box=None):
    key = _digest("segment", label, _file_digest(art), phrase, box.model_dump() if box is not None else None)
    path = job / "_insession" / "paid" / f"sam-{key[:20]}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    guard(muse.SAM_PRICE_USD)
    found = _segment(job, label, art, phrase=phrase, box=box)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    tmp.write_bytes(pickle.dumps(found))
    os.replace(tmp, path)
    return found


def guarded_gemini(path, body):
    guard(0.08 if "generateContent" in path else 0.06)  # a song, or a narration (about $0.04 for 3 minutes)
    return _gemini_call(path, body)


muse.paint, muse.segment, audio._gemini = cached_paint, cached_segment, guarded_gemini


# --- the ask -------------------------------------------------------------------------------------------


def model_for(role: str) -> str:
    return brain.claude_brain()[0] if role == "brain" else brain.CLAUDE[role]["model"]


def make_ask(job: Path):
    base = job / "_insession"
    (base / "answers").mkdir(parents=True, exist_ok=True)
    (base / "pending").mkdir(parents=True, exist_ok=True)

    def ask(role, prompt, schema=None, images=(), *, system=None, label=None):
        if role not in brain.ROLES:
            raise ValueError(f"role must be one of {brain.ROLES}, not {role!r}")
        label = label or role
        schema_json = brain.strict_schema(schema) if schema is not None else None
        images = [Path(p) for p in images]
        key = _digest(label, system or "", prompt, schema_json, [_file_digest(p) for p in images])[:16]
        name = f"{media.slug(label) or 'call'}-{key}"
        answer = base / "answers" / f"{name}.json"
        if not answer.exists():
            folder = base / "pending" / name
            if not folder.exists():
                tmp = base / "pending" / f".{name}.{os.getpid()}-{threading.get_ident()}"
                shutil.rmtree(tmp, ignore_errors=True)
                (tmp / "images").mkdir(parents=True)
                shown = []
                for i, p in enumerate(images, 1):
                    target = tmp / "images" / f"{i}{p.suffix.lower()}"
                    shutil.copyfile(p, target)
                    shown.append(str(folder / "images" / target.name))
                (tmp / "system.md").write_text(system or "", encoding="utf-8")
                (tmp / "prompt.md").write_text(prompt, encoding="utf-8")
                if schema_json is not None:
                    (tmp / "schema.json").write_text(json.dumps(schema_json, indent=1, ensure_ascii=False),
                                                     encoding="utf-8")
                (tmp / "request.json").write_text(json.dumps({
                    "job": job.name, "label": label, "role": role, "model": model_for(role), "name": name,
                    "step": ledger.current_step(),
                    "schema": f"{schema.__module__}:{schema.__qualname__}" if schema is not None else None,
                    "images": shown, "answer": str(answer), "asked": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, indent=1, ensure_ascii=False), encoding="utf-8")
                try:
                    os.rename(tmp, folder)
                except OSError:  # another thread asked the same thing first
                    shutil.rmtree(tmp, ignore_errors=True)
            print(f"  waiting for an answer: {label} ({role})", file=sys.stderr)
            started = time.monotonic()
            while not answer.exists():
                if time.monotonic() - started > TIMEOUT_S:
                    raise Pending(f"no answer for {name} in {TIMEOUT_S}s")
                time.sleep(WAIT_S)
        data = json.loads(answer.read_text(encoding="utf-8"))
        if schema is None:
            return {"text": data["text"]}
        try:
            return schema.model_validate(data).model_dump()
        except ValidationError as e:
            raise brain.InvalidOutput(f"the answer does not fit {schema.__name__}: {e}", json.dumps(data)) from e

    return ask


# --- the run -------------------------------------------------------------------------------------------


def status(job: Path, state: str, step: str = "", detail: str = "") -> None:
    jobs.write(job / "_insession" / "status.json", {"pid": os.getpid(), "state": state, "step": step,
                                                     "detail": detail, "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                     "spent": round(ledger.spent(job), 4)})


LESSON = {"bundle": bundle_step.run, "audit": audit_step.run, "factcheck": factcheck_step.run}


def lesson(job: Path, ask) -> bool:
    for name, step in LESSON.items():
        if run_mod.is_done(job, name):
            continue
        status(job, "running", name)
        print(f"{name}: running", file=sys.stderr)
        started = time.monotonic()
        with ledger.at_step(name):
            result = step(job, ask=ask)
        took = time.monotonic() - started
        if isinstance(result, (AuditReport, FactCheckReport)) and result.status == "failed":
            status(job, "failed", name, result.reason)
            print(f"{name}: failed after {took:.0f}s: {result.reason}", file=sys.stderr)
            return False
        print(f"{name}: done in {took:.0f}s", file=sys.stderr)
    return True


def main(job_id: str) -> int:
    """A job folder with its own request.json (any topic), or a scorecard topic's id."""
    job = jobs.ROOT / job_id
    topic = next((t for t in scorecard.load_topics() if t.id == job_id), None)
    if topic is None and not (job / "request.json").exists():
        sys.exit(f"no jobs/{job_id}/request.json, and {job_id} is not a scorecard topic")
    (job / "_insession").mkdir(parents=True, exist_ok=True)
    if not (job / "request.json").exists():
        jobs.write(job / "request.json", topic.as_request())
    ask = make_ask(job)
    try:
        if not lesson(job, ask):
            return 1
        if jobs.studio_settings(job) != SETTINGS:
            jobs.write(job / jobs.SETTINGS, SETTINGS)
        status(job, "running", "videos")
        result, step, reason = videos_mod.local(job, ask=ask)
        print(f"{result} {step}: {reason}", file=sys.stderr)
        status(job, "done" if result == run_mod.DONE else "failed", step, reason)
        return 0 if result == run_mod.DONE else 1
    except BaseException as e:
        traceback.print_exc()
        status(job, "failed", "", f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
