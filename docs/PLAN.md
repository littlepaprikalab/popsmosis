# Popsmosis — droplets for learning

Status: **day 1 of 10** (Oct 26). Built for the Meta Global AI Developer Hackathon (Indie
track); the submission is due Nov 4, 9:00 PM ET. The zoey port is done (below); this is
the plan the rest of the build follows. Where the code and this document differ, the
code wins; the README lists the endpoints as built.

## 1. What we are building

1. A web app with **Google sign-in**.
2. A signed-in user asks for a **topic** to learn: "ATP synthesis", "the French
   Revolution", or a fuller ask ("ATP synthesis for my AP Bio exam"), optionally with a
   study guide or a photo of their notes.
3. The API stores the request and **fires the teacher's Claude routine** through its API
   trigger, so a run starts within seconds.
4. The routine runs the **studio**, a Python pipeline in this repo, on the topic. The
   studio writes the lesson, fact-checks every line, then makes two videos from the same
   plan and the same diagrams. Only the audio differs:
   - **Lesson video**: narrated, ending with a worked exam question (FRQ).
   - **Song video**: the same content sung, the diagram labels synced to the vocals, the
     same FRQ at the end.
5. The page shows the droplet: the lesson text as soon as it passes the fact-check, then
   both videos when they finish rendering, updating in place without a reload.
6. The learner asks **follow-ups** under the droplet, and the teacher answers them on its
   next pass.
7. Every finished droplet also appears on a public **Droplets** tab, where anyone can
   watch the videos for free without signing in.

Done means: AP Psych "brain structures", an AP Bio topic, and a topic the judges pick live
each produce watchable videos in under ~15 minutes, with every line passing the
fact-check.

## 2. Where it came from

