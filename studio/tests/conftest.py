"""Every studio test starts from the same brain, whatever this machine's environment sets: none in force (Claude
Opus 5.5 at high effort), and none of the variables that used to pick it. A test sets the brain it needs with
monkeypatch.setattr(brain, "_chosen", ...) or brain.chosen().

And no test spends money by mistake: outside a live run (STUDIO_LIVE=1, or STUDIO_LIVE_AUDIO=1 for the audio checks)
the paid APIs' keys are hidden from every test, so a call that should have been stood in for fails at once for want
of a key instead of reaching Anthropic, Meta, Gemini or ElevenLabs. A test that needs a key sets a fake one."""

import os

import pytest

from studio import brain, knobs

PAID_KEYS = (*brain.CLAUDE_KEY_VARS, brain.META_KEY_VAR, "GEMINI_API_KEY", "ELEVENLABS_API_KEY")
LIVE = os.environ.get("STUDIO_LIVE") == "1" or os.environ.get("STUDIO_LIVE_AUDIO") == "1"


@pytest.fixture(autouse=True)
def default_brain(monkeypatch):
    for name in brain.RETIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(brain, "_chosen", None)
    monkeypatch.setattr(brain, "_effort", None)


@pytest.fixture(autouse=True)
def no_knobs(monkeypatch):
    """None of the knobs an admin sets on /admin/studio in force: a test's own settings put them there."""
    monkeypatch.setattr(knobs, "_set", {})


@pytest.fixture(autouse=True)
def no_paid_keys(monkeypatch):
    if LIVE:
        return
    for name in PAID_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(brain, "_clients", {})  # nor a client made with a real key before the tests began


@pytest.fixture(autouse=True)
def no_planner_record(monkeypatch):
    """No test writes to a real Inkmory: a machine with the planner's key set (a routine's environment)
    would otherwise post to its public record from every test that runs the studio. The planner's own
    tests (test_inkmory.py) set a key and send to a stand-in."""
    monkeypatch.delenv("INKMORY_PLANNER_API_KEY", raising=False)
