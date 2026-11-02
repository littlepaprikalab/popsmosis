"""Run some steps of an existing job (any folder name) with drive.py's ask, caches and guard.

    python restep.py <job-folder-name> <step> [<step> ...]
"""
import sys
import time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import drive  # noqa: E402  (patches the clients, checks the keys)
from drive import jobs, ledger, media, make_ask, status  # noqa: E402

job = jobs.ROOT / sys.argv[1]
ask = make_ask(job)
for step in sys.argv[2:]:
    status(job, "running", step)
    print(f"{step}: running", file=sys.stderr)
    started = time.monotonic()
    try:
        with ledger.at_step(step):
            line = media.step_module(step).run(job, ask=ask)
    except BaseException as e:
        status(job, "failed", step, f"{type(e).__name__}: {e}")
        raise
    print(f"{step}: done in {time.monotonic() - started:.0f}s: {line}", file=sys.stderr)
status(job, "done", sys.argv[-1])
