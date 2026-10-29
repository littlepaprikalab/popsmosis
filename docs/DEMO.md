# The demo video

Three minutes at most, recorded on day 10 at the latest (PLAN.md, section 12). The rules ask
that the video show what was built during the hackathon and only that, so judges can tell
it apart from what came before (README, "Built during the hackathon"), and that it show
the product rather than slides. The Indie track is judged on creativity, technical
ambition and whether it works.

## Before recording

- **Make the repository public.** The rules require it. A search of the whole history
  for keys and `.env` files found none; turn on GitHub's secret scanning once it is public.
- **Make one droplet live, end to end, through the site.** Shots 3 and 6 need `run`,
  `videos` and the upload working against the API, which no round has run yet
  (docs/test-rounds.md, "Not tested"): the Supabase buckets and the routine's environment
  first. If the studio runs on a laptop instead of the routine, change the line in shot 3
  that names the routine.
- **Pick the brain.** This is a Meta hackathon, so a Muse brain is the stronger story, but
  the test rounds have made videos only with Muse Spark, whose lyrics were weak, and with
  Claude writing while Muse Image painted (rounds 5 to 16). Muse Wonder has written plans
  and fact-checks, never a video. Run one topic through the videos on Muse Wonder first
  and use it if its song holds up; otherwise Claude writes and Muse Image and SAM 3.1
  paint and locate. Set it on `/admin/studio`, with the painter on Muse Image either way,
  and say which in shot 4.
- **Pick a topic where things move.** The motion teaches best when particles carry
  something, a space fills up or a part turns: the light reactions of photosynthesis (H⁺
  and e⁻ along the chain, the thylakoid lumen filling, ATP synthase turning) show all
  three, and were a test round's topic.
- **Use only real output.** Never show `web/public/samples/` (placeholder clips for
  `seed:dev`) or the sample job's files.
- **Check the song may be published.** The video plays the generated song in public:
  ElevenLabs' terms for the plan the key is on (PLAN.md, section 10), or Google's for a
  Lyria song.
- **Fill in the brackets** below from that droplet and its bill of materials: `[TOPIC]`,
  `[BRAIN]` (Muse Wonder or Claude), `[MINUTES]`, `[COST]`.
- **Freeze `main` at the submission.** The README says every commit after `c037cc0` was
  written during the event. Tag the submitted commit (`git tag hackathon-submission`)
  and push nothing to `main` from 9:00 PM ET on Nov 4 until judging ends on Nov 8, or point
  the README's compare link at the tag.

## Shot list and narration

About 340 words of narration, plus 20 seconds of the song on its own: roughly 2:45 at an
easy pace, leaving room to breathe under 3:00.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:14 | **The song video** of the main droplet, from the moment a structure is first sung: its label appears on the word. The music plays alone for 3 seconds, then drops under the voice. | "I asked Popsmosis for [TOPIC]. [MINUTES] minutes later, it had written this song, fact-checked every line, painted the diagrams and animated them to the words." |
| 0:14–0:30 | **GitHub's commit list** at Oct 26, the "Copy zoey" commit highlighted; then the README's "Built during the hackathon" section. | "I built Popsmosis during this hackathon. On day one I copied zoey, an agent-answered Q&A app, for its sign-in, admin pages and request queue: that's this one commit. Everything after it is new, and the README lists both sides." |
| 0:30–0:52 | **`/learn`, already signed in.** Type the topic, and `Style: pop-punk` on a second line; send. The card shows the bill of materials filling in, step by step. Speed up the wait, with a clock and "×8" in a corner. | "Asking works as it did in zoey: a topic, here with the song's style. What happens next is new. The teacher, a Claude routine, runs my studio pipeline, and the page shows each step land: the model that ran it, its time and its cost." |
| 0:52–1:36 | **Inside the studio,** in order: the plan (`python -m studio show <job>`: facts, scenes, lyrics); the fact-check's verdicts, with the line it corrected; one scene's Muse Image picture, the same picture with SAM's masks over it, then that scene's labelled frame in the video. If the droplet has one, a frame with its "AI-generated picture" card. | "[BRAIN] writes the whole plan in one call: the facts, scenes, lyrics, narration and camera moves. Then a fresh call, which never sees the writer's reasoning, checks every line against the facts. Here it caught one and rewrote it. Nothing is sung until it passes. Muse Image paints each scene, a checker sends back a picture with a part out of place, and SAM 3.1 finds every structure, so each label lands on its own part. An error that survives is told on screen, never hidden. Demucs lifts the vocals out of the song, and Meta's MMS aligner times every word." |
| 1:36–2:14 | **The two videos.** About 20 seconds of the song where structures appear as they are named, with the spotlight and the karaoke band. Then the lesson video at its busiest motion: badged particles travelling between parts, a space filling up, a part turning. | "Two videos come from that one plan. The song." *(20 seconds of the song, no voice.)* "And the narrated lesson, where the motion teaches too: each particle is marked with what it carries, spaces fill up, and parts turn as the flow drives them." |
| 2:14–2:40 | **`/droplets` in a signed-out window:** type a hashtag into the search, play one droplet on its card, show its likes and newest comments. Then the main droplet's bill of materials, opened: the makers and the totals. | "Every finished droplet goes on the public Droplets tab. No account needed to watch, search by hashtag, and sign in to like and comment. And every droplet shows its bill of materials: this one took [MINUTES] minutes and cost [COST], and here is every model that made it." |
| 2:40–2:58 | **The Droplets tab's grid,** then the repository's URL. | "A lesson made once is there for every student who asks after. That's Popsmosis: droplets for learning. The code, and exactly what I built here, is at github.com/littlepaprikalab/popsmosis." |

In shot 4, if the live run's fact-check passed every line the first time, show the
seeded-error test instead ("Every electron hand-off pumps a proton", PLAN.md section 5),
and say so: "Here I planted an error, and it caught it." With a Muse brain, end shot 6 on
"…every model that made it, most of them Meta's."

Keep sign-in, the admin **AGENTS** and **USERS** pages and the follow-up thread off
screen: they are zoey's. The ask box is zoey's too, which is why shot 3 says so.

## Recording

- 1920 × 1080, the browser at 125% zoom, its bookmarks bar hidden, notifications off.
- Record the ask in shot 3 as it happens, and speed up only the waits, with the clock and
  the speed on screen. Never cut so that a wait looks shorter than it was.
- Have a finished droplet ready, in case the live one fails on the day.
- Record the narration first, then cut the picture to it. Keep the music about 18 dB
  under the voice.
- Add captions: judges may watch muted.

## Submitting

At [cerebralvalley.ai](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit),
by 9:00 PM ET on Wednesday, Nov 4: the repository's link, the video, and every accepted
teammate. In the description, paste the first two paragraphs of the README's "Built
during the hackathon" section.
