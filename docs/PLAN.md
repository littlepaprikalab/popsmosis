# zoey — Q&A support with an agent-answered advice column

Status: **implemented** (phases 0–3 and the admin page from phase 4; see section 9).
This document is the design the code follows. It borrows architecture and code from
the `sparks` repo (`jclark256/sparks`), whose advice column (`/advice`) is the closest
existing feature. Where the code and this document differ, the code wins; the README
lists the endpoints as built.

## 1. What we are building

1. A web app with **Google sign-in**.
2. A signed-in user asks a **question** on the advice page.
3. An **agent that runs outside the stack** (a Claude routine, a Cowork scheduled task,
   Claude Code CLI on a cron, or any LLM runner) reads a **skill file**, polls the API
   for unanswered questions, writes an answer, and **submits it over the same public
   HTTP API**, authenticating with an **agent API key**. A request without a valid key
   is rejected.
4. The web page shows the **answer** under the question (polling, no page reload).
5. Any signed-in user can **reply** to the agent's answer, and the agent **replies
   back** on its next heartbeat. Replies show as a conversation thread under the answer.

Everything the agent does goes through the API. It has no database access and no
code in this repo beyond the skill file. That is exactly the sparks model: humans
authenticate with a session cookie, agents authenticate with a bearer API key, no
endpoint accepts both, and the sponsor brings their own runner.

## 2. What we take from sparks, and what we leave behind

Sparks is a large platform (dating, residency, nurseries, partitioned tables, Redis
caching, 1,400-line schema). zoey should copy the **patterns and small library files**,
not the feature set.

### Copy nearly verbatim

| sparks file | why |
|---|---|
| `api/src/lib/apikey.ts` | `generateApiKey()` + sha256 `hashApiKey()`. Change prefix to `sk_zoey_`. |
| `api/src/lib/session.ts` | httpOnly cookie, 7-day JWT, sliding renewal. Rename cookie to `zoey_session`. |
| `api/src/middleware/auth.ts` | `requireUser`, `requireAgent`, `optionalUser`. Drop the Redis cache and residency-key branches. |
| `api/src/routes/auth.ts` (Google half only) | Exchange OAuth code with `google-auth-library`, upsert user, set cookie, `/me`, `/logout`. Drop Supabase. |
| `api/src/lib/errors.ts`, `validation.ts`, `cursor.ts`, `ulid.ts` | Small, self-contained helpers. |
| `api/src/index.ts` (plugin setup) | cors with credentials, cookie, helmet, jwt, rate-limit, error handler, CSRF header check. Drop proxy-secret gate, scanner hooks, Redis, multipart, agent-response stripping. |
| `web/src/lib/api.ts` (the `request()` wrapper) | `credentials: 'include'`, `X-Requested-With` CSRF header, 401 handling, 429 retry. |
| `web/src/lib/auth.tsx` | `AuthProvider` / `useAuth` that probes `/api/auth/me` on mount. |
| `web/src/app/auth/callback/page.tsx` | Builds the Google auth URL with a CSRF `state`, exchanges the code, redirects to `returnTo`. |
| `web/next.config.ts` (rewrites + env) | `/api/*` rewrite to the Fastify API so the cookie is same-origin. |
| `web/src/app/advice/page.tsx` (structure and markup) | Question form, list, per-question answers, polling of a `/status` endpoint, card and badge markup. Strip votes, passes, inner thoughts, status tabs. |
| `web/src/app/globals.css` | The whole visual system: CSS variables for dark and light themes, monospace body font, focus rings, scrollbar, `animate-fade-in` / `animate-slide-down` / `animate-loading-pulse`, `badge-human` / `badge-agent`. Drop the personality, tone, and inner-thought token groups. |
| `web/src/app/layout.tsx` (body shell) | `min-h-screen flex flex-col` body, providers, sticky nav, `main.flex-1`, footer. Drop the SEO metadata and JSON-LD blocks. |
| `web/src/components/Nav.tsx` | Sticky top bar, red uppercase wordmark, uppercase link row with active underline, sign-in / `@username` / logout, hamburger menu on mobile. Cut the link list down to zoey's pages. |
| `web/src/components/Footer.tsx`, `PageContainer.tsx`, `LoadingMessage.tsx`, `Toast.tsx`, `ConfirmDialog.tsx`, `Markdown.tsx`, `ErrorBoundary.tsx` | Shared UI as-is, minus sparks-specific links. |
| `web/src/components/ViewerComments.tsx` (structure) | Template for the reply thread under an answer: avatar dot, name, `badge-human` pill, time-ago, textarea + submit. Strip votes and agent reactions. |
| `web/src/lib/usePolling.ts`, `useGuardedSubmit.ts`, `time.ts` | Polling hook, double-submit guard, `timeAgo`. |

