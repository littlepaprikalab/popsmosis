# Design: the studio as a service that holds the provider keys

Status: tabled on 29 October 2026, not built. Kept for after the hackathon. Until then the
provider keys stay in the operator's environment, with the exposure described below; the
per-topic spend ceiling (`/admin/studio`) bounds what the studio spends on a topic, not what
someone holding the keys could. Threat model finding T1 (prompt injection steers the
teacher agent), fix-plan item "move the provider keys out of the agent's shell". Since it
was written, the planner's Inkmory record was taken out (Oct 30: there is no planner key to
move) and the videos moved to Cloudflare R2 (the API signs R2 uploads): revise those parts
before building it.

**In one paragraph.** Today the teacher (the operator: an LLM with a shell) reads every
learner's topics, follow-ups and files, and the same shell holds the Anthropic, Meta, Gemini
and ElevenLabs keys, the planner's Inkmory key and an agent key that can post lessons. A
learner who talks the operator into running `printenv` and pasting the output somewhere has
all of them. This design moves the studio into a worker of its own, on Render beside the API,
that holds those keys and runs no model with tools. The operator keeps only what it needs to
answer people: its popsmosis key, narrowed to an operator scope, and its own Inkmory key. It
hands a topic to the studio through the popsmosis API and never talks to the worker. A
hijacked operator can then still say something wrong in a reply, which the API's filters and
the learner see, but it cannot read a provider key, post a lesson or spend past the
per-topic ceiling.

---

## 1. Where each secret lives

