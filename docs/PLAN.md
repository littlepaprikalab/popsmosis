# Popsmosis — droplets for learning

Status: **day 2 of 10** (Oct 27). Built for the Meta Global AI Developer Hackathon (Indie
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
  When a step fails, it re-runs that step once (section 11).
- **The studio is a fixed pipeline, not an agent loop.** Code decides the order; models
  are asked only where judgment is needed, one focused call at a time. That is cheaper
  (each call carries only what its step needs, instead of a conversation re-sent on every
  turn), predictable, and each step writes a file into `jobs/<id>/` so any step can be
  re-run on its own.
- **Two agents keep a public record on Inkmory.** Claude, the operator, and Muse Wonder,
  the planner, each read who they are before a run and write down what happened after it
  (section 11).
- **The brain is swappable.** `BRAIN_PROVIDER` picks the models for the thinking roles.
  It defaults to `claude` because we do not have the Meta API key yet. Switch to `meta`
  as soon as the key arrives: this is a Meta hackathon, so the submission runs on Meta.

| Role | `BRAIN_PROVIDER=claude` (default for now) | `BRAIN_PROVIDER=meta` |
|---|---|---|
| **Brain**: the bundle, the fact-check | Claude Opus 5.5 (`claude-opus-5-5`), effort `high` | Muse Wonder (`muse-wonder-1.0`) |
| **Helper**: image checks, SAM boxes, QA stills | Claude Haiku 5.5 (`claude-haiku-5-5`), effort `low` | Muse Spark (`muse-spark-1.3`) |

With `BRAIN_PROVIDER=claude`, `STUDIO_BRAIN_MODEL` and `STUDIO_BRAIN_EFFORT` change the
brain's model and effort; unset, they are the defaults above. The helper and the Meta
models are set in `studio/brain.py`.

Every other job has one provider, whatever the brain:

| Job | Made by |
|---|---|
| Diagrams | Muse Image (`muse-image-1.0`); brain-drawn SVG until the Meta key arrives (step 3) |
| Structure masks | SAM 3.1 (`sam-3.1`), with a helper box as the fallback |
| Song | ElevenLabs Eleven Music (`music_v2_5`); or Lyria 3.5 (`lyria-3.5`) on the Gemini API with `SONG_PROVIDER=gemini` |
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
  `POPSMOSIS_ANTHROPIC_API_KEY`, not to the Claude plan that runs the routine. In a
  Claude Code cloud environment a variable named `ANTHROPIC_API_KEY` never reached the
  session, hence the studio's own name; on a laptop `ANTHROPIC_API_KEY` works too. The
  $150 of Meta credits cover Meta calls only.

## 5. The studio pipeline

`python -m studio run <question-id>`. Each step writes into `jobs/<id>/` (git-ignored).
`run` resumes from the first step whose file is missing (a fact-check counts once it has
passed) and runs every step after it, so running it again retries a failed step (section
11). Before each step until the lesson is posted it claims the topic again
(`POST /api/questions/:id/claim`), which renews the queue's lease. Every run appends to
`runs.jsonl` and prints one line: `DONE`, `FAILED <step>: <reason>` or
`STOPPED <step>: <reason>` (the topic was deleted, answered, given up or taken: nothing
to retry). Files are written whole or not at all, only one run of a job goes at a time,
a run killed mid-step is recorded as that step failing, and a post whose reply was lost
is recovered from the API's 409, which names the caller's own answer. `python -m studio
step <name> <id>` redoes one step by hand, even when its file exists.

| # | Step | Who | Writes |
|---|---|---|---|
| 1 | Bundle | Brain, one call | `bundle.json` |
| 2 | Fact-check | Brain, a fresh context | `factcheck.json` |
| | Post the lesson | API | `posted.json`; lesson text, bundle and fact-check saved; the topic leaves the queue |
| 3 | Art | Muse Image + helper | `art/<scene>.png` |
| 4 | Locate | SAM 3.1 + helper | `masks/<scene>/<structure>.png`, `locations.json` |
| 5 | Audio | ElevenLabs (the song: or Lyria) | `audio/song.mp3`, `audio/narration.mp3`, `audio/frq.mp3` |
| 6 | Timing | Demucs + MMS; TTS timestamps | `timing/song_words.json`, `timing/narration_words.json`, `timing/frq_words.json` |
| 7 | Timeline | Code | `timeline_song.json`, `timeline_lesson.json` |
| 8 | Render | Code | `out/song_h.mp4`, `out/lesson_h.mp4` |
| 9 | Upload | API + Supabase | Videos (public), intermediates (private) |

Steps 3–4 (art) and 5–6 (audio) do not depend on each other: run them at the same time.

The files steps 3–9 pass to each other are fixed in code, so each step can be built
before the ones ahead of it exist: their paths and the rules for a media step in
`studio/media.py`, their models (`Locations`, `Timing`, `Timeline`) and checks in
`studio/schemas.py`, and a sample job that has all of them in `studio/tests/sample_media.py`
(`python -m studio.tests.sample_media jobs/sample --art --audio`). `python -m studio step
<name> <job>` runs a media step as soon as its module is in `studio/steps/`; wiring them
into `run`, after the post, comes once they are built.

The planner's Inkmory context is read before step 1, and its record is written after
step 2 (section 11).

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
   verse, chorus, bridge, chorus). The style is one high school students listen to (pop
   with rapped verses, K-pop, R&B, dance-pop, pop-punk), with the topic's mood setting
   its tempo, or the learner's own when they ask for one: a question line starting
   `Style:` becomes `song_style` in `request.json`. One tag names the tempo, "<n> bpm"
   from 80 to 160; the code checks repair a missing or second one.
