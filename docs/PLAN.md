# Popsmosis — droplets for learning

Status: **day 3 of 10** (Oct 28). Built for the Meta Global AI Developer Hackathon (Indie
track); the submission is due Nov 4, 9:00 PM ET. The zoey port is done (below); this is
the plan the rest of the build follows, and what is left of it is listed at the end of
section 12. Where the code and this document differ, the code wins; the README lists the
endpoints as built.

## 1. What we are building

1. A web app with **Google sign-in**.
2. A signed-in user asks for a **topic** to learn: "ATP synthesis", "the French
   Revolution", or a fuller ask ("ATP synthesis for my AP Bio exam"), optionally with a
   study guide or a photo of their notes.
3. The API stores the request and **fires the teacher's Claude routine** through its API
   trigger, so a run starts within seconds.
4. The routine runs the **studio**, a Python pipeline in this repo, on the topic. The
   studio writes the lesson, fact-checks every line, then makes two videos from the same
   plan and the same diagrams. They do different jobs:
   - **Lesson video**: explains. The narration walks through every step and every part,
     ending on the core idea.
   - **Song video**: makes it stick. A song the learner replays, carrying the 3–5 things
     to remember in rhyme and a chant-like chorus, the diagram labels synced to the
     vocals.
5. The page shows the droplet: both videos when they finish rendering, with the lesson
   text under them, updating in place without a reload. Until then the card shows the
   droplet's bill of materials filling in, step by step, as its progress; the text waits
   for the videos at most 20 minutes after it is posted (README, "While a droplet is made").
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
- **The brain is swappable.** An admin picks the default brain on `/admin/studio`: Claude
  Opus 5.5 (the default for now), Muse Wonder 1.0 or Muse Spark 1.3. Switch to a Muse once
  the key is in the routine's environment: this is a Meta hackathon, so the submission runs
  on Meta. The environment holds only the keys. Who paints the art goes by the brain's
  provider, set there too: by default Gemini 3.1 Flash Image for Claude and Muse Image for
  Muse, SAM 3.1 finding the structures in either; Claude can instead draw it as SVG.

| Role | Claude (default for now) | Muse |
|---|---|---|
| **Brain**: the bundle, the fact-check | Claude Opus 5.5 (`claude-opus-5-5`), effort `high` (set on `/admin/studio`) | Muse Wonder (`muse-wonder-1.0`) or Muse Spark (`muse-spark-1.3`, $1.25 / $4.25 per million tokens, to vet a change) |
| **Helper**: image checks, SAM boxes, QA stills | Claude Sonnet 5.5 (`claude-sonnet-5-5`), effort `low` | Muse Spark (`muse-spark-1.3`) |

The helper's models are set in `studio/brain.py`. An admin can also send one topic to its own
brain on `/learn` (Claude, Muse Wonder or Muse Spark), which wins over the default for that
topic, the painter its provider's: README, "One topic on another brain".

The other jobs:

| Job | Made by |
|---|---|
| Diagrams | Gemini 3.1 Flash Image (`gemini-3.1-flash-image`) with `claude`, Muse Image (`muse-image-1.0`) with `meta`, by default; or brain-drawn SVG with `claude` (`/admin/studio`; step 3) |
| Structure masks | SAM 3.1 (`sam-3.1`) on the painted art, with a helper box as the fallback; the SVG art's own groups |
| Song | ElevenLabs Eleven Music (`music_v2_5`); or Lyria 3.5 (`lyria-3.5`) on the Gemini API, picked on `/admin/studio` |
| Narration | ElevenLabs text-to-speech with timestamps |
| Word timing (song) | Demucs (vocals) + Meta's MMS forced aligner, both open source, run locally |
| Timeline | Code |
| Video | Code: skia-python draws, ffmpeg encodes |

Prices per million tokens (input / output): Opus 5.5 $4 / $20, the same as Muse Wonder;
Sonnet 5.5, the helper, $2 / $10. Muse Image is $0.01 per image, SAM 3.1 $2.50 per 1,000 images.

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
| 3 | Art | Gemini 3.1 Flash Image or Muse Image + helper; or the brain's SVG (`/admin/studio`) | `art/<scene>.png` |
| 4 | Locate | SAM 3.1 + helper on painted art; nothing to do on the SVG art | `masks/<scene>/<structure>.png`, `locations.json` |
| 5 | Audio | ElevenLabs (the song: or Lyria) | `audio/song.mp3`, `audio/narration.mp3` |
| 6 | Timing | Demucs + MMS; TTS timestamps | `timing/song_words.json`, `timing/narration_words.json` |
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
any lines the fact-check changed (plus one per scene for the SVG art, with the Claude
brain). The helper is called per image and per fallback.

