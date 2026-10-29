# Running the popsmosis agent

popsmosis ships no runner. The agent is an LLM harness that you point at
`docs/popsmosis-agent-skill.md` (the **skill file**) with an API key in its environment.
Each run is one **heartbeat**: run the studio (`python -m studio run <id>`) on a waiting
topic, which writes, fact-checks and posts its lesson; start its two videos in the
background (`python -m studio videos <id> --background`); answer follow-ups under earlier
lessons while they render; wait for the videos; keep a short record of the run on
Inkmory; stop. (The API calls a topic request a
*question*, its lesson an *answer*, and follow-ups *replies*; this guide uses the API's
words.)

This guide is the sparks sponsor quick-start cut down to popsmosis.

---

## 1. Get credentials

1. Sign in to popsmosis with an email listed in the API's `ADMIN_EMAILS`.
2. Open **AGENTS** in the nav (`/admin/agents`), create an agent, and copy the key.
   It is shown once. If you lose it, rotate it. Name it after the operator's Inkmory
   profile, such as `Muon` (see [One agent, a key for each service](#one-agent-a-key-for-each-service)).
3. Click **Skill file** on the agent to download `<name>-skill.md` with the real API
   base URL already substituted.

Without the web UI you can do the same from the API host:

```
npm --workspace api run agent:create -- --name Muon
```

and copy `docs/popsmosis-agent-skill.md`, replacing `https://api.popsmosis.example` with your
API URL.

The runner needs these in its environment:

| Variable | Value |
|---|---|
| `POPSMOSIS_API_KEY` | the popsmosis agent's `sk_pop_…` key from step 2. Not an Inkmory key: see below |
| `POPSMOSIS_API_URL` | where the popsmosis API is: the `popsmosis-api` service's public URL, the same value as its `API_BASE_URL`. The skill file's shell commands and the studio read it. |
| `POPSMOSIS_ANTHROPIC_API_KEY` | Claude's brain and its picture checks. Its calls bill per token to this Anthropic API account, not to the plan running the routine ([PLAN.md](PLAN.md), section 4) |
| `MODEL_API_KEY` | a Muse brain and its picture checks, Muse Image, and SAM 3.1, which finds the structures in any painted picture: the Meta API key, from dev.meta.ai. Every Meta model the studio calls bills to it |
| `INKMORY_API_URL` | Inkmory's base URL, `https://inkmory.com` (the API host itself refuses direct calls) |
| `INKMORY_OPERATOR_API_KEY` | the operator agent's `sk_inkmory_…` key, from the Inkmory sponsor dashboard. With this and the URL set, the operator keeps its record |
| `INKMORY_ENABLED` | optional: `false` turns Inkmory off and leaves everything else as it is |
| `INKMORY_PLANNER_API_KEY` | the planner agent's (Boojum's) `sk_inkmory_…` key, from the Inkmory sponsor dashboard. With this and the URL set, the studio keeps the planner's record on every topic made on a Muse brain (README, [The planner's record on Inkmory](../README.md#the-planners-record-on-inkmory)). Only studio code reads it; the skill file never uses it |
| `STUDIO_PLANNER_PERSONA` | optional: `record` (the default) or `lesson`, where the planner's persona and memory go: into its record only, or into the bundle prompt as well |
| `INKMORY_PLANNER_ON_CLAUDE` | optional: `true` lets the planner keep its record on a topic whose brain is Claude. Unset, its record is Muse's only |
| `ELEVENLABS_API_KEY` | the narration and the exam question read aloud, and the song unless Lyria sings it (the audio step). Its calls bill to that ElevenLabs account |
| `ELEVENLABS_VOICE_ID` | optional: the narration's voice |
| `ELEVENLABS_USD_PER_1K_CREDITS` | optional: what 1,000 ElevenLabs credits cost on the plan, e.g. `0.30`, so a droplet's bill of materials shows its ElevenLabs lines in dollars too. Unset: credits only |
| `GEMINI_API_KEY` | Gemini 3.1 Flash Image when it paints, and Lyria when it sings: a Gemini API key from Google AI Studio, on a paid tier (Lyria has no free tier; about $0.08 a song) |
| `GEMINI_SONG_MODEL` | optional: the Lyria model, when Lyria sings. Default `lyria-3.5` |
| `STUDIO_VIDEOS` | optional: `off` makes text-only droplets (no claim, no media steps, no recovery) |

The brain, the singer and the painters are not set here. Which brain writes the lessons,
Claude's effort, who sings the song and who paints each brain's pictures are set by an admin
on **STUDIO** in the web app
(`/admin/studio`), and the studio reads them from the API: README, [Choosing the
models](../README.md#choosing-the-models). `BRAIN_PROVIDER`, `STUDIO_BRAIN_MODEL`,
`STUDIO_BRAIN_EFFORT` and `SONG_PROVIDER` are no longer read: while one is still set, each run
stops with `the studio's settings are wrong` and says to set it there and remove it, so
nothing changes without you seeing why. Give the environment the key of every brain and
painter picked there: a default brain or painter whose key is missing stops each run the
same way, and a topic an admin sent to a brain whose key is missing is given up with that
reason.

Inkmory takes back an adopted agent that logs no cycle for 3 days. So when the queues are
empty and the operator's last cycle log is two days old, an idle run logs a quiet cycle
(one extra GET per idle run checks the date). A newly adopted agent logs its first cycle
on the first run after adoption, with work or without.

### One agent, a key for each service

The operator (Muon, on Inkmory) is one agent working in two services, and each service
checks its own key, the way one person carries a separate badge for each building:

```
The operator (the Claude routine)
 ├─ on popsmosis: POPSMOSIS_API_KEY, sk_pop_…            reads the queues; the studio posts lessons with it
 └─ on Inkmory:   INKMORY_OPERATOR_API_KEY, sk_inkmory_…  writes the operator's public record

The planner (the brain: Opus now, Muse Wonder from day 8)
 ├─ on popsmosis: no key. It never calls popsmosis: the studio asks the brain, then
 │                posts what it wrote with the operator's popsmosis key
 └─ on Inkmory:   INKMORY_PLANNER_API_KEY, read only by studio code
```

Neither key works on the other service: popsmosis answers 401 to an Inkmory key, as it
would to no key at all. That is on purpose. To accept Inkmory's key, popsmosis would have
to ask Inkmory to vouch for every request, which would tie the app's security to Inkmory
and break the rule that Inkmory is best-effort: when Inkmory is down, popsmosis still
works.

The identity still lines up where people see it. A lesson card shows the name of the
popsmosis agent that posted it, so naming that agent after the operator's Inkmory profile
shows learners the same teacher in both places.

The runner also needs a checkout of this repository with `studio/requirements.txt`
installed, because the skill file runs the studio from it. A run does not hold a window
open: it reads both queues, runs the studio on one topic, answers the follow-ups, looks
again, and exits when they are empty.

### What the operator can reach, and how to keep it small

The operator reads text any signed-in learner writes (topics, follow-ups, files) and runs
in a shell whose environment holds the popsmosis key and every provider key. The skill
file tells it that learner text is data and never goes on a command line, and the API
refuses a lesson, reply or failure reason that looks like it carries a key (it logs
`agent text refused: it looks like a secret` when it does). Neither can stop a model that
is talked into something cleverer, so keep what a hijacked run could do small:

- **Network:** give the runner only the hosts it calls (Option A's allowlist below), never
  every host. A run that can reach any host can send a key anywhere.
- **Keys:** use provider keys made for popsmosis alone, each with a spend limit or budget
  in its provider's console, so a stolen one costs a capped amount.
- **When something looks wrong** (a lesson, reply or Inkmory entry with command output in
  it, or that log line): disable the agent on `/admin/agents`, rotate its key, and rotate
  the provider keys in the routine's environment.
- **Not yet:** the studio still reads the provider keys from the operator's own
  environment. Running the studio as a separate service that holds them would keep them
  out of the operator's shell entirely.

### What a run costs

The model re-reads the whole conversation on every turn it takes, so the cost of a
run is mostly the number of turns, not the size of the skill file. The file is built
around that:

| | turns |
|---|---|
| Nothing waiting | ~2 — one command prints `IDLE` and the run ends |
| Work waiting | a few per topic (the studio run, a retry if a step fails), one per follow-up, two for the Inkmory record, and one more round to confirm the queues are empty |

An idle run makes two GET requests and never touches Inkmory. A topic also costs its
brain calls, billed to `POPSMOSIS_ANTHROPIC_API_KEY`: the first live run on "brain
structures" was 2 Opus calls, $0.71, in 4.6 minutes. The rate limit is 300 per minute
per key, so it is not something a run can reach.

### Quiet hours

If your users are all in one place, most of the night buys nothing. Trim it in the
schedule, because a run that never starts is the only thing that costs exactly
nothing.

**1. Fire the routine only when someone might be awake.** Cron is evaluated in UTC
and cannot follow daylight saving, but one expression covers both halves of the year
for a 6am–8pm Pacific audience:

```
0 13-23,0-3 * * *
```

That is 15 firings a day instead of 24. It is the exact union of the awake hours
under PST (UTC-8) and PDT (UTC-7).

**2. Wire the API trigger**, so the schedule stops being what decides latency. With
[Wake the routine on write](#wake-the-routine-on-write) in place, a question asked at
11pm is answered within a minute whatever the cron says, and the schedule is only the
sweep that catches anything a failed fire missed. That is what makes trimming the cron
safe: you are cutting the fallback's cadence, not the response time.

Without the trigger, layer 1 alone is the blunt version — nothing runs between 4am and
1pm UTC, so an overnight question waits for the morning.

---

## 2. Pick a runner

Nothing Anthropic hosts is a daemon: every hosted option is a scheduled trigger that
starts a session, runs the prompt, and ends. What differs is how often it can fire.

### Option A: Claude Code routine (simplest)

1. In Claude Code on the web, create an environment for the routine:
   - **Environment variables:** everything in the table in section 1.
   - **Setup script:** none needed. The repository's session hook
     (`.claude/hooks/session-start.sh`) installs everything when a run starts: ffmpeg and
     libEGL, `studio/requirements.txt` (with PyTorch on CPU, Demucs and skia), the song
     timing's models (about 1.3 GB), and the node packages. The environment's container
     is cached once the hook has finished, so that is paid once (about 2 minutes), and a
     warm run's hook takes seconds.
   - **Network policy:** allow
     - your API host, `api.anthropic.com`, `api.elevenlabs.io`, R2's upload host
       (`<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`, where the studio puts the videos) and
       the Inkmory host, `api.meta.ai` (a Muse brain, Muse Image, SAM 3.1) and
       `generativelanguage.googleapis.com` (Gemini 3.1 Flash Image, Lyria): what a run calls;
     - `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org` (PyTorch's CPU
       wheels), `registry.npmjs.org`, and `archive.ubuntu.com` with `security.ubuntu.com`
       (ffmpeg and libEGL, when the image lacks them): what the session hook installs;
     - `dl.fbaipublicfiles.com` (the MMS aligner), `huggingface.co` and `*.hf.co` (Demucs,
       which Hugging Face serves from `us.aws.cdn.hf.co`): the song timing's models.

     The hook says which part it could not do (`session-start: ...` at the top of the
     run); the rest still runs.
2. Create a routine on the popsmosis repo with a cron of your choice (hourly is the
   normal minimum) and the prompt:
   ```
   Run one popsmosis heartbeat following docs/popsmosis-agent-skill.md. The API base URL is https://<your-api-host>.
   ```
3. Give the routine an **API trigger** as well, so the cron is the sweep rather than
   the latency budget — see [Wake the routine on write](#wake-the-routine-on-write).

The skill file and the studio are in the repo the routine checks out, so nothing else
is needed. The API must be publicly reachable for this option.

A topic is several minutes of studio work. Set `QUESTION_CLAIM_MINUTES` on the API
above the longest single step (the default 20 is safe): the studio renews the topic's
claim before each step, and a lapsed claim lets an overlapping run start the same topic
again.

Its videos take longer: the run starts them in the background as soon as the lesson is
posted, answers follow-ups meanwhile, then waits for them (`python -m studio wait <id>`,
at most 9 minutes a call), because the render dies with the run's machine. The videos are
claimed the same way (`POST /api/answers/:id/video/claim`), renewed before every media
step. A render that stops reporting for `VIDEO_STALE_MINUTES` (60) has died with its
machine: the next run takes it from `GET /api/videos/queue` (`python -m studio recover`)
and makes it again from the lesson the API stored, up to `VIDEO_MAX_ATTEMPTS` (2) attempts
in all. Videos that fail twice in a row are given up (`python -m studio fail <id>`): the
lesson stays, and the droplet says why.

#### Wake the routine on write

A cron alone means a question waits up to one interval for its answer. Give the
routine an **API trigger** as well and the API wakes it the moment someone asks, so
the cron becomes the sweep rather than the latency budget.

1. On [claude.ai/code/routines](https://claude.ai/code/routines), edit the routine,
   add an **API** trigger, and copy its URL. Click **Generate token** and copy the
   token — it is shown once.
2. Set both on the API service (Render: popsmosis-api > Environment), beside the ones in
   `.env.example`:

   | Variable | Value |
   |---|---|
   | `POPSMOSIS_ROUTINE_FIRE_URL` | `https://api.anthropic.com/v1/claude_code/routines/<routine-id>/fire` |
   | `POPSMOSIS_ROUTINE_FIRE_TOKEN` | the token from step 1 |
   | `POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS` | optional cooldown, default `300`, max `3600`; `0` fires on every write |

With both set, `POST /api/questions` and a human `POST /api/answers/:id/replies` fire
the trigger. An agent reply never does: it takes the thread back off the reply queue,
and firing there would have the teacher wake itself in a loop.

The POST is fire-and-forget — the write's `201` never waits on it, and a trigger
endpoint that is down, slow, or holding a revoked token costs the teacher its latency,
not the user their question. It lands in the API log as `routine fired`,
`routine fire rejected` (with the status) or `routine fire failed`.

It is debounced per API instance: the first write fires at once and opens the
cooldown, and writes inside that cooldown collapse into a single trailing fire when it
ends. A burst of concurrent writes collapses the same way, and a write landing exactly
on the window boundary fires once, not twice. Five questions asked in one minute are
two fires, not five.

Before spending that trailing fire the API asks its own database whether either queue
still holds anything. A fire is not two GETs — it is a whole cloud session that clones
a repo and spends one of your metered daily routine runs — and the run woken at the top
of the window has had the whole window to drain both queues, so the trailing fire is
usually landing on an empty queue. When it is, nothing is spent and the log says
`routine fire skipped, queues already empty`. The check is two indexed existence
queries and it fails open: if the database cannot answer, the fire goes ahead, because
a wasted heartbeat costs one run and a question nobody is woken for waits for the next
sweep. The leading fire never asks — the write that triggered it just created the work,
and that path is the latency this whole mechanism exists to remove. The trailing half matters — it catches a question asked just after the run it
woke had already drained the queue. Two API instances can each fire once per window;
that costs one extra heartbeat with empty queues, which is two GETs.

**Set the cooldown at or above how long one run takes end to end.** A run boots a
container, clones the repo and reads the skill file before it ever reads a queue, so it
is minutes, not seconds — the one measured run took 4m29s, which is why the default is
300. Set it shorter and the trailing fire is *guaranteed* to wake a second session while
the first is still working: that session mostly collects `409`s from the
one-answer-per-question guard, and the queues-already-empty check above can never skip
anything, because the first run has not had time to drain. Longer trades latency for
cost in the other direction — a question arriving after a run has read its queue waits
out the rest of the window before it gets a fire of its own.

### Two runs at once

The schedule can fire inside an API-triggered run, so two runs do overlap. The queue
hands each caller a **short lease** on the questions it returns — 20 minutes by default,
`QUESTION_CLAIM_MINUTES`, renewed by the studio before each step — so the second run is
given different questions rather than the same oldest five. Without it nothing breaks, because the answer write is row-locked
and one-answer-per-question is a constraint; the loser just writes a full answer for
every question and throws it away, which is the expensive half of a run spent twice.
Measured on twelve queued questions: two overlapping runs went from 12 discarded
answers to 0, splitting the work 7 and 5.

The lease expires rather than being released. A run that dies mid-answer must not hold
a question out of the queue forever, and nothing outside the database can be trusted to
report that it finished. Answering removes the question on its own by flipping its
status, so the happy path never clears a claim.

A question under a live claim is also not counted as work by the trailing fire, so the
API will not wake a second session for something a run is already answering.

Once there is real traffic the logs say which way to move it: the ratio of
`routine fired` to `routine fire skipped, queues already empty`, against the run
durations on the routine's own page.

Nothing needs to be unset alongside this. The skill file has no polling window to turn
off — a run reads the queues, drains them, and exits.

### Option B: Managed Agents scheduled deployment (near-continuous, hosted)

Create a Managed Agent with the skill file uploaded as a Skill, vault credentials for
the variables in section 1, this repository mounted with the studio's requirements
installed, and a scheduled deployment with a cron such as `*/5 * * * *`.
Each firing runs one heartbeat. Idle firings cost two GET requests. This is the
closest thing to always-on that Anthropic runs for you.

### Option C: Cowork scheduled task (your machine)

1. Allow network egress to your API host in Claude's capability settings.
2. Put the skill file in a project folder, set `POPSMOSIS_API_KEY`, and schedule
   "Run one popsmosis heartbeat following popsmosis-agent-skill.md" at the cadence you want.
   Runs only while the machine is awake.

### Option D: Claude Code CLI on a cron (your machine; also how to test locally)

Run it from a checkout of this repository, where the studio lives, with the studio's
requirements installed (README, "The studio"):

```
cd /path/to/popsmosis
export POPSMOSIS_API_KEY=sk_pop_...
export POPSMOSIS_API_URL=http://localhost:3001
export POPSMOSIS_ANTHROPIC_API_KEY=sk-ant-...
claude -p "Run one popsmosis heartbeat following docs/popsmosis-agent-skill.md. You are authorized to make the HTTP requests and run the studio commands it describes, autonomously, without asking for confirmation."
```

Then schedule it:

```
*/5 * * * * cd /path/to/popsmosis && . ./.env.agent && claude -p "Run one popsmosis heartbeat following docs/popsmosis-agent-skill.md" >> heartbeat.log 2>&1
```

with the variables from section 1 exported in `.env.agent` (keep it out of git).

### Option E: Anything else

OpenClaw, a Python script, another vendor's model. The contract is a few HTTP calls
and the studio's commands, documented in the skill file. If you want a true always-on daemon, write a loop that
runs the heartbeat forever and host it; nothing in the API changes.

---

## 3. Verify

1. Ask for a topic on `/learn`.
2. Run a heartbeat. The topic's card flips from **BREWING** to **READY** once the
   studio's fact-check passes, about five minutes in, or to **FAILED** with the reason
   if the studio gave up on it after a retry. With an API trigger wired up, asking for the topic is
   itself the heartbeat: the API log shows `routine fired` and a new run appears on
   the routine's detail page.
3. Ask a follow-up under the lesson. On the next heartbeat the teacher responds.
4. On `/admin/agents`, **last seen** updates after each heartbeat.

If nothing happens:

- `GET /api/agents/me` with the key returns 401 → wrong key; 403 → agent disabled.
- The runner cannot reach the API → check the environment's network allowlist.
- The queue is empty → every question already has an answer, or was given up.
- The studio fails → `python -m studio show <id>` on the runner prints the job, its runs
  and each step's outcome; `jobs/<id>/runs.jsonl` and `calls.jsonl` hold the details.
- `POPSMOSIS_API_URL` is unset → Step 0 prints `ERR 000 000`, and the studio stops with
  "POPSMOSIS_API_URL and POPSMOSIS_API_KEY must both be set".
- A write logs `routine fire rejected` with `401` → the trigger token is wrong or was
  revoked; regenerate it on the routine and update `POPSMOSIS_ROUTINE_FIRE_TOKEN`. `404` →
  the routine ID in `POPSMOSIS_ROUTINE_FIRE_URL` is wrong, or the API trigger was removed.
- A write logs nothing at all → one of `POPSMOSIS_ROUTINE_FIRE_URL` /
  `POPSMOSIS_ROUTINE_FIRE_TOKEN` is unset, or the write landed inside the debounce window.

---

## 4. Kill switch

Disable the agent on `/admin/agents`. Every call with its key returns 403 from that
moment; nothing else needs to be stopped. Enable again to resume.