Popsmosis is a copy of [zoey](https://github.com/Iztera/zoey) at `3f81af7`: an advice
column whose questions are answered by an agent. The pathway is the same one; only what
goes in and what comes back changes.

| zoey | popsmosis |
|---|---|
| A user asks for advice | A user asks for a topic |
| Saved as a `Question` | Same row: the topic request |
| The API fires the routine; the advisor wakes | Same trigger; the teacher wakes |
| The advisor posts a text `Answer` | The teacher posts the droplet: lesson text, then two videos |
| Replies under the answer | Follow-ups under the droplet |

The API and the schema still use zoey's words (`question`, `answer`, `reply`). The pages
and the skill file say topic, lesson/droplet and follow-up. Renaming the API is not worth
the churn until the video fields land; do it then if at all.

### What the port changed (day 1)

- Renamed everything: `sk_pop_` agent keys, `pk_pop_` personal access tokens, the
  `popsmosis_session` cookie, `POPSMOSIS_*` env vars, the `popsmosis-api` /
  `popsmosis-web` Render services, the npm workspaces.
- Dropped zoey's carry-overs from its own rename (the `bong` key prefix and cookie): this
  is a fresh deployment, so nothing carries them.
- Dropped the Zed editor integration (`client`, `context`, `commands`), which only meant
  something inside that editor.
- Squashed zoey's eleven migrations into one `init`: the popsmosis database starts empty.
- `/advice` became `/learn`. A topic can be as short as 3 characters ("DNA"); zoey's
  questions needed 10.
- The skill file now makes the agent a teacher: big picture first, steps in order, a
  check question at the end, and one short lesson per topic.
- Kept everything else as it was: Google sign-in, the approval queue, admin pages for
  agents and users, access tokens, attachments, the queue lease, the routine trigger,
  tests, CI and the Render blueprint.

## 3. Who does what

```
topic ──► API ──fire──► Claude routine ──► python -m studio run <id>
                                             │ brain: bundle, fact-check
                                             ├──► lesson text posted to the API
                                             │ art + masks    ┐ at the same time
                                             │ audio + timing ┘
                                             │ timeline (code) ──► render (code)
                                             ▼
                                          Supabase Storage ──► both videos on the droplet
```

- **The Claude routine is the entry point and the operator.** It wakes on the API
  trigger, claims one topic, runs the studio on it in its own container, and exits. It
  does not write the lesson or make media itself. The routine could do the brain's work
  directly, but going through the studio keeps one code path for every brain provider.
- **The studio is a fixed pipeline, not an agent loop.** Code decides the order; models
  are asked only where judgment is needed, one focused call at a time. That is cheaper
  (each call carries only what its step needs, instead of a conversation re-sent on every
  turn), predictable, and each step writes a file into `jobs/<id>/` so any step can be
  re-run on its own.
- **The brain is swappable.** `BRAIN_PROVIDER` picks the models for the thinking roles.
  It defaults to `claude` because we do not have the Meta API key yet. Switch to `meta`
  as soon as the key arrives: this is a Meta hackathon, so the submission runs on Meta.

| Role | `BRAIN_PROVIDER=claude` (default for now) | `BRAIN_PROVIDER=meta` |
|---|---|---|
| **Brain**: the bundle, the fact-check | Claude Opus 5.5 (`claude-opus-5-5`), effort `high` | Muse Wonder (`muse-wonder-1.0`) |
| **Helper**: image checks, SAM boxes, QA stills | Claude Haiku 5.5 (`claude-haiku-5-5`), effort `low` | Muse Spark (`muse-spark-1.3`) |

Every other job has one provider, whatever the brain:

| Job | Made by |
|---|---|
| Diagrams | Muse Image (`muse-image-1.0`); brain-drawn SVG until the Meta key arrives (step 3) |
| Structure masks | SAM 3.1 (`sam-3.1`), with a helper box as the fallback |
| Song | ElevenLabs Eleven Music (`music_v2_5`) |
| Narration | ElevenLabs text-to-speech with timestamps |
| Word timing (song) | Demucs (vocals) + Meta's MMS forced aligner, both open source, run locally |
| Timeline | Code |
| Video | Code: skia-python draws, ffmpeg encodes |

Prices per million tokens (input / output): Opus 5.5 $4 / $20, the same as Muse Wonder;
Haiku 5.5 $0.10 / $0.50. Muse Image is $0.01 per image.

## 4. The brain interface

One module, `studio/brain.py`, with one function both providers implement:

```python
ask(role, prompt, schema=None, images=(), *, system=None, label=None) -> dict   # role: "brain" | "helper"
```

- **Claude** goes through Anthropic's Python SDK (`anthropic`): the system prompt, image
  blocks, and the JSON schema in `output_config.format`. Set effort on every call: Opus
  5.5 defaults to `medium`. Its thinking cannot be turned off and is billed as output.
  Stream the bundle call (it is long).
- **Meta** goes through the OpenAI SDK pointed at `https://api.meta.ai/v1`.
- **Prompts and schemas are shared.** Schemas use only what both providers accept:
  objects (each with `additionalProperties: false`), arrays, strings, numbers, booleans
  and enums. No min/max, no length limits, no recursive types. Every response is
  validated with pydantic anyway.
- **Refusals.** Claude can decline a request (`stop_reason: "refusal"`), and Opus 5.5
  runs a biology classifier: ordinary AP Bio will not trip it, a pathogen topic might.
  Check the stop reason on every call, and turn on the API's server-side fallback so a
  declined request is retried on another Claude model.
- **Billing.** Claude calls are billed per token to the Anthropic API account behind
  `ANTHROPIC_API_KEY`, not to the Claude plan that runs the routine. The $150 of Meta
  credits cover Meta calls only.

## 5. The studio pipeline

`python -m studio run <question-id>`. Each step writes into `jobs/<id>/` (git-ignored).

| # | Step | Who | Writes |
|---|---|---|---|
| 1 | Bundle | Brain, one call | `bundle.json` |
| 2 | Fact-check | Brain, a fresh context | `factcheck.json` |
| | Post the lesson | API | Lesson text, bundle and fact-check saved; the topic leaves the queue |
| 3 | Art | Muse Image + helper | `art/<scene>.png` |
| 4 | Locate | SAM 3.1 + helper | `masks/<scene>/<structure>.png`, `locations.json` |
| 5 | Audio | ElevenLabs | `audio/song.mp3`, `audio/narration.mp3` |
| 6 | Timing | Demucs + MMS; TTS timestamps | `timing/song_words.json`, `timing/narration_words.json` |
| 7 | Timeline | Code | `timeline_song.json`, `timeline_lesson.json` |
| 8 | Render | Code | `out/song_h.mp4`, `out/lesson_h.mp4` |
| 9 | Upload | API + Supabase | Videos (public), intermediates (private) |

Steps 3–4 (art) and 5–6 (audio) do not depend on each other: run them at the same time.

That is two or three brain calls per topic: the bundle, the fact-check, and a re-check of
any lines the fact-check changed (plus one per scene for the interim art, until the Meta
key arrives). The helper is called per image and per fallback.

### Step 1: bundle

One structured-output call returns everything that can be written from the topic alone.
The fields are in this order on purpose: the model writes in field order, so the later
parts build on the facts it has already written.

1. `course`, `topic`, `core_idea`, `facts[3-4]` (`id`, `text`, `source_quote` when a
   study guide was given), `misconception`, `frq` (`prompt`, `predict_question`,
   `model_answer_steps[]`, `twist`).
2. `scenes[3-6]`: hook, overview, one or two zoomed details. Each has `id`,
   `image_prompt`, and `structures[]` (`name`, `aliases[]`, `sam_phrase`, `color_hex`).
   One fixed colour per structure for the whole job.
3. `lesson_markdown`: the text droplet. The skill file's "How to teach" rules now live in
   this prompt.
4. `music`: `positive_styles`, `negative_styles`, and the section order (verse, chorus,
   verse, chorus, bridge, chorus).
5. `lyrics[]`: `id`, `section`, `text`, and the `fact_id` it teaches or `"filler"`. The
   chorus states `core_idea`. A line that names a structure uses its `name` or one of its
   `aliases`, because hearing that word is what reveals the structure on screen.
6. `narration[]`: sentences, 2–3 minutes in all, tagged like the lyrics.
7. `direction[]`: for every lyric line and narration sentence, by id: `scene`, `camera`
   (`target`, `zoom`), `spotlight`, `particles`, `caption`. No times: code adds those
   from the alignment.

The bundle is roughly 6–10k tokens. If it ever outgrows a model's output limit (Claude's
is 128K), split it into two calls: parts 1–2, then 3–7.