### Step 1: bundle

One structured-output call returns everything that can be written from the topic alone.
The fields are in this order on purpose: the model writes in field order, so the later
parts build on the facts it has already written.

1. `course`, `topic`, `audience` ("high school" unless the learner's request names
   someone else; it sets the level of everything after it), `core_idea`, `facts[3-4]`
   (`id`, `text`, `source_quote` when a study guide was given), `misconception`.
2. `scenes[3-6]`: the hook (where it can, the topic in the learner's world), overview,
   one or two zoomed details; and, when they help, the analogy drawn on its own (its parts
   in the colours of what they stand for) and a detail showing what happens when a part
   fails. Each has `id`,
   `image_prompt`, `structures[]` (`name`, `aliases[]`, `sam_phrase`, `color_hex`) and
   `stage_of`. One fixed colour per structure for the whole job. A process whose parts move
   or change shape from stage to stage (mitosis, the phases of the Moon) takes a scene per
   stage, 2 to 5 of them, each with `stage_of` naming the process, all opening with the same
   framing sentence; then up to 8 scenes in all.
3. `lesson_markdown`: the text droplet. The skill file's "How to teach" rules now live in
   this prompt.
4. `music`: `positive_styles`, `negative_styles`, and the section order (intro, verse,
   pre-chorus, chorus, verse, pre-chorus, bridge, chorus, outro). The style is one the audience listens to (for high
   school: pop with rapped verses, K-pop, R&B, dance-pop, pop-punk), with the topic's
   mood setting its tempo, or the learner's own when they ask for one: a question line
   starting `Style:` becomes `song_style` in `request.json`. One tag names the tempo,
   "<n> bpm" from 80 to 160; the code checks repair a missing or second one.
5. `lyrics[]`: `id`, `section`, `text`, `names` (the structure names or aliases it says)
   and the `fact_id` it teaches or `"filler"`. The song is for memory, not a sung lesson:
   3–5 ideas, rhyming couplets, a story through the steps, a title line that opens the
   song and comes back in the chorus, and a chorus that chants `core_idea`; the bridge
   sings what happens when a part fails. A line need not name a structure; one that does
   uses its `name` or one of its `aliases`, because hearing that word is what reveals the
   structure on screen.
6. `narration[]`: sentences, 2–3 minutes in all, tagged like the lyrics. It explains
   what the song only names: every fact, step by step, and every structure drawn.
7. `direction[]`: for every lyric line and narration sentence, by id: `scene`, `camera`
   (`target`, `zoom`), `spotlight`, `particles`, `carries` (what the particles are: "H⁺",
   "e⁻", "glucose"; "" for none), `caption`. No times: code adds those
   from the alignment. Then `builds_up`: what piles up in which space as the lesson goes
   (`[{"carries": "H⁺", "region": "Thylakoid lumen"}]`), which the video fills and empties,
   `spins`: what turns while particles drive it (`[{"structure": "ATP synthase"}]`), and
   `blocks`: what stops the particles reaching it (`[{"structure": "Herbicide"}]`). Each
   direction line may also name a `glow`: a part an outside cause switches on.
8. `tags`: `song[]` and `lesson[]`, 6–10 hashtags for each video, the way a TikTok is
   tagged, so the Droplets tab can be searched by them: the topic and its key terms first,
   then the video's own (the song's genre, "#studysong"; "#explained"). Last, so they are
   written knowing the whole plan. Code cleans them to one lowercase word each and never
   fails a bundle over one; the fact-check (step 2) passes or drops each one (README,
   "Tags").

Code checks then hold each line's `names` and its direction together
(`studio/checks.py`): a line names only structures its scene draws, and a spotlight
or camera target waits until its structure has been named on that scene. They
repair what code safely can (no spotlight, the whole scene) and report the rest
in `checks.json`.

The bundle is roughly 6–10k tokens. If it ever outgrows a model's output limit (Claude's
is 128K), split it into two calls: parts 1–2, then 3–7. Claude's structured outputs compile the schema into a grammar
with a size limit, and the bundle's schema outgrew it on Oct 28 (with `direction` and
`tags`): a schema the API refuses for that is asked for in the prompt instead, and the
answer validated as every answer is (`studio/brain.py`).

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

The same call checks each video's hashtags and its marked motions ("H⁺ travels from
Stroma to Cytochrome b6f to Thylakoid lumen": what a line's particles carry and their
route), each once however many lines show it. Neither is ever rewritten or fails the job:
a tag that fails is dropped, and a motion that fails is left out of the video. Motions
add about $0.02 a lesson.

