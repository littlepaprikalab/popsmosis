"""The operator against the real API: the seams the simulation's stand-in API cannot prove (tools/sim/README.md).

    python tools/sim/seam.py --api http://127.0.0.1:3191 --key sk_pop_... --db postgresql://.../scratch [names]

The API is the real one (api/), started on a scratch database you made for this and nothing else (it writes users,
topics and answers there, and ages their timestamps): `npm --workspace api run db:deploy` against it, then the API
with JWT_SECRET, PORT and placeholder R2 settings (it signs upload URLs without asking R2), and an agent key from
`npm --workspace api run agent:create`. The studio runs its real commands, as the simulation's operator drives them
(teacher.py), with the stand-in providers (fakes.py); the bytes of the upload alone go nowhere
(STUDIO_SIM_STORAGE=discard). Faults the real API cannot be told to make are made in the studio's own client
("client" in fakes.py). Between heartbeats the topic's and its answer's times are moved back 61 minutes in the
database, as a lease lapsing and a render going stale would.

Each scenario checks what the database says at the end, and the table says whether it held.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from teacher import Teacher  # noqa: E402

FAST = {"STUDIO_STEP_MINUTES": "0.33"}


def sql(db: str, query: str) -> str:
    return subprocess.run(["psql", db, "-v", "ON_ERROR_STOP=1", "-tAc", query], capture_output=True, text=True,
                          check=True).stdout.strip()


def state(db: str, qid: str) -> dict:
    row = sql(db, f"""SELECT row_to_json(t) FROM (SELECT q.status, q.runs, q."failedReason", a.id AS answer,
        a."videoStatus", a."videoAttempts", a."videoError", a.model, a."lessonBreakdown" AS breakdown, a."videoBreakdown",
        a."lessonVideoUrl", a."songVideoUrl", q."songStyle" AS "songStyle", a.bundle->>'headline' AS headline
        FROM questions q LEFT JOIN answers a ON a."questionId" = q.id WHERE q.id = '{qid}') t""")
    return json.loads(row)


def ask(db: str, topic: str, style: str = "") -> str:
    """A topic as /learn writes it; style: the song style field, in the learner's words ("": none)."""
    uid, qid = "u" + uuid.uuid4().hex[:20], "q" + uuid.uuid4().hex[:20]
    song_style = f"'{style}'" if style else "NULL"
    sql(db, f"""INSERT INTO users (id, email, "googleId", "updatedAt") VALUES ('{uid}', '{uid}@seam.test', '{uid}', now());
               INSERT INTO questions (id, "userId", content, "songStyle")
               VALUES ('{qid}', '{uid}', '{topic}', {song_style})""")
    return qid


def asked_style(sim: Path, qid: str) -> str | None:
    """The song style the studio wrote into the job's request, on whichever machine made the lesson."""
    for path in sim.glob(f"machine-*/{qid}/request.json"):
        return json.loads(path.read_text(encoding="utf-8")).get("song_style")
    return None


def age(db: str, qid: str, minutes: int = 61) -> None:
    sql(db, f"""UPDATE questions SET "claimedAt" = "claimedAt" - interval '{minutes} minutes' WHERE id = '{qid}';
               UPDATE answers SET "videoUpdatedAt" = "videoUpdatedAt" - interval '{minutes} minutes',
                                  "createdAt" = "createdAt" - interval '{minutes} minutes' WHERE "questionId" = '{qid}'""")


def session_lines(bill: dict | None) -> int:
    return sum(1 for s in (bill or {}).get("steps", []) for m in s.get("models", []) if m.get("via") == "session")


# The song style the baseline's topic is asked with: the field reaches the studio through the claim, and the lesson
# it posts carries its headline back.
BASELINE_STYLE = "lo-fi with soft rapped verses"

# name -> (faults, env, how many heartbeats at most, the check on the end state)
SCENARIOS = {
    "baseline": ({}, {}, 1, lambda s: s["status"] == "ANSWERED" and s["runs"] == 1 and s["videoStatus"] == "READY"
                 and s["videoAttempts"] == 1 and s["model"] == "claude-opus-5-5" and bool(s["breakdown"])
                 and bool(s["videoBreakdown"]) and bool(s["lessonVideoUrl"])
                 and s["songStyle"] == BASELINE_STYLE and s["askedStyle"] == BASELINE_STYLE and bool(s["headline"])),
    "brain-down-session": ({"brain": {"*": {"fault": "overloaded", "times": 999}}}, {}, 1,
                           lambda s: s["videoStatus"] == "READY" and session_lines(s["breakdown"]) > 0
                           and session_lines(s["videoBreakdown"]) > 0),
    "runs-capped": ({"brain": {"bundle": {"fault": "kill", "times": 999}}}, {}, 4,
                    lambda s: s["status"] == "FAILED" and s["runs"] == 3 and s["answer"] is None),
    "videos-never-claimed": ({"client": {"claim_videos": {"fault": "error", "times": 2}}}, {}, 3,
                             lambda s: s["videoStatus"] == "READY" and s["videoAttempts"] == 1),
    "render-hangs-twice": ({"steps": {"render": {"fault": "hang", "times": 2}}}, FAST, 3,
                           lambda s: s["videoStatus"] == "READY" and s["videoAttempts"] == 2),
    "render-abandoned-on-its-last-attempt": ({"steps": {"art": {"fault": "hang", "times": 999}}},
                                            {"STUDIO_STEP_MINUTES": "10"}, 4,
                                            lambda s: s["videoStatus"] == "FAILED" and s["videoAttempts"] == 2
                                            and "last attempt" in (s["videoError"] or "")),
}