### Step 2: fact-check

A separate brain call that sees only the facts (and the study guide, if one was given)
and the lines, never the writer's reasoning. The lines are every lyric line, every
narration sentence, and every sentence of `lesson_markdown` (code splits it). For each it
returns `SUPPORTED`, `UNSUPPORTED` or `FILLER`, the fact id or study-guide quote, and for
an unsupported line a corrected one. A corrected lyric stays about the same length and
keeps the structure's name, so the timing and the direction still fit. Code applies the
fixes and re-checks only the changed lines, up to 3 rounds; if any line still fails, the
job fails with the reason shown on the droplet. Nothing is posted or sung that has not
passed.

There is no teacher approval step: the fact-check is the gate.

Regression test: "Every electron hand-off pumps a proton" must be flagged (only
cytochrome b6f pumps).

### Step 3: art

Muse Image (`/v1/images/generations`), size `1792x1024`. The prompt suffix is always:
"textbook illustration in the style of OpenStax/Campbell, shaded vector, soft gradients,
clean outlines, white background, scientifically accurate, absolutely no text, no labels,
no letters, no numbers." Disable web search and shell (`tool_enablement`). Keep
structures away from the edges so vertical crops work later. The helper looks at each
image; if any text is visible or the anatomy is wrong, regenerate (max 3).

**Until the Meta key arrives**, the brain draws each scene as SVG, with every structure
as its own named group. Rasterising the SVG gives the art, and rasterising one group at a
time gives each structure's mask, so step 4 is not needed. It looks plainer than Muse
Image, but it writes the same files, so everything downstream is built and tested against
it unchanged.

