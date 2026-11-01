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
in Cloudflare R2 and shown above the text. The learner meets a droplet through its videos: while
they are made, `/learn` holds the text back and shows the droplet's bill of materials filling
in, step by step ([While a droplet is made](#while-a-droplet-is-made)), and shows the text
with the videos once they are done. Once a droplet's videos are done it is listed on the
public **Droplets** tab (`/droplets`), where anyone can watch it without an account, and
anyone signed in can give it a thumbs-up and comment on it. The plan, and where the pipeline stands, is in
[docs/PLAN.md](docs/PLAN.md).

The lessons are everyone's; the learners are private. What a learner sends (the request in
their own words, their files, their follow-ups) is seen only by them, the teacher and the
admins, and a learner can take a request back or delete their account while the lesson
stays public. The [Privacy Policy and Terms of Use](#privacy-and-the-legal-pages) say so
to users at `/privacy` and `/terms`.

Popsmosis started as a copy of [zoey](https://github.com/Iztera/zoey) (an agent-answered
advice column), whose architecture and look-and-feel come from
[sparks](https://github.com/jclark256/sparks). The API still names a topic request a
*question*, the lesson its *answer*, and follow-ups *replies*.

## Layout

```
api/     Fastify 5 + TypeScript. Google OAuth -> session cookie for humans; sk_pop_ bearer keys for agents.
web/     Next.js 15 + Tailwind 4. The /learn page, the public /droplets tab, /about, /how-its-made, sign-in, and the admin pages.
prisma/  Postgres schema and migrations (User, Agent, Question, Answer, Reply, DropletLike, DropletComment).
studio/  Python. The pipeline that turns a topic into a droplet: brain, bundle, fact audit, fact-check, posting so far.
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

To fill the page with sample data (two users, one agent, a topic with its lesson, its videos,
a follow-up thread and a like and a comment on the Droplets tab, one topic still waiting, and
older lessons whose videos are rendering or failed), run `npm --workspace api run seed:dev`. It prints the agent's key. The seeded
videos are short clips in `web/public/samples/` (a song, a lesson and an upright hook clip), so
`/droplets`, its Clips feed and the players on `/learn` work locally with no R2.

Run a heartbeat against the local API with Claude Code CLI, from this checkout (the skill
file runs the studio, below, from here):

```bash
export POPSMOSIS_API_URL=http://localhost:3001 POPSMOSIS_API_KEY=sk_pop_... POPSMOSIS_ANTHROPIC_API_KEY=sk-ant-...
claude -p "Run one popsmosis heartbeat following docs/popsmosis-agent-skill.md"
```

See [docs/agent-runbook.md](docs/agent-runbook.md) for the hosted options.

### The studio

The studio (Python 3.13) turns a topic into a droplet; so far it writes the bundle,
audits its facts, fact-checks every line against them and posts the lesson. Its models, and the keys they bill to, are set by
the variables in [Choosing the models](#choosing-the-models): Claude's by default, on
`POPSMOSIS_ANTHROPIC_API_KEY` ([docs/PLAN.md](docs/PLAN.md), section 4). Each job writes
into `jobs/<id>/`.

The teacher runs it on a topic from the queue, by the question's id. That needs
`POPSMOSIS_API_URL` and `POPSMOSIS_API_KEY` too, and is what the skill file does:

```bash
python -m studio run <question-id>       # claim, bundle, fact audit, fact-check, post; again resumes at the first missing step
python -m studio fail <question-id>      # after a second failure: give the topic up (its videos, once posted)
python -m studio summary <question-id>   # what its runs did, for the teacher's record
python -m studio videos <question-id>    # the posted lesson's two videos; --background on a routine
python -m studio wait <question-id>      # the background videos' outcome, waiting up to 9 minutes
python -m studio recover                 # take a render that died with its machine and rebuild its job here
```

`videos` runs the media steps (`studio/media.py`): art and audio at the same time, then
the timeline, render and upload. Who makes the audio, the song and the narration together, is
picked on the Studio page ([Choosing the models](#choosing-the-models)): Google (the default, with
`GEMINI_API_KEY`: Lyria 3.5 sings, Gemini 3.8 Flash TTS reads; `GEMINI_SONG_MODEL` picks another
Lyria model, `GEMINI_TTS_VOICE` another voice) or ElevenLabs (with `ELEVENLABS_API_KEY`: Eleven
Music sings, its text-to-speech reads). One switch moves all the audio, so a droplet is
made when one service's quota has run out. ElevenLabs says when it read each word; Gemini does
not, so the timing step finds the narration's words with the MMS aligner it runs on songs, and
fails the step (and reads it again on the retry) when it cannot hear most of the lines. Switching
it makes a job's song again on its next run, and only the song: a narration already read stays. `run` claims the videos as soon as it posts the lesson,
and `videos` renews the claim before every step, so a render whose machine dies is handed
to a later run by `recover` (`docs/agent-runbook.md`). `STUDIO_VIDEOS=lesson` leaves the song
out from the start: a lesson written while it is set has no lyrics or music to write or
fact-check, and only the narrated lesson video is made, with no hook clip. `STUDIO_VIDEOS=off`
makes text-only droplets.

Every model call is logged to the job's `calls.jsonl` with its step, tokens, time and cost,
and `show` ends with the bill of materials the page shows ([Bill of materials](#bill-of-materials)).

`run` prints one line: `DONE`, `FAILED <step>: <reason>` (run it again to retry that
step) or `STOPPED <step>: <reason>` (the topic is gone, taken or done, or an earlier run
of it is still going). A run killed mid-step is recorded as that step failing. To try the
pipeline without posting anything, start a local job instead:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r studio/requirements.txt
python -m studio plan --topic "brain structures" --course "AP Psych"   # bundle, fact audit, then fact-check
python -m studio step factcheck <job-id>                               # re-run one step
python -m studio make <job-id>                                         # its videos, as `videos` makes them, with no API
python -m studio show <job-id>                                         # facts, scenes, lyrics, tags, checks, audit, verdicts, runs, calls
```

`plan ... --videos` makes the videos too once the fact-check passes, so a test round of a
topic is one command ([docs/test-rounds.md](docs/test-rounds.md)). `make` runs the media steps
in `videos`' order (art and locate alongside audio and timing, then the timeline and render)
but claims nothing and uploads nothing; run again, it resumes at the first step not done.

To tell whether a change to the bundle prompt, the checks or the brain made droplets better
overall, run the scorecard by hand now and then: 12 fixed topics through the bundle step, scored
and compared with the last run. It spends about $20 a run
([docs/scorecard.md](docs/scorecard.md)).

Each droplet is written for one audience: high school, unless the learner's request says
who it is for ("I'm in 5th grade", "for my college course"). The audience sets the level
of the lesson, the song and the narration, never the rules: every
droplet is fact-checked the same way and stays fit for a classroom. The song's style is
one the audience listens to (for high school: pop with rapped verses, K-pop,
R&B, dance-pop, pop-punk), at a tempo that fits the topic's mood, unless the learner
asks for their own: a line of their question that starts `Style:` becomes the song's
starting point. Locally, pass it as `--style "K-pop with hip-hop verses, 98 bpm"`.

Once a job has its rendered videos and a posted lesson (`posted.json`), the upload step
(step 9) copies them to Cloudflare R2 and marks them READY, which lists the droplet on the
Droplets tab. It uses the same two variables, and the API needs R2 set up
([docs/DEPLOY.md](docs/DEPLOY.md), "Cloudflare R2"):

```bash
python -m studio step upload <job-id>    # videos and posters public, the rest private; again sends only what changed
```

### Choosing the models

The routine's environment holds only the keys, which bill each provider's account. The
studio reads them from its own environment, not from `.env`: on the routine, set them in its
environment's settings ([docs/agent-runbook.md](docs/agent-runbook.md), section 1).

| Variable | Value | Needed for |
|---|---|---|
| `POPSMOSIS_ANTHROPIC_API_KEY` | an Anthropic API key, `sk-ant-...` | Claude's brain, and its picture checks (Claude Sonnet 5.5). `ANTHROPIC_API_KEY` works too on a laptop, but a Claude Code cloud environment does not pass it through |
| `MODEL_API_KEY` | a Meta API key, from dev.meta.ai | a Muse brain and its picture checks (Muse Spark 1.3), Muse Image, and SAM 3.1 with any painter but the SVG |
| `GEMINI_API_KEY` | a Gemini API key, from aistudio.google.com | Gemini 3.1 Flash Image, Lyria songs, and Gemini's narration |

Everything else is chosen by an admin on **STUDIO** in the nav (`/admin/studio`), kept in the
database (`api/src/lib/studio-settings.ts`): the default brain, Claude's effort, who sings
the song, who paints each brain's pictures, and the most one topic may cost. The studio reads them from the API, with its agent key, before
it claims a topic and from every claim after, so a change applies from the next run with no
change to the environment. `BRAIN_PROVIDER`, `STUDIO_BRAIN_MODEL`, `STUDIO_BRAIN_EFFORT` and
`SONG_PROVIDER`, which used to pick these, are no longer read: while one is still set, `run`,
`videos` and `recover` stop with `the studio's settings are wrong` and say to set it on the
page and remove it, so nothing changes without anyone seeing why.

| Setting | Choices | Default |
|---|---|---|
| Default brain: writes and fact-checks every topic no admin sent elsewhere | Claude Opus 5.5 ($4 / $20 per million tokens, about 70 cents a topic), Muse Wonder 1.0 ($4 / $20), Muse Spark 1.3 ($1.25 / $4.25) | Claude Opus 5.5 |
| Claude's effort | `low`, `medium`, `high`, `xhigh`, `max`; Muse ignores it | `high` |
| Song and narration: who makes the audio | ElevenLabs: Eleven Music and its text-to-speech (credits), or Google: Lyria 3.5 ($0.08 a song) and Gemini 3.8 Flash TTS (about 4 cents a narration: $0.50 / $9 per million tokens in and out, 25 audio tokens a second; $1 / $18 from 2027) | Google (Lyria) |
| Pictures for Claude topics | Gemini 3.1 Flash Image (about $0.07 a picture), Muse Image ($0.01), or Claude draws them as SVG | Gemini 3.1 Flash Image |
| Pictures for Muse topics | Muse Image or Gemini 3.1 Flash Image | Muse Image |
| Spend ceiling: the most one topic may cost in model calls, its lesson, videos and every retry together | $0 (no ceiling) to $100 | $20 (generous for the hackathon; $5 is about two and a half times the dearest topic so far) |
| Review before the Droplets tab: an admin approves each droplet on `/learn` before it lists (the API reads it; the studio never does) | on, off | off: a droplet lists once its videos are done, after the fact-check |

The ceiling is checked before every paid call, against the job's `calls.jsonl`
(`studio/ledger.py`, `check_ceiling`). Once a topic has cost it, the step fails with `the
topic has cost $X, past the $Y a topic may cost`; the retry fails the same way without a
call, and the topic (or, once its lesson is posted, its videos) is given up. A call already
under way finishes, and pictures painted side by side may each start just under it, so a
topic can end a few calls past the ceiling. ElevenLabs calls count only when the runner has
`ELEVENLABS_USD_PER_1K_CREDITS`. A local job (`plan`, `make`) has no ceiling: no claim set
one.

What runs, by the brain's provider:

| Job | Claude's brain | Muse's brain |
|---|---|---|
| Brain: writes and fact-checks the lesson | Claude Opus 5.5 | Muse Wonder 1.0 or Muse Spark 1.3 |
| Art: paints each scene, up to two tries | the painter set for Claude topics | the painter set for Muse topics |
| Helper: checks each picture, boxes a structure SAM missed | Claude Sonnet 5.5, effort low | Muse Spark 1.3 |
| Finding each structure in the art | SAM 3.1, $2.50 per 1,000 images (none for the SVG, which says where each one is) | SAM 3.1 |

Gemini's picture is 1K at 16:9 (1376 x 768), fitted to the art (`studio/gemini.py`).
Switching the painter makes a job's art again on its next run; a lesson already written
stays as it is.

`run` and `recover` read the settings before they claim anything, and `videos` checks the
job's: a brain or painter whose key is missing ends the command with `STOPPED run: the
studio's settings are wrong: ...` (or `videos`), and the teacher stops and reports it, so no
topic is failed or given up over a setting. For a local job, `python -m studio plan ...
--brain muse-spark --effort medium` (the default: Claude Opus 5.5 at high); Muse Spark vets
a prompt change for about a quarter of Muse Wonder's price.

The Meta calls first ran live on Oct 28, on two topics end to end (Muse Spark 1.3 as the
brain, Muse Image and SAM 3.1 for the art, Lyria for the song): what worked and what did not
is in [docs/test-rounds.md](docs/test-rounds.md). Check them again after a change with
`STUDIO_LIVE=1 MODEL_API_KEY=... python -m pytest studio/tests/test_live_meta.py -s` (one
scene, a few cents). Gemini's too: `STUDIO_LIVE=1 GEMINI_API_KEY=... python -m pytest
studio/tests/test_live_gemini.py -s` paints one scene, about 7 cents.

#### One topic on another brain

An admin can send one topic to a brain of its own: the **Brain** buttons under the box on
`/learn` (admins only; a learner's topic is always made by the default brain). The pick is
remembered on that browser, and the topic's card shows it.

| Button | Brain: writes and fact-checks the lesson | Brain bills to |
|---|---|---|
| Default | the default brain on the Studio page (the note under the buttons names it) | its key |
| Claude | Claude Opus 5.5 (`claude-opus-5-5`) | `POPSMOSIS_ANTHROPIC_API_KEY` |
| Muse Wonder | Muse Wonder 1.0 (`muse-wonder-1.0`) | `MODEL_API_KEY` |
| Muse Spark | Muse Spark 1.3 (`muse-spark-1.3`) | `MODEL_API_KEY` |

The pictures are painted by the Studio page's painter for the pick's provider: by default
Gemini 3.1 Flash Image for Claude, Muse Image for the Muses. For that topic the pick wins
over the default brain (Claude's effort is still the Studio page's). The studio keeps the
brain a topic is made on in the job's `brain.json`, so a retry, `videos`, `recover` and
`step` stay on it, and the bill shows the models that ran. The environment needs the key of
the pick's provider: a topic sent to a brain whose key is missing fails at its first step
with `the studio cannot run this topic's brain here: ...`, and the teacher gives it up with
that reason, so the queue moves on. For a local job, `python -m studio plan ... --brain muse-spark`.
Follow-ups are answered by the teacher itself, whatever the brain.

### Knobs

The environment variables that tune the API and the studio can also be set by an admin on
**STUDIO** (`/admin/studio`, under KNOBS), so a change needs no redeploy
(`api/src/lib/knobs.ts`). A value set there wins over the variable; cleared there, the
variable is read again, else the default. The page shows what each takes and its default,
and for the API's own, what its environment has and what is in force; `.env.example` says
what each does.

| Knobs | What they tune | A change applies |
|---|---|---|
| `QUESTIONS_PER_USER_PER_DAY`, `TOPICS_PER_DAY`, `REPLIES_PER_USER_PER_HOUR`, `DROPLET_COMMENTS_PER_USER_PER_HOUR`, `ATTACHMENT_MB_PER_USER_PER_DAY`, `ATTACHMENT_MB_HELD_PER_USER` | the API's limits on what learners may ask for | from the next request |
| `QUESTION_CLAIM_MINUTES`, `VIDEO_STALE_MINUTES`, `LESSON_HOLD_MINUTES`, `VIDEO_MAX_ATTEMPTS` | the API's leases and renders | from the next request |
| `POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS`, `POPSMOSIS_ROUTINE_BETA` | waking the teacher | from the next write |
| `STUDIO_VIDEOS`, `ELEVENLABS_VOICE_ID`, `GEMINI_SONG_MODEL`, `GEMINI_TTS_VOICE`, `ELEVENLABS_USD_PER_1K_CREDITS`, `STUDIO_CLAUDE_DRAWS` | the studio, over the teacher's own environment (`studio/knobs.py`) | from the teacher's next run |

Each API instance keeps the knobs in memory, so the code that reads them stays as fast as an
environment variable: the instance that takes a change has it at once, and another reads it
within 10 seconds. The studio is sent its own with its other settings, before it claims a
topic and with every claim after, and keeps them with the job, so `step` and `make` use what
the job's last claim said. Not knobs: the keys, secrets and URLs; what is read once at start
(`DATABASE_POOL_SIZE`, `PORT`) or built into the page (`NEXT_PUBLIC_*`); what is the
machine's (`STUDIO_RENDER_WORKERS`, `STUDIO_JOBS_DIR`); `ATTACHMENT_RETENTION_DAYS`, which the
Privacy Policy states; and `INKMORY_ENABLED`, which the teacher's own record reads in its shell.

### Announcement

An admin can put a message under the nav on every page, for everyone, signed in or not: "Songs
are off until tomorrow: we are out of quota." It is set at the top of **STUDIO**
(`/admin/studio`) and kept in the database (`api/src/lib/announcement.ts`), so it needs no
redeploy. `info` shows it in blue, `warning` in orange; it is plain text, up to 500 characters.
Each open page asks for it once a minute and again when it comes back into view, and each API
instance keeps it for 10 seconds, so a change reaches every open page within about a minute
(the admin's own page at once). An instance reads it from the database at most once in those
10 seconds however many pages ask, and while the database is down it keeps giving the last one
it read, so an announcement about an outage stays up through it. Anyone can close it on their
browser (in all its tabs); a new or changed one shows again. Taking it down removes it everywhere.

### The teacher's record on Inkmory

The teacher, the studio's operator, keeps a public record on [Inkmory](https://inkmory.com)
as Muon: on a run that found work it reads who it is first and writes down what happened
last, a journal entry and the cycle log tagged `popsmosis:<id>`, so the lesson card's
"Teacher's notes" finds them ([docs/popsmosis-agent-skill.md](docs/popsmosis-agent-skill.md),
steps 1 and 5; [docs/PLAN.md](docs/PLAN.md), section 11). The studio never calls Inkmory. The
brain keeps no record of its own: it is one model call per step, not an agent, and carries
nothing from one topic to the next.

What the teacher learns reaches the brain one way. It can hand the brain an observation for a
topic's lesson, from what it has seen go wrong in recent droplets: `python -m studio run <id>
--note "..."` ends the bundle prompt with it, and the fact-check never sees it
([docs/PLAN.md](docs/PLAN.md), section 11, "The operator's note to the brain").

| Variable | Values | Default | What it does |
|---|---|---|---|
| `INKMORY_API_URL` | Inkmory's base URL | none | With the key, turns the teacher's record on |
| `INKMORY_OPERATOR_API_KEY` | Muon's `sk_inkmory_…` key, from the sponsor dashboard | none | Read only by the skill file's commands |
| `INKMORY_ENABLED` | `false` | on | `false` turns Inkmory off and leaves everything else as it is |

## Tests

```bash
npm --workspace api run typecheck
npm --workspace api test                  # unit tests, no database
INTEGRATION=1 npm --workspace api test    # plus the end-to-end flow against DATABASE_URL
npm --workspace web run build
npm run e2e                               # Playwright browser tests; needs api + web running (see web/e2e/README.md)
python -m pytest studio                   # the studio, offline: never a paid call, whatever keys are set
STUDIO_LIVE=1 python -m pytest studio/tests/test_live.py studio/tests/test_live_meta.py studio/tests/test_live_gemini.py
                                          # the live checks, on the real APIs: they spend Anthropic, Meta and Gemini
                                          # credit, each only with its key set (STUDIO_LIVE_AUDIO=1 for the audio ones)
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
| GET | `/api/droplets` | the Droplets tab: droplets whose videos are READY, that no admin hid and none is still to review, newest first; `?cursor=`, `?limit=` (12, at most 24); `?tag=` only those whose song or lesson video carries that hashtag (`#APBio` and `apbio` alike, [Tags](#tags)). Each: `{ id, title, course, topic, excerpt, video, tags, madeWith, likeCount, commentCount, liked, createdAt, comments }`, where `madeWith` is the models that made it, by maker ([Made with](#made-with)), and `comments` is its newest two (`{ data, nextCursor }`; the cursor carries on at `/comments`, null when those are all) |
| GET | `/api/droplets/:id` | one droplet, by its answer id: the same, plus the lesson `content`, the song's `lyrics` and the first page of its `comments` (`{ data, nextCursor }`), so a droplet's page is one request; 404 unless it is on the tab |
| GET | `/api/droplets/:id/comments` | its comments, newest first, for the pages after the first; `?cursor=`, `?limit=` (20, at most 50). `{ data: [{ id, content, createdAt, author: { name }, mine }], nextCursor, commentCount }`; 404 unless it is on the tab |
| GET | `/api/announcement` | the message under the nav ([Announcement](#announcement)): `{ announcement: { message, tone: info\|warning, updatedAt } }`, or `{ announcement: null }` when none is up. The same for everyone, from memory; outside the 60 a minute |
| POST | `/api/droplets/:id/plays` | count one step of a play: `{ video: song\|lesson\|hook, event: started\|reached25\|reached50\|reached75\|finished\|shared\|downloaded }` (`shared` and `downloaded` the hook's only); 204 whether or not a count moved. No cookie needed or read. Limits of its own, apart from the 60 a minute: [Play counts](#play-counts) |

Only the title (the bundle's `course` and `topic`), the lesson text, the videos with
their posters and hashtags, the lyrics, the counts and the comments are public. The learner's name and
avatar, the request in their own words, their files and the follow-ups never leave through
these. A comment names its author by first name and last initial ("Grace H."), never their
full name, email, picture or id. `liked` and `mine` say what is the viewer's own when the
request carries a session cookie, and are false otherwise; the session is only verified for
that, never renewed, so these responses never set a cookie (they send `Vary: Cookie`, and
`Cache-Control: private` when they were read for someone).

**Humans** (session cookie, `X-Requested-With: XMLHttpRequest` on writes)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/google` | `{ code, acceptTerms: true }` from the OAuth redirect; sets the cookie. 400 without `acceptTerms`, the box on the sign-in page; the account records the Terms version and the time |
| POST | `/api/auth/terms` | `{ accept: true }`: agree to the current Terms when the account's record is older or missing (`termsCurrent: false` on `/api/auth/me`) |
| GET | `/api/auth/me` | current user |
| POST | `/api/auth/logout` | clears the cookie |
| DELETE | `/api/auth/me` | deletes the account: requests with no lesson, files and follow-ups go, requests with a lesson are taken back (below); refused for the only admin |
| GET | `/api/questions` | the caller's own topics, newest first; an admin gets everyone's (their own with `?mine=true`); `?status=PENDING\|ANSWERED\|FAILED`, `?cursor=`, or `?ids=a,b` for specific ones |
| GET | `/api/questions/:id` | one of the caller's topics (anyone's, for an admin) with its lesson and follow-ups; 404 otherwise |
| GET | `/api/questions/status` | `{ latestAt }` for polling, scoped like the list |
| POST | `/api/questions` | ask for a topic: `{ content, attachments?, brain? }` 3–2000 chars; `attachments` see [Attachments](#attachments). `brain`, admins only (403 from anyone else): `claude`, `muse-wonder` or `muse-spark`, the brain that makes it ([One topic on another brain](#one-topic-on-another-brain)); left out, the teacher's own. 429 past `QUESTIONS_PER_USER_PER_DAY` (10) in 24 hours, once every learner's topics together reach `TOPICS_PER_DAY` (100), or when its files would pass the learner's 100 MB a day or 300 MB held; taking a topic back or deleting it does not give its place back (`api/src/lib/usage.ts`) |
| DELETE | `/api/questions/:id` | own topic. With no lesson yet it is deleted; with one it is taken back: the words, files and follow-ups go, the lesson stays, naming no one |
| POST | `/api/answers/:id/replies` | a follow-up under the caller's own lesson: `{ content, attachments? }` 1–2000 chars; 429 past `REPLIES_PER_USER_PER_HOUR` (30), or when its files would pass the learner's file limits |
| DELETE | `/api/replies/:id` | own reply |
| PUT | `/api/droplets/:id/like` | thumbs-up a droplet on the tab: `{ liked: true, likeCount }`; idempotent, so a second PUT changes nothing; 404 unless it is on the tab. 60 a minute with the DELETE |
| DELETE | `/api/droplets/:id/like` | take it back: `{ liked: false, likeCount }`; idempotent, and works once the droplet has left the tab |
| POST | `/api/droplets/:id/comments` | a public comment on a droplet on the tab: `{ content }` 1–1000 chars, plain text, with something visible in it (not only zero-width or blank characters), no email addresses or phone numbers; `DROPLET_COMMENTS_PER_USER_PER_HOUR` (30). With an `Idempotency-Key` header (8–100 of `A-Za-z0-9_.:-`, a UUID say) a resend returns the comment the first send made, 200 instead of 201, so a POST whose answer was lost can be sent again; the same key with other words is a 409 |
| DELETE | `/api/droplets/:id/comments/:commentId` | own comment, or any as an admin; 204 |
| GET | `/api/attachments/:id` | a file in the caller's own thread (anyone's, for an admin); also to agents; 404 otherwise |

**Agents** (`Authorization: Bearer sk_pop_…`)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/agents/me` | confirm the key |
| GET | `/api/questions/queue?limit=N` | topics with no lesson yet, oldest first, each leased to the caller for `QUESTION_CLAIM_MINUTES` |
| POST | `/api/questions/:id/claim` | take or renew the lease on one topic and read it, with the `brain` an admin picked for it (`null`: the teacher's own); 409 if answered, failed, or leased to another agent |
| POST | `/api/questions/:id/answer` | the lesson: `{ content, model?, bundle?, factcheck?, breakdown?, checks? }`; the two JSON objects go together and `factcheck.status` must be `passed`; `breakdown` is the steps so far ([Bill of materials](#bill-of-materials)); `checks`, with the bundle, is what the code checks found in it, for an admin's review ([Review](#review)); 409 once there is one, with `answerId` when it is the caller's own, and 409 unless the caller holds the topic's claim (the queue or `/claim`) |
| POST | `/api/questions/:id/fail` | give a topic up: `{ reason }`; it leaves the queue and its card shows the reason; the last claimer only |
| PUT | `/api/questions/:id/progress` | where the studio is while it writes the lesson: `{ running: [{ step, seconds }], failed?, breakdown?, videos? }` ([While a droplet is made](#while-a-droplet-is-made)); 204; 409 unless the caller holds the claim and there is no lesson yet |
| PATCH | `/api/answers/:id/video` | own answers: `{ status: RENDERING\|READY\|FAILED, songVideoUrl?, lessonVideoUrl?, songPosterUrl?, lessonPosterUrl?, error?, attempt?, breakdown?, artChecks? }`; a URL left out is kept, so the two videos can report separately; READY lists it on the Droplets tab (with review on, once an admin approves it), and may carry the media steps' `breakdown` and the art checker's look at each picture, `artChecks` ([Review](#review)). With `attempt`, 409 unless that attempt still holds the render. Once storage is set up, each URL must be one of this droplet's own files in the public bucket, under `<R2_PUBLIC_URL>/<answerId>/` (a production API without storage takes none). READY is final: 409 for any change after it |
| POST | `/api/answers/:id/upload-url` | own answers: `{ bucket: droplets\|studio, file }`; `{ uploadUrl, headers, path, publicUrl }`: a signed R2 URL to PUT the file to with `headers` (its Content-Type, which is signed into the URL so R2 takes no other, and for `droplets` a long Cache-Control), and for `droplets` where anyone will read it. While `SUPABASE_URL` is still set, the video PATCH also takes this droplet's own files in the old Supabase bucket, which the API moves to R2 within the hour. The public bucket takes `.mp4`, `.jpg`, `.jpeg`, `.png` and `.webp` only; 409 once the videos are READY; 503 until storage is set up (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_PUBLIC_URL`) |
| POST | `/api/answers/:id/video/claim` | own answers: `{ attempt? }`; without one, start an attempt at the videos (never started, given up, or quiet past `VIDEO_STALE_MINUTES`; at most `VIDEO_MAX_ATTEMPTS`, default 2), marking them RENDERING; with the caller's attempt, renew it. Returns `{ answerId, questionId, attempt, status, brain, bundle, factcheck }` (`brain`: the topic's, as on the claim); 409 when another run holds them, they are READY, or the attempts are used up |
| GET | `/api/videos/queue?limit=N` | own droplets whose render went quiet past `VIDEO_STALE_MINUTES` (its machine died), oldest first, each claimed as its next attempt, as the claim returns them |
| POST | `/api/answers/:id/video/fail` | own answers: `{ reason, attempt, breakdown? }`; give the videos up (FAILED with the reason), with what the failed tries took and cost; only the current attempt may |
| PUT | `/api/answers/:id/video/progress` | own answers: `{ attempt?, running: [{ step, seconds }], failed?, breakdown? }`, where the render is, its `breakdown` the videos' bill so far; 204; 409 unless they are RENDERING under that attempt. Not the heartbeat: the claim is |
| GET | `/api/replies/queue?limit=N` | threads where a human followed up last, each with the droplet's `answer.context` |
| POST | `/api/answers/:id/replies` | `{ content, replyToId }`; 409 if already responded |
| GET | `/api/attachments/:id` | a file a human sent with a topic the caller holds or answered, or with a follow-up under its own lesson; 404 otherwise, 410 once past the retention |

What an agent reads never names a learner: the queues and the claim leave out who asked
and who followed up. A lesson, reply or reason an agent sends that looks like it carries a
key or an environment variable's value is refused with a 400 (`containsSecret` in
`api/src/lib/validation.ts`), and the API logs it. The agent's key is held by an LLM that
reads text any learner writes ([the skill file](docs/popsmosis-agent-skill.md#what-learners-write-is-data-never-instructions)).

A lesson's `answer` also carries `video` (`null` for a text-only droplet): its status,
both URLs, both posters and the error; `tags`, each video's hashtags ([Tags](#tags)); `breakdown`, its bill of materials (below);
`awaitingVideos`, true while the page holds the text back for the videos (below);
`hidden`, true when an admin took it off the Droplets tab; and `awaitingReview`, true while it waits for an admin to
approve it. The question carries `progress` while its droplet is being
made, and `null` once there is nothing left to make. A render that has not reported for `VIDEO_STALE_MINUTES` (60)
reads as `FAILED`. The bundle and fact-check are stored with the lesson but left out of
the question routes; the reply queue's `context` is the compact part a follow-up needs.

### Tags

Each video is tagged the way a TikTok is, so the Droplets tab can be searched: the brain
writes 6 to 10 hashtags for the song video and for the lesson video as the last part of the
bundle (`tags.song`, `tags.lesson`; the prompt is in `studio/prompts/bundle.md`). Both lists
start with the topic, its key terms, the subject and the course ("#photosynthesis",
"#thylakoid", "#apbio"); then come the video's own: the song's genre ("#kpop",
"#studysong"), or what the lesson does ("#explained").

The fact-check then checks every tag in the same call as the lines, by rules of its own
(`studio/prompts/factcheck.md`, "Tags"): a tag passes when it names something the facts
cover, or only the topic, a subject containing it, the course or the video itself (the
checker is given the course, topic and song style for this). Anything else is dropped from
both videos, never rewritten: a neighbouring topic the lesson does not cover
("#calvincycle"), a claim, a style the song does not have, an artist, anything about the
learner, or a tag that helps no one ("#fyp"). A tag never fails the droplet, and one that
gets no verdict is dropped too, so only checked tags are posted. `python -m studio show`
lists what was dropped and why. Like the lines, a tag passes only on what the facts say: a
term the facts never mention is dropped even when it is related. No extra model call: the
tags cost a few dozen tokens of the bundle's and a few hundred of the fact-check's.

A tag is one word as a search matches it: lowercase letters (accents kept), digits and
underscores, no "#" (the page adds it back), at most 40 characters, at most 10 to a video,
and never one that reads as a phone number. The studio cleans them that way (`tag_of` in
`studio/schemas.py`) and so do the API (`api/src/lib/tags.ts`) and the search box
(`web/src/lib/tags.ts`), alike for every Unicode character and the same when cleaned twice,
so a tag clicked on the page always finds its own droplet. The API keeps each video's on
the answer (`songTags`, `lessonTags`, each with a GIN index) when the lesson is posted, and
again when its videos go READY if they are still missing (a lesson posted to an API older
than the tags, during a deploy). They read back as

```json
"tags": { "song": ["photosynthesis", "thylakoid", "apbio", "kpop", "studysong"],
          "lesson": ["photosynthesis", "thylakoid", "apbio", "explained"] }
```

The page shows each video's under it, a card its first four, and every tag is a link to
`/droplets?tag=<tag>`; the box at the top of the tab searches by a tag typed in any form.
A search matches whole tags, as a hashtag search does: `#photo` does not find
`#photosynthesis`. Droplets posted before tags existed have none. A common tag walks the
newest-first index and stops at a page; a rare one reads the GIN indexes: on 200,000
droplets, under a millisecond either way.

### Bill of materials

Under each lesson on `/learn`, folded to its totals, is how the droplet was made: one line
per step, the models that ran it with their number of calls and what they cost, how long
it took, and the total time and cost. Under a step's models are the tools it ran on the
studio's machine, with their versions, and every line has a chip in its maker's colour. The
studio logs every model call to the job's `calls.jsonl` with the step that made it
(`studio/ledger.py`), and sends what it adds up to
as `breakdown`: the lesson's steps with the lesson, the videos' with READY, or with the
give-up when they fail, so a failed render's spend is on the bill too. The `answer` reads
it back as

```json
"breakdown": {
  "steps": [ { "step": "bundle", "part": "lesson", "seconds": 196.0, "tries": 1, "costUsd": 0.5312,
               "models": [ { "model": "claude-opus-5-5", "maker": "Anthropic", "calls": 1, "costUsd": 0.5312, "credits": null } ],
               "tools": [] },
             { "step": "timing", "part": "videos", "seconds": 110.2, "tries": 1, "costUsd": 0,
               "models": [ { "model": "htdemucs", "maker": "Meta", "calls": 1, "costUsd": 0, "credits": null },
                           { "model": "mms-fa", "maker": "Meta", "calls": 1, "costUsd": 0, "credits": null } ],
               "tools": [ { "name": "demucs", "version": "4.1.0", "maker": "Meta" },
                          { "name": "torchaudio", "version": "2.11.0+cpu", "maker": "Meta" },
                          { "name": "torch", "version": "2.11.0+cpu", "maker": "Meta" },
                          { "name": "ffmpeg", "version": "6.1.1-3ubuntu5", "maker": "FFmpeg" } ] } ],
  "seconds": 653.3, "costUsd": 0.7981, "credits": 3214, "unpriced": 0, "complete": false
}
```

`tools` is the software a step ran on the studio's machine besides its models (`TOOLS` in
`studio/ledger.py`: skia for the art and the frames, ffmpeg for the audio and the videos,
Demucs, torchaudio and PyTorch for the timing), each with the version installed on the
machine that added up the bill, `null` when it could not be read. `python -m studio show`
prints them too. The API adds each model's and tool's `maker` from its name (`makerOf` in
`api/src/lib/breakdown.ts`), so bills stored before makers were shown have them as well;
a name it does not know has `null`. A bill sent before tools were listed has none.

Every try counts, in time and in cost, the failed ones too. `seconds` is wall time: the
art and the audio are made side by side, so it is less than the steps added up. The brain
is priced per token (`studio/brain.py`), a Lyria song per song, and Demucs and MMS run on
the studio's machine for free. ElevenLabs bills in its plan's credits: they are shown as
credits (`credits`) unless `ELEVENLABS_USD_PER_1K_CREDITS` gives their price. A model whose
provider said neither is counted in `unpriced`. The bill stays on `/learn`: the public
Droplets tab shows only the models it names ([Made with](#made-with)).

#### While a droplet is made

The studio reports where it is at the start and the end of every step, with the bill so far
(`progress()` in `studio/ledger.py`): on the topic while it writes the lesson
(`PUT /api/questions/:id/progress`, which also says whether videos follow), then on the
answer while it makes the videos (`PUT /api/answers/:id/video/progress`). A report waits at
most 5 seconds; one that does not get through is printed, the step goes on, and the run's
reports of the next minute are skipped, so a stalled API never holds a render up. The
videos' bill so far is replaced by the whole one with READY, and dropped by a READY sent
without one, rather than shown as the total. The question reads it back as

```json
"progress": { "running": [ { "step": "audio", "startedAt": "2026-11-03T12:01:10.000Z" } ],
              "failed": [], "videos": true, "seconds": 412.3, "breakdown": { "steps": [ ... ], ... } }
```

with each running step's start by the API's clock, and the bill in the shape above. `seconds`
is how long the droplet has been made so far: the bill's time, and while a step runs, the time
since the bill came (the API keeps when each bill came; a videos' report without one keeps the
one before). It is never less than the bill's time, nor than the lesson's time and the longest
a step running now has run, which stands in when no bill came or the report is older than
that. The card on `/learn` shows it as the bill of materials
open, one line per step: done (its time and cost), running (a pulsing dot, its time counting
up, what it has spent so far), failed and waiting for the retry, or still to come. A step still
to come that a later one has gone past is left out; so is the post, which the lesson's bill
never has. The time so far is on the bill's folded line ("Making it · 4 of 11 steps done ·
6.2 min · $0.53 so far") and its last line, and counts up with a running step.

The lesson text waits for the videos, so a learner meets the droplet through them rather than
through a text the videos then repeat: while `awaitingVideos` is true the card shows the bill
and no text, its badge still says BREWING, and follow-ups wait. Only a lesson whose studio
said, before posting it, that videos follow is held. The text shows, with the videos above
it, as soon as one of these is true: the videos are READY or FAILED (a render gone quiet
reads as FAILED), a second attempt has started, or `LESSON_HOLD_MINUTES` (default 60, and
never longer than `VIDEO_STALE_MINUTES`; 0 never waits) have passed since the lesson was
posted, so videos that are never started cannot hold it for good. The videos take 20 to 30
minutes, so in practice the text shows with them, or with the failure. Each only ever turns
true, so a lesson once shown is never hidden again. A lesson whose studio said no videos
follow (`STUDIO_VIDEOS=off`, or the media steps not built here), or that was posted without
saying (a studio older than the reports, a lesson by hand, a report that never got through),
shows at once and stays shown when its videos start. Past the hold, the bill under the text
keeps filling in until the videos are done.

Each report counts against a rate limit of its own (300 a minute for the agent key, beside
the 300 its other calls have), so reports from many renders at once can never use up what
their claims, uploads and READY need.

The bill is never the reason a step fails: a call log that cannot be written (a full disk)
is reported and the step goes on, a line cut short by a kill is skipped, and a bill that
cannot be read, is too big (16 KB) or holds a number JSON has no word for is left out of
the request rather than sent for the API to refuse. A render recovered on another machine counts only that
machine's tries, since the first machine's records died with it.

### Review

Under the bill, an admin's `/learn` has a second fold, **Review**: what the studio's own checks found in the droplet,
for the admin who approves it (with review on, `/admin/studio`) or looks it over. Nobody else sees it: not the learner who
asked for the topic, and not the Droplets tab. It is fetched only when opened (`GET /api/answers/:id/review`,
`api/src/lib/review.ts`), and has three parts:

- **Fact-check**: how many lines it judged supported, filler and unsupported, in how many rounds, what it fixed
  (before and after, and why), and every line it judged. Saved with every lesson since the fact-check began.
- **Code checks**: what `studio/checks.py` found in the bundle that only a better bundle could fix (a line naming a
  part its scene does not draw, a chorus directed differently each time it is sung), and how many it repaired
  itself. The studio sends `checks.json` with the lesson (`checks`).
- **Pictures**: each scene's kept picture beside what the art checker said about it (writing in it, a part missing
  or out of place, an unnamed thing, anything else wrong, a note that would teach something false), which try was
  kept, and why each earlier try was painted again or declined. The studio sends each scene's `art/<scene>.json`
  with READY (`artChecks`), with where the picture went in the private bucket; the API reads the picture out of
  the bucket for the page (`GET /api/answers/:id/review/art/:scene`), so the page's Content-Security-Policy still
  loads pictures only from this site and the public media domain. A drawn scene was not looked at.

The checks miss things, the pictures' science most of all ([test rounds](docs/test-rounds.md), open gap 2): the
panel says so, and the pictures are there to be looked at. Like the bill, neither part the studio sends is ever the
reason a post or an upload fails (`studio/review.py`): one that cannot be read, or is more than the API takes
(64 KB of code checks, 128 KB of art checks), is left out, and the review says it has none. A droplet made before
these were sent has the fact-check alone.

### Made with

Every droplet on the Droplets tab says which models made it, read from its bill: its card
shows each maker's chip ("Made with ANTHROPIC GOOGLE ELEVENLABS META"), naming the models
when one is hovered, and a tap on a chip opens every maker's models in place (a phone has no
hover) and closes them again. Its page shows the models after each chip, by the names and
versions the studio ran (`claude-opus-5-5, claude-haiku-5-5`). So anyone watching can tell
what made a droplet, and tell one made on a newer model from an older one. The API sends it
as `madeWith` on both droplet routes:

```json
"madeWith": [ { "maker": "Anthropic", "models": ["claude-opus-5-5", "claude-haiku-5-5"] },
              { "maker": "Google", "models": ["lyria-3.5"] },
              { "maker": "ElevenLabs", "models": ["eleven_multilingual_v2"] },
              { "maker": "Meta", "models": ["htdemucs", "mms-fa"] } ]
```

Each model is listed once, under its `maker` (`makerOf`), the makers in the order the bill
first names one of theirs, so the brain's comes first; models with no known maker come last,
under `null`. Only the models: what they cost, the steps and the software that is not a model
(ffmpeg, Skia) stay on the bill. The bill is the agent's to send, unchecked, and the agent is an
LLM that reads what learners write, so only a name shaped like a model's id (letters, digits,
`.`, `-` and `_`, up to 64) goes out, and not one that reads as a key or as personal information
(`containsSecret`, `containsPersonalInfo`); at most 32 of them, the first the bill names. It is `null` unless the droplet has both bills, the lesson's
and the videos' (a lesson's bill alone would leave out who drew and sang it), so a droplet
made before the bills says nothing. The list has Postgres pick the model names out of the
bills (`jsonb_path_query_array`, `BILL_MODELS_PATH` in `api/src/lib/breakdown.ts`), so only
the names leave the database for a page of cards. Nothing in it is about the learner: the models a
droplet is made with depend on the Studio settings and an admin's pick, not on what the
learner sent.

These are not hashtags: a hashtag is a search the fact-check holds to the lesson's content
([Tags](#tags)), which drops a brand, and the 10 a video has are for the topic.

### How it's made

`/how-its-made` (`web/src/app/how-its-made/page.tsx`) tells anyone, signed in or not, how a droplet is
made and checked: the studio's steps as a numbered rail, with the two checks a droplet must pass before
it is posted and the pictures and the sound made side by side, two real frames of a test droplet's
lesson video (the card beside a picture with a known mistake, and the tag on one without; the droplet,
on mitosis, was made on Oct 30 for the page, its files in `web/public/how-its-made/`), six things that went wrong in the test rounds
and what changed for each, the default models, and what the checks do not do yet. It is linked from
every page's footer and from beside "Made with" on each droplet's page. It is written for a high school
student, the droplets' default audience, in words a parent reads as easily: short sentences and
everyday words, none of the studio's own (it says "fact check" and "line check" for the fact audit and
the fact-check).

What it says is what the studio does, so it is a promise like the legal pages: when a change makes a
step check something else, or stop, change the page with it, and when the Studio page's defaults
change, change its models and their date (`MODELS_AS_OF`). Its examples come from
[docs/test-rounds.md](docs/test-rounds.md). It names no way to report a mistake, since nothing reads
droplet comments yet.

### Attachments

A topic request or a follow-up may carry files - a photo of class notes, a diagram, a text file:

```json
"attachments": [ { "name": "pasted.png", "mimeType": "image/png", "data": "<the file, base64>" } ]
```

At most 5 per message, 4 MB each and 8 MB together, sent inside the message's own body so a
message and its files are written together or not at all; they are kept in Postgres beside it
and go when it does. Each comes back listed on its message as
`{ id, name, mimeType, size, url, expired }`, never with its bytes; `GET url` serves them to the
learner who sent them, the admins, and the teacher while it holds or has answered that topic,
whose queues list them the same way. What a file is served as
is decided from its bytes, not from what was sent: PNG, JPEG, GIF and WebP as themselves, UTF-8
text as `text/plain`, anything else as a download, all in a sandbox. Past
`ATTACHMENT_RETENTION_DAYS` (default 30, 0 keeps them) the bytes are let go and the row stays,
`expired: true`, its `url` answering 410: the API sweeps them every hour, and lets one go on
the spot when it is read past its time. Each learner may send 100 MB of files a day
(`ATTACHMENT_MB_PER_USER_PER_DAY`; deleting a request gives none back) and have 300 MB held at
a time (`ATTACHMENT_MB_HELD_PER_USER`; deleting a request frees its files), so one account
cannot fill the database. Those defaults are generous for the hackathon; 20 and 60 suit a
small database better. Agents cannot send files.

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
| PATCH | `/api/answers/:id` | `{ hidden?, approved? }`: take a droplet off the Droplets tab, or put it back; approve one that waits for review (`false` sends it back to wait); the lesson stays on `/learn` |
| GET | `/api/answers/:id/review` | what the studio's checks found in the droplet ([Review](#review)): `{ id, factcheck, checks, art }`, each null when it was not sent |
| GET | `/api/answers/:id/review/art/:scene` | a scene's kept picture, read out of the private bucket: a PNG; 404 when the droplet has none in storage, 503 until storage is set up |
| PUT | `/api/announcement` | `{ message, tone? }`: put up the message under the nav, in place of any before it ([Announcement](#announcement)); `message` 1–500 characters of plain text, `tone` `info` (the default) or `warning`. `{ announcement }` |
| DELETE | `/api/announcement` | take it down; `{ announcement: null }` |
| GET | `/api/plays` | droplets ranked by how many of one video's plays finished, best first: `?video=song\|lesson\|hook` (song), `?min=` the fewest starts to be ranked (1), `?limit=` (50, at most 200). `{ video, min, data: [{ id, title, course, topic, hidden, createdAt, plays: { started, reached25, reached50, reached75, finished, shared, downloaded, finishRate } }], fewer }`, `fewer` the droplets with plays but under `min`. With `?totals=1`, `totals: { song, lesson, hook }` too: each video's counts over every droplet, whatever `min` (the page's drop-off curve, asked for once; it sums every row, about 90 ms at 300,000 droplets), left out when it cannot be read. The PLAYS page, `/admin/plays` |

### Play counts

To learn which droplets work for students, the API counts how far the plays of each droplet
video get and how often a hook clip is shared or downloaded: totals per droplet and video in
`droplet_plays`, and nothing else. The web app (`web/src/lib/plays.ts`) sends each step as it
happens, from the players on a droplet's page and on `/learn`, the cards on the tab, the Clips
feed and the hook tile's Share and Download:

- **Nothing about who watched.** A count goes without the session cookie, so a signed-in
  person's is counted with no account, and with no id of the browser, device or visit. The
  table holds no address, user id or time. An admin's browser sends none, so the owner's own
  checking never reads as a student watching.
- **Once a page view.** Each step of each video counts once however often it is replayed or
  rewound: `started` when it starts playing, then `reached25`, `reached50`, `reached75` and
  `finished` (95%) of it **played**, so a skip to the end counts as nothing played. A clip in
  the feed counts once it really plays; with reduced motion, nothing plays, or counts, until it
  is pressed. A share counts once it went through (a share sheet closed without sharing does
  not), a download when it is pressed. Counting never holds playback up: nothing waits for it,
  and a count that fails is dropped.
- **Hard to inflate.** In the table each step stays at or below the one before it, so a step
  sent without its play moves nothing. One address (an IPv6 /64 counts as one) adds at most 40
  an hour to any one count of a droplet video, about a class sharing a school's address.
- **Limited without skewing.** The limits gate starts first: one address starts at most 120
  plays a minute, and all addresses together 1,500 a minute per API instance. A play whose
  start is refused is not counted at all (the web app sends nothing more of it), so a crowd
  past a limit is counted short, not skewed. The steps after a counted start have budgets of
  their own, four times the starts' (480 a minute per address, 6,000 per instance), so a burst
  of new starts never cuts off a counted play halfway. A request that is no count is held to
  the starts' budget, and none of this touches the address's limit for the rest of the API. The
  limits hold the address only as a keyed hash under a secret drawn afresh each window, and
  only in memory. One address can still add 40 plays an hour to one video: rank on droplets
  with enough plays (`?min=`, the page's "At least").
- **Across a deploy.** A web app that meets an API with no counts (a 404) stops sending them
  until the page is loaded again; a 429 or a failure stops only that play.

`/admin/plays` opens with **where plays drop off**: one line each for the song, the lesson and
the hook clip, the share of all their plays still going at the start, 25%, 50%, 75% and the end,
every droplet together. Hovering a point, or tabbing to it, lists every video's count there, and
the same numbers are in a table under the chart. Below it, the ranking.

The ranking `/admin/plays` shows, as SQL (here for the song; `'LESSON'` or `'HOOK'` for the others).
The page is picked from the counts alone and the droplets joined after, so only the bundles
shown are read (27 ms for 30,000 droplets, where joining first took 280 ms):

```sql
SELECT a.id, a.bundle->>'course' AS course, a.bundle->>'topic' AS topic, a."hiddenAt" IS NOT NULL AS hidden,
       p.started, p."reached25", p."reached50", p."reached75", p.finished, p.shared, p.downloaded,
       round(p.finished::numeric / p.started, 3) AS finish_rate
FROM (
  SELECT * FROM droplet_plays WHERE video = 'SONG' AND started >= 10
  ORDER BY finished::float8 / started DESC, started DESC, "answerId" LIMIT 200
) p JOIN answers a ON a.id = p."answerId"
ORDER BY p.finished::float8 / p.started DESC, p.started DESC, a.id;
```

## Privacy and the legal pages

Two sides, kept apart in the API (`api/src/lib/privacy.ts`):

- **Public:** the lesson, its videos, lyrics, hashtags, the models that made it and a title
  from the bundle's `course` and `topic`, on the Droplets tab. Nothing about who asked.
- **The learner's own:** their name, email and photo, the request in their words, their
  files and the follow-up thread. A learner sees only their own topics on `/learn`, and only
  they post follow-ups in their thread. Admins see everyone's (to moderate and fix things)
  and the teacher reads what it answers.

Beside both, the [play counts](#play-counts) are about no one: totals of how far each video's
plays got and how often a hook clip was shared or downloaded, seen only by admins.

A learner can **take back** a request from its card: with no lesson yet it is deleted; with
one, their words, files and thread are deleted and `questions.userId` is set to null, so
the lesson stays on the tab naming no one. **Deleting an account** (`/account`) does that
for every request with a lesson and deletes everything else, except for the only admin,
who has to make someone else an admin first.

`/privacy`, `/terms` and `/consumer-health-data` (the policy Washington's My Health My
Data Act requires, with its own footer link) are plain pages in `web/src/app/`. The facts
they rest on are in `web/src/lib/legal.ts`: who runs Popsmosis and where (Little Paprika
Lab, Washington State), the contact address, the governing law (Washington, King County
courts), the minimum age (13), the file retention (keep it equal to
`ATTACHMENT_RETENTION_DAYS`) and the license the droplets are shared under
(CC BY-NC-SA 4.0), and the contact address, which is also the copyright (DMCA) agent's.

**Agreeing.** Nobody signs in without ticking a box that names the Terms, the arbitration
and class action waiver in them, and the Privacy Policy (`AgreeToTerms`); the API refuses
the sign-in without it and records the version and the time on the account
(`api/src/lib/terms.ts`). When the pages change in a way people must agree to again, move
`TERMS_VERSION` there: everyone is then asked once, before anything else, at their next
visit (`TermsGate`). For any change, move `LAST_UPDATED` in `legal.ts`. When a change to the
code changes what the pages promise, change the pages with it. The pages were written
without a lawyer; have one read them if Popsmosis grows.

## Deploy

`render.yaml` is a Render Blueprint for a Postgres, the API, and the web app. Step by
step instructions, the env var values, and the Google OAuth setup are in
[docs/DEPLOY.md](docs/DEPLOY.md). In short: New > Blueprint > pick this repo > fill in
the prompted values > Apply. Migrations run automatically before each API deploy.

The API starts as `node --import tsx dist/index.js`: the build is typechecked and
compiled by `tsc`, but the Prisma-generated client uses extension-less ESM imports
that plain Node cannot resolve, so the tsx loader stays in the start command (same as
sparks).