There is no teacher approval step: the fact-check is the gate.

The same call checks the tags, by their own rules (`studio/prompts/factcheck.md`, "Tags"):
SUPPORTED when a tag names something the facts cover, FILLER when it names only the topic,
a subject containing it, the course or the video itself (with `about`, the course, topic
and song style, sent for the tags alone), UNSUPPORTED otherwise. A tag that fails, or gets
no verdict in 3 rounds, is dropped from both videos, never rewritten, and never fails the
job; the posted bundle carries only the tags that passed. They add a few hundred tokens to
the call, not a call of their own.

Regression test: "Every electron hand-off pumps a proton" must be flagged (only
cytochrome b6f pumps).

### Step 3: art

**Painted, by Gemini 3.1 Flash Image or Muse Image** (`/admin/studio`; Gemini by default with `claude`, Muse Image with `meta`), every scene at the same time. Gemini: `generateContent` at 16:9, 1K (1376 x 768), about $0.07 a picture, run live. Muse Image (`/v1/images/generations`), size `1792x1024`
(an aspect ratio). Either picture is fitted to the art on its own background colour.
The prompt is short: the scene's `image_prompt`, which asks for it
the way a teacher asks for a standard textbook diagram of its subject ("Educational
diagram, as an Earth science textbook draws it, of the Sun and Earth oriented to show why
Earth has seasons..."), each structure's colour by name, and the style line (`art.STYLE`: a
plain dark slate blue background, room for labels, smooth rounded proteins, no text; the
hook and the analogy leave the proteins out). The picture is a still stage: the request
says where everything is and which way it faces, never what it does, and the video draws
every flow as moving dots (round 8: the same layouts without their actions came back
clean, [test-rounds.md](test-rounds.md)). The kind of diagram is the scene's: a cell biology figure in the Campbell/OpenStax manner, an
astronomy schematic as its textbooks draw one. (One OpenStax/Campbell style for every
subject, hex colours and a list of shapes were right once in 6 on the seasons scenes; the
plain request 8 in 12: [test-rounds.md](test-rounds.md).) Disable Muse Image's web search and shell
(`tool_enablement`); turning them on made no difference. Keep
structures away from the edges so vertical crops work later. The helper looks at each
image, told what kind of scene it is (hook, overview, analogy or close-up) and that the
video animates the flows; if any text is visible or a structure is missing, out of place or
facing the wrong way, regenerate (max 2, since Oct 28) with what was wrong. Anything else
wrong is recorded, not repainted: a second picture for those was no better 9 times in 11. A
picture with text is never used; after the second, the scene keeps the text-free picture
with the fewest structures out of place. Built (`studio/steps/art.py`, `studio/muse.py`); first
run live on Oct 28 ([test-rounds.md](test-rounds.md)).

