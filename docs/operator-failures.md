# Where a droplet fails: the operator, simulated (Nov 3)

The teacher (the operator, [docs/popsmosis-agent-skill.md](popsmosis-agent-skill.md)) was run through 41 failures,
one at a time, each at a point of the pipeline, with `python tools/sim/scenarios.py` ([tools/sim](../tools/sim/README.md)).
No call reached a paid API: the popsmosis API and R2 were an in-memory stand-in with the real one's leases and video
attempts, the brain and the media steps were stand-ins, and the operator ran the real `python -m studio` commands,
heartbeat by heartbeat, each on a fresh machine whose detached runs died when it ended. Between heartbeats the clock
moved on 61 minutes, so a lease lapsed and a render that died went stale, as between two sweeps.

A droplet here is a lesson posted with its videos READY. 24 of the 41 came through as one; 9 were given up, 4 got
their lesson but not their videos, and 4 are stuck for good:

## Stuck: nothing will ever finish them

| Scenario | What happened |
|---|---|
| `videos-never-claimed` | The claim right after the post failed, and so did `videos --background`'s. The videos' status stays empty, and the video queue (`GET /api/videos/queue`) hands on only renders that are RENDERING and stale, so no later run ever makes them. The same happens when the operator's machine dies between the post and Step 2b after a failed first claim. |
| `render-hangs-twice` | A render that outlasts the operator's hour on both attempts (`VIDEO_MAX_ATTEMPTS` 2) stays RENDERING for good: the queue skips it once its attempts are used, and nothing marks it FAILED. |
| `lesson-hangs-always` | A bundle that runs past the operator's hour, every time: each sweep's machine starts the topic over from nothing, pays for it again ($0.60 a sweep in the simulation, four sweeps and counting), and goes away. Nothing counts the sweeps, and the spend ceiling counts only the calls on the machine at hand, so it never trips. |
| `brain-nokey-no-session` | The brain's key missing (or the settings wrong): every run stops before it claims anything, as designed, and the topic waits until an admin fixes the settings. |

## Given up after a short outage

The operator's one retry starts at once, so an outage longer than a minute or two fails both tries, and `fail`
gives the topic up for good.

| Scenario | What happened |
|---|---|
| `api-claim-outage` | The claim before a step failed four times in a row: given up in the first heartbeat. |
| `api-attachment-blip` | A file the learner sent failed to download twice (502): given up. The download is not retried, and a file that cannot be had fails the topic, where an expired one is only left out. |
| `api-post-down` | Posting the finished, fact-checked lesson failed twice: the lesson, $1.50 of calls in the simulation, is thrown away and the topic given up. |
| `brain-outage-no-session`, `brain-down-no-session` | The brain's API failed on two calls in a row, or for the whole run: given up. |
| `killed-in-bundle-twice` | The run died mid-bundle twice (a machine restart, an OOM): given up. |

## Lesson only: videos given up with an attempt left

The operator retries the videos once, at once, then `fail`s them; the API keeps a second attempt
(`VIDEO_MAX_ATTEMPTS` 2) only for a machine that died, and its queue never takes a render that was given up.

| Scenario | What happened |
|---|---|
| `audio-fails-twice` | The song's provider failed on both tries (a Lyria outage longer than its own retries). |
| `timing-drifts-twice` | The song drifted from its lyrics twice (the timing step deletes it and the audio sings it again). |
| `storage-outage` | R2 refused six PUTs in a row: the upload's three tries, twice. |
| `drawer-down-no-session` | Claude's SVG drawer failed on both tries. |

## Given up by design

| Scenario | What happened |
|---|---|
| `audit-wrong-always`, `factcheck-fails-always` | The fact audit or the fact-check failed the lesson on both tries. The retry checks the same bundle again rather than writing a new one, so a lesson the checks refuse once is refused again. |
| `spend-ceiling` | The topic passed its ceiling (`spend.topicUsd`): the step fails, the retry fails at once, and the topic is given up. |

## Came through

The baseline; the settings read failing three times (three heartbeats late); one failed claim; the post's reply
lost (the API names its answer on the retry); every progress report failing; the brain failing once; an answer out
of its schema twice (the bundle step asks again itself); the audit finding the facts wrong once; one kill mid-bundle;
one hang past the hour (a second heartbeat, at twice the cost); the videos' claim failing once (recovered when the
render went stale); the art failing once; a heartbeat claim failing; a render killed once, or past the hour once
(recovered); two failed storage PUTs; and three failed READYs.

## The session fallback

A model call the API fails now waits for the operator's own Claude session to answer it, in a run started with
`--background` (`studio/session.py`; the skill file's "When the studio asks your session"). With it on, each brain
failure above came through:

| Without the session | With it |
|---|---|
| `brain-outage-no-session`: given up | `brain-outage-session`: droplet, 1 call answered in session |
| `brain-down-no-session`: given up | `brain-down-session`: droplet, 9 calls in session, $0 on the API key |
| `brain-nokey-no-session`: stuck | `brain-nokey-session`: droplet, 9 calls in session, $0 on the API key |
| `drawer-down-no-session`: lesson only | `drawer-down-session`: droplet, 3 drawings in session |
| | `brain-truncated-session`, `brain-refused-session`, `brain-invalid-session` (its first answer handed back as NOT VALID, then saved): droplets |

Each such call is in calls.jsonl with the model the operator named, `via: "session"`, $0, and `api_usd`, what it would
have cost on the API; the droplet's bill lists it as "in session".

## What the simulation does not cover

The providers' own behaviour (the stand-ins fail when told to), the quality of what the brain writes, and the media
steps' code but the upload's. From reading the media steps (no simulation): no step has a wall-clock limit but the
render's encode; the timing step's lock (`jobs/.timing.lock`) has no timeout; a picture the locate step flips is not
put back when the check after the flip fails (`studio/repair.py`); a failed heartbeat claim is charged to the step
about to run; and there is no fallback from one painter to the other (Muse Image to Gemini, or back), nor from SAM 3.1,
which every painted picture needs.

## What would close the gaps (not done)

1. **Videos never claimed:** let the video queue hand on a posted lesson whose videos were never claimed, after a
   while, as it does a stale render.
2. **A render stuck RENDERING:** mark it FAILED once it is stale with its attempts used, so the page stops waiting.
3. **Retries spread out in time:** for a failure an outage explains (the API, a provider, the brain), retry in the
   run with a backoff before failing the step, and have `fail` give up only what a retry cannot fix, leaving the rest
   to the next sweep, with the API counting tries across machines so it still ends.
4. **The post:** retry it for minutes, not twice at once: the lesson is paid for.
5. **A learner's file:** retry its download, and leave it out rather than fail the topic.
6. **Hangs:** a time limit on each step, under the operator's hour, so a run fails where it is instead of being
   left behind, and a limit on how many machines a topic may be started on.
7. **The checks:** on the retry after the fact audit or the fact-check fails, write the bundle again.
8. **The videos' second attempt:** let a later sweep take videos given up for an outage, up to `VIDEO_MAX_ATTEMPTS`.
9. **The painters:** paint with the other one (Muse Image or Gemini) when one fails for good.