### Leave behind (for now)

- Table partitioning, composite primary keys, raw SQL counters. Plain Prisma relations are fine at zoey's scale.
- Redis / Upstash caching and the Redis rate-limit store.
- Inner thoughts, votes, passes, feed events, timeline events, residency keys.
- The `PROXY_SECRET` gate between Next.js and the API. Worth adding once deployed publicly; not needed for v1.
- Supabase (auth and storage).

### Also copy (the agent side)

| sparks file | why |
|---|---|
| `docs/sparks-agent-skill.md` (Authentication + "Advice Questions" phase) | Template for `docs/zoey-agent-skill.md`. zoey's file is a fraction of the size: one auth section, one heartbeat with three calls. |
| `api/src/routes/agents.ts` (`GET /api/agents/:id/skill`) | Serves the skill file with the real base URL substituted, so the runner never has to edit it by hand. |
| `docs/sponsor-quickstart.md` | Template for `docs/agent-runbook.md`: how to run the skill under a Claude routine, Cowork, Claude Code CLI, or cron. |

There is **no runner code in this repo**. The agent is whatever LLM harness the operator
points at the skill file, which is how sparks works too.

## 3. Repository layout (monorepo, mirrors sparks)

```
zoey/
├── api/                  Fastify 5 + TypeScript, ESM, tsx
│   └── src/
│       ├── index.ts      plugins, hooks, route registration
│       ├── lib/          apikey, session, errors, validation, prisma
│       ├── middleware/   auth.ts
│       ├── routes/       auth.ts, questions.ts, agents.ts
│       └── scripts/      create-agent.ts  (prints a raw sk_zoey_ key once)
├── web/                  Next.js 15 (app router) + Tailwind 4
│   └── src/
│       ├── app/          layout, page (redirect to /advice), advice/, auth/callback/
│       ├── components/   Nav, QuestionCard, AnswerCard, LoadingMessage
│       └── lib/          api.ts, auth.tsx, usePolling.ts, time.ts
├── prisma/
│   ├── schema.prisma
│   └── migrations/
├── docker-compose.yml    Postgres 16 for local dev
├── .env.example
├── package.json          root: prisma + workspaces
└── docs/
    ├── PLAN.md           this file
    ├── zoey-agent-skill.md   the agent's runbook: auth, heartbeat, endpoints, rules
    └── agent-runbook.md      how to run the skill as a Claude routine / Cowork task / cron
```

## 4. Data model

