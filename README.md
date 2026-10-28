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
in Supabase and shown above the text. The learner meets a droplet through its videos: while
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
web/     Next.js 15 + Tailwind 4. The /learn page, the public /droplets tab, sign-in, and the admin pages.
prisma/  Postgres schema and migrations (User, Agent, Question, Answer, Reply, DropletLike, DropletComment).
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

To fill the page with sample data (two users, one agent, a topic with its lesson, its videos,
a follow-up thread and a like and a comment on the Droplets tab, one topic still waiting, and
older lessons whose videos are rendering or failed), run `npm --workspace api run seed:dev`. It prints the agent's key. The seeded
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
fact-checks it and posts the lesson. Its models, and the keys they bill to, are set by
the variables in [Choosing the models](#choosing-the-models): Claude's by default, on
`POPSMOSIS_ANTHROPIC_API_KEY` ([docs/PLAN.md](docs/PLAN.md), section 4). Each job writes
into `jobs/<id>/`.

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
the timeline, render and upload. `SONG_PROVIDER` picks who sings the song: `elevenlabs`
(the default, Eleven Music, with `ELEVENLABS_API_KEY`) or `gemini` (Lyria 3.5 on the Gemini
API, with `GEMINI_API_KEY`; `GEMINI_SONG_MODEL` picks another Lyria model). The narration
is ElevenLabs either way. Switching it makes a job's song again on its next run, and only
the song. `run` claims the videos as soon as it posts the lesson,
and `videos` renews the claim before every step, so a render whose machine dies is handed
to a later run by `recover` (`docs/agent-runbook.md`). `STUDIO_VIDEOS=off` makes
text-only droplets.

Every model call is logged to the job's `calls.jsonl` with its step, tokens, time and cost,
and `show` ends with the bill of materials the page shows ([Bill of materials](#bill-of-materials)).

`run` prints one line: `DONE`, `FAILED <step>: <reason>` (run it again to retry that
step) or `STOPPED <step>: <reason>` (the topic is gone, taken or done, or an earlier run
of it is still going). A run killed mid-step is recorded as that step failing. To try the
pipeline without posting anything, start a local job instead:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r studio/requirements.txt
python -m studio plan --topic "brain structures" --course "AP Psych"   # bundle, then fact-check
python -m studio step factcheck <job-id>                               # re-run one step
python -m studio show <job-id>                                         # facts, scenes, lyrics, tags, checks, verdicts, runs, calls
```

Each droplet is written for one audience: high school, unless the learner's request says
who it is for ("I'm in 5th grade", "for my college course"). The audience sets the level
of the lesson, the song, the narration and the exam question, never the rules: every
droplet is fact-checked the same way and stays fit for a classroom. The song's style is
one the audience listens to (for high school: pop with rapped verses, K-pop,
R&B, dance-pop, pop-punk), at a tempo that fits the topic's mood, unless the learner
asks for their own: a line of their question that starts `Style:` becomes the song's
starting point. Locally, pass it as `--style "K-pop with hip-hop verses, 98 bpm"`.

Once a job has its rendered videos and a posted lesson (`posted.json`), the upload step
(step 9) copies them to Supabase and marks them READY, which lists the droplet on the
Droplets tab. It uses the same two variables, and the API needs Supabase set up
([docs/DEPLOY.md](docs/DEPLOY.md), "Supabase Storage"):

```bash
python -m studio step upload <job-id>    # videos and posters public, the rest private; again sends only what changed
```

### Choosing the models

These variables pick the models the studio runs and the accounts they bill to. The studio
reads them from its own environment, not from `.env`: on the routine, set them in its
environment's settings ([docs/agent-runbook.md](docs/agent-runbook.md), section 1).

| Variable | Values | Default | What it does |
|---|---|---|---|
| `BRAIN_PROVIDER` | `claude` or `meta`, in lowercase | `claude` | Whose models write and fact-check each lesson, and who makes its art (the next table) |
| `POPSMOSIS_ANTHROPIC_API_KEY` | an Anthropic API key, `sk-ant-...` | none | Needed with `claude`. `ANTHROPIC_API_KEY` works too on a laptop, but a Claude Code cloud environment does not pass it through |
| `MODEL_API_KEY` | a Meta API key, from dev.meta.ai | none | Needed with `meta`. Every Meta model the studio calls bills to it |
| `STUDIO_BRAIN_MODEL` | with `claude`, a Claude model id; with `meta`, a Muse model id (the last table) | `claude-opus-5-5` with `claude`, `muse-wonder-1.0` with `meta` | The brain's model. Change it when you change `BRAIN_PROVIDER`: a model from the other provider stops the run (below) |
| `STUDIO_BRAIN_EFFORT` | `low`, `medium`, `high`, `xhigh`, `max` | `high` | How hard the brain thinks, with `claude` only; `meta` ignores it |

What each `BRAIN_PROVIDER` runs:

| Job | `claude` | `meta` |
|---|---|---|
| Brain: writes and fact-checks the lesson | Claude Opus 5.5, or `STUDIO_BRAIN_MODEL` | Muse Wonder 1.0, or `STUDIO_BRAIN_MODEL` |
| Helper: checks each picture, boxes a structure SAM missed | Claude Haiku 5.5 (not called: code checks the SVG art) | Muse Spark 1.3 |
| Art | the brain draws each scene as SVG | Muse Image 1.0 paints it, $0.01 a picture, up to three a scene |
| Finding each structure in the art | the SVG says where each one is | SAM 3.1, $2.50 per 1,000 images |

Switching `BRAIN_PROVIDER` makes a job's art again on its next run; a lesson already
written stays as it is.

`run`, `videos` and `recover` check these settings before they claim anything. A wrong one
(an unknown provider, its key missing, a model from the other provider, a misspelled
effort) ends the command with `STOPPED run: the studio's settings are wrong: ...` (or
`videos`), and the teacher stops and reports it, so no topic is failed or given up over a
setting.

The brain's models, for `STUDIO_BRAIN_MODEL`:

| `STUDIO_BRAIN_MODEL` | With | Price per million tokens (in / out) | Use it for |
|---|---|---|---|
| `claude-opus-5-5` (the default) | `claude` | $4.00 / $20.00 | the real lessons, about 70 cents a topic |
| `claude-haiku-5-5` | `claude` | $0.10 / $0.50 | trying a change, about 2 cents a topic |
| `muse-wonder-1.0` (the default) | `meta` | $4.00 / $20.00 | the real lessons |
| `muse-spark-1.3` | `meta` | $1.25 / $4.25 | vetting a change, on any topic |
| `muse-spark-1.3-contributor` | `meta` | $0.10 / $0.20 | test topics only (`python -m studio plan`): Meta may train on everything it is sent, learners' requests included. Never on the routine |

Any other model of the provider's works too (another Claude model id, or a Muse one such
as `muse-spark-1.2`); a model the bill has no price for shows as unpriced. To try a prompt
change on a local job: `STUDIO_BRAIN_MODEL=claude-haiku-5-5 python -m studio plan ... --brief`,
or with `BRAIN_PROVIDER=meta`, `STUDIO_BRAIN_MODEL=muse-spark-1.3`.

The Meta calls follow Meta's docs but have not run against the live API yet: check them
first with `MODEL_API_KEY=... python -m pytest studio/tests/test_live_meta.py -s` (one
scene, a few cents).

## Tests

```bash
npm --workspace api run typecheck
npm --workspace api test                  # unit tests, no database
INTEGRATION=1 npm --workspace api test    # plus the end-to-end flow against DATABASE_URL
npm --workspace web run build
npm run e2e                               # Playwright browser tests; needs api + web running (see web/e2e/README.md)
python -m pytest studio                   # the studio, offline; test_live.py also runs when POPSMOSIS_ANTHROPIC_API_KEY is set,
                                          # and test_live_meta.py when MODEL_API_KEY is
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
| GET | `/api/droplets` | the Droplets tab: droplets whose videos are READY and that no admin hid, newest first; `?cursor=`, `?limit=` (12, at most 24); `?tag=` only those whose song or lesson video carries that hashtag (`#APBio` and `apbio` alike, [Tags](#tags)). Each: `{ id, title, course, topic, excerpt, video, tags, likeCount, commentCount, liked, createdAt, comments }`, where `comments` is its newest two (`{ data, nextCursor }`; the cursor carries on at `/comments`, null when those are all) |
| GET | `/api/droplets/:id` | one droplet, by its answer id: the same, plus the lesson `content`, the song's `lyrics` and the first page of its `comments` (`{ data, nextCursor }`), so a droplet's page is one request; 404 unless it is on the tab |
| GET | `/api/droplets/:id/comments` | its comments, newest first, for the pages after the first; `?cursor=`, `?limit=` (20, at most 50). `{ data: [{ id, content, createdAt, author: { name }, mine }], nextCursor, commentCount }`; 404 unless it is on the tab |

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
| POST | `/api/questions` | ask for a topic: `{ content, attachments? }` 3–2000 chars; `attachments` see [Attachments](#attachments) |
| DELETE | `/api/questions/:id` | own topic. With no lesson yet it is deleted; with one it is taken back: the words, files and follow-ups go, the lesson stays, naming no one |
| POST | `/api/answers/:id/replies` | a follow-up under the caller's own lesson: `{ content, attachments? }` 1–2000 chars |
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
| POST | `/api/questions/:id/claim` | take or renew the lease on one topic and read it; 409 if answered, failed, or leased to another agent |
| POST | `/api/questions/:id/answer` | the lesson: `{ content, model?, bundle?, factcheck?, breakdown? }`; the two JSON objects go together and `factcheck.status` must be `passed`; `breakdown` is the steps so far ([Bill of materials](#bill-of-materials)); 409 once there is one, with `answerId` when it is the caller's own |
| POST | `/api/questions/:id/fail` | give a topic up: `{ reason }`; it leaves the queue and its card shows the reason; the last claimer only |
| PUT | `/api/questions/:id/progress` | where the studio is while it writes the lesson: `{ running: [{ step, seconds }], failed?, breakdown?, videos? }` ([While a droplet is made](#while-a-droplet-is-made)); 204; 409 unless the caller holds the claim and there is no lesson yet |
| PATCH | `/api/answers/:id/video` | own answers: `{ status: RENDERING\|READY\|FAILED, songVideoUrl?, lessonVideoUrl?, songPosterUrl?, lessonPosterUrl?, error?, attempt?, breakdown? }`; a URL left out is kept, so the two videos can report separately; READY lists it on the Droplets tab, and may carry the media steps' `breakdown`. With `attempt`, 409 unless that attempt still holds the render |
| POST | `/api/answers/:id/upload-url` | own answers: `{ bucket: droplets\|studio, file }`; a signed Supabase upload URL, and for `droplets` its public URL; 503 until storage is set up (`SUPABASE_URL` and a secret key in `SUPABASE_SERVICE_ROLE_KEY`), with `retryable: true` only when waiting may fix it |
| POST | `/api/answers/:id/video/claim` | own answers: `{ attempt? }`; without one, start an attempt at the videos (never started, given up, or quiet past `VIDEO_STALE_MINUTES`; at most `VIDEO_MAX_ATTEMPTS`, default 2), marking them RENDERING; with the caller's attempt, renew it. Returns `{ answerId, questionId, attempt, status, bundle, factcheck }`; 409 when another run holds them, they are READY, or the attempts are used up |
| GET | `/api/videos/queue?limit=N` | own droplets whose render went quiet past `VIDEO_STALE_MINUTES` (its machine died), oldest first, each claimed as its next attempt, as the claim returns them |
| POST | `/api/answers/:id/video/fail` | own answers: `{ reason, attempt, breakdown? }`; give the videos up (FAILED with the reason), with what the failed tries took and cost; only the current attempt may |
| PUT | `/api/answers/:id/video/progress` | own answers: `{ attempt?, running: [{ step, seconds }], failed?, breakdown? }`, where the render is, its `breakdown` the videos' bill so far; 204; 409 unless they are RENDERING under that attempt. Not the heartbeat: the claim is |
| GET | `/api/replies/queue?limit=N` | threads where a human followed up last, each with the droplet's `answer.context` |
| POST | `/api/answers/:id/replies` | `{ content, replyToId }`; 409 if already responded |
| GET | `/api/attachments/:id` | a file a human sent; 410 once past the retention |

A lesson's `answer` also carries `video` (`null` for a text-only droplet): its status,
both URLs, both posters and the error; `tags`, each video's hashtags ([Tags](#tags)); `breakdown`, its bill of materials (below);
`awaitingVideos`, true while the page holds the text back for the videos (below); and
`hidden`, true when an admin took it off the Droplets tab. The question carries `progress` while its droplet is being
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
Droplets tab never shows it.

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
              "failed": [], "videos": true, "breakdown": { "steps": [ ... ], ... } }
```

with each running step's start by the API's clock, and the bill in the shape above. The card
on `/learn` shows it as the bill of materials open, one line per step: done (its time and
cost), running (a pulsing dot, its time counting up, what it has spent so far), failed and
waiting for the retry, or still to come. A step still to come that a later one has gone past
is left out; so is the post, which the lesson's bill never has.

The lesson text waits for the videos, so a learner meets the droplet through them rather than
through a text the videos then repeat: while `awaitingVideos` is true the card shows the bill
and no text, its badge still says BREWING, and follow-ups wait. Only a lesson whose studio
said, before posting it, that videos follow is held. The text shows, with the videos above
it, as soon as one of these is true: the videos are READY or FAILED (a render gone quiet
reads as FAILED), a second attempt has started, or `LESSON_HOLD_MINUTES` (default 20; 0
never waits) have passed since the lesson was posted, so a stuck render never holds it for
long. Each only ever turns true, so a lesson once shown is never hidden again. A lesson
whose studio said no videos follow (`STUDIO_VIDEOS=off`, or the media steps not built here),
or that was posted without saying (a studio older than the reports, a lesson by hand, a
report that never got through), shows at once and stays shown when its videos start. Past
the 20 minutes the bill under the text keeps filling in until the videos are done.

Each report counts against a rate limit of its own (300 a minute for the agent key, beside
the 300 its other calls have), so reports from many renders at once can never use up what
their claims, uploads and READY need.

The bill is never the reason a step fails: a call log that cannot be written (a full disk)
is reported and the step goes on, a line cut short by a kill is skipped, and a bill that
cannot be read, is too big (16 KB) or holds a number JSON has no word for is left out of
the request rather than sent for the API to refuse. A render recovered on another machine counts only that
machine's tries, since the first machine's records died with it.

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

## Privacy and the legal pages

Two sides, kept apart in the API (`api/src/lib/privacy.ts`):

- **Public:** the lesson, its videos, lyrics, hashtags and a title from the bundle's
  `course` and `topic`, on the Droplets tab. Nothing about who asked.
- **The learner's own:** their name, email and photo, the request in their words, their
  files and the follow-up thread. A learner sees only their own topics on `/learn`, and only
  they post follow-ups in their thread. Admins see everyone's (to moderate and fix things)
  and the teacher reads what it answers.

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
