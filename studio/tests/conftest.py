"""Every studio test starts from the same brain, whatever this machine's environment sets: none in force (Claude
Opus 5.5 at high effort), and none of the variables that used to pick it. A test sets the brain it needs with
monkeypatch.setattr(brain, "_chosen", ...) or brain.chosen()."""

import pytest

from studio import brain


@pytest.fixture(autouse=True)
def default_brain(monkeypatch):
    for name in brain.RETIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(brain, "_chosen", None)
    monkeypatch.setattr(brain, "_effort", None)