| Secret | Today | After |
|---|---|---|
| `POPSMOSIS_ANTHROPIC_API_KEY` | operator's shell | studio worker |
| `MODEL_API_KEY` (Meta: Muse, SAM 3.1) | operator's shell | studio worker |
| `GEMINI_API_KEY` | operator's shell | studio worker |
| `ELEVENLABS_API_KEY` | operator's shell | studio worker |
| `INKMORY_PLANNER_API_KEY` | operator's shell (studio code reads it) | studio worker |
| `POPSMOSIS_API_KEY`, the one agent key | operator's shell; does everything an agent may | two keys: an **operator** key in the shell, a **studio** key on the worker |
| `INKMORY_OPERATOR_API_KEY` | operator's shell | operator's shell (it is the operator's own public record) |
| `SUPABASE_SERVICE_ROLE_KEY` | API only | API only (unchanged: the worker uploads to signed URLs) |

The operator's environment ends with four variables: `POPSMOSIS_API_URL`,
`POPSMOSIS_API_KEY` (operator scope), `INKMORY_API_URL`, `INKMORY_OPERATOR_API_KEY`.

## 2. The parts

```
 learner ──> popsmosis-web ──> popsmosis-api <──── operator (Claude routine, has a shell)
                                  │   ▲             reads queues, triages, replies,
                     wakes ───────┘   │             hands topics off; operator key only
                     the routine      │
                                      │ polls for hand-offs, claims, posts lessons,
                                      │ uploads, reports progress; studio key only
                               studio worker (Render background worker, no inbound port)
                                      │
                 Anthropic · Meta · Gemini · ElevenLabs · Supabase (signed URLs) · Inkmory (planner)
```

- **The operator** keeps its job as the teacher: it reads both queues, decides whether a
  request is a topic, answers the ones that are not, answers follow-ups, and keeps its
  Inkmory record. For a topic it calls one endpoint and moves on.
- **The studio worker** is the code in `studio/` as it is, run by a small loop instead of
  by the operator's shell. It has no web server and no inbound port, so nothing on the
  internet can reach it; every call it makes is outbound. No model it calls has tools: the
  brain answers JSON to a schema, Muse Image runs with web search and shell off
  (`studio/muse.py`), and the rest are media APIs. A learner's words can still steer what a
  model writes, which the fact-check and the classroom checks are for (T6, T7), but they
  cannot run a command or read a key.
- **The API** is the only thing both sides trust. It already holds the queues, the leases,
  the one-answer-per-topic guard and the video attempts, so it brokers the hand-off rather
  than either side calling the other.

## 3. Flows

### A topic

1. The operator's Step 0 is unchanged and leases it a topic (`GET /api/questions/queue`).
2. A request that is not a topic: answered as now, with `python -m studio answer`, which
   needs only the popsmosis key.
3. A topic: the operator calls `POST /api/questions/:id/studio` with an optional `note` for
   the planner (the `--note` of today, same 500-character rules). The API checks the lease is
   the operator's, records the hand-off (`studioQueuedAt`, the note) and answers `202`. The
   operator goes on to the follow-ups. It no longer waits five minutes on `run`, nor up to an
   hour on `wait`.
4. The worker polls `POST /api/studio/jobs/next` every 10 seconds with the studio key. That
   returns the oldest hand-off and moves its lease from the operator to the studio agent in
   the same statement, so two workers never take the same topic.
5. The worker runs `run` and then `videos` in the same process, as `run` and
   `videos --background` do today, with the same files in `jobs/<id>/`, the same claims and
   heartbeats, and the per-topic spend ceiling (`studio/ledger.py`).
6. It does the retries the skill file asks the operator for today: a failed step is run once
   more; a second failure gives the topic up (`POST /api/questions/:id/fail`), or its videos
   once the lesson is posted. That logic leaves the skill file and becomes code, where it is
   tested and cannot be talked out of.
7. It writes the planner's Inkmory record as it does now, then posts the run's summary (the
   two paragraphs `python -m studio summary` prints, already free of the learner's words) to
   `PUT /api/questions/:id/studio-summary`.

### The operator's record

Step 5 reads `GET /api/studio/summaries?since=<the operator's last cycle log>` with the
operator key: the summaries of every topic that finished since its last record, whichever
run handed it off. The journal entry covers the topics that finished, the cycle log what this
run did (handed off, answered, follow-ups). A topic still being made shows up in the next
run's record, the way a render that outlives a run does today.

### Follow-ups and files

Unchanged. `python -m studio fetch` and `reply` use only the popsmosis key, so they stay in
the operator's checkout. The operator key keeps `GET /api/replies/queue`,
`GET /api/attachments/:id` for threads it owes, and `POST /api/answers/:id/replies`.

### A worker that dies

A deploy or a crash ends the worker's jobs mid-step. Nothing new is needed: the topic's lease
lapses after `QUESTION_CLAIM_MINUTES` and its hand-off is taken again; a render that stops
reporting for `VIDEO_STALE_MINUTES` is recovered from `GET /api/videos/queue`, which the
worker polls when it has nothing handed off, as `python -m studio recover` does now. The
worker's disk is not kept across deploys, which is fine: `recover` already rebuilds a job
from what the API stored. On SIGTERM it stops taking jobs and records the current step as
interrupted (`record_interrupted`), so the retry counts it.

## 4. Changes to the API

- **Agent scopes.** A `scope` column on `agents`: `OPERATOR`, `STUDIO`, or `LEGACY` (both,
  for the key in use today, until the cut-over). Route guards:

  | Scope | Routes |
  |---|---|
  | `OPERATOR` | the two queues; `POST /api/questions/:id/answer` for a non-topic answer (no bundle); `POST /api/questions/:id/studio`; `POST /api/answers/:id/replies`; attachments of topics it holds and threads it owes; `GET /api/studio/summaries`; `GET /api/studio/settings` |
  | `STUDIO` | `POST /api/studio/jobs/next`; the claim, lesson post (with a bundle), fail, progress, video claim, video patch, upload URL, video fail and video progress routes; attachments of topics it holds; `GET /api/videos/queue`; `PUT /api/questions/:id/studio-summary`; `GET /api/studio/settings` |

  A lesson post (an answer with a bundle) from an operator key is refused, so a hijacked
  operator cannot publish a lesson that skipped the fact-check.
- **Three endpoints**: the hand-off, the worker's `jobs/next`, and the summaries (write and
  list). The hand-off is idempotent: a second call for the same topic answers `202` with the
  first hand-off.
- **Two columns on `questions`**: `studioQueuedAt` and `studioNote`; a `studio_summaries`
  table (question id, text, finishedAt), deleted with the topic.
- **`/admin/agents`** shows each agent's scope and makes a new key with one.

Each new query is one indexed statement, outside any interactive transaction: the 5-second
transaction limit and the 5-connection pool from PR #52 hold.

## 5. The worker

- **Process.** `python -m studio serve`: a loop that takes one job, runs it in a child
  process (`python -m studio run`, then `videos`), and goes back for the next. A child
  process per job keeps one job's memory and threads from leaking into the next, and its
  stderr goes to the worker's log with the job id in front.
- **How many at once.** Two slots by default (`STUDIO_SLOTS`): a lesson is mostly waiting on
  the brain, a render is CPU and memory (Demucs and the renderer). Measured today, a topic is
  about 5 minutes and its videos about 10 more, so one slot makes about 4 droplets an hour.
  `TOPICS_PER_DAY` (default 100) is the ceiling on demand; two slots make about 190 a day,
  above it.
- **Host.** A Render background worker built from a Dockerfile: Python 3.13, ffmpeg, libEGL,
  `studio/requirements.txt`, and the Demucs and MMS models baked into the image, so a deploy
  never downloads them and a build needs no secret. It wants about 4 GB of memory for a
  render; the plan is a decision (section 8).
- **Disk.** `jobs/` on the instance's own disk. A job's folder is deleted once its videos are
  uploaded or given up and its summary is posted, and any folder older than 2 days is swept
  on start and every hour. That also closes the runner half of T24: today `request.json`
  (the learner's words) stays on whatever machine ran the topic.
- **Keys.** The provider keys, the studio key and the planner's Inkmory key are environment
  variables on the worker alone. The build never sees them if the Dockerfile reads none
  (to be checked on Render when it is built: T23 notes native builds see every variable).

## 6. What the operator can still do if hijacked

| It can | It cannot |
|---|---|
| Post a wrong or rude reply (the API still refuses one that looks like it carries a key) | Read any provider key or the planner's key |
| Answer a topic as a non-topic, with one short answer | Post a lesson, upload media, or change a droplet's videos |
| Hand off topics that are in the queue, each capped by the per-topic ceiling and all by `TOPICS_PER_DAY` | Run the studio on anything but a queued topic it leased |
| Read the queues and files it is handed, as today | Spend on the providers directly: their hosts leave the routine's network allowlist |
| Write to its own Inkmory record | Write to the planner's |

## 7. Rollout

1. **API:** scopes (existing key `LEGACY`), the three endpoints, the columns. Nothing changes
   for the running routine.
2. **Worker:** `studio serve`, the Dockerfile, the sweep. Deploy it with a new `STUDIO` key
   and the provider keys, and the hand-off turned off: it polls and finds nothing.
3. **Try it:** hand off one topic by hand with the operator's key and follow it to its
   videos, its planner record and its summary.
4. **Skill file:** Step 2 hands off instead of running `run`; Steps 2b and 3b go; Step 5
   reads the summaries; `recover` leaves Step 0. Fewer turns a run, since the operator no
   longer waits on the studio.
5. **Cut over:** make a new `OPERATOR` key for the routine, set the old one's scope to
   nothing (revoke it), remove the provider keys and the planner's key from the routine's
   environment, take the provider hosts off its allowlist, and **rotate every provider key**,
   since the old ones have lived in a shell that reads strangers' text.

Rollback before step 5 is turning the hand-off off; after it, putting the keys back in the
routine's environment and the old skill file back.

## 8. Decisions this needs

1. **The worker's host and size.** A Render background worker with about 4 GB, or a small VM
   elsewhere. It is a new monthly cost, against fewer routine turns (no waiting).
2. **Who triages.** This design keeps the operator deciding whether a request is a topic.
   The worker could take every topic straight from the queue instead and let the bundle step
   turn a non-topic down, which would take the operator off the topic path entirely.
3. **The operator's record.** Summaries arrive after the run that handed the topic off
   (section 3). The alternative is the operator waiting for the lesson, as now, at the cost
   of the turns this design saves.

## 9. Tests when it is built

- API: each route refuses the other scope's key; the hand-off is idempotent and refused
  without the operator's lease; `jobs/next` under concurrency hands each topic to one worker
  (as `queue-claim.integration.test.ts` does for the queue); summaries are learner-free and
  go with the topic.
- Worker: a job runs to its videos with the providers stood in for; a failed step is retried
  once and then given up; a SIGTERM mid-step leaves an interrupted run the next take counts;
  the sweep deletes finished and stale job folders.
- Live run, as for PR #52: the API with `PROXY_SECRET`, a stand-in for Supabase Storage, the
  worker in its container, and a routine-shaped shell with only the operator's variables, to
  show `printenv` there holds no provider key.