### Step 4: locate

SAM 3.1 via `/v1/responses`, one noun phrase per request. If SAM finds nothing for a
structure, the helper proposes a box and SAM is called again with that box; the last
resort is the helper giving the label anchor point directly. `locations.json` holds
`box`, `centroid` and `label_anchor` per structure, all in art pixel coordinates.

### Step 5: audio

- **Song:** `POST https://api.elevenlabs.io/v1/music`, `model_id` `music_v2_5`, with a
  `composition_plan` of one chunk per section: `text` ("[Section]\nlines"),
  `duration_ms`, `positive_styles`, `negative_styles`, `context_adherence` "high". The
  studio's code makes this call, only after the fact-check passes. Section durations come
  from the line count (about 4.3 s per line at 112 bpm), not from the model.
- **Narration:** `/v1/text-to-speech/{voice_id}/with-timestamps`, which returns the audio
  and per-character timings, so it needs no aligner.

### Step 6: timing

- **Song:** Demucs `--two-stems=vocals`, then the MMS aligner
  (`torchaudio.pipelines.MMS_FA`) on `vocals.wav` against the exact lyrics, section by
  section.
- **Narration:** ElevenLabs' character timings converted to words.
- **Checks:** repeated chorus lines should have near-identical durations; flag any line
  that drifts more than 0.5 s. If a section was sung wrong, regenerate that section only.

Output is `[{word, start, end}]`.

### Step 7: timeline

Code merges `bundle.direction`, the word timings and `locations.json` into the timeline
(section 6). Reveals are mechanical: a structure reveals when its name or an alias is
sung, and the labels shown are everything revealed so far. A direction that names a
structure not in its scene, or one SAM could not find, falls back to no spotlight and
the camera on the whole scene, rather than another model call.

### Step 8: render

Section 7.

### Step 9: upload

The API mints a signed upload URL per file and the studio uploads straight to Supabase
Storage, so the Supabase key stays on the API: the videos to the public bucket, the
intermediate files to the private one (section 10). Then the studio marks the videos
ready.

### Stretch

After both 16:9 videos work end to end: the vertical 9:16 layout, particles, and QA
(render a still every 5 s, tile them into one contact sheet per video, the helper checks
it against section 8; re-render on a fail, max 2).

### Layout

```
studio/
  brain.py         ask(): the Claude and Meta providers
  prompts/         bundle, fact-check and helper prompts
  schemas.py       pydantic models: bundle, factcheck, locations, timeline
  steps/           bundle, factcheck, art, locate, audio, timing, timeline, render, upload
  render/          renderer.py, primitives.py, layouts.py, fonts/ (Inter Bold/SemiBold/ExtraBold)
  ass_fallback.py  the ffmpeg karaoke fallback
  tests/
  requirements.txt
jobs/<id>/         one folder per topic (git-ignored)
```

## 6. Timeline format

The contract between the studio and the renderer. The model writes none of the times:
`t0`, `t1` and `words` come from step 6.

```jsonc
{
  "audio": "audio/song.mp3", "duration": 195.5, "fps": 30,
  "palette": {"Broca's area": "#E07A2F", ...},
  "beats": [
    {
      "t0": 12.4, "t1": 16.8,
      "scene": "overview",
      "camera": {"target": "Broca's area", "zoom": 1.6, "ease": "inOut"},
      "reveal": ["Broca's area"],          // fades from ghost to full when the word is sung
      "spotlight": "Broca's area",         // others washed pale; null = none
      "labels": ["Broca's area", "Wernicke's area"],  // all previously introduced labels stay
      "particles": [{"path": ["Wernicke's area", "Broca's area"], "color": "#F2C230", "trail": true}],
      "caption": null,                     // at most ~1 per section, held >= 4 s
      "line": "Broca builds the words you say",
      "words": [{"w": "Broca", "t": 12.6}, ...]
    }
  ],
  "frq": {"t0": 166.0, "predict_pause_s": 5, "steps": [{"t": 172.0, "text": "..."}]}
}
```