def run(name: str, api: str, key: str, db: str, root: Path) -> tuple[str, bool, dict, float]:
    faults, env, beats, check = SCENARIOS[name]
    sim = root / name
    sim.mkdir(parents=True, exist_ok=True)
    (sim / "faults.json").write_text(json.dumps(faults))
    qid = ask(db, f"seam check {name.replace('-', ' ')}", BASELINE_STYLE if name == "baseline" else "")
    teacher = Teacher(sim, api, {"STUDIO_SIM_STORAGE": "discard", **env}, api_key=key)
    started = time.monotonic()
    for n in range(1, beats + 1):
        teacher.heartbeat(n)
        s = state(db, qid)
        if s["videoStatus"] in ("READY", "FAILED") or s["status"] == "FAILED":
            break
        age(db, qid)
    s = {**state(db, qid), "askedStyle": asked_style(sim, qid)}
    (sim / "operator.log").write_text("\n".join(teacher.log) + "\n\nend state:\n" + json.dumps(s, indent=1) + "\n")
    return name, bool(check(s)), s, time.monotonic() - started


def scale(api: str, key: str, db: str, root: Path, topics: int, operators: int) -> bool:
    """Many topics at once, several operators overlapping (the trigger fires a run while the schedule's is going):
    each topic made exactly once, on one run, with its videos; no two machines on one topic."""
    from concurrent.futures import ThreadPoolExecutor

    ids = [ask(db, f"seam scale topic {i}") for i in range(topics)]
    started = time.monotonic()

    def operator(n: int) -> Teacher:
        sim = root / f"scale-operator-{n}"
        sim.mkdir(parents=True, exist_ok=True)
        (sim / "faults.json").write_text("{}")
        teacher = Teacher(sim, api, {"STUDIO_SIM_STORAGE": "discard"}, api_key=key)
        for beat in range(1, 4):  # a heartbeat takes up to four topics; three each are plenty
            teacher.heartbeat(beat)
            if teacher.log and teacher.log[-1] == "IDLE":
                break
        return teacher

    with ThreadPoolExecutor(operators) as pool:
        teachers = list(pool.map(operator, range(1, operators + 1)))
    rows = [json.loads(line) for line in sql(db, f"""SELECT row_to_json(t) FROM (SELECT q.id, q.status, q.runs,
        count(a.id) AS answers, max(a."videoStatus") AS videos FROM questions q LEFT JOIN answers a ON a."questionId" = q.id
        WHERE q.id IN ({", ".join(f"'{i}'" for i in ids)}) GROUP BY q.id, q.status, q.runs) t""").splitlines()]
    starts = [line for t in teachers for line in t.log if line.startswith("$ studio run ")]
    ok = (len(rows) == topics and all(r["status"] == "ANSWERED" and r["answers"] == 1 and r["runs"] == 1
                                      and r["videos"] == "READY" for r in rows) and len(starts) == topics)
    print(f"{'HELD' if ok else 'BROKE':<6} scale: {topics} topics, {operators} operators at once, "
          f"{time.monotonic() - started:.0f}s; lessons started {len(starts)}, "
          f"per operator {[sum(1 for l in t.log if l.startswith('$ studio run ')) for t in teachers]}; "
          f"rows {json.dumps([(r['status'], r['runs'], r['answers'], r['videos']) for r in rows])[:300]}")
    return ok


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python tools/sim/seam.py")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--api", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--db", required=True, help="the scratch database the API runs on")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scale", type=int, default=0, help="also: this many topics at once, for several operators")
    parser.add_argument("--operators", type=int, default=3)
    args = parser.parse_args(argv)
    root = (args.out or REPO / "jobs" / "_seam" / time.strftime("%Y%m%d-%H%M%S")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    import os

    os.environ["STUDIO_SIM_DIR"] = str(root)
    names = [n for n in SCENARIOS if not args.names or any(n.startswith(p) for p in args.names)]
    # One at a time: they share the real topic queue, which would hand one scenario's topic to another's operator.
    results = [run(n, args.api, args.key, args.db, root) for n in names]
    for name, ok, s, seconds in results:
        brief = {k: s[k] for k in ("status", "runs", "videoStatus", "videoAttempts", "failedReason", "videoError")}
        print(f"{'HELD' if ok else 'BROKE':<6} {name:<38} {seconds:5.0f}s  {json.dumps(brief)[:220]}")
    held = all(ok for _, ok, _, _ in results)
    if args.scale:
        held = scale(args.api, args.key, args.db, root, args.scale, args.operators) and held
    print(root)
    return 0 if held else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
