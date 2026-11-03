# The operator, simulated: every failure, no paid call

`python tools/sim/scenarios.py` runs the teacher's heartbeat ([docs/popsmosis-agent-skill.md](../../docs/popsmosis-agent-skill.md))
through a failure at each point of the pipeline, and says how each topic ended: a **droplet** (the lesson posted and
its videos READY), **lesson only** (the videos given up), **given up** (no lesson), or **stuck** (nothing will ever
finish it). It makes no call to Anthropic, Meta, Gemini, ElevenLabs, Inkmory or the real popsmosis API, and needs
no key: every paid key is removed from the environment it runs the studio in, and the providers are stand-ins.

    python tools/sim/scenarios.py                  # all of them, about 10 minutes with 8 at a time
    python tools/sim/scenarios.py brain- videos-   # the ones whose names start so
    python tools/sim/scenarios.py --list           # what each one breaks
    python tools/sim/scenarios.py --out DIR        # default jobs/_sim/<time>/

Each scenario writes `<out>/<name>/operator.log`: every command the operator ran and its line, then what the API saw
happen and every request it took. `faults.log` says which fault fired where; `machine-<n>/` is each heartbeat's jobs
folder, with the studio's own `run.log`, `videos.log`, `runs.jsonl` and `calls.jsonl`. `report.md` is the table.

## What is real and what is a stand-in

| Part | In the simulation |
|---|---|
| The operator | `teacher.py`: the skill file's steps, on the real `python -m studio` commands (`run --background`, `wait`, the one retry, `fail`, `videos --background`, `recover`, and `session save` for an `ASK`) |
| The machine | a fresh jobs folder per heartbeat, whose detached runs are killed when the heartbeat ends, as the routine's container goes away |
| The popsmosis API and R2 | `fakeapi.py`: in memory, with the real API's lease, the runs a topic is started on (`QUESTION_MAX_RUNS` 3), one answer per topic, video attempts (`VIDEO_MAX_ATTEMPTS` 2), stale or never-claimed renders handed on by its queue (`VIDEO_STALE_MINUTES` 60), a render quiet on its last attempt marked FAILED; the upload's PUTs land in it too |
| Time | the API's clock moves 61 minutes between heartbeats; a `wait` is 3 seconds, and 10 of them on one step stand for the operator's hour; the studio's own waits after an outage are a fiftieth (`STUDIO_RETRY_SCALE`), and its time limit on a step is 20 seconds (`STUDIO_STEP_MINUTES`) |
| The lesson's steps | the studio's own code: request, bundle (script, early audit, narration edit), audit, fact-check, post |
| The brain | `fakes.py` answers each call as the tests' brain does (`studio/tests/sample.py`), at made-up prices |
| The media steps | stand-ins that mark themselves done (the art asks the drawer, the arrows the helper); the **upload is the real step** |
| The session (`ASK`) | the operator answers each waiting call as the tests' brain would, then `session save` checks it for real |

`boot/sitecustomize.py` puts the stand-ins in force in every studio process the operator starts, the detached ones
too, and only when `STUDIO_SIM_DIR` is set.

## Faults

A scenario sets them in `faults.json` (the brain and the media steps, `fakes.py`) and on the fake API (`fakeapi.py`):

- **the API**: a status for the next N matching requests, as a proxy's HTML page (a service that is down) or the
  API's own JSON error; a dropped connection; or a request carried out and its answer lost.
- **the brain**: an outage (529), a refusal, an answer cut off or out of its schema, a missing key, a fact audit or
  fact-check that finds everything wrong, a call that never returns, or the process killed mid-call.
- **a media step**: a failure with a code, a kill, a hang past the operator's hour, or a slow step.

Add a scenario to `SCENARIOS` in `scenarios.py`.
