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

Today a droplet is the written lesson. Next come two videos made from the same
fact-checked plan and diagrams: a narrated lesson and a karaoke-style lesson song, stored
in Supabase and shown beside the text. The plan, and where the pipeline stands, is in
[docs/PLAN.md](docs/PLAN.md).

Popsmosis started as a copy of [zoey](https://github.com/Iztera/zoey) (an agent-answered
advice column), whose architecture and look-and-feel come from
[sparks](https://github.com/jclark256/sparks). The API still names a topic request a
*question*, the lesson its *answer*, and follow-ups *replies*.

## Layout

```
api/     Fastify 5 + TypeScript. Google OAuth -> session cookie for humans; sk_pop_ bearer keys for agents.
web/     Next.js 15 + Tailwind 4. The /learn page, sign-in, and an admin page for agents.
prisma/  Postgres schema and migrations (User, Agent, Question, Answer, Reply).
studio/  Python. The pipeline that turns a topic into a droplet: brain, bundle, fact-check so far.
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
| Non-browser clients | `pk_pop_` personal access tokens (see below) |

## Local development

Prerequisites: Node 22+, Docker (or a local Postgres 16), Google OAuth credentials
with `http://localhost:3000/auth/callback` as an authorized redirect URI.

```bash
docker compose up -d                      # Postgres on :5432
cp .env.example .env                      # fill in JWT_SECRET, GOOGLE_*, ADMIN_EMAILS (and optionally ALLOWED_EMAIL_DOMAINS)
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
rotate, revoke, mint, enable, disable, and download the skill file. **USERS** is where
access is granted: anyone with a Google account can sign in, but they see only a
waiting page until an admin approves them there (or denies them, or revokes them
later). Addresses on `ALLOWED_EMAIL_DOMAINS` / `ALLOWED_EMAILS` / `ADMIN_EMAILS` skip
the queue. The same page promotes and demotes admins — except for an address in `ADMIN_EMAILS`,
which is an admin by configuration and is refused there rather than demoted and quietly
restored. From the shell,
`npm --workspace api run user:promote -- --email them@example.com` does the same (and
approves them), and so does `UPDATE users SET role = 'ADMIN', status = 'APPROVED' WHERE
email = '…'` in SQL. However the role gets set, the API keeps access in step with it on
the next request: an admin found waiting is approved, and an address added to
`ADMIN_EMAILS` takes effect on a session that is already open, so nobody ends up stuck
on the waiting page they were meant to be able to clear.

### Access tokens (non-browser clients)

A human signs in with Google and gets a session cookie, which is no use to
something without a browser. **TOKENS** in the nav mints a personal access
token (`pk_pop_...`, shown once) that authenticates as you on the same
endpoints your browser uses, from curl, a script, or another app. From the shell:

```bash
npm --workspace api run token:create -- --email you@example.com --name "my script"
```

A token is exactly its owner and no more: it is refused if the account is not
approved, and it cannot mint or revoke tokens (that needs the cookie), so a
leaked token can neither replace itself nor revoke the one you would kill it
with. Revoking takes effect at once on the instance that handles it, and within
`USER_TOKEN_TTL_MS` (10s) on any other instance that had recently seen the token
— the auth caches are per-process, as they already are for agent keys. Unlike a browser request, a token
call goes straight to the API rather than through the Next.js proxy.

To fill the page with sample data (two users, one agent, a topic with its lesson and a
follow-up thread, one topic still waiting), run `npm --workspace api run seed:dev`. It prints
the agent's key.

Run a heartbeat against the local API with Claude Code CLI:

```bash
mkdir ../popsmosis-agent && cp docs/popsmosis-agent-skill.md ../popsmosis-agent/ && cd ../popsmosis-agent
sed -i 's#https://api.popsmosis.example#http://localhost:3001#' popsmosis-agent-skill.md
POPSMOSIS_API_KEY=sk_pop_... claude -p "Run one popsmosis heartbeat following popsmosis-agent-skill.md"
```

See [docs/agent-runbook.md](docs/agent-runbook.md) for the hosted options.

### The studio

The studio (Python 3.13) turns a topic into a droplet; so far it writes the bundle and
fact-checks it. It reads `ANTHROPIC_API_KEY`, and its calls bill per token to that API
account ([docs/PLAN.md](docs/PLAN.md), section 4). Each job writes into `jobs/<id>/`.

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r studio/requirements.txt
python -m studio plan --topic "brain structures" --course "AP Psych"   # bundle, then fact-check
python -m studio step factcheck <job-id>                               # re-run one step
python -m studio show <job-id>                                         # facts, lyrics, verdicts, calls
```

## Tests

```bash
npm --workspace api run typecheck
npm --workspace api test                  # unit tests, no database
INTEGRATION=1 npm --workspace api test    # plus the end-to-end flow against DATABASE_URL
npm --workspace web run build
npm run e2e                               # Playwright browser tests; needs api + web running (see web/e2e/README.md)
python -m pytest studio                   # the studio, offline; test_live.py also runs when ANTHROPIC_API_KEY is set
```

CI runs all of it: API typecheck and tests against a Postgres service, the web build,
the browser tests against the real stack, and the studio's offline tests.

## API

Errors are always `{ "error": { "code", "message" } }`.

**Humans** (session cookie, `X-Requested-With: XMLHttpRequest` on writes)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/google` | `{ code }` from the OAuth redirect; sets the cookie |
| GET | `/api/auth/me` | current user |
| POST | `/api/auth/logout` | clears the cookie |
| GET | `/api/questions` | everyone's topics, newest first; `?mine=true`, `?cursor=`, or `?ids=a,b` for specific ones |
| GET | `/api/questions/:id` | one topic with its lesson and follow-ups |
| GET | `/api/questions/status` | `{ latestAt }` for polling |
| POST | `/api/questions` | ask for a topic: `{ content, attachments? }` 3–2000 chars; `attachments` see [Attachments](#attachments) |
| DELETE | `/api/questions/:id` | own topic, only while it has no lesson |
| POST | `/api/answers/:id/replies` | a follow-up: `{ content, attachments? }` 1–2000 chars |
| DELETE | `/api/replies/:id` | own reply |
| GET | `/api/attachments/:id` | a file sent with a question or reply; also to agents |

**Agents** (`Authorization: Bearer sk_pop_…`)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/agents/me` | confirm the key |
| GET | `/api/questions/queue?limit=N` | topics with no lesson yet, oldest first |
| POST | `/api/questions/:id/answer` | the lesson: `{ content, model? }`; 409 once there is one |
| GET | `/api/replies/queue?limit=N` | threads where a human followed up last |
| POST | `/api/answers/:id/replies` | `{ content, replyToId }`; 409 if already responded |
| GET | `/api/attachments/:id` | a file a human sent; 410 once past the retention |

### Attachments

A topic request or a follow-up may carry files - a photo of class notes, a diagram, a text file:

```json
"attachments": [ { "name": "pasted.png", "mimeType": "image/png", "data": "<the file, base64>" } ]
```

At most 5 per message, 4 MB each and 8 MB together, sent inside the message's own body so a
message and its files are written together or not at all; they are kept in Postgres beside it
and go when it does. Each comes back listed on its message as
`{ id, name, mimeType, size, url, expired }`, never with its bytes; `GET url` serves them to any
approved user and to the teacher, whose queues list them the same way. What a file is served as
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

## Deploy

`render.yaml` is a Render Blueprint for a Postgres, the API, and the web app. Step by
step instructions, the env var values, and the Google OAuth setup are in
[docs/DEPLOY.md](docs/DEPLOY.md). In short: New > Blueprint > pick this repo > fill in
the prompted values > Apply. Migrations run automatically before each API deploy.

The API starts as `node --import tsx dist/index.js`: the build is typechecked and
compiled by `tsc`, but the Prisma-generated client uses extension-less ESM imports
that plain Node cannot resolve, so the tsx loader stays in the start command (same as
sparks).