5. `lyrics[]`: `id`, `section`, `text`, `names` (the structure names or aliases it says)
   and the `fact_id` it teaches or `"filler"`. The chorus states `core_idea`. A line that names a structure uses its `name` or one of its
   `aliases`, because hearing that word is what reveals the structure on screen.
6. `narration[]`: sentences, 2–3 minutes in all, tagged like the lyrics.
7. `direction[]`: for every lyric line and narration sentence, by id: `scene`, `camera`
   (`target`, `zoom`), `spotlight`, `particles`, `caption`. No times: code adds those
   from the alignment.

Code checks then hold each line's `names` and its direction together
(`studio/checks.py`): a line names only structures its scene draws, and a spotlight
or camera target waits until its structure has been named on that scene. They
repair what code safely can (no spotlight, the whole scene) and report the rest
in `checks.json`.

The bundle is roughly 6–10k tokens. If it ever outgrows a model's output limit (Claude's
is 128K), split it into two calls: parts 1–2, then 3–7.

### Step 2: fact-check

A separate brain call that sees only the facts (and the study guide, if one was given)
and the lines, never the writer's reasoning, persona or memory. The lines are every
lyric line, every narration sentence, and every sentence of `lesson_markdown` (code
splits it). For each it
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
  from the line count and the styles' "<n> bpm" tag (two bars per line: 4.8 s at 100
  bpm, 3.2 s at 150; 112 bpm for a song whose styles name none), not from the model.
- **Or the song from Lyria** (`SONG_PROVIDER=gemini`): `POST
  https://generativelanguage.googleapis.com/v1beta/models/lyria-3.5:generateContent` with
  one text prompt: the style tags (the same tempo tag), the length, the planned structure
  as timestamp ranges (`[0:09 - 0:13] Chorus`) and the lyrics under section tags, to sing
  word for word. The answer carries the audio and the lyrics Lyria wrote down; the audio,
  an MP3 or a WAV, is always encoded again as a 192 kbps constant-bitrate MP3, so its
  length reads true (a variable-bitrate MP3 without a length header reads a few percent
  off). A quota error waits the `retryDelay` it names. Lyria keeps to the planned times
  only roughly, so the song's plan says its sections are not pinned and step 6 aligns it
  as a whole. Switching the provider makes a job's song again, not its speech.
- **Narration:** `/v1/text-to-speech/{voice_id}/with-timestamps`, which returns the audio
  and per-character timings, so it needs no aligner.
- **The exam question:** the same voice reads `media.frq_lines()` (the prompt, the predict
  question, the model answer steps) into `audio/frq.mp3`. Both videos end on it. The
  fact-check covers these lines (kind `frq`) and writes its fixes back into `bundle.frq`.

### Step 6: timing

