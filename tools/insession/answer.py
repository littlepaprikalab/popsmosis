"""The session's side of drive.py: list, claim, check and save the answers to pending Claude calls.

    python answer.py pending [--all]          the calls waiting for an answer (unclaimed, or all)
    python answer.py claim <call>...          mark calls as handed to a subagent
    python answer.py show <call>              a call's request, for a subagent
    python answer.py check <call> <file>      validate an answer against the call's schema
    python answer.py save <call> <file>       validate it and save it as the call's answer
    python answer.py wait [--stable S]        block until unclaimed calls have been still for S seconds, or a driver ends
    python answer.py spend                    each round job's spend, from its calls.jsonl

<call> is a pending call's folder (jobs/<id>/_insession/pending/<name>). For a call with no schema (an SVG drawing)
the file is the answer's text; a .json file holding {"text": ...} works too.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
JOBS = Path(os.environ.get("STUDIO_JOBS_DIR") or REPO / "jobs")


def calls(include_claimed: bool = False) -> list[Path]:
    out = []
    for job in sorted(JOBS.iterdir()) if JOBS.exists() else []:
        pending = job / "_insession" / "pending"
        if not pending.is_dir():
            continue
        for call in sorted(pending.iterdir()):
            if call.name.startswith(".") or not (call / "request.json").exists():
                continue
            if include_claimed or not (call / "claimed").exists():
                out.append(call)
    return out


def request(call: Path) -> dict:
    return json.loads((Path(call) / "request.json").read_text(encoding="utf-8"))


def load_answer(call: Path, file: Path):
    req = request(call)
    text = Path(file).read_text(encoding="utf-8")
    if req["schema"] is None:
        if str(file).endswith(".json"):
            try:
                data = json.loads(text)
                if isinstance(data, dict) and isinstance(data.get("text"), str):
                    return data
            except ValueError:
                pass
        return {"text": text}
    return json.loads(text)


def problems(call: Path, data) -> list[str]:
    req = request(call)
    if req["schema"] is None:
        return [] if isinstance(data.get("text"), str) and data["text"].strip() else ["the answer is empty"]
    import jsonschema
    from pydantic import ValidationError

    found = []
    schema = json.loads((Path(call) / "schema.json").read_text(encoding="utf-8"))
    for error in jsonschema.Draft202012Validator(schema).iter_errors(data):
        where = "/".join(str(p) for p in error.absolute_path) or "(top)"
        found.append(f"schema: at {where}: {error.message[:300]}")
    module, name = req["schema"].split(":")
    model = importlib.import_module(module)
    for part in name.split("."):
        model = getattr(model, part)
    try:
        model.model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            where = "/".join(str(p) for p in err["loc"]) or "(top)"
            found.append(f"validator: at {where}: {err['msg'][:300]}")
    return found


def main(argv: list[str]) -> int:
    command, args = argv[0], argv[1:]
    if command == "pending":
        for call in calls("--all" in args):
            req = request(call)
            size = sum((call / f).stat().st_size for f in ("system.md", "prompt.md") if (call / f).exists())
            claimed = " (claimed)" if (call / "claimed").exists() else ""
            print(f"{req['job']:<24} {req['role']:<7} {req['label']:<40} {len(req['images'])} img {size:>7} ch  "
                  f"{call}{claimed}")
        return 0
    if command == "groups":
        groups = {}
        for call in calls():
            req = request(call)
            groups.setdefault((req["job"], kind(req), req["role"]), []).append(str(call))
        for (job, k, role), paths in sorted(groups.items()):
            print(f"== {job} | {k} | {role} | {len(paths)}")
            for path in paths:
                print(path)
        return 0
    if command == "claim":
        for call in args:
            (Path(call) / "claimed").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
        return 0
    if command == "show":
        call = Path(args[0])
        print(json.dumps(request(call), indent=1))
        return 0
    if command in ("check", "save"):
        call, file = Path(args[0]), Path(args[1])
        try:
            data = load_answer(call, file)
        except ValueError as e:
            print(f"NOT VALID: the file is not JSON: {e}")
            return 1
        found = problems(call, data)
        if found:
            print("NOT VALID: fix only these, then try again:\n" + "\n".join(f"- {p}" for p in found))
            return 1
        if command == "check":
            print("valid")
            return 0
        target = Path(request(call)["answer"])
        tmp = target.with_name(f".{target.name}.partial")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
        done = call.parent.parent / "done"
        done.mkdir(exist_ok=True)
        os.replace(call, done / call.name)
        print(f"saved {target.name}")
        return 0
    if command == "wait":
        stable = float(args[args.index("--stable") + 1]) if "--stable" in args else 45.0
        start_states = states()
        seen, since = None, time.monotonic()
        while True:
            now = states()
            ended = [j for j, s in now.items() if s != start_states.get(j) and s.split(":")[0] != "running"]
            if ended:
                print("driver ended: " + ", ".join(f"{j} {now[j]}" for j in ended))
                return 0
            open_calls = tuple(str(c) for c in calls())
            if open_calls != seen:
                seen, since = open_calls, time.monotonic()
            elif open_calls and time.monotonic() - since >= stable:
                print(f"{len(open_calls)} call(s) waiting:")
                main(["pending"])
                return 0
            time.sleep(3)
    if command == "spend":
        sys.path.insert(0, str(REPO))
        from studio import ledger

        total = 0.0
        for job in sorted(JOBS.iterdir()):
            if not (job / "_insession").is_dir():
                continue
            rows = ledger.read(job)
            by = {}
            for r in rows:
                if isinstance(r.get("cost_usd"), (int, float)):
                    by[r["model"]] = by.get(r["model"], 0) + r["cost_usd"]
            spent = sum(by.values())
            total += spent
            print(f"{job.name:<24} ${spent:.4f}  " + ", ".join(f"{m} ${c:.4f}" for m, c in sorted(by.items())))
        print(f"{'total':<24} ${total:.4f}")
        return 0
    print(__doc__)
    return 2


KINDS = ("art check", "art notes", "art flags", "locate box", "label check", "repair check", "repair")


def kind(req: dict) -> str:
    label = req["label"]
    for k in KINDS:
        if label.startswith(k + " "):
            return k
    if label.startswith("art ") and req["schema"] is None:
        return "art drawing"
    return label


def states() -> dict[str, str]:
    """Each round job's driver state; a driver whose process is gone while it says running reads as dead."""
    out = {}
    for job in sorted(JOBS.iterdir()) if JOBS.exists() else []:
        path = job / "_insession" / "status.json"
        if not path.exists():
            continue
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        state = s.get("state", "?")
        if state == "running":
            try:
                os.kill(int(s["pid"]), 0)
            except (OSError, ValueError, KeyError):
                state = "dead"
        out[job.name] = f"{state}:{s.get('step', '')}"
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
