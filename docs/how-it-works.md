# How a droplet is made, end to end

A droplet goes through three phases: its text is written and checked, the lesson is posted, then its videos are
made. Each step writes a file in the droplet's folder (`jobs/<id>/`) and counts as done once that file is there, so
running again after a failure starts at the step that failed. The details are in `docs/PLAN.md`; this is the map.

Who does each step: **code** (no model, no cost), **the brain** (the costly calls), **a helper** (small checks),
or **another model** (a painter, SAM, Eleven Music, an ElevenLabs voice, Demucs, MMS). The brain is Claude Opus by default; an
admin can switch it to Meta's Muse Wonder or Muse Spark (`/admin/studio`, or a topic's own on `/learn`), and the
helper switches with it (Claude Sonnet for Claude, Muse Spark for Muse). Every brain call below goes to the brain in
force for the topic: the script and the bundle are two calls, then the audit, the narration edit, a lesson rewrite,
the fact-check and the songwriter. Two stay Claude whatever the brain: the SVG drawer (Claude Opus) and the
scorecard's judge (Claude Opus). Prompt changes tested in a session run on Claude subagents, so they say how a
prompt works on Claude's brain, not on Muse's.

## Phase 1: the text (about 15 minutes, about $2)

1. **Request** (code). The operator, an automated routine following `docs/popsmosis-agent-skill.md`, takes the
   next question from the queue and runs `python -m studio run <id>`. Code writes `request.json`
   (`run.make_request`): the learner's words split into the topic and any song style (`split_style`), their photos
   of notes, their text files joined into a study guide. Nobody reads the question before the brain does.
2. **Script** (the brain, about 2 min; `prompts/script.md`, called by code at the start of the bundle step; no
   person reads or edits it). The plan: the audience, the learner's question, the
   core idea, the why chain, the key terms, the facts, the misconception, the scenes, the narration's sentences.
3. **Bundle** (the brain, about 10 min; `prompts/bundle.md`). The whole droplet from the plan: the text lesson,
   the song (its style, lyrics and chorus), the narration, the scenes and their parts, what each line shows (the
   camera, the spotlight, what moves), the key lines on screen, the tags, a graph's meanings. The song's style is
   the learner's, or else one of the nine in `styles.SONG_STYLES`, picked by code from the job's name, which the
   brain treats as a seed (its genre and tempo kept, its voice and sound its own). Inside this step too:
   - the **fact audit** (the brain, `prompts/audit.md`), begun on the plan's facts beside the bundle call;
   - the **learner test**: a helper reads only the lesson, as a first-time learner, and another helper call
     grades what it took away; when it missed the core idea, the brain rewrites the lesson and it is read again;
   - the **narration edit** (the brain): the voice-over made one explanation;
   - the **code checks** (`checks.py`): names, scenes, line lengths, motions and more; some repaired, the rest
     recorded for the prompt to fix.
4. **Audit** (code, usually nothing left to do): done in step 3 when the bundle kept the plan's facts; otherwise
   the brain judges the facts here. A wrong fact is corrected once and the correction judged again.
5. **Fact-check** (the brain, 1 to 3 rounds; `prompts/factcheck.md`). Every line held to the audited facts: the
   lesson, the narration and the key lines must follow from them; a song couplet is judged by its own question,
   would a student who sings it believe something false? A failing couplet goes to the **songwriter** (the
   brain, `prompts/songwriter.md`), which keeps it singable and may appeal a wrong verdict; a failing arrow, tag
   or graph meaning is left out of the video, never rewritten.
6. **Post** (code). The lesson is published on the API.

## Phase 2: the videos (about 10 minutes)

Two branches run side by side, then the rest in order (`videos.py`):

- **Pictures**
  - **Art** (`steps/art.py`): each scene painted by **the painter** (Gemini's image model for Claude's brain,
    Muse Image for Muse's, by default; an admin can change it), or drawn as SVG by **Claude Opus**
    (`prompts/art.md`) for a graph, a chart to scale or a math lesson's picture, whoever the brain: on a light
    page, each curve's name placed by the drawing. A painting labels its own parts. A **helper** looks at each
    painting for wrong labels and parts missing or out of place; what it finds is shown
    beside the picture, never hidden.
  - **Locate** (`steps/locate.py`): **SAM** finds each part in a painting, so the camera and the spotlight
    know where it is. A drawing needs none: its parts are its own.
- **Sound**
  - **Audio** (`steps/audio.py`): the song by **Lyria**, the narration read by **Gemini's voice**, by default; an
    admin can switch both to ElevenLabs (Eleven Music sings, an ElevenLabs voice reads), and a song Lyria will not
    sing goes to Eleven Music. The studio asks every song for clear diction once.
  - **Timing** (`steps/timing.py`): **Demucs** takes the vocals out of the song, and **MMS** finds when each
    word is sung or said. A song that did not sing its lyrics, or a narration not read as written, is made again.
- **Timeline** (code, instant; `steps/timeline.py`): for every moment, which scene shows, where the camera goes,
  which names, meanings, arrows and values are on, the karaoke words and the key line.
- **Render** (code, about 3 to 5 min; `render/`): three videos, the song and the lesson across, and the hook
  (a cut of the song) upright.
- **Upload** (code): the videos attached to the posted lesson.

## Checking quality, outside the pipeline

- **Tests** (`python -m pytest studio`, no paid calls): about 840, run before every change is pushed.
- **The scorecard** (`docs/scorecard.md`): 12 fixed topics through Phase 1, run in a Claude Code session with
  subagents standing in for every model call (CLAUDE.md), each droplet judged blind beside an earlier run's.
  Prompt changes are tested this way before they ship; `docs/test-rounds.md` has the results.

## Where things are

| | |
|---|---|
| Each step's code | `studio/steps/<step>.py` |
| What each model is told | `studio/prompts/` |
| The song styles | `studio/styles.py` |
| The full design | `docs/PLAN.md` |
| Test rounds and in-session tests | `docs/test-rounds.md` |
| What makes a droplet faster, and what was decided | `docs/speed.md` |
| The songs held up as the standard | `docs/reference-songs.md` |