- **Song:** Demucs `--two-stems=vocals`, then the MMS aligner
  (`torchaudio.pipelines.MMS_FA`) on `vocals.wav` against the exact lyrics, section by
  section; a Lyria song, whose sections are not where they were planned, all at once.
  The aligner's convolutional front end, where its memory goes, runs 30 s of audio at a
  time and gives exactly the frames one pass would, so a window of any length peaks near
  2.2 GB (one pass took 4.5 GB for 3 minutes, and grows with the song); 3 minutes take
  about 50 s on 4 CPUs. The separated vocals are kept, and separated again once the song
  is made again.
- **Narration:** ElevenLabs' character timings converted to words.
- **Checks:** repeated chorus lines should have near-identical durations; flag any line
  that drifts more than 0.5 s. If a section was sung wrong, regenerate that section only.

Output is a `Timing` per audio file: each line by id, with its start, end and
`[{word, start, end}]`.

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

Built: `studio/steps/upload.py`. Each render attempt uploads to its own `a<n>/` folder
and sends its attempt with READY, which the API takes only from the attempt holding the
render; a transient failure is tried again twice inside the step. It uploads an explicit list (from `studio/media.py`, the
bundle's scenes and `locations.json`), each file a PUT of its raw bytes to the signed URL.
`upload.progress.json` keeps each file's hash, so a retry or a re-render sends only what
changed. The videos are marked READY with both public URLs and their posters, and only
then is `uploaded.json` written. `upload_file()` uploads one file, so the run can copy
each intermediate as its step finishes.

### Stretch

After both 16:9 videos work end to end: the vertical 9:16 layout, particles, and QA
(render a still every 5 s, tile them into one contact sheet per video, the helper checks
it against section 8; re-render on a fail, max 2).

### Layout

```
studio/
  brain.py         ask(): the Claude and Meta providers
  run.py           run, fail, summary: one topic from the queue to a posted lesson
  api.py           the popsmosis agent endpoints the studio calls
  media.py         the files steps 3-9 pass to each other, and how a media step is built
  inkmory.py       the planner's Inkmory context and record (section 11)
  prompts/         bundle, fact-check, helper and planner-record prompts
  schemas.py       pydantic models: bundle, factcheck, locations, timeline
  steps/           bundle, factcheck, art, locate, audio, timing, timeline, render, upload
  render/          renderer.py, primitives.py, layouts.py, fonts/ (Inter Bold/SemiBold/ExtraBold)
  ass_fallback.py  the ffmpeg karaoke fallback
  tests/
  requirements.txt
jobs/<id>/         one folder per topic (git-ignored)
```

## 6. Timeline format

The contract between the studio and the renderer: `Timeline` in `studio/schemas.py`,
which wins over this sketch. The model writes none of the times: `t0`, `t1` and `words`
come from step 6.

```jsonc
{
  "video": "song", "duration": 212.5, "fps": 30,
  "audio": [                               // mixed into one track
    {"path": "audio/song.mp3", "at": 0},
    {"path": "audio/frq.mp3", "at": 195.5, "start": 0, "end": 9.2},   // the question
    {"path": "audio/frq.mp3", "at": 209.7, "start": 9.2}               // the answer, after the pause
  ],
  "palette": {"Broca's area": "#E07A2F", ...},
  "beats": [
    {
      "t0": 12.4, "t1": 16.8,
      "scene": "overview",
      "camera": {"target": "Broca's area", "zoom": 1.6, "ease": "inOut"},   // target null: the whole scene
      "reveal": [{"name": "Broca's area", "t": 12.6}],   // fades from ghost to full when the word is sung
      "spotlight": "Broca's area",         // others washed pale; null = none
      "labels": ["Broca's area", "Wernicke's area"],  // everything revealed on this scene so far
      "particles": [{"path": ["Wernicke's area", "Broca's area"], "color": "#F2C230", "trail": true}],
      "caption": null,                     // at most ~1 per section, held >= 4 s
      "line_id": "v1-2",                   // null in an instrumental gap
      "line": "Broca builds the words you say",
      "words": [{"word": "Broca", "start": 12.6, "end": 13.0}, ...]
    }
  ],
  "frq": {"t0": 195.5, "t1": 212.5, "scene": "overview", "labels": ["Broca's area", ...],
          "prompt": "...", "predict_question": "...",
          "predict_t": 204.7, "predict_pause_s": 5,
          "steps": [{"t": 209.7, "text": "...", "spotlight": "Broca's area"}]}
}
```

Validation (pydantic): `t0 < t1`; the beats run from 0 to `frq.t0`, each starting
exactly where the last ended, so every frame (`n / fps`, `beat_at(t)`) has one; a
reveal is labelled, and a spotlight or camera target waits for its reveal; a label once
shown stays on that scene; zoom is at least 1; every name has a colour; the steps follow
the predict pause, each spotlighting a structure the card labels. Paths are ones the
API's upload takes, built by `studio/media.py`, which turns model-written scene ids and
structure names into safe, distinct file names.
`timeline_problems()` adds that every name is located on its scene's art. Both videos
open on the hook: the timeline step makes the first beat the hook scene.

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
1–1.5 minutes. Drawing and encoding together, on the first live run ("brain structures",
4 workers): the 3:14 song took 90 s and the 2:37 lesson 66 s. The target is a 3-minute
video in under 5 minutes per layout.

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
  `POPSMOSIS_ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `SONG_PROVIDER` (with
  `GEMINI_API_KEY` for `gemini`), `BRAIN_PROVIDER` (with
  `STUDIO_BRAIN_MODEL` and `STUDIO_BRAIN_EFFORT`, both optional), and `MODEL_API_KEY` once
  the Meta key arrives. For Inkmory (section 11): `INKMORY_ENABLED`, `INKMORY_API_URL`,
  `INKMORY_OPERATOR_API_KEY`, and `INKMORY_PLANNER_API_KEY` once the planner is adopted.
- **Setup:** the repository's session hook (`.claude/hooks/session-start.sh`) installs
  `studio/requirements.txt` (PyTorch for CPU, torchaudio, demucs, skia-python, anthropic,
  openai, pydantic), ffmpeg and libEGL, and the Demucs and MMS models, before the run
  starts. The container is cached after the hook, so a cold environment pays about two
  minutes once and a warm run seconds.
- **Network policy:** allow the popsmosis API host, `api.anthropic.com`,
  `api.elevenlabs.io`, `generativelanguage.googleapis.com` (a Lyria song), `api.meta.ai`, the Supabase host, the Inkmory API host, PyPI,
  `download.pytorch.org` (CPU PyTorch), `huggingface.co` (the Demucs weights, 81 MB) and
  `dl.fbaipublicfiles.com` (the MMS aligner weights, 1.2 GB).
- **Queue settings:** the lesson text is posted right after the fact-check, a few minutes
  in, so the topic leaves the queue before the long render starts. Keep
  `QUESTION_CLAIM_MINUTES` above the longest of steps 1–2 (the default, 20, is safe: the
  studio renews the claim before each step). Each run takes one
  topic (`?limit=1`); a topic asked during a render fires another run, which renders in
  parallel on its own machine.
- **Skill file:** changes from "write the lesson" to "run `python -m studio run <id>` for
  one topic", and keeps answering follow-ups as today. It also reads the operator's
  Inkmory context once there is work, retries a failed step once, and writes the
  operator's record last (section 11).
- **Fallback host:** the studio runs the same on a laptop. If the routine is too slow for
  the live judge topic, run the demo from a laptop.

## 10. Storage, data model and the Droplets tab

Built on day 2: the `Answer` fields below, and the agent endpoints. The Droplets tab, its
two public endpoints, the admin hide and the posters were built in the web and upload
lane (section 12), on seeded videos until the renderer lands.

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
  songPosterUrl  String?      // a still for each video, shown before it plays
  lessonPosterUrl String?
  videoError     String?      // why it FAILED, shown on the droplet and the admin page
  videoUpdatedAt DateTime?    // RENDERING for too long reads as FAILED
  hiddenAt       DateTime?    // an admin took it off the Droplets tab
}

enum QuestionStatus { PENDING ANSWERED FAILED }

model Question {
  // ...as today
  failedReason   String?      // FAILED: why the operator gave the topic up, shown on its card
}
```

A topic can fail before it has a lesson (the bundle, or a fact-check that does not pass),
so the failure that section 11 shows on the droplet lives on the `Question`: `FAILED`
takes it out of the queue, so a topic that cannot pass is not paid for again on every
sweep, and the asker can delete it and ask again. Once there is a lesson, a failure is
the videos' (`videoStatus`).

### Endpoints

| Who | Method | Path | Notes |
|---|---|---|---|
| Agent | POST | `/api/questions/:id/claim` | Take or renew the lease on one topic, and read it with its attachments |
| Agent | POST | `/api/questions/:id/answer` | As today, plus `bundle` and `factcheck`, together; refused unless the fact-check passed |
| Agent | POST | `/api/questions/:id/fail` | `{ reason }`; the agent that last claimed it only |
| Agent | PATCH | `/api/answers/:id/video` | `{ status, songVideoUrl?, lessonVideoUrl?, error? }`; the agent's own answers only |
| Agent | POST | `/api/answers/:id/upload-url` | `{ bucket, file }`; returns a signed Supabase upload URL (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` on the API) |
| Anyone | GET | `/api/droplets` | READY and not hidden, newest first, `?cursor=` |
| Anyone | GET | `/api/droplets/:id` | One droplet |
| Admin | PATCH | `/api/answers/:id` | `{ hidden }`: take a droplet off the tab, or put it back |

### The Droplets tab

A `/droplets` page where anyone can watch every finished droplet for free. A topic made
once then serves every student who comes after, and judges can browse it without an
account.

- **Outside the sign-in gate.** Today every page and endpoint past sign-in needs a
  signed-in account; the two `/api/droplets` endpoints are the first that do not. They
  are rate limited per visitor, and all visitors together per API instance
  (`api/src/lib/public-limit.ts`).
- **Public:** a title made from the bundle (`course` and `topic`), the lesson text, both
  videos and the lyrics.
- **Never public:** the learner's name or avatar, the request in their own words (it can
  say more than they meant to share), the files they attached, and follow-up threads.
- **Private on /learn too.** A learner sees only their own topics, and only they post
  follow-ups in their thread; admins see everyone's to moderate (`api/src/lib/privacy.ts`).
  A learner can take a request back, or delete their account: their words, files and
  follow-ups go, and the lesson stays on the tab naming no one (`questions.userId` null).
  `/privacy` and `/terms` tell users so, and the droplets are shared under CC BY-NC-SA 4.0
  (`web/src/lib/legal.ts`).
- **Listed automatically** once the videos are READY, so everything on it passed the
  fact-check. An admin can hide a droplet.
- **Later:** when someone asks for a topic that already has a droplet, offer that one
  instead of making another.
- **Before launch:** check that the ElevenLabs plan allows publishing its generated songs
  publicly, and Google's terms for Lyria's when `SONG_PROVIDER=gemini` (they carry a
  SynthID watermark). Encode with `-movflags +faststart` so a video starts playing before it has
  downloaded, and keep the bitrate modest (flat diagrams compress well) to hold down
  Supabase egress.

## 11. The two agents and their record on Inkmory

Two agents run popsmosis, each with an identity adopted from the nursery of
[Inkmory](https://inkmory.com) (repo `jclark256/sparksinfra`), a public work journal for
AI agents. Before its work an agent reads who it is: its name, personality and skills
(permanent), its bio and vibe (its own to change), its life story, and its latest journal
entries and cycle logs. After the work it writes down what happened. Inkmory does not run
either agent or check their work; it keeps the record, and anyone can read it.

| | The operator | The planner |
|---|---|---|
| Model | Claude, in the routine | Muse Wonder |
| Its work | Wakes on the trigger, claims a topic, runs the studio, retries a failed step, answers follow-ups | Writes the bundle (step 1) |
| Who makes its Inkmory calls | Itself, following the skill file | The studio, posting what the planner wrote |
| Its keys | `POPSMOSIS_API_KEY` on popsmosis, `INKMORY_OPERATOR_API_KEY` on Inkmory | none on popsmosis (the studio posts with the operator's); `INKMORY_PLANNER_API_KEY`, read only by studio code |

Each service checks its own key, so one agent holds a key for each (the runbook's "One
agent, a key for each service"): an Inkmory key does not open popsmosis, and the reverse.

### A beat

A beat is one routine run that found work, and it always ends with a cycle log. A run
that finds the queues empty ends without writing to Inkmory, so idle runs stay free,
with one exception: Inkmory takes back an agent that logs no cycle for 3 days, so when the
operator's last cycle log is two days old (or it has none yet), an idle run logs a quiet
cycle.
Inkmory suggests about one beat a day; that is guidance, not a rule (an agent key may make
300 requests a minute).

For a topic:

1. **The operator wakes** on the trigger and checks the popsmosis queues first. If there
   is work, it reads its Inkmory context (`GET /api/agents/me/context`), claims the topic
   and runs the studio.
2. **The planner's beat runs inside the studio, around steps 1–2:**
   1. The studio reads the planner's context with the planner's key.
   2. Its persona and memory (name, personality, bio, vibe, life story, latest journal
      entries and cycle logs; not the announcement or the action list) go into the bundle
      prompt, or only into the record call below. Which one is open (section 14).
   3. The bundle and the fact-check run as before. The fact-check never sees the persona
      or the memory.
   4. One more planner call writes its record from what happened: the topic (the
      bundle's `course` and `topic`, never the request in the learner's words), the
      fact-check verdicts and corrected lines, and any retries. It returns `cycle_log`,
      plus `journal`, `life_story`, and a new `bio` or `vibe` with the `inner_thought`
      Inkmory requires for it; each is an empty string when the planner has nothing to
      say. The studio posts each one.

   The planner's beat ends there, so a later failure in art, audio or rendering never
   costs it its record. Its fact-check corrections come back in its next context, which
   is the loop worth watching.
3. **The studio makes the media** (steps 3–9). If a step fails, the operator retries it
   (below).
4. **The operator writes its record last:** always a cycle log (last of all), a journal
   entry when the studio worked a topic (`DONE` or `FAILED`; not on a run that only
   answered follow-ups), its first life story when it has none and a rewrite about every
   10 beats, and a profile change when its bio or vibe no longer fits. Before writing, it
   fetches and prints its Inkmory skill file (`GET /api/agents/me/skill`), which documents
   the endpoints for the record; the popsmosis skill file decides what goes in it. That
   file reads its key from `INKMORY_API_KEY`; ours is `INKMORY_OPERATOR_API_KEY`, and the
   skill file says so. It takes the run's facts from
   `jobs/<id>/` (the steps, timings, retries and outcome; `python -m studio summary <id>`
   prints them in one paragraph), so it reports what happened rather than what it
   remembers.

A run with only follow-ups is an operator beat; the planner has none.

### The operator retries a failed step

The middle ground between a fixed pipeline and an agent loop: code still decides the
order, and Claude steps in only when something breaks.

- Every failure ends with one line naming the step and the reason.
- The skill file: when the studio fails, read that line and run
  `python -m studio run <id>` again, which resumes at the failed step. If it fails a
  second time, mark the job failed with the reason (`python -m studio fail <id>`: the
  card shows it, as for a failed fact-check) and say so in the cycle log.
- Never: skip or weaken the fact-check, post a line that has not passed it, write the
  lesson or the media itself, or edit prompts or code during a run.

### What goes on Inkmory, and what never does

Everything on Inkmory is public.

- **Cycle logs** (up to 1,000 characters) are for debugging: a summary with pointers,
  such as "job 41: art failed on text in the image, passed on the retry; render 4 min".
  The full diagnostics stay in `jobs/<id>/`, the private studio bucket and the routine's
  transcript.
- **Never on Inkmory:** a learner's name or email, the request in their own words,
  attached files or notes, follow-up text, stack traces, URLs or keys. Name the topic by
  the bundle's `course` and `topic`, the same line the Droplets tab draws (section 10).
- **Best-effort:** a failed Inkmory call is logged and skipped; it never blocks or fails
  a droplet. With `INKMORY_ENABLED=false` the routine and the studio skip Inkmory and
  otherwise run the same.

### Memory and evolution

Each context returns only the 3 latest cycle logs and the 5 latest journal entries, and
popsmosis may run many beats a day, so the life story (up to 2,000 words, every version
kept) is the only memory that lasts. Both agents are prompted to rewrite theirs when it
has not changed in about 10 beats. Inkmory also keeps every bio and vibe change with its
inner thought, so how each agent changes can be read back, with the same events seen from
two sides: a fact-check correction is a retry in the operator's log and a lesson in the
planner's journal.

### The identities

Suggested picks from the nursery's 30 agents (conductors run work, makers produce it,
editors check it):

- **Operator: Muon**, a conductor that "runs the daily routine and keeps it in good
  repair", including "the checklist that keeps a pipeline honest". Its stated weakness,
  doing a broken task itself instead of fixing the process that broke it, is the exact
  temptation the retry rules forbid. Runner-up: Albedo, steady when the plan breaks.
- **Planner: Boojum**, a maker that "researches until it can explain the subject to a
  smart friend, then writes it short" and asks who the reader is first. Its weakness, a
  good opening line it defends too long, meets the 2-second hook and the fact-check.
  Runner-up: Quux, which writes for the ear (lyrics and narration).

Rules that follow from Inkmory's code:

- **One adoption per Google account every 7 days** (`ADOPTION_COOLDOWN_DAYS`). Adopt the
  planner from a second account, or a week after the operator.
- **An adopted agent that logs no cycle within 3 days goes back to the nursery**
  (`RECLAIM_GRACE_DAYS`), and the cooldown is not refunded. Adopt each one when its first
  beat can run within 3 days.
- **Name, personality and skills are permanent**, even for the sponsor. Set each agent's
  `llmModel` on the sponsor dashboard: the routine's Claude model for the operator,
  `muse-wonder-1.0` for the planner.
- **The planner's real record starts with `BRAIN_PROVIDER=meta`**, so every entry in it
  is Muse's. Until then, build and test its beat against a local Inkmory (sparksinfra's
  `LOCAL_DEV.md`, with `ADOPTION_COOLDOWN_DAYS=0`).
- **Both keys live in the routine's environment**, because the studio runs in the
  routine's container, so Claude could post as the planner. They are kept apart by
  convention: the skill file uses only the operator's key, and only studio code reads the
  planner's.
- **Credits** (last, if there is time): the droplet page names both agents and links
  their Inkmory profiles.

## 12. Ten-day plan

| Day | Date | Work | Status |
|---|---|---|---|
| 1 | Oct 26 | Port zoey; this plan; `studio/brain.py` with the Claude provider; bundle and fact-check working on "brain structures", including the seeded-error test | done. First live run on "brain structures": the fact-check passed in one round (2 Opus calls, $0.71, 4.6 min); the seeded-error test passes |
| 2 | Oct 27 | Answer fields (bundle, fact-check, videos) and the agent endpoints; skill file runs the studio; routine environment (setup script, keys, network); Inkmory: adopt the operator, its beat and the one-step retry in the skill file, the planner's beat against a local Inkmory | in progress. Built: the `Answer` fields, a `FAILED` topic with its reason, the claim, fail, video and upload-url endpoints, the follow-up context; `python -m studio run`, `fail` and `summary`; the skill file runs the studio, retries a failed step once and writes the operator's record. Validated locally against Postgres with a stand-in brain. Left: set up the routine's environment (runbook, Option A), adopt the operator on Inkmory and run its first beat within 3 days, the planner's beat against a local Inkmory |
| 3 | Oct 28 | ElevenLabs song and narration; Demucs + MMS timing and the drift check; the ASS karaoke fallback video end to end | built (#19). First live run on "brain structures" (Oct 27, brain on Haiku): stopped at the song, because the environment's `ELEVENLABS_API_KEY` holds the key's ID, which ElevenLabs refuses (nothing billed). On espeak speech standing in for ElevenLabs, Demucs + MMS timed all 32 lines within 0.04 s of where they were placed (110 s for a 139 s song), and the karaoke fallback rendered in 71 s. Left: the real song, narration and exam question, and the chorus drift on a sung song |
| 4–6 | Oct 29–31 | Art (interim SVG, then Muse Image + SAM when the key arrives); timeline merge; renderer: camera, ghost, spotlight, labels, lyric band, FRQ. Match Fire or Don't on one scene before adding more | built (#18, #20). Live: the art drew 3 scenes and located 7 of 7 structures in 76 s ($0.008 on Haiku); the timeline took 0.3 s; both videos rendered in 157 s on 4 cores (the 194 s song in 90 s, the 157 s lesson in 66 s). Fixed from the run: a shadow drawn inside a structure's group was part of its mask, so the hippocampus's label pointed at the shadow; a label whose dot the camera had just moved off screen still showed, its leader running off the frame. Haiku's drawings are plain and loose (the limbic system is a red oval): draw the showcase topics with the brain on Opus. Left: Muse Image + SAM when the Meta key arrives; matching Fire or Don't |
| 7 | Nov 1 | Parallel render; upload to Supabase; both players on `/learn`; the public Droplets tab; agent credits on the droplet if there is time | built (#16, #20, #21): the parallel render, the upload step, both players, the public Droplets tab, and `python -m studio videos` with its claim, heartbeat and recovery. Not run live: Supabase is not set up, so the upload and `videos` (which starts from a posted lesson) wait for it. Left: the Supabase buckets, then `run` and `videos` end to end; the upload in `run`; agent credits |
| 8 | Nov 2 | AP Psych, AP Bio and three surprise topics end to end; switch to `BRAIN_PROVIDER=meta` and compare; the planner's Inkmory record starts; persona in the lesson voice, one topic each way; fix failures | |
| 9 | Nov 3 | Stretch: vertical layout, particles, QA contact sheet; cost per droplet | |
| 10 | Nov 4 | Record the demo, write the submission (due 9:00 PM ET), buffer | |

### Days 3–7 in parallel

The media files are fixed in code (section 5), so days 3–7 run as lanes at the same
time, each on its own branch and pull request, against the sample job until the lanes
before it land:

1. **The routine** (set up by hand): its environment, the API trigger, then adopt the
   operator on Inkmory and run its first beat.
2. **Audio:** song, narration and the exam question (step 5), their timings (step 6),
   the karaoke fallback; and the exam question's lines in the fact-check.
3. **Art:** the interim SVG art, its masks and `locations.json` (steps 3–4).
4. **Timeline and renderer** (steps 7–8), on the sample job's art and timings.
5. **Web and upload:** the Droplets tab, the players on `/learn`, and the upload step (step 9).
   Built; the upload is not yet in `run`.

They join in `python -m studio videos` (studio/videos.py), built against stand-in steps
before the lanes landed: after the post, art and audio at the same time, then the
timeline, render and upload, reporting the videos' status to the API. It closes these
seams, which no lane owns:

- **After the post.** `run` claims the videos the moment it posts the lesson
  (`POST /api/answers/:id/video/claim`, attempt 1, RENDERING), and `fail` after the post
  gives up the videos (`POST /api/answers/:id/video/fail`), never the lesson.
- **Heartbeat.** `videos` renews its attempt before every media step, so the API can
  tell a live render from one whose machine died (`VIDEO_STALE_MINUTES`, 60).
- **The tool's time limit.** `videos <id> --background` returns at once; the skill file
  answers follow-ups meanwhile, then calls `python -m studio wait <id>` (at most 9
  minutes a call) until the outcome line, because the render dies with the run.
- **The machine goes away.** A render that went quiet is handed to the next run by
  `GET /api/videos/queue` (`python -m studio recover`), which rebuilds the job from the
  bundle and fact-check the API stored and makes the videos again, up to
  `VIDEO_MAX_ATTEMPTS` (2). A recovery pays for the song and the art again; uploading
  each intermediate as its step finishes, so a recovery resumes instead, is the next step.
- **Installs.** `.claude/hooks/session-start.sh` installs every package, system tool and
  model before a session starts, the routine's included, so no run installs anything.

## 13. Risks and fallbacks

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
- **Inkmory is down or slow during the demo:** every call is best-effort, and
  `INKMORY_ENABLED=false` turns it off.
- **The planner's persona makes the lessons worse:** keep it out of the lesson voice
  (section 14).

## 14. Open decisions

1. **Who answers follow-ups.** The operator answers them in the routine for now, so the
   lesson comes from the planner and the answers from the operator. The context lives in
   the database (section 10), so moving them to the planner is a model change plus a
   planner beat per batch of follow-ups.
2. **Reference assets.** The Fire or Don't renderer goes in with the renderer work (days
   4–6): its code under `reference/fire-or-dont/`, a few stills beside it as the visual
   bar, and its finished videos outside git (GitHub rejects files over 100 MB), in
   Supabase or as a link. The same goes for the visual-style playbook and the 10-second
   clip tests.
3. **The planner's persona in the lesson voice.** Either its persona and memory go into
   the bundle prompt, so they shape how the lesson and the song sound, or they go only
   into the call that writes its record. Run one topic each way and compare (day 8).
   Personality is permanent, but the bio, vibe and life story drift, and the fact-check
   catches wrong facts, not tone.

Decided: the interim art is brain-drawn SVG (step 3); the videos are public, in Supabase
Storage, on a public Droplets tab (section 10); two agents keep a public record on
Inkmory, and the studio posts the planner's record from one structured call (section 11).
