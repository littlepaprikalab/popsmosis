"""The operator in the simulation: one heartbeat of docs/popsmosis-agent-skill.md, run on the real commands.

Each heartbeat is a routine run on a machine of its own (Machine): a fresh jobs folder, since the cloud container
is new every time, whose detached runs are killed when the heartbeat ends, as the container goes away. It does
what the skill file says, and nothing more:

    Step 0   the topic queue (?limit=1); with none, `recover`
    Step 2   `run <id> --background`, then `wait` until an outcome; FAILED: once more; FAILED again: `fail`;
             RUNNING for longer than the wait budget (the skill's hour): stop waiting
    Step 2b  `videos <id> --background`; FAILED claim: leave them to a later run
    Step 3b  `wait`; FAILED: once more; FAILED again: `fail`
    Step 4   look again, at most three more rounds
    ASK      while a run waits on the session (studio/session.py): a stand-in subagent answers each call from its
             folder, as the test brain would, and `session save` checks and saves the answers

Time is scaled: a `wait` is WAIT_MINUTES (seconds, not nine minutes), and RUNNING_BUDGET of them on the same
step stand for the skill's hour. The studio's own waits are scaled the same way: its tries after an outage
(STUDIO_RETRY_SCALE) and its time limit on a step (STUDIO_STEP_MINUTES, under the budget).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BOOT = Path(__file__).resolve().parent / "boot"
PAID_KEYS = ("ANTHROPIC_API_KEY", "POPSMOSIS_ANTHROPIC_API_KEY", "MODEL_API_KEY", "GEMINI_API_KEY",
             "ELEVENLABS_API_KEY", "POPSMOSIS_API_URL", "POPSMOSIS_API_KEY", "INKMORY_API_URL",
             "INKMORY_OPERATOR_API_KEY", "INKMORY_PLANNER_API_KEY", "ANTHROPIC_BASE_URL",
             "BRAIN_PROVIDER", "STUDIO_BRAIN_MODEL", "STUDIO_BRAIN_EFFORT", "SONG_PROVIDER", "ART_PROVIDER")
FAKE_KEY = "sim-not-a-key"  # every provider is a stand-in; a call that slipped past one is refused, never billed
WAIT_MINUTES = 0.05
RUNNING_BUDGET = 10  # waits on the same step: 30 seconds, the skill's hour
STEP_MINUTES = 0.33  # 20 seconds: under the budget, as 40 minutes is under the hour
SESSION_MODEL = "claude-opus-5-5"


def environment(sim: Path, jobs: Path, api_url: str, extra: dict | None = None, api_key: str = "sk_pop_sim") -> dict:
    env = {k: v for k, v in os.environ.items() if k not in PAID_KEYS}
    env.update({
        "STUDIO_SIM_DIR": str(sim), "STUDIO_JOBS_DIR": str(jobs), "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": os.pathsep.join(p for p in (str(BOOT), os.environ.get("PYTHONPATH", "")) if p),
        "POPSMOSIS_API_URL": api_url, "POPSMOSIS_API_KEY": api_key,
        "POPSMOSIS_ANTHROPIC_API_KEY": FAKE_KEY, "GEMINI_API_KEY": FAKE_KEY, "MODEL_API_KEY": FAKE_KEY,
        "STUDIO_SESSION_WAIT_S": "40",
        "STUDIO_RETRY_SCALE": "0.02", "STUDIO_STEP_MINUTES": str(STEP_MINUTES),
    })
    for name, value in (extra or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


class Machine:
    """One routine run's container: its own jobs folder and processes, gone when the run ends."""

    def __init__(self, sim: Path, n: int, api_url: str, extra_env: dict | None = None, log: list[str] | None = None,
                 api_key: str = "sk_pop_sim"):
        self.jobs = sim / f"machine-{n}"
        self.jobs.mkdir(parents=True, exist_ok=True)
        self.env = environment(sim, self.jobs, api_url, extra_env, api_key)
        self.log: list[str] = [] if log is None else log  # the operator's, so the lines stay in order
        self.output = ""  # the last command's whole output

    def cmd(self, *args: str, timeout: float = 180) -> str:
        """Run `python -m studio <args>` as the operator does; its last line of output."""
        try:
            done = subprocess.run([sys.executable, "-m", "studio", *args], cwd=REPO, env=self.env,
                                  capture_output=True, text=True, timeout=timeout)
            self.output = done.stdout
            lines = done.stdout.strip().splitlines()
            line = lines[-1] if lines else f"(no output, exit {done.returncode}: {done.stderr.strip()[-300:]})"
        except subprocess.TimeoutExpired:
            line = "(the command ran past its time limit)"
        self.log.append(f"$ studio {' '.join(args)}\n  {line}")
        return line

    def go_away(self) -> None:
        """The container ends: every run it detached dies with it."""
        for record in list(self.jobs.glob("*/run.background.json")) + list(self.jobs.glob("*/videos.background.json")):
            try:
                os.killpg(int(json.loads(record.read_text())["pid"]), signal.SIGKILL)
            except (OSError, ValueError, KeyError):
                pass