**Saying what is left wrong.** The check also writes, for each problem that would teach the
student something false, a sentence for them ("The chromosomes' bends point the wrong way:
each is pulled toward its pole by its middle, so the bend leads."), leaving out matters of
look. Whatever those notes say of the picture the video uses, after its tries, is shown
beside it (`Timeline.notices`): a white card at the right under "AI-generated picture", up
to three notes, for as long as the picture is on screen. The picture moves left to make
room, and so do the other stages of its process. The check can miss a mistake (round 16's
anaphase had twice the chromosomes it should); the card says only what it found. So every
other picture carries a small "AI-generated picture" tag at its bottom left
(`Timeline.ai_pictures`), mistakes found or not, and whatever made it: Muse Image's pictures
and the brain's SVG drawings alike.

**Stages** are painted as one subject in one framing: each later stage of a process is
redrawn from the first stage's picture (`/v1/images/edits`, the first stage's picture
with the stage's own prompt after "Redraw this picture as a later stage of mitosis: the
same subject, at the same size and in the same place..."), and the helper is told which
stage of how many it checks. A later stage is painted again whenever its first stage is;
if Muse Image will not redraw a picture, the stage is painted afresh. Gemini 3.1 Flash
Image is not given the first stage's picture yet (`gemini.paint` takes a prompt only), so
with Gemini painting each stage is painted afresh, from its own prompt.

**Repair before repainting** (`studio/repair.py`, run by the locate step once the
structures are found): code fixes what it can for at most a helper look or two, where a
repaint costs a picture, its check and locating it again, and is a gamble. A space the
bundle fills (`builds_up`) is cleared of small specks painted wholly inside it, for free;
a structure the art check found out of place, which the helper then names as upside down,
is turned over about the middle of its narrow stretch (a stalk in its membrane), its old
place filled from beside it; the art check looks again, and the flip is kept only if
fewer structures are out of place. Code cannot redraw a part drawn wrong (a protein with
two heads): those wait for a repaint, or are reported.

**Drawn by Claude's brain as SVG** (`/admin/studio`), the brain draws each scene as SVG, with every structure
as its own named group. Rasterising the SVG gives the art, and rasterising one group at a
time gives each structure's mask, so step 4 is not needed. It looks plainer than Muse
Image, but it writes the same files, so everything downstream is built and tested against
it unchanged. Switching the painter makes a job's art again on its next run.

### Step 4: locate

SAM 3.1 via `/v1/responses`, one noun phrase per request (the structure's `sam_phrase`:
its colour and an everyday noun, "yellow flashlight"). Every match at least 0.5 sure makes
up its mask. If SAM finds nothing that sure for a structure, it is asked again in plainer
words (its colour and the phrase's last noun, then its colour and "region"), and only then
does the helper propose a box for SAM to look in. Every match is held to the structure's
colour: one mostly in another structure's colour is dropped. A structure none of these finds
is left out, like one the SVG art leaves out (no spotlight, no camera move), and a scene
with fewer than half its structures found fails the step. `locations.json` holds `box`,
`centroid` and `label_anchor` per structure, all in art pixel coordinates. Built
(`studio/steps/locate.py`); first run live on Oct 28, when SAM missed most long phrases and a
helper box around two Earths labelled one's northern half "Southern Hemisphere", so the
plainer words and the colour check came next ([test-rounds.md](test-rounds.md)).

### Step 5: audio

- **Song:** `POST https://api.elevenlabs.io/v1/music`, `model_id` `music_v2_5`, with a
  `composition_plan` of one chunk per section: `text` ("[Section]\nlines"),
  `duration_ms`, `positive_styles`, `negative_styles`, `context_adherence` "high". The
  studio's code makes this call, only after the fact-check passes. Section durations come
  from the line count and the styles' "<n> bpm" tag (one bar per line below 130 bpm, 2.4 s
  at 100 bpm, so the lines come short and punchy; two bars from 130 bpm, where one would be
  under 1.9 s: 3.2 s at 150; 112 bpm for a song whose styles name none), not from the model.
- **Or the song from Lyria** (picked on `/admin/studio`): `POST
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
- **No exam question:** both videos ended on a read-aloud exam question (`audio/frq.mp3`)
  until Oct 28, when it left them (the song's bridge still poses one, sung). A bundle
  written before still loads, its `frq` dropped.

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
  Built: the flag, in `timing/song_report.json` and the step's summary line. Not built:
  the regeneration.

Output is a `Timing` per audio file: each line by id, with its start, end and
`[{word, start, end}]`.

### Step 7: timeline

Code merges `bundle.direction`, the word timings and `locations.json` into the timeline
(section 6). Reveals are mechanical: a structure reveals when its name or an alias is
first sung, and the labels shown are everything revealed so far. Once named, a structure
stays labelled: a later scene that draws it shows its label from that scene's first line,
so a learner never loses the name of a part they have already heard. A direction that names a
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

Particles are built (section 7). For QA, `python -m studio.render sheet <job> <video>
--every 5` makes the contact sheet; the helper's check and the re-render are not built.
The vertical layout is not started.

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
    {"path": "audio/song.mp3", "at": 0}
  ],
  "palette": {"Broca's area": "#E07A2F", ...},
  "beats": [
    {
      "t0": 12.4, "t1": 16.8,
      "scene": "overview",
      "camera": {"target": "Broca's area", "zoom": 1.6, "ease": "inOut"},   // target null: the whole scene
      "reveal": [{"name": "Broca's area", "t": 12.6}],   // fades from ghost to full when the word is sung
      "spotlight": "Broca's area",         // others washed pale; null = none
      "labels": ["Broca's area", "Wernicke's area"],  // everything named so far that this scene draws
      "particles": [{"path": ["Wernicke's area", "Broca's area"], "color": "#F2C230", "trail": true, "label": ""}],
      "caption": null,                     // at most ~1 per section, held >= 4 s
      "line_id": "v1-2",                   // null in an instrumental gap
      "line": "Broca builds the words you say",
      "words": [{"word": "Broca", "start": 12.6, "end": 13.0}, ...]
    }
  ]
}
```

Validation (pydantic): `t0 < t1`; the beats run from 0 to `duration`, each starting
exactly where the last ended, so every frame (`n / fps`, `beat_at(t)`) has one; a
reveal is labelled, and a spotlight or camera target waits for its reveal; a label once
shown stays on that scene; zoom is at least 1; every name has a colour. Paths are ones the
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
  - **Ghost:** unrevealed structures faded into the art's background through their mask (a
    wash in the background's colour, read off the art's edge, at ~85% alpha, blurred edge:
    pale on the SVG art's white, dark on Muse Image's slate); fade to full over 0.4 s when
    revealed. A structure the video
    never names on a scene is drawn as it is, unlabelled: as a ghost it washed its part of
    the picture out for as long as the scene was on screen.
  - **Spotlight:** wash all other structures toward the background. Never add a glow on top
    (it tints the colours).
  - **Label:** white rounded pill, 3.5 px outline in the structure's colour, text in a
    darkened version of that colour, Inter Bold, a leader line and a 6 px dot at the
    mask's `label_anchor`. Pills stay inside the frame and avoid each other (a simple
    repulsion pass). Once introduced, a label stays on every shot of that diagram.
  - **Particles:** for a line whose direction names `particles`, dots stream slowly along
    a polyline between the structures' label anchors (always inside them), and one lead
    particle with a trail eases from stop to stop, holding at each one beside the label's
    dot, then starts over. Over the diagram, under the labels; a white ring, as the dots
    have, keeps them readable on any colour. Lines in a row with the same path are one
    run, so the motion carries on across them, and it waits for its stops to be revealed.
    When the line's direction says what the particles carry, the lead particle is a badge
    with it written on, as a textbook prints it ("H⁺", "e⁻"; a pill for "glucose"), and each
    kind has its own colour in every lesson (protons gold, electrons cyan), so a proton
    reads apart from an electron and from anything painted in the picture.
  - **Spaces filling up:** for what the bundle's `builds_up` says piles up where ("H⁺" in
    the thylakoid lumen), the space fills with that kind's dots while marked particles end
    there and empties while they start from it, tinted its colour as it fills; one dot in
    three is marked. How full it is runs on across scenes. The dots sit inside the space's
    mask, drift a few pixels in place, and stay bright while particles fill or empty the
    space, whatever the spotlight. The pictures paint no loose ions, so nothing painted
    looks like them.
  - **Parts that turn:** for what the bundle's `spins` names (a rotor, a turbine, a water
    wheel), the part eases up to turning while particles flow into or through it and eases
    to a stop when they stop, so the flow is seen to drive it. How it turns is read off the
    picture: one that looks the same turned a quarter and a half turn (a wheel seen
    face-on) turns in place, the disc of the picture around it; any other (a rotor seen
    edge-on, a mushroom-shaped ATP synthase, which turned in place would stand on its head)
    gets a white curved arrow going round it, its far half faint, as textbooks draw one.
  - **Blocked flows:** particles whose last stop is a blocker (the bundle's `blocks`: a
    poison, a drug, a herbicide) go once and hold there; the stream queues up behind them,
    and a red cross shows at the blocker once the first arrives. The fact-check reads such
    a motion as "e⁻ flow from Photosystem II is stopped at Herbicide".
  - **Glow:** a line's `glow` lights its part with a warm light past its edge, pulsing,
    for the line: light exciting photosystem II, a neuron firing. Rare, and like the
    spotlight, never on a part not yet named.
  - **Stages:** where the video goes from one stage of a process to the next
    (`Timeline.stages`), the next stage's picture fades in over the last for 0.8 s with
    the camera going on from where it was, in place of a cut; what was shown on the stage
    before is on in full from the next stage's first frame. Since every stage is painted
    in the same framing, what changes between them is what the viewer sees move.
  - **Lyric band:** a white band at the bottom (110 px tall in 16:9), the current line
    centred, the current word highlighted by its timing. In the song, the next line shows
    dimmed for the last 1.5 s of an instrumental gap, as karaoke does (the camera and
    spotlight still move when it is sung).
  - **Caption pill:** optional, top area, held at least 4 s.
  - **Hook:** frame 0 already shows motion and the hook claim; no title card.
  - **Ending:** the last picture stays on screen 1.5 s after the voice ends.
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