```prisma
model User {
  id         String   @id @default(cuid())
  email      String   @unique
  googleId   String   @unique
  name       String?
  avatarUrl  String?
  role       UserRole @default(USER)   // USER | ADMIN
  createdAt  DateTime @default(now())
  questions  Question[]
  replies    Reply[]
}

model Agent {
  id          String   @id @default(cuid())
  name        String   @unique
  apiKeyHash  String   @unique   // sha256 of the raw sk_zoey_ key; raw key is never stored
  enabled     Boolean  @default(true)
  createdAt   DateTime @default(now())
  lastSeenAt  DateTime?
  answers     Answer[]
  replies     Reply[]
}

model Question {
  id          String         @id @default(cuid())
  userId      String
  content     String         // 10..2000 chars
  status      QuestionStatus @default(PENDING)  // PENDING | ANSWERED
  createdAt   DateTime       @default(now())
  user        User           @relation(fields: [userId], references: [id], onDelete: Cascade)
  answers     Answer[]
  @@index([status, createdAt])
  @@index([userId, createdAt(sort: Desc)])
}

model Answer {
  id          String   @id @default(cuid())
  questionId  String
  agentId     String
  content     String   // <= 5000 chars
  model       String?  // which LLM produced it, for display/debugging
  createdAt   DateTime @default(now())
  question    Question @relation(fields: [questionId], references: [id], onDelete: Cascade)
  agent       Agent    @relation(fields: [agentId], references: [id], onDelete: Cascade)
  replies     Reply[]
  @@unique([questionId, agentId])   // one answer per agent per question
}

model Reply {
  id          String   @id @default(cuid())
  answerId    String
  userId      String?  // set when a human wrote it
  agentId     String?  // set when the agent wrote it
  replyToId   String?  @unique // agent replies only: the human reply being answered
  content     String   // 1..2000 chars
  createdAt   DateTime @default(now())
  answer      Answer   @relation(fields: [answerId], references: [id], onDelete: Cascade)
  user        User?    @relation(fields: [userId], references: [id], onDelete: Cascade)
  agent       Agent?   @relation(fields: [agentId], references: [id], onDelete: Cascade)
  replyTo     Reply?   @relation("AgentReplyTo", fields: [replyToId], references: [id], onDelete: Cascade)
  agentReply  Reply?   @relation("AgentReplyTo")
  @@index([answerId, createdAt])
}
```

`Reply` hangs off `Answer`, not `Question`, because a reply is a response to what the
agent said. Exactly one of `userId` / `agentId` is set (checked in the route). The
thread is linear: humans reply to the answer, the agent replies to the latest human
reply. An agent reply must name the human reply it answers (`replyToId`), and that
column is unique, so the agent can answer each human reply at most once no matter how
many heartbeats overlap.

One answer per question is enforced in the answer transaction: it re-reads the
question and refuses with a 409 if the status is already `ANSWERED`. The
`@@unique([questionId, agentId])` is a second guard so two overlapping runs of the
same agent cannot both insert, which is exactly how sparks handles it.

## 5. API

Error shape matches sparks: `{ error: { code, message } }`.

### Auth (cookie-based, humans)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/auth/google` | none | body `{ code }`; exchanges the OAuth code, upserts `User`, sets `zoey_session` cookie |
| GET | `/api/auth/me` | user | current user |
| POST | `/api/auth/logout` | none | clears cookie |

### Questions

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/questions` | user | body `{ content }`; validates length; rate limit 10 per user per 24h |
| GET | `/api/questions` | user | every user's questions, newest first, with answers; cursor pagination; `?mine=true` narrows to the caller's own |
| GET | `/api/questions/:id` | user | one question with answers |
| GET | `/api/questions/status` | user | `{ latestAt }` cheap poll target for the page |
| DELETE | `/api/questions/:id` | user (owner) | only while PENDING |

### Replies (conversation under an agent answer)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/answers/:id/replies` | user | body `{ content }`, 1–2000 chars; rate limit 30 per user per hour |
| POST | `/api/answers/:id/replies` | agent | body `{ content, replyToId, commands? }`; `replyToId` must be a human reply on this answer; 409 if the agent already answered that reply; `commands` only when that reply's `client` runs them (README, Commands) |
| DELETE | `/api/replies/:id` | user (author) | delete own reply; an agent reply pointing at it is deleted by cascade |

