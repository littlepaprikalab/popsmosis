"""The simulation's stand-ins, put in force in every studio process by boot/sitecustomize.py (tools/sim/README.md).

Nothing here reaches a paid API. The brain's two providers (brain._ask_claude, brain._ask_meta) answer from the
test samples (studio/tests/sample.py, the light reactions) and record each call with a made-up price, so the
ledger, the spend ceiling and the bill work as they do for real. The media steps but the upload are stand-ins that
mark themselves done; the art asks the drawer for each scene and the arrows ask the helper, so a model call fails
in the videos too. The render writes a few bytes where each video goes, and the real upload step sends them to the
fake API's storage.

Faults come from $STUDIO_SIM_DIR/faults.json, shared by every process of a scenario, with counters in
counters.json, so "fail twice" holds across a run and its retry:

    {"brain": {"<label or prefix, or *>": {"fault": "error|overloaded|refused|truncated|invalid|nokey|kill|hang|
                                                     wrong|unsupported", "times": 2}},
     "steps": {"<media step>": {"fault": "fail|kill|hang|slow", "times": 1, "code": "the song provider failed",
                                "part": "song"}}}     # part: the song's alone, as the real steps say (studio/anneal.py)

    error, overloaded   the API did not answer (a BrainError, as anthropic.APIError becomes one)
    refused, truncated  the model declined, or ran out of tokens
    invalid             an answer that does not fit its schema
    nokey               as if the brain's key were missing here
    kill                the process dies mid-call or mid-step (its machine restarted, a kill)
    hang                the call or step never ends (the operator gives up waiting)
    slow                the step takes SLOW_S seconds, then succeeds
    wrong               the fact audit judges every fact wrong
    unsupported         the fact-check finds every line unsupported
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import time
from pathlib import Path
from types import SimpleNamespace

SIM = Path(os.environ["STUDIO_SIM_DIR"])
SLOW_S = 4.0
PRICE_USD = {"brain": 0.30, "helper": 0.02, "judge": 0.10, "drawer": 0.07}  # made up, per call


@contextlib.contextmanager
def _locked():
    with (SIM / "counters.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def take(kind: str, key: str) -> str | None:
    """The fault to answer this call or step with, or None; counts it against its `times`."""
    try:
        plan = json.loads((SIM / "faults.json").read_text())
    except (OSError, ValueError):
        return None
    rules = plan.get(kind) or {}
    name = next((r for r in rules if r == key), None) or next(
        (r for r in rules if r != "*" and key.startswith(r)), None) or ("*" if "*" in rules else None)
    if name is None:
        return None
    rule = rules[name]
    with _locked():
        path = SIM / "counters.json"
        try:
            counters = json.loads(path.read_text())
        except (OSError, ValueError):
            counters = {}
        seen = counters.get(f"{kind}:{name}", 0)
        if seen >= rule.get("times", 1):
            return None
        counters[f"{kind}:{name}"] = seen + 1
        path.write_text(json.dumps(counters))
    _note(f"fault {rule['fault']} on {kind} {key} ({seen + 1}/{rule.get('times', 1)})")
    return rule["fault"]


def _note(line: str) -> None:
    with (SIM / "faults.log").open("a") as log:
        log.write(f"{time.strftime('%H:%M:%S')} pid {os.getpid()}: {line}\n")


def _die() -> None:
    os.kill(os.getpid(), signal.SIGKILL)


# --- the brain ------------------------------------------------------------------------------------------


def answer(role, prompt, schema=None, images=(), *, system=None, label=None, verdict="SUPPORTED", fact="CORRECT"):
    """What the test brain answers a call (studio/tests/test_run.py, ScriptedBrain: a script, the sample bundle,
    every fact correct, every line supported), as a dict; a call with no schema gets a drawing."""
    from studio.tests.test_run import ScriptedBrain

    if schema is None:
        return {"text": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1792 1024"></svg>'}
    data = ScriptedBrain(verdict=verdict, fact=fact)(role, prompt, schema, images, system=system, label=label)
    # Every property present, as the API's structured output and the session's strict schema have it.
    return schema.model_validate(data if isinstance(data, dict) else data.model_dump()).model_dump(mode="json")


def install() -> None:
    from studio import brain, media

    def provider(role, prompt, schema, images, system, label):
        fault = take("brain", label or role)
        claude = role in brain.CLAUDE_ONLY or brain.provider() == "claude"
        model = (brain.claude_brain()[0] if role == "brain" else brain.CLAUDE[role]["model"]) if claude \
            else brain.META.get(role, brain.CLAUDE.get(role, {})).get("model", "muse-spark-1.3")
        if fault == "nokey" or (claude and not brain.claude_key()):
            raise brain.BrainError(f"{model} needs {' or '.join(brain.CLAUDE_KEY_VARS)}")
        if fault == "kill":
            _die()
        if fault == "hang":
            time.sleep(3600)
        if fault in ("error", "overloaded"):
            raise brain.BrainError(f"{model}: Error code: 529 - overloaded_error (simulated)")
        if fault == "refused":
            raise brain.Refused(model, "simulated", "the simulation declined")
        if fault == "truncated":
            raise brain.Truncated(f"{model}: the answer hit max_tokens (simulated)")
        data = answer(role, prompt, schema, images, system=system, label=label,
                      verdict="UNSUPPORTED" if fault == "unsupported" else "SUPPORTED",
                      fact="WRONG" if fault == "wrong" else "CORRECT")
        text = data["text"] if schema is None else json.dumps(data)
        if fault == "invalid":
            text = '{"not": "the schema"}'
        brain._record(brain.Call(label or role, role, model, len(prompt) // 4, len(text) // 4,
                                 PRICE_USD.get(role, 0.05), 0.1, "end_turn"))
        return [text]

    brain._ask_claude = provider
    brain._ask_meta = provider

    real_step_module = media.step_module

    def step_module(name):
        if name == "upload":
            return real_step_module(name)  # the real upload, to the fake API's storage
        if name not in media.MEDIA_STEPS:
            raise LookupError(f"{name!r} is not a media step")
        return _fake_step(name)

    media.step_module = step_module

    # Faults in the studio's own client, for the run against the real API (seam.py), which has none of its own:
    # {"client": {"<Api method>": {"fault": "error", "status": 503, "times": 2}}}.
    from studio import api as api_module

    for name in ("claim", "claim_videos", "post_answer", "upload_url", "set_video", "download", "videos_queue"):
        _client_fault(api_module.Api, name)
    if os.environ.get("STUDIO_SIM_STORAGE") == "discard":  # R2 itself: the bytes go nowhere, the API's URLs are real
        api_module.Api.put_file = lambda self, upload_url, data, headers: None


def _client_fault(cls, name: str) -> None:
    from studio.api import ApiError

    real = getattr(cls, name)

    def call(self, *args, **kwargs):
        if take("client", name):
            raise ApiError(503, "HTTP_ERROR", "the API answered 503")
        return real(self, *args, **kwargs)

    setattr(cls, name, call)


def _fake_step(name: str):
    from studio import jobs, media
    from studio.schemas import Bundle

    marker = f".sim-{name}.done"

    def run(job: Path, ask=None) -> str:
        rule = (json.loads((SIM / "faults.json").read_text()).get("steps") or {}).get(name) or {}
        # A fault of the song's alone ("part": "song") has nothing left to break once the videos went on without it.
        fault = None if rule.get("part") == "song" and media.lesson_only(job) else take("steps", name)
        if name in ("audio", "timing"):
            _narration(job, name)  # the lesson video's voice, made (and timed) beside the song, as the real steps do
        if fault == "kill":
            _die()
        if fault == "hang":
            time.sleep(3600)
        if fault == "slow":
            time.sleep(SLOW_S)
        if fault == "fail":
            failed = media.StepFailed(f"{name}: simulated failure", code=rule.get("code") or "step failed")
            failed.part = rule.get("part") or ""
            raise failed
        (job / marker).unlink(missing_ok=True)
        if name == "art" and ask is not None:
            for scene in jobs.read(job / "bundle.json", Bundle).scenes:
                ask("drawer", f"Draw the scene {scene.id}: {scene.image_prompt}", None, label=f"art {scene.id}")
        if name == "arrows" and ask is not None:
            ask("helper", "Is each arrow where it should be?", None, label="arrows look")
        if name == "render":
            for video in media.videos(job):
                for path in (media.video_path(video), media.poster_path(video)):
                    (job / path).parent.mkdir(parents=True, exist_ok=True)
                    (job / path).write_bytes(f"sim {video} {path}".encode())
        (job / marker).write_text("ok")
        return f"{name} made (simulated)"

    return SimpleNamespace(run=run, is_done=lambda job: (job / marker).exists())


def _narration(job: Path, step: str) -> None:
    """What the real audio and timing steps leave of the narration, which studio/videos.py looks for before it lets
    the song go: the narration made from the bundle's lines, or its timing written."""
    from studio import jobs, media
    from studio.schemas import Bundle
    from studio.steps import audio

    if step == "audio":
        bundle = jobs.read(job / "bundle.json", Bundle)
        (job / "audio").mkdir(exist_ok=True)
        (job / media.audio_path("narration")).write_bytes(b"sim narration")
        jobs.write(job / audio.plan_path("narration"),
                   {"source": [[i, t] for i, t in audio.speech_lines(bundle, "narration")]})
    else:
        (job / "timing").mkdir(exist_ok=True)
        (job / media.timing_path("narration")).write_text("{}")
