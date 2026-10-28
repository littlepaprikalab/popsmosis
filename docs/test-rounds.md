# Test rounds: two science topics through the video pipeline

Oct 28. Two rounds of the studio's whole video pipeline on two science topics, run
locally, to find where it falls short before the showcase topics (PLAN section 12, day 8).
Four bugs found in round 1 are fixed on this branch; the rest are listed below, worst first.

## What ran

| | |
|---|---|
| Settings | `BRAIN_PROVIDER=meta`, `STUDIO_BRAIN_MODEL=muse-spark-1.3`, `SONG_PROVIDER=gemini` (Lyria 3.5), ElevenLabs narration, as the cloud environment is set up |
| Topics | **AP Bio: the light reactions of photosynthesis** (anatomy-style diagrams; the plan's own fact-check case). **8th grade Earth Science: why Earth has seasons**, asked in a learner's words ("Is it because Earth gets closer to the sun?") with `--style "pop-punk, upbeat, 150 bpm"` (spatial diagrams, a younger audience, a style of the learner's own) |
| How | `python -m studio plan`, then `python -m studio step` for art, locate, audio, timing, timeline and render, the art and audio branches side by side as `videos` runs them. Local jobs only: nothing was posted to the API, so `run`, `videos` and the upload were not run (below) |
| Round 1 | Both topics as the code stood |
| Round 2 | Both topics again from scratch with the fixes; and the round-1 jobs again from locate on, on the same art and audio, to compare before and after |

Every first was live: the Muse Spark brain, Muse Image, SAM 3.1 and Lyria had not run
against their APIs before, and ElevenLabs had not run since its key was fixed.

## At a glance

| | Round 1 photosynthesis | Round 1 seasons | Round 2 photosynthesis | Round 2 seasons |
|---|---|---|---|---|
| Fact-check | passed, 2 rounds, 2 lines fixed | passed, 2 rounds, 1 line fixed | passed, 2 rounds, 1 line fixed | passed, 2 rounds, 2 lines fixed |
| Art | 5 scenes, 5 pictures | 4 scenes, 6 pictures; one kept with a science error | 5 scenes, 6 pictures | 4 scenes, 5 pictures |
| Located | 17/17, 4 by the helper's box | 13/13, 9 by the box; **one labelled wrong** | 16/17, 5 by the box; one left out | 14/14, none by the box |
| Song | 138 s; sung from **10.0 s** | 105 s; sung from 0.4 s | 148 s; sung from 9.4 s (cut to 1.0 s) | 106 s; sung from 6.8 s (cut to 1.0 s) |
| Narration | 113 s (244 words) | 109 s (293 words) | 109 s (238 words) | 124 s (328 words) |
| Videos | song 3:17, lesson 2:53 | song 2:37, lesson 2:41 | song 3:26, lesson 2:55 | song 2:37, lesson 3:02 |
| Topic to both videos | 9.5 min | 11.1 min | 10.9 min | 8.3 min |
| Cost | $0.49 + 970 credits | $0.61 + 1,014 credits | $0.59 + 979 credits | $0.45 + 1,223 credits |

The minutes are wall time on one 4-core container with both topics running at once (the
CPU-heavy timing and render took turns), so a topic alone is faster. All four are inside
the plan's 15 minutes. Credits are ElevenLabs' (narration and exam question); set
`ELEVENLABS_USD_PER_1K_CREDITS` to see them in dollars.

## What works

- **The Meta brain.** Muse Spark 1.3 wrote both bundles in about 2 minutes ($0.13-0.15)
  and the fact-check passed in two rounds each time, its corrections right: plastocyanin
  comes after cytochrome b6f, not before it; "the axis points north" became "points the
  same way" because the facts say only that. The audience ("8th grade") and the learner's
  style (pop-punk, 150 bpm) carried through; all tags passed.
- **Muse Image** painted every scene in the house style, with no text in any of 22 pictures.
- **SAM 3.1** traces what it matches tightly (membrane proteins, a water wheel, a dam's
  turbine), once it is asked in words it knows (below).
- **Lyria** made each song in 23-33 s, within 3% of its planned length; Demucs and the
  aligner timed every line, and the chorus drift stayed under 0.5 s on two of four songs.
- **ElevenLabs** narration and the exam question work with the current key.
- **The renderer**: labels, karaoke band, captions, particles, the exam card with its
  countdown and spotlit steps. A 3-minute video renders in about 50 s.

## Fixed on this branch

1. **Labels on the wrong structure** (`studio/steps/locate.py`). SAM 3.1 found nothing for
   most of the bundle's phrases ("golden yellow small flashlight", "medium blue rounded
   dome") and nothing for any scientific name ("stroma", "Sun"), so 13 of 30 structures
   fell to the helper's box. A box drawn around two Earths came back as one Earth's
   northern half and was labelled "Southern Hemisphere", in both videos and on the exam
   card. Box masks were also noisy and spilled over their neighbours: the overview's
   "Stroma" covered the thylakoids, which showed as white speckle every time the chorus
   played. Probing SAM showed it matches colour and an everyday noun: "yellow flashlight"
   found both flashlights, "blue region" both northern halves, "blue complex" cytochrome
   b6f. So now:
   - SAM is asked again in plainer words before the helper is: the colour and the
     phrase's last noun, then the colour and "region" (a quarter of a cent each).
   - Every match is held to the structure's colour: one whose pixels are mostly another
     structure's colour, or mostly the white background, is dropped. A structure left
     out is better than one labelled wrong. On the round-1 art this separated every right
     mask (0.58 or more of its own colour, at most 0.39 background) from the wrong ones
     (0.40; 1.00 background for a "gray plug" that came back as the pale sky).
   - The bundle prompt asks for `sam_phrase` that way: "yellow flashlight", not "golden
     yellow small flashlight". Round 2's phrases all were.

   On the round-1 art again: no wrong labels, no speckle, both flashlights lit, boxes
   down from 13 to 2 of 30 structures, and seasons located in 75 s instead of 156 s.
2. **A 10-second intro before the hook** (`studio/steps/timeline.py`). Lyria ignores "the
   singing starts within the first two seconds": three of four songs started late (10.0,
   9.4 and 6.8 s), so the song video opened on 10 s of a ghosted diagram with an empty
   band, against the 2-second hook (PLAN section 8). A song sung later than 2 s now plays
   from 1 s before its first sung word, fading in over 0.5 s (`AudioClip.fade_in`), and
   its timings move with it. Checked against the separated vocals: the cut lands before
   the first sung line in all three (only faint ad-libs before it go). The narration is
   never cut.
3. **Docs** said the Meta calls had never run live (README, PLAN steps 3-4, `muse.py`,
   `brain.py`); they now point here.

## Open gaps

### It can teach something wrong

1. **A picture the helper says is wrong is used anyway.** After three tries the art step
   keeps "the text-free picture with the fewest problems" (PLAN step 3), whatever the
   problems are. Round 1's seasons "sunlight detail" was kept with rays coming out of the
   poles, an axis not perpendicular to the equator and a Sun no bigger than Earth: in a
   lesson about how sunlight strikes the tilted Earth. *Decision needed:* fail the scene
   (and so the videos) when the problem is a science error, fall back to the brain's SVG
   for that scene, or ask once more with a simpler prompt.
2. **The helper's art check misses science errors, some of the time.** It made good catches
   (an ATP synthase with its head in the lumen; the north tipped toward the Sun in a winter
   scene), but it passed the round-1 seasons hook with the axis not running pole to pole,
   flagged the same fault on another scene's second try and passed it on the third. In
   round 2 it passed a flashlight analogy with no slanted flashlight (the wide patch has
   nothing making it), Earth painted as a purple marble (the bundle chose purple for
   "Earth") and membrane proteins that sit on the membrane instead of spanning it. The check sees only the scene's image prompt and
   structure list; giving it the facts, and a stronger model for it, are the obvious tries.
3. **A group and its own part in one scene.** The bundle prompt forbids it, the bundle did
   it in both rounds (round 1: "Chloroplast" with its thylakoids, lumen and stroma; round 2:
   "leaf" with its "chloroplasts", which are small dots inside it) and no check catches it.
   In round 2 both hook masks cover the whole leaf, so "chloroplast" points at the leaf's
   surface; the colour check cannot help when both are green. Options: a code check in
   `checks.py` (a structure whose name another's contains, a known group word), or in
   locate, carving the smaller mask out of the larger when two overlap most of the way.
4. **The exam card's scene may not draw what an answer step names.** Round 1's
   photosynthesis card used the electron-transport scene, which has no ATP synthase, so the
   step "ATP synthase makes little or no ATP" spotlit the lumen.

### It looks broken or reads badly

5. **A structure never named on its scene stays a ghost for as long as the scene is on
   screen.** Round 1's seasons "sunlight detail": the narration says "the hemisphere leaning
   toward the Sun", which is neither name nor alias, so the whole Earth stayed washed out.
   Either draw a structure that is never named on a scene plainly (unlabelled), or have
   `checks.py` report it so the prompt can be fixed.
6. **The photosynthesis narration is short.** 244 and 238 words where the prompt asks for
   300-450, so its lesson video has under 2 minutes of teaching. Seasons was 293, then 328.
   A code check could ask for a longer one; or try Muse Wonder, the default brain, which
   these rounds did not use.
7. **Long narration sentences shrink the band's text** until it is hard to read (round 1,
   "From Photosystem I, electrons pass through ferredoxin and NADP+ reductase to make
   NADPH in the Stroma."). Ask for shorter sentences, or let the band take two lines.
8. **Common words as aliases.** "north" (an alias of Northern Hemisphere) revealed it on "the
   axis points north"; "tilt" for the axis. The prompt already warns against it.
9. **The lesson text opened with the misconception** (round 1 seasons), and named it twice,
   where the prompt asks for the right idea first.

### The pipeline itself

10. **Song drift is reported, never acted on.** The timing step flags chorus repeats more
    than 0.5 s off (two lines in round 1, three in round 2) and lines the aligner is unsure
    of (4 to 9 of about 30, most of them gang-vocal choruses), but nothing regenerates a
    section, as PLAN step 6 plans; Lyria makes the whole song at once, so there is no
    section to regenerate.
11. **The intro cut trusts the aligner's first line.** Were the first line aligned late, the
    cut would take sung words with it. It was right in all four songs here; checking the
    vocals' loudness before cutting would make sure.
12. **Locating costs more when a structure ends in the helper's box anyway.** The plainer
    words add up to two SAM calls (half a cent, a few seconds) before the box. Round 2's
    photosynthesis spent 176 s and $0.14 on locate (round 1: 115 s, $0.09), most of it on
    the leaf and chloroplast that nothing could separate (gap 3).
13. **No local command makes the videos.** `plan` stops after the fact-check and `videos`
    needs a posted lesson, so trying the media steps on a local job means running
    `step art`, `step locate`, `step audio` and so on by hand. A `make <job>` that runs
    `videos`' order without the API would make rounds like these one command.
14. **`show` on a local job reads "cut off" and "time n/a" for every step**, since `plan`
    and `step` write no `runs.jsonl`; the bill's times look broken.
15. **The note "the brain is set by the environment" lists `STUDIO_BRAIN_EFFORT`** on
    `BRAIN_PROVIDER=meta`, which ignores it.
16. **A refused picture's check is not kept**: only the next try's prompt quotes it, so
    working out why a scene took three tries means reading prompts.

## Why the diagrams come out wrong

Every wrong picture (gaps 1-2) got a spatial relation wrong: an axis not through the poles,
proteins on the wrong side of a membrane, the north tipped the wrong way. To tell whether
the prompt is too complex, three scenes that went wrong were painted again, three times
each, from three prompts:

- **V0 current**: what the art step sends today (100-170 words: the audience, the scene, a
  list of structures with hex colours, the style line).
- **V1 short**: the same scene description with the wrapper cut: colour names instead of
  hex codes, no second list of the structures, one short style line.
- **V2 explicit**: as short, but rewritten to state the spatial facts ("the axis passes
  through both poles, at right angles to the equator"; "each complex passes through the
  membrane, ATP synthase's head above it").

Judged by rules set before looking (seasons: the axis through both poles at about right
angles to the line between the halves, the north leaning toward the Sun, no rays out of
Earth; thylakoid: every complex crossing the membrane, ATP synthase's head on the stroma
side, the lumen below):

| Scene | V0 current | V1 short | V2 explicit | SVG drawn by Muse Spark |
|---|---|---|---|---|
| Seasons, sunlight on the tilted Earth | 0/3 | 2/3 | 0/3 | 2/2 |
| Seasons, hook | 1/3 | 3/3 | 0/3 | 2/2 geometry, 1 rendered black |
| Thylakoid membrane | 1/3 | 2/3 | 3/3 | not tried |
| All | 2/9 | 7/9 | 3/9 | |

- **The wrapper hurts.** The same scene descriptions without it went from 2/9 right to 7/9.
  V1 also swapped the style line ("OpenStax/Campbell, shaded vector, soft gradients" for
  "flat shaded vector"), so the gain is the length and the style together.
- **Words cannot pin geometry.** Told "a blue northern half on top and an orange southern
  half below" and "at right angles to the equator", Muse Image drew a level equator and a
  tilted rod through it, all six times: it keeps the concrete placement ("on top") over the
  relation ("at right angles"). The same kind of rewrite fixed the membrane (3/3), whose
  facts are placements ("above", "through").
- **Drawing gets geometry right.** The art step's SVG route (studio/prompts/art.md), with
  Muse Spark drawing, put the axis, the tilt and the rays right in all four drawings, about
  40 s and 4 cents each. One rendered the Sun and Earth black (a gradient that did not
  resolve) and passed the SVG checks, so that route needs a check on the rendered colours.
- **One style for every subject.** Both art prompts name the same house style, "in the style
  of OpenStax/Campbell" (PLAN section 8, "One style"), chosen for the two showcase topics
  (brain structures, an AP Bio topic). It is what the plan asked for, not a bug, but it
  asks an astronomy diagram to look like a cell biology figure.

What this suggests: a diagram type per scene, chosen by the bundle from a short list, that
picks both the style and how the scene is made. Cell and molecular biology in the
Campbell/OpenStax manner, anatomy as a medical textbook draws it, and the everyday objects of
hooks and analogies are painted by Muse Image from a short prompt; geometry that must be
exact (orbits and tilt, forces, light rays, circuits, graphs) is drawn as SVG. Both routes
write the same files, and `art.located()` already accepts scenes made either way.

## Not tested

- `python -m studio run`, `videos`, `wait`, `recover` and the upload step: they post to the
  API in `POPSMOSIS_API_URL`, which here is production, and Supabase is not set up.
- `BRAIN_PROVIDER=claude` (the SVG art), Muse Wonder as the brain, and Eleven Music
  (`SONG_PROVIDER=elevenlabs`).
- The planner's Inkmory record (`INKMORY_ENABLED` is not set).

## Running a round again

```bash
python -m studio plan --topic "..." --course "..." --request "..." --job <id> --brief
for s in art locate audio timing timeline render; do python -m studio step $s <id> --brief; done
python -m studio show <id> --brief
```

The jobs from these rounds were `r1-*` and `r2-*` in `jobs/` (git-ignored, gone with the
container). To look at a video, take a still every few seconds (`ffmpeg -ss <t> -i
out/song_h.mp4 -frames:v 1 still.png`); to check what was located, lay each
`masks/<scene>/<structure>.png` over its `art/<scene>.png`.