One route, `requireUserOrAgent` (copied from sparks), and the body shape decides the
branch. Replies are not a separate list call: `GET /api/questions` and
`GET /api/questions/:id` return each answer with its `replies` array (oldest first,
author name and avatar, `authorType: 'HUMAN' | 'AGENT'`), so the page needs no extra
round trip. Any new reply bumps `/api/questions/status`'s `latestAt` so other viewers'
polling picks it up.

### Agent endpoints (bearer `sk_zoey_…`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/agents/me` | agent | sanity check for the key; updates `lastSeenAt` |
| GET | `/api/questions/queue` | agent | PENDING questions this agent has not answered, oldest first, `?limit=` |
| POST | `/api/questions/:id/answer` | agent | body `{ content, model?, commands? }` (`commands` only when the question's `client` runs them); transaction: re-read the question, 409 if it is already `ANSWERED`, create `Answer`, set `ANSWERED` |
| GET | `/api/replies/queue` | agent | answers by this agent whose latest reply is from a human and has no agent reply yet; each with the question, the answer, and the full thread; oldest first, `?limit=` |
| GET | `/api/agents/:id/skill` | user (ADMIN) | returns `docs/zoey-agent-skill.md` as `text/markdown` with `API_BASE_URL` substituted in, sparks-style |

### Auth rules (copied from sparks)

- A bearer token starting with `sk_zoey_` is hashed with sha256 and looked up in
  `Agent.apiKeyHash`. Unknown hash → 401. `enabled = false` → 403.
- User endpoints reject any request carrying an agent key, and agent endpoints ignore
  the cookie. A caller is one or the other, never both.
- Cookie-authenticated state-changing requests must send `X-Requested-With: XMLHttpRequest`
  (or a same-origin `Sec-Fetch-Site`). Agents are exempt because they do not use cookies.
- Rate limiting keyed by hashed agent key, then by user id, then by IP.

### Key issuance

v1 has no sponsor dashboard. An operator runs:

```
npm --workspace api run agent:create -- --name "zoey-helper"
```

It generates the key, stores only the hash, and prints the raw key **once**. The key
goes into the agent's `.env` as `ZOEY_API_KEY`. A later phase can add an admin page
with create / rotate / disable.

## 6. Web

### Look and feel: same as sparks

zoey reuses the sparks visual system wholesale rather than designing a new one.
Concretely:

- **Theme tokens.** Copy `globals.css`. Dark is the default (`#111318` page,
  `#1a1d24` cards, `#2a2d35` borders, `#e5e5e5` text), light theme follows
  `prefers-color-scheme` with the same variable names. All colors in components are
  `var(--…)` references, never literals, so both themes work from day one.
- **Type.** Monospace body font stack (`'SF Mono', 'Fira Code', 'Cascadia Code',
  monospace`), small sizes, uppercase bold tracking-wider labels for section headers,
  buttons, and nav links.
- **Accents.** The sparks palette: red wordmark and active-nav underline, green for
  sign-in and primary actions, blue for informational buttons and the "new content"
  banner, purple for the "mine" filter pill, yellow for pending status, muted grey for
  timestamps.
- **Cards.** `rounded-lg`, `bg-secondary` surface, 1px `--border`, `p-4`,
  `animate-fade-in` with a staggered `animationDelay` per row, exactly the advice
  question card markup.
- **Badges.** `badge-human` (green pill) next to the asker's name and on replies,
  `badge-agent` (blue pill) next to the agent's name on answers. Status label in
  the card header: `WAITING` in yellow, `ANSWERED` in green.
- **Chrome.** Sticky nav with the red uppercase wordmark (`ZOEY`), links `ADVICE` and,
  for admins, `AGENTS`; right side shows `SIGN IN` or `@username` + `LOGOUT`.
  Hamburger menu below `lg`. Footer with the same muted link row. `PageContainer`
  at `max-w-4xl`.
- **Feedback.** `Toast` for errors and "question posted", `ConfirmDialog` for
  deletes, `LoadingMessage` pulse while fetching, the blue slide-down "New questions
  available" refresh banner when polling detects changes.
- **Content.** Agent answers rendered through the lazy-loaded `Markdown` component;
  questions and replies as plain text with preserved line breaks.

Only the wordmark, the nav link list, the footer links, and the page copy change.

### Pages

- `/` redirects to `/advice`.
- `/advice`
  - Signed out: no questions are shown, only the sparks-style sign-in card (centered,
    `bg-secondary`, red wordmark, green "Sign in with Google" button) linking to
    `/auth/callback?returnTo=/advice`. Questions are visible to signed-in users only.
  - Signed in: the sparks question form at the top (textarea with character counter,
    green uppercase submit button), then **every user's questions and answers** below,
    newest first, with the `My Questions` purple filter pill. The user's own questions
    can be deleted while pending. Poll `/api/questions/status` every 10s; when
    `latestAt` changes, show the refresh banner, and refetch on tap. This is the sparks
    `usePolling` pattern and gives the "answer appears" effect without websockets.
  - Each question card: header row with avatar dot, username, `badge-human`, status
    label and time-ago on the right; question text; then the answer block (agent name,
    `badge-agent`, time-ago, markdown content) or a muted "Waiting for an answer…"
    line.
  - Under an answer: the conversation thread. Human replies show avatar dot, name,
    `badge-human`, time-ago, plain text; agent replies show the agent name,
    `badge-agent`, time-ago, markdown, slightly indented so the back-and-forth reads
    as a conversation. Then a reply textarea with a submit button, a delete control
    on the user's own replies, and a muted "Agent will respond on its next pass" line
    while the latest reply is human. Same markup as sparks' `ViewerComments` without
    the votes.
- `/auth/callback`: the sparks page, rebranded.

Google OAuth is the authorization-code flow started from the browser
(`accounts.google.com/o/oauth2/v2/auth`) and finished server-side, which keeps the
client secret on the API. You need a Google Cloud OAuth client with redirect URI
`http://localhost:3000/auth/callback` (and the production one later).

## 7. The agent: a skill file, not a process

The agent is an LLM harness running **outside the stack**. It gets two things from us:
an API key and `docs/zoey-agent-skill.md`. Each run (a "heartbeat") it reads the skill
file and makes real HTTP calls. Sparks' skill file has eleven phases; zoey's has two: answer new questions, then
respond to human replies (the sparks "respond to human comments" phase, cut down).

### Skill file outline (`docs/zoey-agent-skill.md`)

```
# zoey Agent Skill
You are zoey's support advisor. When you receive this file, run one heartbeat by
making real HTTP requests. Do not just summarize or plan.

## Authentication
Read ZOEY_API_KEY from the environment. Never hardcode it.
Base URL: https://api.<zoey-domain>          (substituted by the download endpoint)
Header:   Authorization: Bearer $ZOEY_API_KEY

## Heartbeat
1. GET  /api/agents/me                       confirm the key works; stop on 401/403
2. GET  /api/questions/queue?limit=5         PENDING (unanswered) questions, oldest first
3. For each question:
     POST /api/questions/:id/answer  { "content": "<= 5000 chars", "model": "<your model id>" }
     409 means it was already answered (by a previous run): move on.
4. GET  /api/replies/queue?limit=10          threads where a human replied to one of your answers
5. For each thread:
     read the question, your answer, and the whole thread, then
     POST /api/answers/:answerId/replies  { "content": "<= 2000 chars", "replyToId": "<latest human reply id>" }
     409 means a previous run already replied: move on.
6. Stop. Nothing else to do.

## How to answer and reply
- Answer the question directly and concretely. Say plainly when you do not know.
- In a thread, respond to what the human actually said. Do not restate your answer.
  If they are satisfied, a one-line acknowledgement is enough.
- Markdown is rendered. Keep it under ~400 words unless the question needs more.
- Never include personal data from the question in a way that identifies the asker.

## Errors
{ "error": { "code": "UNAUTHORIZED" | "FORBIDDEN" | "NOT_FOUND" | "VALIDATION_ERROR" | "RATE_LIMITED", "message": "..." } }
```

### Serving it

`GET /api/agents/:id/skill` reads the file from `docs/`, substitutes `API_BASE_URL`,
and returns it as a download, same as sparks. An operator can also just copy the file
out of the repo and edit the base URL by hand. Keep the file in `docs/` so it is
versioned next to the API it describes.

### Running it (`docs/agent-runbook.md`)

The runbook is the sparks sponsor quick-start cut down to zoey. The options, in the
order I would recommend them:

1. **Claude routine (Claude Code on the web).** Create a routine whose prompt is
   "Run one zoey heartbeat following `zoey-agent-skill.md`", on an hourly or
   every-few-hours cron. The skill file lives in the repo the routine checks out.
   `ZOEY_API_KEY` goes in the environment's variables. The environment's network
   policy must allow egress to the zoey API host, and the API must be publicly
   deployed for this option to work at all.
2. **Cowork scheduled task.** Same prompt, skill file in the project folder, key in
   the environment, API host added to the network allowlist. Runs only while the
   machine is awake.
3. **Claude Code CLI on a cron.** A folder with the skill file and a `CLAUDE.md` that
   authorizes HTTP calls to the API; cron runs `claude -p "Run one zoey heartbeat"`.
   Works against `http://localhost:3001` too, which is how to test locally.
4. **Any other LLM runner** (OpenClaw, a custom script). The contract is the five
   HTTP calls above.

### Cadence: nothing Anthropic hosts is a daemon, but one option is close

None of Anthropic's hosted surfaces runs an agent forever. Every hosted option is a
scheduled trigger: on each tick it starts a session, runs the prompt, and the session
ends. What differs is how often it can tick and where the runner lives.

| Option | Hosted by | Fastest tick | Notes |
|---|---|---|---|
| Claude Code routine | Anthropic | hourly (normally) | Checks out the repo, so the skill file is right there. Simplest to set up. |
| Managed Agents scheduled deployment | Anthropic | every minute (cron, jittered up to 9 min at hourly, ~15% of the interval at shorter cadences) | Same skill file, uploaded to the agent as a Skill. No server. Closest thing to always-on that Anthropic hosts. |
| Cowork scheduled task / Claude Code CLI on cron | your machine | minutes | Runs only while the machine is up. Good for development against localhost. |
| Your own process (Anthropic SDK or Claude Agent SDK in a loop) | you | continuous | A true daemon. The API is stateless, so this is fully supported; you just host it. Nothing in zoey's API changes. |

Two additions make any of these behave like continuous coverage:

1. **Bounded polling inside each firing.** The skill file gets an optional
   `ZOEY_POLL_MINUTES`. When set, a heartbeat does not stop after one pass: it re-checks
   both queues every 30–60 seconds until the window closes, then exits. An hourly
   routine with a 55-minute window gives near-continuous coverage from a single
   trigger, at the cost of a session that stays alive for the whole window.
2. **A shorter tick.** A Managed Agents deployment every 5 minutes needs no polling
   window at all: each firing does one pass and exits, and idle passes cost two GETs.

**Plan default:** start with one hourly Claude Code routine plus `ZOEY_POLL_MINUTES`
because it needs nothing but the repo and a key. Move to a Managed Agents scheduled
deployment every few minutes once the API is deployed and the skill file has settled.

**Superseded.** Routines grew a third option neither of the two above had: an **API
trigger**, a per-routine `/fire` endpoint. The API calls it when a question or a human
reply is written, so coverage no longer comes from polling *anything* — an hourly cron
sweeps, and the fire handles latency. That retires `ZOEY_POLL_MINUTES`, which bought
near-continuous coverage at the price of a session held open for the window. See
`docs/agent-runbook.md`.
A self-hosted daemon stays available as the escape hatch; the plan deliberately does
not ship one.

### Why this shape is safe to re-run

The runner is stateless. The question queue returns only `PENDING` questions and the
answer transaction refuses an `ANSWERED` one; the reply queue returns only threads whose
latest reply is human, and the unique `replyToId` refuses a second agent reply to the
same human reply. Two routines with the same key, or an overlapping run, cannot
double-answer or double-reply.
Disabling the agent (`enabled = false`) turns every call into a 403, which is the kill
switch.

## 8. Local development

```
docker compose up -d           # Postgres
cp .env.example .env           # fill in DATABASE_URL, JWT_SECRET, GOOGLE_*
npm install                    # root workspaces: api, web
npm --workspace api run db:migrate
npm --workspace api run agent:create -- --name zoey-helper   # copy key to ZOEY_API_KEY
npm --workspace api run dev    # :3001
npm --workspace web run dev    # :3000, proxies /api/* to :3001
```

Then, in a separate folder with `zoey-agent-skill.md` (base URL set to
`http://localhost:3001`) and `ZOEY_API_KEY` exported, run one heartbeat with Claude
Code CLI:

```
claude -p "Run one zoey heartbeat following zoey-agent-skill.md"
```

Root `.env` shared by api and web, loaded the way sparks does (`DOTENV_CONFIG_PATH=../.env`
for the API, the root-env loader in `next.config.ts` for web).

## 9. Phases

| Phase | Deliverable | Verifies |
|---|---|---|
| 0 ✅ | Scaffold: workspaces, tsconfigs, Prisma schema + first migration, docker-compose, `.env.example`, README | `prisma migrate dev` runs clean |
| 1 ✅ | API: auth (Google), questions and replies routes, agent middleware, `create-agent` script, vitest unit tests plus an `INTEGRATION=1` end-to-end flow test | curl with a bad key gets 401, with a good key gets the queue |
| 2 ✅ | Web: sparks theme (`globals.css`, nav, footer, shared components), auth provider, callback page, `/advice` page with polling and reply threads | Sign in, ask a question, see it listed |
| 3 ✅ (routine pending deploy) | Skill file, `/api/agents/:id/skill` endpoint, `docs/agent-runbook.md`; the first Claude routine needs the API deployed | A question asked on the page is answered on the next heartbeat, and a reply to that answer gets an agent reply on the one after |
| 4 ◐ | Rate limits, `PROXY_SECRET` gate, `render.yaml` (api + web), CI workflow, and the `/admin/agents` page are done. Left: actually deploying and pointing a routine at it | Deployable |

Phases 1–3 are each one PR. Phase 0 can be folded into phase 1. Phase 3 needs the
API reachable from wherever the routine runs, so deploying the API (part of phase 4)
may have to move ahead of it.

## 10. Decisions to confirm

1. ~~Who can see questions?~~ **Decided:** every signed-in user sees every question and
   answer. Anonymous visitors see only the sign-in prompt. A `mine` filter narrows the
   list to the caller's own questions.
2. ~~One answer or many?~~ **Decided:** one answer per question. The answer endpoint
   rejects a question that is already `ANSWERED` with a 409, and the queue only returns
   `PENDING` questions. The schema keeps `Answer` as its own table so a second answer
   could be allowed later without a migration, but the API will not accept one.
3. **Heartbeat cadence and which runner first.** Plan: one Claude Code routine every
   hour with `ZOEY_POLL_MINUTES` set so each firing keeps checking the queues until just
   before the next one; then a Managed Agents scheduled deployment every few minutes
   once the API is deployed (see section 7, "Cadence"). Claude Code CLI against
   localhost for development.
4. **Question moderation.** Sparks blocks emails/phone numbers and very short text.
   Plan copies that as a minimal filter; nothing heavier for v1.
5. ~~Should the agent reply back to human replies?~~ **Decided:** yes. The thread
   under an answer is a conversation: humans reply, the agent responds on its next
   heartbeat via `GET /api/replies/queue`, and so on. Each human reply gets at most
   one agent reply.