- One style: a modern textbook illustration (soft gradients, semi-realistic parts, colour
  coding) on a plain dark slate blue background; the SVG art's on white. Never anime, personified characters,
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
  `POPSMOSIS_ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `GEMINI_API_KEY` (for Lyria and
  Gemini 3.1 Flash Image), and `MODEL_API_KEY` for a Muse brain, Muse Image and SAM 3.1; the
  brain, the singer and the painters are chosen on `/admin/studio`, not here. For Inkmory (section 11): `INKMORY_ENABLED`, `INKMORY_API_URL`,
  `INKMORY_OPERATOR_API_KEY`, and `INKMORY_PLANNER_API_KEY` once the planner is adopted.
- **Setup:** the repository's session hook (`.claude/hooks/session-start.sh`) installs
  `studio/requirements.txt` (PyTorch for CPU, torchaudio, demucs, skia-python, anthropic,
  openai, meta-sam-parser, pydantic), ffmpeg and libEGL, and the Demucs and MMS models, before the run
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
context taken from the bundle: `core_idea`, the facts, the misconception and the
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
  likeCount      Int          // thumbs-up on the Droplets tab (DropletLike), kept by a trigger
  commentCount   Int          // comments on it (DropletComment), likewise
  songTags       String[]     // each video's hashtags, from the bundle when the lesson is posted; GIN-indexed
  lessonTags     String[]     // for the tab's search by tag
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
| Agent | PUT | `/api/questions/:id/progress` | Where the studio is while it writes the lesson: the steps running, the bill so far, whether videos follow |
| Agent | PUT | `/api/answers/:id/video/progress` | Where the render is: the media steps running and the videos' bill so far; the current attempt only |
| Agent | PATCH | `/api/answers/:id/video` | `{ status, songVideoUrl?, lessonVideoUrl?, error? }`; the agent's own answers only |
| Agent | POST | `/api/answers/:id/upload-url` | `{ bucket, file }`; returns a signed Supabase upload URL (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` on the API) |
| Anyone | GET | `/api/droplets` | READY and not hidden, newest first, `?cursor=`; `?tag=` those tagged with it |
| Anyone | GET | `/api/droplets/:id` | One droplet |
| Admin | PATCH | `/api/answers/:id` | `{ hidden }`: take a droplet off the tab, or put it back |
| Anyone | GET | `/api/droplets/:id/comments` | Its comments, newest first, `?cursor=` |
| Human | PUT / DELETE | `/api/droplets/:id/like` | Thumbs-up, and taking it back; idempotent both ways |
| Human | POST | `/api/droplets/:id/comments` | `{ content }`, with an optional `Idempotency-Key` |
| Human | DELETE | `/api/droplets/:id/comments/:commentId` | Its author, or an admin |

