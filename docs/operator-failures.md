# Where a droplet fails: the operator, simulated (Nov 3)

The teacher (the operator, [docs/popsmosis-agent-skill.md](popsmosis-agent-skill.md)) was run through a failure at
each point of the pipeline, one at a time, with `python tools/sim/scenarios.py` ([tools/sim](../tools/sim/README.md)).
No call reached a paid API: the popsmosis API and R2 were an in-memory stand-in with the real one's leases, runs and
video attempts, the brain and the media steps were stand-ins, and the operator ran the real `python -m studio`
commands, heartbeat by heartbeat, each on a fresh machine whose detached runs died when it ended. Between heartbeats
the clock moved on 61 minutes, so a lease lapsed and a render that died went stale, as between two sweeps.

A droplet is a lesson posted with its videos READY. The first run found 24 of 41 failures coming through as one; 9
were given up, 4 got their lesson but not their videos, and 4 were stuck for good. The fixes below were made for
what it found, and the scenarios run again: [the results](#after-the-fixes).

## What the first run found

**Stuck: nothing would ever finish them.**

- `videos-never-claimed`: the claim right after the post failed, and so did `videos --background`'s. The videos'
  status stayed empty, and the video queue handed on only renders that were RENDERING and stale.
- `render-hangs-twice`: a render that outlasted the operator's hour on both attempts stayed RENDERING for good.
- `lesson-hangs-always`: a bundle that ran past the operator's hour every time was started over, and paid for, on
  every sweep, with nothing counting the sweeps; the spend ceiling counts only the calls on the machine at hand.
- `brain-nokey-no-session`: the brain's key missing, every run stopped before it claimed anything, as designed.

**Given up after a short outage.** The operator's one retry started at once, so an outage longer than a minute or
two failed both tries: the claim failing four times (`api-claim-outage`), a learner's file failing to download
twice (`api-attachment-blip`: the download was not retried, and a file that could not be had failed the topic),
the finished, fact-checked lesson failing to post twice (`api-post-down`: $1.50 of calls thrown away), the brain's
API failing on two calls or the whole run, and the run killed mid-bundle twice.

**Lesson only, an attempt left.** The operator retried the videos once, at once, then gave them up, and the API's
second attempt (`VIDEO_MAX_ATTEMPTS` 2) went only to a machine that died: a song provider failing twice, a song
drifting from its lyrics twice, R2 refusing six PUTs, Claude's drawer failing twice.

**Given up by design.** A lesson the fact audit or the fact-check refused was checked again unchanged on the
retry, and refused again; and the spend ceiling.

## The fixes

| What | Where |
|---|---|
| A model call the API fails waits for the operator's own Claude session to answer it, at $0 on the API key, shown as "in session" on the bill | `studio/session.py`, the skill file's "When the studio asks your session" |
| A step an outage failed (the API, a provider or the brain did not answer, or failed on its side) is tried again in the run after 30 seconds, 2 minutes and 5 minutes; a song or narration the timing step deleted is made again once | `studio/retry.py`, `studio/run.py`, `studio/videos.py` |
| A Lyria, Gemini TTS or ElevenLabs outage has a code of its own ("the audio provider failed"), and a declined picture one that is not an outage | `studio/steps/audio.py`, `studio/gemini.py`, `studio/muse.py` |
| No step runs past `STUDIO_STEP_MINUTES` (40): it is ended and recorded as failed, "timed out", so the operator always gets an outcome within its hour; time waiting for the session does not count | `studio/retry.py` |
| `fail` gives up only what no later try would get past, or what has no run (`QUESTION_MAX_RUNS`, 3) or attempt (`VIDEO_MAX_ATTEMPTS`) left; otherwise it says DEFERRED and a later sweep takes it up | `studio/run.py`, `studio/videos.py` |
| The API counts a run when a machine first claims a topic with its run's id (`{"runId": ...}`, counted once however often it claims), gives the topic up once its runs are used and the last one's lease has lapsed, and sends `runs`, `maxRuns` and `maxAttempts` | `api/src/routes/questions.ts`, `api/src/routes/videos.ts`, migration `20261114120000_question_runs` |
| The video queue hands on videos never claimed (an hour to 25 hours after the post), and marks a render quiet on its last attempt FAILED | `api/src/routes/videos.ts`, `api/src/lib/video-claim.ts` |
| A lesson the fact audit or the fact-check refused is set aside (`bundle.rejected-<n>.json`) and written again on the retry | `studio/run.py` |
| A learner's file is downloaded again on a failure, and left out rather than failing the topic | `studio/run.py` |
| A picture whose painter fails for good is painted by the other one (Muse Image or Gemini), when the brain may use it and its key is here | `studio/steps/art.py` |
| A picture flipped by the locate step's repair is put back when the check after the flip fails | `studio/repair.py` |

## After the fixes

All 43 scenarios again, with every fix (the two new ones: `factcheck-fails-once`, `killed-in-bundle-always`): **36 droplets**, 6 given up, 1 stuck, none with the lesson alone. None is given up for an outage of a few minutes any more, and nothing is started over for good.

| Scenario | Before | After | Heartbeats |
|---|---|---|---|
| `api-claim-outage` | given up | **droplet** | 1 |
| `api-attachment-blip` | given up | **droplet** | 1 |
| `api-post-down` | given up | **droplet** | 1 |
| `brain-outage-no-session` | given up | **droplet** | 1 |
| `killed-in-bundle-twice` | given up | **droplet** | 2 |
| `lesson-hangs-always` | stuck | **given up** | 3 |
| `videos-never-claimed` | stuck | **droplet** | 2 |
| `audio-fails-twice` | lesson only | **droplet** | 1 |
| `timing-drifts-twice` | lesson only | **droplet** | 1 |
| `drawer-down-no-session` | lesson only | **droplet** | 2 |
| `render-hangs-twice` | stuck | **droplet** | 2 |
| `storage-outage` | lesson only | **droplet** | 1 |
| `factcheck-fails-once` | new | **droplet** | 1 |
| `killed-in-bundle-always` | new | **given up** | 3 |

What still does not end as a droplet, and why:

- `brain-down-no-session` (given up): the brain's API down for good and the session fallback turned off: each sweep tries, and the API gives the topic up after its 3 runs. With the fallback on (`brain-down-session`), a droplet.
- `brain-nokey-no-session` (stuck): the brain's key missing and the session fallback turned off: every run stops before it claims anything, so no run is counted and the topic waits for an admin, as designed. With the fallback on (`brain-nokey-session`), a droplet.
- `audit-wrong-always` (given up): the fact audit refused both lessons written for it: given up, as the gate should.
- `factcheck-fails-always` (given up): the fact-check refused both lessons written for it: given up, as the gate should.
- `spend-ceiling` (given up): the topic passed its spend ceiling: given up, as the admin set it.
- `killed-in-bundle-always` (given up): the run died mid-bundle on every machine: given up after its 3 runs, at a cost the cap holds, where before it would have been given up on the first.
- `lesson-hangs-always` (given up): the bundle never returned: each run ended at the step's time limit, and the topic was given up after its 3 runs; before, it was started over, and paid for, on every sweep for good.

## Checked again: reliability, scale, and the seams between the parts

Three reviews of the change (the API's queries under concurrency and at scale; the studio's processes, threads and
retries; the contract between operator, studio, API and web), and a run of the operator against the real API on a
scratch database (`tools/sim/seam.py`), found these, all fixed:

| Found | Fixed |
|---|---|
| A claim whose answer was lost was sent again, and counted a second run: one machine could use all 3 | Every claim of a machine's run carries one `runId`; the API counts a run once per id, and at the cap turns a new run away (409) while a live one holds the lease, failing the topic only once it has lapsed |
| The videos' claim was renewed only before each step: a long step (art waiting on the session) went quiet past `VIDEO_STALE_MINUTES`, and the render was taken over or failed | A thread renews it every 5 minutes for the whole run, as the lesson's does |
| A song, a narration or a picture that timed out was asked again in the run, though the provider may still be making, and charging for, the first | "the provider took too long": left to a later sweep, never asked again in the run |
| A painter's 4xx (a key refused, a model retired) was taken for an outage | Coded as a refusal: no tries, no DEFERRED |
| The API's own 503 (its storage not set up) was taken for an outage | "the API is not set up for it", as the upload already read it |
| The time limit restarted with each try of a step | It is the step's, across its tries and waits |
| A call nobody answered stayed listed, paused the time limit, and could keep a finished run alive | Its folder is removed; only this process's calls pause the limit; a detached run ends as soon as its outcome is written |
| The time limit and the run's own end could both record the run | Whichever begins first records it; a `run.current.json` written again in passing is not counted twice |
| CPU-heavy steps of runs that share a machine fought for its cores | The timing and the render take turns per machine (`media.cpu_turn()`); waiting for a turn does not count against the time limit. A routine's run has a machine of its own, so it never waits |
| The skill claimed every command was pre-approved; `session decline` wanted the bare name; docs said a second failure always gives up | Corrected |

Against the real API, all held: a droplet; the brain down for a whole run, answered in session, its bill saying so in
both the lesson's and the videos'; a topic given up once its 3 runs were used; videos never claimed, recovered; a render
that hung twice, recovered on its second attempt; a render abandoned on its last attempt, marked FAILED with why. At
scale, 8 topics with 3 operators at once: each made exactly once, on one run, with its videos, 21 seconds in all. The
API's new queries ran in under a millisecond on 300,000 topics and answers with the indexes the tables have.

**Waking the teacher.** The API's trigger debounced new topics with the replies: a burst of 10 topics woke one or two
runs, each making one topic at a time, 20 minutes or more each, and the rest waited for the schedule. Every new topic
now wakes a run of its own at once (`POPSMOSIS_ROUTINE_EVERY_TOPIC`, on), each on its own machine, so they are made
side by side; replies stay debounced. Each wake-up is one of the routine's metered runs, bounded by the API's limits on
topics a day.

## What the simulation does not cover

The providers' own behaviour (the stand-ins fail when told to), the quality of what the brain writes, and the media
steps' code but the upload's: the painter fallback and the repair are covered by the studio's tests instead. There is
still no fallback for SAM 3.1, which every painted picture needs to find its structures.
