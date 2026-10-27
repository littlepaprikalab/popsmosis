# Popsmosis

**Droplets for learning.**

Signed-in users ask for a topic they want to learn - "ATP synthesis", "the French
Revolution". An AI teacher, running **outside** this stack as a skill-file-driven agent
(a Claude routine, a Cowork scheduled task, Claude Code CLI on a cron, or anything else
that can make HTTP calls), turns each request into a **droplet** - a short lesson -
through the public API using an agent API key. Users ask follow-ups under the lesson and
the teacher responds on its next pass. When the teacher runs as a Claude routine with an
API trigger, the API fires that trigger on a new topic or a follow-up, so the pass starts
within seconds rather than at the next scheduled run.

The teacher does not write the lessons itself: it runs the **studio**, a Python
pipeline in this repo, which writes each lesson, fact-checks every line and posts it.
Today a droplet is that written lesson. Next come two videos made from the same
fact-checked plan and diagrams: a narrated lesson and a karaoke-style lesson song, stored
in Supabase and shown beside the text. Once a droplet's videos are done it is listed on the
public **Droplets** tab (`/droplets`), where anyone can watch it without an account. The plan, and where the pipeline stands, is in
[docs/PLAN.md](docs/PLAN.md).

Popsmosis started as a copy of [zoey](https://github.com/Iztera/zoey) (an agent-answered
advice column), whose architecture and look-and-feel come from
[sparks](https://github.com/jclark256/sparks). The API still names a topic request a
*question*, the lesson its *answer*, and follow-ups *replies*.

## Layout

```
api/     Fastify 5 + TypeScript. Google OAuth -> session cookie for humans; sk_pop_ bearer keys for agents.
web/     Next.js 15 + Tailwind 4. The /learn page, the public /droplets tab, sign-in, and the admin pages.
prisma/  Postgres schema and migrations (User, Agent, Question, Answer, Reply).
studio/  Python. The pipeline that turns a topic into a droplet: brain, bundle, fact-check, posting so far.
docs/    PLAN.md, the agent skill file, and the runbook for running it.
```

## Stack

| Layer | Choice |
|---|---|
| API | Fastify + TypeScript |
| Database | PostgreSQL + Prisma 7 |
| Auth | Google OAuth (humans) + hashed API keys (agents) |
| Frontend | Next.js + Tailwind |
| Agent | `docs/popsmosis-agent-skill.md`, run by any LLM harness |

## Local development

Prerequisites: Node 22+, Docker (or a local Postgres 16), Google OAuth credentials
with `http://localhost:3000/auth/callback` as an authorized redirect URI.

```bash
docker compose up -d                      # Postgres on :5432
cp .env.example .env                      # fill in JWT_SECRET, GOOGLE_*, ADMIN_EMAILS
npm install
npm --workspace api run db:migrate        # apply migrations, generate the client
npm --workspace api run dev               # API on :3001
npm --workspace web run dev               # web on :3000 (proxies /api/* to :3001)
```

Create an agent and get its key (shown once):

```bash
npm --workspace api run agent:create -- --name popsmosis-teacher
```

Or sign in with an `ADMIN_EMAILS` address and use **AGENTS** in the nav: create,
rotate, revoke, mint, enable, disable, and download the skill file. Anyone with a
Google account can sign in and use the site; there is no approval step. **USERS**
promotes and demotes admins — except for an address in `ADMIN_EMAILS`, which is an
admin by configuration and is refused there rather than demoted and quietly restored.
From the shell, `npm --workspace api run user:promote -- --email them@example.com`
does the same, and so does `UPDATE users SET role = 'ADMIN' WHERE email = '…'` in SQL.
An address added to `ADMIN_EMAILS` takes effect on a session that is already open, on
its next request, without a new sign-in.

To fill the page with sample data (two users, one agent, a topic with its lesson, its videos
and a follow-up thread, one topic still waiting, and older lessons whose videos are rendering
or failed), run `npm --workspace api run seed:dev`. It prints the agent's key. The seeded
videos are two short clips in `web/public/samples/`, so `/droplets` and the players on
`/learn` work locally with no Supabase.

Run a heartbeat against the local API with Claude Code CLI, from this checkout (the skill
file runs the studio, below, from here):

```bash
export POPSMOSIS_API_URL=http://localhost:3001 POPSMOSIS_API_KEY=sk_pop_... POPSMOSIS_ANTHROPIC_API_KEY=sk-ant-...
claude -p "Run one popsmosis heartbeat following docs/popsmosis-agent-skill.md"
```

See [docs/agent-runbook.md](docs/agent-runbook.md) for the hosted options.

### The studio

The studio (Python 3.13) turns a topic into a droplet; so far it writes the bundle,
fact-checks it and posts the lesson. It reads `POPSMOSIS_ANTHROPIC_API_KEY` (or
`ANTHROPIC_API_KEY`, which a Claude Code cloud environment does not pass through), and its
calls bill per token to that API account ([docs/PLAN.md](docs/PLAN.md), section 4). Each
job writes into `jobs/<id>/`.

The teacher runs it on a topic from the queue, by the question's id. That needs
`POPSMOSIS_API_URL` and `POPSMOSIS_API_KEY` too, and is what the skill file does:

```bash
python -m studio run <question-id>       # claim, bundle, fact-check, post; again resumes at the first missing step
python -m studio fail <question-id>      # after a second failure: give the topic up (its videos, once posted)
python -m studio summary <question-id>   # what its runs did, for the teacher's record
python -m studio videos <question-id>    # the posted lesson's two videos; --background on a routine
python -m studio wait <question-id>      # the background videos' outcome, waiting up to 9 minutes
python -m studio recover                 # take a render that died with its machine and rebuild its job here
```

`videos` runs the media steps (`studio/media.py`): art and audio at the same time, then
the timeline, render and upload. `run` claims the videos as soon as it posts the lesson,
and `videos` renews the claim before every step, so a render whose machine dies is handed
to a later run by `recover` (`docs/agent-runbook.md`). `STUDIO_VIDEOS=off` makes
text-only droplets.

`run` prints one line: `DONE`, `FAILED <step>: <reason>` (run it again to retry that
step) or `STOPPED <step>: <reason>` (the topic is gone, taken or done, or an earlier run
of it is still going). A run killed mid-step is recorded as that step failing. To try the
pipeline without posting anything, start a local job instead:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r studio/requirements.txt
python -m studio plan --topic "brain structures" --course "AP Psych"   # bundle, then fact-check
python -m studio step factcheck <job-id>                               # re-run one step
python -m studio show <job-id>                                         # facts, scenes, lyrics, checks, verdicts, runs, calls
```

Once a job has its rendered videos and a posted lesson (`posted.json`), the upload step
(step 9) copies them to Supabase and marks them READY, which lists the droplet on the
Droplets tab. It uses the same two variables, and the API needs Supabase set up
([docs/DEPLOY.md](docs/DEPLOY.md), "Supabase Storage"):

```bash
python -m studio step upload <job-id>    # videos and posters public, the rest private; again sends only what changed
```

`STUDIO_BRAIN_MODEL` and `STUDIO_BRAIN_EFFORT` set the brain's model and effort (defaults
`claude-opus-5-5` and `high`; effort is one of `low`, `medium`, `high`, `xhigh`, `max`).
They apply with `BRAIN_PROVIDER=claude` only. To try a prompt change cheaply, run the brain on
Haiku first (about 2 cents a topic instead of 70):
`STUDIO_BRAIN_MODEL=claude-haiku-5-5 python -m studio plan ... --brief`.

## Tests

```bash
npm --workspace api run typecheck
npm --workspace api test                  # unit tests, no database
INTEGRATION=1 npm --workspace api test    # plus the end-to-end flow against DATABASE_URL
npm --workspace web run build
npm run e2e                               # Playwright browser tests; needs api + web running (see web/e2e/README.md)
python -m pytest studio                   # the studio, offline; test_live.py also runs when POPSMOSIS_ANTHROPIC_API_KEY is set
```

CI runs all of it: API typecheck and tests against a Postgres service, the web build,
the browser tests against the real stack, and the studio's offline tests.

In a Claude Code cloud session, `.claude/hooks/session-start.sh` sets all of this up before
the session starts: ffmpeg and libEGL, the studio's packages (PyTorch, Demucs and skia
included) and the song timing's models, the node packages and Prisma client, and a running
Postgres with the `popsmosis` database migrated and `DATABASE_URL` set. The first session in
an environment takes about two minutes; later ones skip what has not changed.

## API

Errors are always `{ "error": { "code", "message" } }`.

**Anyone** (no sign-in; 60 requests a minute per visitor)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/droplets` | the Droplets tab: droplets whose videos are READY and that no admin hid, newest first; `?cursor=`, `?limit=` (12, at most 24). Each: `{ id, title, course, topic, excerpt, video, createdAt }` |
| GET | `/api/droplets/:id` | one droplet, by its answer id: the same, plus the lesson `content` and the song's `lyrics`; 404 unless it is on the tab |

Only the title (the bundle's `course` and `topic`), the lesson text, the videos with
their posters and the lyrics are public. The learner's name and avatar, the request in
their own words, their files and the follow-ups never leave through these.

**Humans** (session cookie, `X-Requested-With: XMLHttpRequest` on writes)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/google` | `{ code }` from the OAuth redirect; sets the cookie |
| GET | `/api/auth/me` | current user |
| POST | `/api/auth/logout` | clears the cookie |
| GET | `/api/questions` | everyone's topics, newest first; `?mine=true`, `?status=PENDING\|ANSWERED\|FAILED`, `?cursor=`, or `?ids=a,b` for specific ones |
| GET | `/api/questions/:id` | one topic with its lesson and follow-ups |
| GET | `/api/questions/status` | `{ latestAt }` for polling |
| POST | `/api/questions` | ask for a topic: `{ content, attachments? }` 3–2000 chars; `attachments` see [Attachments](#attachments) |
| DELETE | `/api/questions/:id` | own topic, only while it has no lesson (waiting, or `FAILED`) |
| POST | `/api/answers/:id/replies` | a follow-up: `{ content, attachments? }` 1–2000 chars |
| DELETE | `/api/replies/:id` | own reply |
| GET | `/api/attachments/:id` | a file sent with a question or reply; also to agents |

**Agents** (`Authorization: Bearer sk_pop_…`)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/agents/me` | confirm the key |
| GET | `/api/questions/queue?limit=N` | topics with no lesson yet, oldest first, each leased to the caller for `QUESTION_CLAIM_MINUTES` |
| POST | `/api/questions/:id/claim` | take or renew the lease on one topic and read it; 409 if answered, failed, or leased to another agent |
| POST | `/api/questions/:id/answer` | the lesson: `{ content, model?, bundle?, factcheck? }`; the two JSON objects go together and `factcheck.status` must be `passed`; 409 once there is one, with `answerId` when it is the caller's own |
| POST | `/api/questions/:id/fail` | give a topic up: `{ reason }`; it leaves the queue and its card shows the reason; the last claimer only |
| PATCH | `/api/answers/:id/video` | own answers: `{ status: RENDERING\|READY\|FAILED, songVideoUrl?, lessonVideoUrl?, songPosterUrl?, lessonPosterUrl?, error? }`; a URL left out is kept, so the two videos can report separately; READY lists it on the Droplets tab |
| POST | `/api/answers/:id/upload-url` | own answers: `{ bucket: droplets\|studio, file }`; a signed Supabase upload URL, and for `droplets` its public URL; 503 until `SUPABASE_URL` is set |
| POST | `/api/answers/:id/video/claim` | own answers: `{ attempt? }`; without one, start an attempt at the videos (never started, given up, or quiet past `VIDEO_STALE_MINUTES`; at most `VIDEO_MAX_ATTEMPTS`, default 2), marking them RENDERING; with the caller's attempt, renew it. Returns `{ answerId, questionId, attempt, bundle, factcheck, video }`; 409 when another run holds them, they are READY, or the attempts are used up |
| GET | `/api/videos/queue?limit=N` | own droplets whose render went quiet past `VIDEO_STALE_MINUTES` (its machine died), oldest first, each claimed as its next attempt, as the claim returns them |
| POST | `/api/answers/:id/video/fail` | own answers: `{ reason, attempt }`; give the videos up (FAILED with the reason); only the current attempt may |
| GET | `/api/replies/queue?limit=N` | threads where a human followed up last, each with the droplet's `answer.context` |
| POST | `/api/answers/:id/replies` | `{ content, replyToId }`; 409 if already responded |
| GET | `/api/attachments/:id` | a file a human sent; 410 once past the retention |

A lesson's `answer` also carries `video` (`null` for a text-only droplet): its status,
both URLs, both posters and the error; and `hidden`, true when an admin took it off the
Droplets tab. A render that has not reported for `VIDEO_STALE_MINUTES` (60)
reads as `FAILED`. The bundle and fact-check are stored with the lesson but left out of
the question routes; the reply queue's `context` is the compact part a follow-up needs.

### Attachments

A topic request or a follow-up may carry files - a photo of class notes, a diagram, a text file:

```json
"attachments": [ { "name": "pasted.png", "mimeType": "image/png", "data": "<the file, base64>" } ]
```

At most 5 per message, 4 MB each and 8 MB together, sent inside the message's own body so a
message and its files are written together or not at all; they are kept in Postgres beside it
and go when it does. Each comes back listed on its message as
`{ id, name, mimeType, size, url, expired }`, never with its bytes; `GET url` serves them to any
signed-in user and to the teacher, whose queues list them the same way. What a file is served as
is decided from its bytes, not from what was sent: PNG, JPEG, GIF and WebP as themselves, UTF-8
text as `text/plain`, anything else as a download, all in a sandbox. Past
`ATTACHMENT_RETENTION_DAYS` (default 30, 0 keeps them) the bytes are let go and the row stays,
`expired: true`, its `url` answering 410. Agents cannot send files.

**Admins** (session cookie, `ADMIN` role)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/agents` | list |
| POST | `/api/agents` | `{ name }`; returns the raw key once |
| POST | `/api/agents/:id/rotate-key` | mint a key (new key, old one dead) |
| POST | `/api/agents/:id/revoke-key` | no key works until one is minted |
| PATCH | `/api/agents/:id` | `{ enabled }` kill switch |
| GET | `/api/agents/:id/skill` | skill file with the real base URL |
| GET | `/api/users?q=` | list users |
| PATCH | `/api/users/:id` | `{ role: 'ADMIN' \| 'USER' }`; never yourself or the last admin |
| PATCH | `/api/answers/:id` | `{ hidden }`: take a droplet off the Droplets tab, or put it back; the lesson stays on `/learn` |

## Deploy

`render.yaml` is a Render Blueprint for a Postgres, the API, and the web app. Step by
step instructions, the env var values, and the Google OAuth setup are in
[docs/DEPLOY.md](docs/DEPLOY.md). In short: New > Blueprint > pick this repo > fill in
the prompted values > Apply. Migrations run automatically before each API deploy.

The API starts as `node --import tsx dist/index.js`: the build is typechecked and
compiled by `tsc`, but the Prisma-generated client uses extension-less ESM imports
that plain Node cannot resolve, so the tsx loader stays in the start command (same as
sparks).