### The Droplets tab

A `/droplets` page where anyone can watch every finished droplet for free. A topic made
once then serves every student who comes after, and judges can browse it without an
account.

- **Outside the sign-in gate.** Today every page and endpoint past sign-in needs a
  signed-in account; the two `/api/droplets` endpoints are the first that do not. They
  are rate limited per visitor, and all visitors together per API instance
  (`api/src/lib/public-limit.ts`).
- **Public:** a title made from the bundle (`course` and `topic`), the lesson text, both
  videos with their hashtags, and the lyrics.
- **Searchable by tag.** Every hashtag under a video is a link to the tab searched by it,
  and a box at the top searches by one typed in.
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
- **Thumbs-up and comments.** Anyone signed in can like a droplet on the tab and comment
  on it; anyone can read both. A comment names its author as "Grace H.", never their
  full name, picture or email, since the tab is open to anyone and many of the people
  here are students. Comments are not follow-ups: they are for other viewers, not the
  teacher, who never reads them. An admin can delete any comment; a hidden droplet's
  likes and comments are kept and come back with it.
  - *Counts:* `likeCount` and `commentCount` on the `Answer`, kept by triggers on
    `droplet_likes` and `droplet_comments`, so a list of cards reads them off the row it
    already reads, and a deleted user's likes leave the counts exact.
  - *Reliable:* liking is a PUT and unliking a DELETE, each idempotent, so retries are
    safe; the page sends only where the clicks ended up, one request at a time. A
    comment carries an `Idempotency-Key`, so a resend after a lost answer returns the
    first comment instead of a second.
  - *Who:* a public read says which like and comments are the viewer's own when it
    carries a session cookie, without renewing it, so a public response never sets one.
  - *One request a page:* a droplet comes with the first page of its comments. Every
    public request spends the instance's shared ceiling, so a page that needed two
    would serve half as many visitors.
- **Later:** when someone asks for a topic that already has a droplet, offer that one
  instead of making another.