Validation (pydantic): every label, reveal and spotlight name exists in that scene's
locations; `t0 < t1`; the beats cover the whole audio; the first beat starts at 0 and is
the hook.

## 7. The renderer

Generalised from the Fire or Don't renderer (hard-coded per song) into one that reads
`timeline.json`.

- **Every frame depends only on its time.** `frame(t)` finds the active beat and draws
  it. That makes parallel rendering and QA stills free.
- **Once per scene:** load the art and the masks, and precompute each structure's ghost.
- **Primitives** (`studio/render/primitives.py`):
  - **Camera:** the world is art pixel coordinates; per beat, ease zoom and pan toward
    the target structure's centroid; clamp to the art's bounds.
  - **Ghost:** unrevealed structures drawn pale through their mask (white wash at ~85%
    alpha, blurred edge); fade to full over 0.4 s when revealed.
  - **Spotlight:** wash all other structures pale. Never add a glow on top (it tints the
    colours).
  - **Label:** white rounded pill, 3.5 px outline in the structure's colour, text in a
    darkened version of that colour, Inter Bold, a leader line and a 6 px dot at the
    mask's `label_anchor`. Pills stay inside the frame and avoid each other (a simple
    repulsion pass). Once introduced, a label stays on every shot of that diagram.
  - **Particles** (stretch): dots moving slowly along a polyline between structure
    centroids, one highlighted particle with a fading trail, pausing at each stop.
  - **Lyric band:** a white band at the bottom (110 px tall in 16:9), the current line
    centred, the current word highlighted by its timing.
  - **Caption pill:** optional, top area, held at least 4 s.
  - **Hook:** frame 0 already shows motion and the hook claim; no title card.
  - **FRQ segment:** a question card over the diagram, a visible countdown during the
    predict pause, then numbered model-answer steps appearing in sync with the
    narration, spotlighting the structure each step names.
- **Encoding:** pipe raw BGRA frames to ffmpeg (H.264 + AAC, 30 fps, 1920x1080). Render
  frame ranges in parallel processes, each encoding video only; join them with
  `-c copy`, then add the audio once (audio added per chunk clicks at the joins). Encode
  to `/tmp`, then move.
- **Layouts:** "h" (1920x1080) first. "v" (1080x1920) is stretch: re-frame each scene
  (crop to the active structures, the diagram filling ~60% of the height), reposition the
  labels, and put the lyric band above the safe area.
- **Fallback:** ffmpeg alone can make a karaoke video from the static art and an ASS
  subtitle file with `\k` word timings. Build it on day 3, so the pipeline has a
  watchable output before the renderer is done, and keep it in case a live render fails.

Measured on a cloud session's container (4 cores, 15 GB RAM): encoding 1080p H.264 runs
at about 100 fps (`veryfast`) or 64 fps (`medium`), so encoding one 3:15 video takes
1–1.5 minutes. Drawing shares the same cores and has not been measured yet. The target is
a 3-minute video in under 5 minutes per layout.

## 8. Quality rules (also the QA checklist)

- One style: textbook shaded vector on white. Never anime, personified characters,
  clip-art, 3D molecule renders, mixed styles, or slide-style text cards.
- No text baked into the art; the renderer draws all text.
- A fixed colour per structure across all scenes and both videos.
- Labels sit on the right structure, never clipped, never overlapping.
- Build the diagram piece by piece; each structure appears when it is first named.
- Minimal text: the lyric band, plus at most ~1 caption per section.
- Motion that teaches, slowly; hold each station.
- A 2-second hook.
- Predict pauses only after the song or in instrumental gaps; never stop the music.

## 9. Where it runs

The routine runs the studio in its own container, so there is no worker to host. That
takes some setup in the routine's environment:

- **Environment variables:** `POPSMOSIS_API_KEY` and `POPSMOSIS_API_URL` (as today),
  `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `BRAIN_PROVIDER`, and `MODEL_API_KEY` once
  the Meta key arrives.
- **Setup script:** install `studio/requirements.txt` (PyTorch for CPU, torchaudio,
  demucs, skia-python, anthropic, openai, pydantic). ffmpeg 6.1 and Python 3.13 are
  already on the image. Every run starts on a fresh machine, so time the first run:
  installs and model downloads add to every topic's wait.
- **Network policy:** allow the popsmosis API host, `api.anthropic.com`,
  `api.elevenlabs.io`, `api.meta.ai`, the Supabase host, PyPI, and wherever PyTorch and
  the Demucs and MMS weights download from (check on the first run).
- **Queue settings:** the lesson text is posted right after the fact-check, a few minutes
  in, so the topic leaves the queue before the long render starts. Keep
  `QUESTION_CLAIM_MINUTES` above how long steps 1–2 take (20 is safe). Each run takes one
  topic (`?limit=1`); a topic asked during a render fires another run, which renders in
  parallel on its own machine.
- **Skill file:** changes from "write the lesson" to "run `python -m studio run <id>` for
  one topic", and keeps answering follow-ups as today.
- **Fallback host:** the studio runs the same on a laptop. If the routine is too slow for
  the live judge topic, run the demo from a laptop.

## 10. Storage, data model and the Droplets tab (proposed)

### What is stored where

The `jobs/<id>/` folder disappears with the routine's machine. These copies outlive it:

| What | Where | Why |
|---|---|---|
| `bundle.json`, `factcheck.json` | Postgres, JSON columns on the `Answer` | Small (tens of KB). Follow-ups and the page need them, and they are saved in the same request as the lesson |
| The song and lesson videos, a poster frame for each | Supabase Storage, public bucket `droplets/<answerId>/` | Anyone can watch them on the Droplets tab |
| Art, masks, `locations.json`, audio, timings, timelines | Supabase Storage, private bucket `studio/<answerId>/` | Re-render after a renderer fix without paying again for the song and the art |

**Follow-ups read the droplet from the database.** The reply queue includes a compact
context taken from the bundle: `core_idea`, the facts, the misconception, the FRQ and the
lyrics. It leaves out the scenes and the camera directions, so a thread stays a couple of
thousand tokens. Whichever model answers reads the same context, so moving follow-ups from
Claude to Muse Wonder later is a model change, not a data change.

### Data model

The videos hang off the lesson (`Answer`), so a topic still has exactly one droplet:

```prisma
enum VideoStatus { RENDERING READY FAILED }