class Teacher:
    def __init__(self, sim: Path, api_url: str, extra_env: dict | None = None, session: str = "answer",
                 api_key: str = "sk_pop_sim"):
        self.sim, self.api_url, self.extra_env, self.api_key = sim, api_url, extra_env, api_key
        self.session = session  # answer: a subagent answers every call; invalid-first: its first answer is wrong
        self.log: list[str] = []
        self.session_calls = 0

    # --- Step 0 -------------------------------------------------------------------------------------

    def queue(self) -> tuple[int, list[dict]]:
        request = urllib.request.Request(f"{self.api_url}/api/questions/queue?limit=1",
                                         headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read()).get("data", [])
        except urllib.error.HTTPError as e:
            return e.code, []
        except OSError:
            return 0, []

    def heartbeat(self, n: int) -> None:
        machine = Machine(self.sim, n, self.api_url, self.extra_env, self.log, self.api_key)
        self.log.append(f"== heartbeat {n}")
        try:
            for _ in range(4):  # Step 4: at most three more rounds
                status, topics = self.queue()
                if status in (401, 403):
                    self.log.append(f"AUTH {status}")
                    break
                if status != 200:
                    self.log.append(f"ERR {status}: the queue did not answer; the heartbeat stops")
                    break
                recovered = None
                if not topics:
                    line = machine.cmd("recover")
                    if line.startswith("RECOVER "):
                        recovered = line.split()[1].rstrip(":")
                if not topics and not recovered:
                    self.log.append("IDLE")
                    break
                if topics:
                    self.lesson(machine, topics[0]["id"])
                if recovered:
                    self.videos(machine, recovered)
        finally:
            machine.go_away()

    # --- Steps 2, 2b and 3b ----------------------------------------------------------------------------

    def lesson(self, machine: Machine, qid: str) -> None:
        for _ in range(2):  # the run, and once more
            line = machine.cmd("run", qid, "--background")
            if line.startswith("STOPPED") and ("still going" in line or "background" in line):
                line = self.wait(machine, qid)
            elif line.startswith("STARTED"):
                line = self.wait(machine, qid)
            if line.startswith("DONE"):
                self.videos(machine, qid)
                return
            if not line.startswith("FAILED"):  # STOPPED, or waited past the budget
                return
        machine.cmd("fail", qid)

    def videos(self, machine: Machine, qid: str) -> None:
        for _ in range(2):
            line = machine.cmd("videos", qid, "--background")
            if line.startswith("STARTED"):
                line = self.wait(machine, qid)
            elif line.startswith("FAILED claim"):
                return  # the skill: a later run picks them up
            if not line.startswith("FAILED"):
                return
        machine.cmd("fail", qid)

    def wait(self, machine: Machine, qid: str) -> str:
        running, on = 0, None
        while True:
            line = machine.cmd("wait", qid, "--minutes", str(WAIT_MINUTES))
            if line.startswith("ASK"):
                self.answer(machine, qid)
                continue
            if line.startswith("RUNNING"):
                step = line.split()[1].rstrip(":")
                running, on = (running + 1, on) if step == on else (1, step)
                if running >= RUNNING_BUDGET:
                    self.log.append(f"stopped waiting on {qid} after the wait budget (the skill's hour)")
                    return "GAVE UP WAITING"
                continue
            return line

    # --- ASK: the session answers -----------------------------------------------------------------------

    def answer(self, machine: Machine, qid: str) -> None:
        """A subagent per call, from its folder alone: here, the test brain's answer to what the folder asks."""
        fakes = _fakes()
        pending = machine.jobs / qid / "session" / "pending"
        for call in sorted(p for p in pending.iterdir() if (p / "request.json").is_file()) if pending.is_dir() else []:
            req = json.loads((call / "request.json").read_text())
            schema = None
            if req["schema"]:
                module, name = req["schema"].split(":")
                schema = importlib.import_module(module)
                for part in name.split("."):
                    schema = getattr(schema, part)
            data = fakes.answer(req["role"], (call / "prompt.md").read_text(), schema,
                                system=(call / "system.md").read_text(), label=req["label"])
            if self.session == "invalid-first" and not (call / ".tried").exists():
                (call / ".tried").write_text("1")
                (call / "answer.json").write_text('{"half": "an answer"}')  # the save lists what is wrong
            elif schema is None:
                (call / "answer.txt").write_text(data["text"])
            else:
                (call / "answer.json").write_text(json.dumps(data))
            self.session_calls += 1
        machine.cmd("session", "save", qid, "--model", SESSION_MODEL)
        if "NOT VALID" in machine.output:
            machine.log.append("  " + machine.output.strip().replace("\n", "\n  "))
            self.answer(machine, qid)  # handed back to the subagent with what to fix


_FAKES = None


def _fakes():
    global _FAKES
    if _FAKES is None:
        spec = importlib.util.spec_from_file_location("studio_sim_fakes_driver", Path(__file__).parent / "fakes.py")
        _FAKES = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_FAKES)
    return _FAKES