- **Before launch:** check that the ElevenLabs plan allows publishing its generated songs
  publicly, and Google's terms for Lyria's when Lyria sings (they carry a
  SynthID watermark). Keep the bitrate modest (flat diagrams compress well) to hold down
  Supabase egress. Done: the videos are encoded with `-movflags +faststart`, so one starts
  playing before it has downloaded.

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
      say. The studio posts each one, the journal entry and the cycle log tagged with
      the topic (below).

   The planner's beat ends there, so a later failure in art, audio or rendering never
   costs it its record. Its fact-check corrections come back in its next context, which
   is the loop worth watching.
3. **The studio makes the media** (steps 3–9). If a step fails, the operator retries it
   (below).
4. **The operator writes its record last:** always a cycle log (last of all), a journal
   entry when the studio worked a topic (`DONE` or `FAILED`; not on a run that only
   answered follow-ups), its first life story when it has none and a rewrite about every
   10 beats, and a profile change when its bio or vibe no longer fits; the journal entry
   and the cycle log carry a ref for each topic the run touched (below). Before writing, it
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
- **Refs tie the record to the topic.** Each journal entry and cycle log is tagged
  `popsmosis:<question id>` for every topic it is about (Inkmory's `refs`, up to 10 a
  record), and each lesson card on `/learn` links to Inkmory's search for its topic's tag
  (`/search?ref=popsmosis:<id>`, "Teacher's notes", with `NEXT_PUBLIC_INKMORY_URL` set):
  the journal entries and cycle logs about it, from both agents, and each one's life story
  as it stood then. That is the diagnostic view of one droplet. The id says nothing about
  the topic or who asked, so it may be public.
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
- **The planner's real record starts with a Muse brain** (`/admin/studio`), so every entry in it
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
| 2 | Oct 27 | Answer fields (bundle, fact-check, videos) and the agent endpoints; skill file runs the studio; routine environment (setup script, keys, network); Inkmory: adopt the operator, its beat and the one-step retry in the skill file, the planner's beat against a local Inkmory | in progress. Built: the `Answer` fields, a `FAILED` topic with its reason, the claim, fail, video and upload-url endpoints, the follow-up context; `python -m studio run`, `fail` and `summary`; the skill file runs the studio, retries a failed step once and writes the operator's record. Validated locally against Postgres with a stand-in brain. Left: set up the routine's environment (runbook, Option A), adopt the operator on Inkmory and run its first beat within 3 days, the planner's beat against a local Inkmory (not started: there is no `studio/inkmory.py`) |
| 3 | Oct 28 | ElevenLabs song and narration; Demucs + MMS timing and the drift check; the ASS karaoke fallback video end to end | built (#19). First live run on "brain structures" (Oct 27, brain on Haiku): stopped at the song, because the environment's `ELEVENLABS_API_KEY` holds the key's ID, which ElevenLabs refuses (nothing billed). On espeak speech standing in for ElevenLabs, Demucs + MMS timed all 32 lines within 0.04 s of where they were placed (110 s for a 139 s song), and the karaoke fallback rendered in 71 s. Left: the real song, narration and exam question, and the chorus drift on a sung song |
| 4–6 | Oct 29–31 | Art (interim SVG, then Muse Image + SAM when the key arrives); timeline merge; renderer: camera, ghost, spotlight, labels, lyric band, FRQ. Match Fire or Don't on one scene before adding more | built (#18, #20). Live: the art drew 3 scenes and located 7 of 7 structures in 76 s ($0.008 on Haiku); the timeline took 0.3 s; both videos rendered in 157 s on 4 cores (the 194 s song in 90 s, the 157 s lesson in 66 s). Fixed from the run: a shadow drawn inside a structure's group was part of its mask, so the hippocampus's label pointed at the shadow; a label whose dot the camera had just moved off screen still showed, its leader running off the frame. Haiku's drawings are plain and loose (the limbic system is a red oval): draw the showcase topics with the brain on Opus. Muse Image + SAM 3.1 built with `BRAIN_PROVIDER=meta` (Oct 28), not run live yet. Left: their first live run; matching Fire or Don't |
| 7 | Nov 1 | Parallel render; upload to Supabase; both players on `/learn`; the public Droplets tab; agent credits on the droplet if there is time | built (#16, #20, #21): the parallel render, the upload step, both players, the public Droplets tab, and `python -m studio videos` with its claim, heartbeat and recovery. The upload is in the pipeline: `run` claims the videos as it posts the lesson, and the skill file then starts `videos`, which ends with the upload. Not run live: Supabase is not set up, so the upload and `videos` (which starts from a posted lesson) wait for it. Left: the Supabase buckets, then `run` and `videos` end to end; agent credits |
| 8 | Nov 2 | AP Psych, AP Bio and three surprise topics end to end; switch the default brain to a Muse on `/admin/studio` and compare; the planner's Inkmory record starts; persona in the lesson voice, one topic each way; fix failures | |
| 9 | Nov 3 | Stretch: vertical layout, particles, QA contact sheet; cost per droplet | Cost per droplet built early: every model call is logged with its step (`studio/ledger.py`), and each lesson on `/learn` carries a bill of materials, one line per step with its models, calls, time and cost, and the totals (README, "Bill of materials"). ElevenLabs is shown in credits unless `ELEVENLABS_USD_PER_1K_CREDITS` prices them. Particles built early (Oct 28). The contact sheet can be made (`python -m studio.render sheet`); the helper's check on it is not built. Left: the vertical layout, the QA check |
| 10 | Nov 4 | Record the demo, write the submission (due 9:00 PM ET), buffer | The script and what to do before recording: `docs/DEMO.md`. What was built during the event, for the judges: README, "Built during the hackathon" |

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
   Built; `videos` runs the upload after the render.

They join in `python -m studio videos` (studio/videos.py), built against stand-in steps
before the lanes landed: after the post, art and audio at the same time, then the
timeline, render and upload, reporting the videos' status to the API. It closes these
seams, which no lane owns:

- **After the post.** `run` claims the videos the moment it posts the lesson
  (`POST /api/answers/:id/video/claim`, attempt 1, RENDERING), and `fail` after the post
  gives up the videos (`POST /api/answers/:id/video/fail`), never the lesson.
- **Heartbeat.** `videos` renews its attempt before every media step, so the API can
  tell a live render from one whose machine died (`VIDEO_STALE_MINUTES`, 60).
- **Progress.** `run` and `videos` report the steps running and the bill so far at the
  start and end of every step, so the page shows the droplet being made while the lesson
  text waits for the videos (README, "While a droplet is made").
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

### Left (Oct 28)

Setup by hand. Until it is done, no droplet runs live from topic to videos:

- **The ElevenLabs key.** `ELEVENLABS_API_KEY` must hold the key itself, not its ID
  (day 3).
- **The routine:** its environment and the API trigger (the runbook, Option A).
- **Supabase Storage:** the `droplets` and `studio` buckets, and `SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY` on the API (DEPLOY.md, section 6).
- **The operator on Inkmory:** adopt it, then run its first beat within 3 days.

To build:

- **The planner's beat** (section 11): `studio/inkmory.py`, reading its context before
  step 1 and posting its record after step 2, tested against a local Inkmory. Nothing
  reads `INKMORY_PLANNER_API_KEY` yet.
- **A recovery that resumes:** upload each intermediate as its step finishes
  (`upload_file()` is ready for it), so a recovered render does not pay for the song and
  the art again.
- **Re-sing a section that drifts:** step 6 flags it; nothing regenerates it yet.
- **The Fire or Don't reference** (section 14), and matching it on one scene.
- **Agent credits** on the droplet, if there is time.

To run live (day 8):

- The real song, narration and exam question, and the chorus drift on a sung song.
- Muse Image and Gemini 3.1 Flash Image, each with SAM 3.1 (`/admin/studio`; Gemini's
  answer and price already checked live).
- `run` and `videos` end to end, with the upload.
- The topics that mean done (section 1); a Muse brain against Claude's (`/admin/studio`); the planner's persona
  in the lesson voice, one topic each way (section 14).

Then the stretch (day 9): the vertical layout and the QA check. Before launch: the song
terms (section 10). Day 10: the demo and the submission (`docs/DEMO.md`); the repository
must be public by then, as the rules require.

## 13. Risks and fallbacks

- **No Meta key in time, or Meta's API misbehaves:** Claude as the default brain with
  the SVG art (both on `/admin/studio`) runs the whole pipeline on Claude alone. Nothing
  downstream changes.
- **SAM misses illustrated anatomy:** a helper box plus a SAM box prompt; a structure
  neither finds goes unlabelled.
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
   clip tests. None of it is in the repo yet.
3. **The planner's persona in the lesson voice.** Either its persona and memory go into
   the bundle prompt, so they shape how the lesson and the song sound, or they go only
   into the call that writes its record. Run one topic each way and compare (day 8).
   Personality is permanent, but the bio, vibe and life story drift, and the fact-check
   catches wrong facts, not tone.

Decided: the interim art is brain-drawn SVG (step 3); the videos are public, in Supabase
Storage, on a public Droplets tab (section 10); two agents keep a public record on
Inkmory, and the studio posts the planner's record from one structured call (section 11).
