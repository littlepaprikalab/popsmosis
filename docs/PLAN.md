# Popsmosis — droplets for learning

Status: **day 1 of 10 done** (the zoey port, below). This is the plan the rest of the
build follows. Where the code and this document differ, the code wins; the README lists
the endpoints as built.

## 1. What we are building

1. A web app with **Google sign-in**.
2. A signed-in user asks for a **topic** to learn: "ATP synthesis", "the French
   Revolution", or a fuller ask ("ATP synthesis for my AP Bio exam").
3. The API stores the request and **fires the teacher's Claude routine** through its API
   trigger, so a run starts within seconds.
4. The teacher (an agent running outside this stack, driven by
   `docs/popsmosis-agent-skill.md`) picks the topic off the queue and produces a
   **droplet**: the written lesson and, from day 7 on, a **karaoke-style lesson video**.
5. The page shows the droplet - the text, and the video from its Supabase Storage link -
   updating in place without a reload.
6. The learner asks **follow-ups** under the droplet, and the teacher answers them on its
   next pass.

## 2. Where it came from

Popsmosis is a copy of [zoey](https://github.com/Iztera/zoey) at `3f81af7`: an advice
column whose questions are answered by an agent. The pathway is the same one; only what
goes in and what comes back changes.

| zoey | popsmosis |
|---|---|
| A user asks for advice | A user asks for a topic |
| Saved as a `Question` | Same row: the topic request |
| The API fires the routine; the advisor wakes | Same trigger; the teacher wakes |
| The advisor posts a text `Answer` | The teacher posts the droplet: lesson text now, video next |
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

## 3. The droplet video (days 3–7)

What the page will show: the lesson text and a video stored in Supabase Storage. The video
is a lesson **song** with diagrams behind it and the lyrics on screen, each word lighting up
as it is sung.

```
topic ──► API ──fire──► teacher routine (Claude)
                          │  lesson text, key facts, diagram plan
                          │  lyrics from a Meta (Llama) model, fact-checked by Claude
                          ▼
                       render job ──► ElevenLabs: the song (or a spoken narration)
                          │           ElevenLabs forced alignment: when each word is sung
                          │           diagrams: SVG drawn by Claude, rasterized
                          │           ffmpeg: diagrams + audio + karaoke subtitles (ASS \k tags)
                          ▼
                       Supabase Storage ──► video URL on the droplet ──► the page plays it
```

- **Claude is the teacher and the director.** It writes the lesson and the diagram plan,
  prompts the Meta model for lyrics, and checks the lyrics for accuracy before anything
  is sung: rhymes bend facts.
- **Karaoke timing is the hard part.** Highlighting words needs the time each one is
  sung. ElevenLabs forced alignment returns word timings for audio plus its text, but it
  is built for speech; test it on sung vocals early (day 3–4). Narration is the fallback
  and is easy: ElevenLabs speech returns word timings directly.
- **Compositing is standard.** Once the timings exist, ffmpeg's subtitle renderer
  (libass) does karaoke highlighting natively with ASS `\k` tags.

### Where the media work runs (proposed, not yet decided)

The routine does the thinking and hands a job to a **render worker in this repo** (a
Render background worker), which calls ElevenLabs, runs ffmpeg and uploads to Supabase:

- Building media, retrying and timing out are more reliable as code than as an agent
  following instructions.
- The ElevenLabs, Meta and Supabase keys stay on our servers rather than in the routine's
  environment.
- A routine run is slow (zoey measured 4m29s for one) and counts against a daily limit,
  so it should not also sit through a render.

The alternative is the routine doing all of it in its own container: possible, but the
routine then needs every key, and its network policy must allow every host.

### Data model (proposed)

The droplet's video hangs off the lesson (`Answer`), so a topic still has exactly one
droplet:

```prisma
enum VideoStatus { QUEUED RENDERING READY FAILED }

model Answer {
  // ...as today
  videoStatus   VideoStatus? // null: a text-only droplet
  videoUrl      String?      // Supabase Storage link, set when READY
  videoError    String?      // why it FAILED, for the admin page
  lyrics        String?      // what was sung, shown under the video
}
```

## 4. Ten-day plan

| Day | Work | Status |
|---|---|---|
| 1 | Port zoey, rename, drop Zed, `/learn`, teacher skill file | done |
| 2 | Video fields on the droplet; a render job queue the routine can post to | |
| 3–4 | Lyrics from Meta, the song from ElevenLabs; test word timing on sung audio | |
| 5–6 | Diagrams, the ffmpeg karaoke composite | |
| 7 | Upload to Supabase Storage; the player on `/learn` | |
| 8–9 | End to end, failures and retries, cost per droplet | |
| 10 | Deploy, polish | |

## 5. Open decisions

1. **Meta provider.** Which API serves the Llama model: Meta's Llama API, Together,
   Groq, Replicate or AWS Bedrock? Its key goes in the API's environment.
2. **Render worker.** Confirm the split in section 3 (routine thinks, worker renders).
3. **Supabase Storage.** Does the project exist, and should videos be public links or
   signed links for signed-in users only? The approval gate suggests signed links.
4. **Song or narration.** Songs only at first, or a spoken narration as well (or as the
   fallback when the song's timing fails)?