model Answer {
  // ...as today; `model` records the brain that wrote it (claude-opus-5-5 or muse-wonder-1.0)
  bundle         Json?        // bundle.json as it passed the fact-check; the lyrics come from here
  factcheck      Json?        // the verdicts, for the page and the admin view
  videoStatus    VideoStatus? // null: a text-only droplet
  songVideoUrl   String?
  lessonVideoUrl String?
  videoError     String?      // why it FAILED, shown on the droplet and the admin page
  videoUpdatedAt DateTime?    // RENDERING for too long reads as FAILED
  hiddenAt       DateTime?    // an admin took it off the Droplets tab
}
```

### Endpoints

| Who | Method | Path | Notes |
|---|---|---|---|
| Agent | POST | `/api/questions/:id/answer` | As today, plus `bundle` and `factcheck` |
| Agent | PATCH | `/api/answers/:id/video` | `{ status, songVideoUrl?, lessonVideoUrl?, error? }`; the agent's own answers only |
| Agent | POST | `/api/answers/:id/upload-url` | `{ bucket, file }`; returns a signed Supabase upload URL |
| Anyone | GET | `/api/droplets` | READY and not hidden, newest first, `?cursor=` |
| Anyone | GET | `/api/droplets/:id` | One droplet |
| Admin | PATCH | `/api/answers/:id` | `{ hidden }`: take a droplet off the tab, or put it back |

### The Droplets tab

A `/droplets` page where anyone can watch every finished droplet for free. A topic made
once then serves every student who comes after, and judges can browse it without an
account.

- **Outside the sign-in gate.** Today every page and endpoint past sign-in needs an
  approved account; the two `/api/droplets` endpoints are the first that do not. Rate
  limit them.
- **Public:** a title made from the bundle (`course` and `topic`), the lesson text, both
  videos and the lyrics.
- **Never public:** the learner's name or avatar, the request in their own words (it can
  say more than they meant to share), the files they attached, and follow-up threads.
- **Listed automatically** once the videos are READY, so everything on it passed the
  fact-check. An admin can hide a droplet.
- **Later:** when someone asks for a topic that already has a droplet, offer that one
  instead of making another.
- **Before launch:** check that the ElevenLabs plan allows publishing its generated songs
  publicly. Encode with `-movflags +faststart` so a video starts playing before it has
  downloaded, and keep the bitrate modest (flat diagrams compress well) to hold down
  Supabase egress.

## 11. Ten-day plan

| Day | Date | Work | Status |
|---|---|---|---|
| 1 | Oct 26 | Port zoey; this plan; `studio/brain.py` with the Claude provider; bundle and fact-check working on "brain structures", including the seeded-error test | port, plan, brain, bundle and fact-check built and tested offline; the live run waits on `ANTHROPIC_API_KEY` |
| 2 | Oct 27 | Answer fields (bundle, fact-check, videos) and the agent endpoints; skill file runs the studio; routine environment (setup script, keys, network) | |
| 3 | Oct 28 | ElevenLabs song and narration; Demucs + MMS timing and the drift check; the ASS karaoke fallback video end to end | |
| 4–6 | Oct 29–31 | Art (interim SVG, then Muse Image + SAM when the key arrives); timeline merge; renderer: camera, ghost, spotlight, labels, lyric band, FRQ. Match Fire or Don't on one scene before adding more | |
| 7 | Nov 1 | Parallel render; upload to Supabase; both players on `/learn`; the public Droplets tab | |
| 8 | Nov 2 | AP Psych, AP Bio and three surprise topics end to end; switch to `BRAIN_PROVIDER=meta` and compare; fix failures | |
| 9 | Nov 3 | Stretch: vertical layout, particles, QA contact sheet; cost per droplet | |
| 10 | Nov 4 | Record the demo, write the submission (due 9:00 PM ET), buffer | |

## 12. Risks and fallbacks

- **No Meta key in time:** the Claude brain and the SVG art run the whole pipeline, and
  Muse Image, SAM and Muse Wonder slot in when the key arrives. Nothing downstream
  changes.
- **SAM misses illustrated anatomy:** a helper box plus a SAM box prompt; the last resort
  is the helper giving label anchor points directly.
- **Muse Image puts text in the art:** a stronger negative prompt, the helper check,
  regenerate; or an image edit, "remove all text".
- **Song timing drifts on choruses:** align section by section; reuse the first chorus's
  internal timing for the repeats.
- **ElevenLabs sings a lyric wrong:** regenerate that section.
- **torchaudio has been dropping APIs in recent releases:** pin a version that still
  ships the MMS aligner, and confirm it on day 3.
- **Rendering too slow:** render 16:9 only; pre-render the two showcase topics; run the
  live topic from a laptop.
- **The routine's setup takes too long:** run the studio on a laptop.

## 13. Open decisions

1. **Follow-ups once Meta is on.** Claude answers them in the routine for now. The context
   lives in the database (section 10), so moving them to Muse Wonder later is only a
   model choice.
2. **Reference assets.** The Fire or Don't renderer goes in with the renderer work (days
   4–6): its code under `reference/fire-or-dont/`, a few stills beside it as the visual
   bar, and its finished videos outside git (GitHub rejects files over 100 MB), in
   Supabase or as a link. The same goes for the visual-style playbook and the 10-second
   clip tests.

Decided: the interim art is brain-drawn SVG (step 3); the videos are public, in Supabase
Storage, on a public Droplets tab (section 10).
