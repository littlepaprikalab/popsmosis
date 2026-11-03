"""Put the simulation's stand-ins in force in every Python process started with this folder on PYTHONPATH and
STUDIO_SIM_DIR set: the operator's commands and the runs they detach (tools/sim/README.md). Without
STUDIO_SIM_DIR it does nothing but load the sitecustomize it hides, if the machine has one."""

import importlib.util
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for _entry in sys.path:  # the machine's own sitecustomize, which this one hides on PYTHONPATH, runs first
    _other = Path(_entry or ".").resolve() / "sitecustomize.py"
    if _other.parent != _here and _other.is_file():
        _spec = importlib.util.spec_from_file_location("_machine_sitecustomize", _other)
        _spec.loader.exec_module(importlib.util.module_from_spec(_spec))
        break

if os.environ.get("STUDIO_SIM_DIR"):
    _repo = _here.parents[2]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    # Loaded by its path, so tools/sim never shadows a module of the same name.
    _spec = importlib.util.spec_from_file_location("studio_sim_fakes", _repo / "tools" / "sim" / "fakes.py")
    _fakes = importlib.util.module_from_spec(_spec)
    sys.modules["studio_sim_fakes"] = _fakes
    _spec.loader.exec_module(_fakes)
    _fakes.install()
