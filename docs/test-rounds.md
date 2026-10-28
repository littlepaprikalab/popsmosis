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

5. *(Fixed after round 3.)* **A structure never named on its scene stays a ghost for as long as the scene is on
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
13. *(Fixed: `python -m studio make <job>`, and `plan --videos`.)* **No local command makes the videos.** `plan` stops after the fact-check and `videos`
    needs a posted lesson, so trying the media steps on a local job means running
    `step art`, `step locate`, `step audio` and so on by hand. A `make <job>` that runs
    `videos`' order without the API would make rounds like these one command.
14. *(Fixed for the videos: `make` keeps runs.jsonl.)* **`show` on a local job reads "cut off" and "time n/a" for every step**, since `plan`
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

The SVG drawings are right but look plain next to Muse Image's pictures, so the next test
asked Muse Image the way a teacher would: "Educational diagram of the Sun and Earth, oriented
to show why Earth has seasons during northern summer: the Sun on the left, Earth on the right
with its axis tilted 23.5 degrees toward the Sun, and parallel rays of sunlight reaching
Earth. Clean textbook illustration on a white background. No text..." (and the same for the
hook), with and without one sentence of colours ("Earth's northern hemisphere is shaded blue,
its southern hemisphere orange, and its axis is a purple rod"), each with Muse Image's tools
off (as the studio sends it) and on (shell and search, with which Meta says it arranges
structured graphics before drawing). Three tries each, judged as above:

| Prompt | Sunlight, tools off / on | Hook, tools off / on | All |
|---|---|---|---|
| The studio's prompt (above) | 0/3 | 1/3 | 1/6 |
| Plain "educational diagram" | 2/3, 2/3 | 1/3, 1/3 | 6/12 |
| Plain, with the colour sentence | 2/3, 2/3 | 2/3, 2/3 | 8/12 |

- **Name the diagram, not its shapes.** Asked for the standard diagram in plain words, Muse
  Image drew it as textbooks do, with better-looking art than any prompt before: a shaded
  Sun, parallel rays, a tilted globe, the continents faint under the colours.
- **The colour sentence is needed.** Without it Muse Image paints a realistic Earth, and half
  the time no visible axis or tilt; nor could SAM find a hemisphere on it to label.
- **The tools made no difference** (7/12 off, 7/12 on).
- **What still goes wrong is small.** Three of the four misses stop the rod inside the globe
  instead of out through the south pole; one got the tilt wrong. Meta's `/v1/images/edits`
  ("extend the rod out through the south pole, keep everything else") could mend such a
  picture instead of the art step painting it again from scratch.

What this suggests: say what a scene is the way a teacher asks for it ("Educational diagram
of..."), name the real things rather than shapes ("Earth's northern hemisphere", not "blue
dome"), and give each part's colour in one plain sentence, with no hex codes and no second
list of parts. The style line can follow the subject: cell and molecular biology in the
Campbell/OpenStax manner, anatomy as a medical textbook draws it, astronomy and physics as
their textbooks' clean schematics. That is a change to the bundle prompt's `image_prompt`
guidance and to `art.muse_prompt()`.

## Rounds 3 and 4: the plain request, ElevenLabs songs, a faster round

Round 3 ran both topics end to end with the scene prompt rewritten as the plain request
above (the bundle prompt asks the brain for it, and `art.muse_prompt()` sends it with the
colours by name, no hex codes and no list of shapes) and the song from Eleven Music
(`SONG_PROVIDER=elevenlabs`). Round 4 ran the bundle, art and locate only, with two more
changes from looking at round 3: cell scenes in the classic textbook look, and natural
colours.

| | Round 3 photosynthesis | Round 3 seasons | Round 4 photosynthesis | Round 4 seasons |
|---|---|---|---|---|
| Located | 10/10, none by the box | 17/18, none by the box | failed: 4 of 9 on one scene | 11/12, none by the box |
| Song (Eleven Music) | 137 s, made in 12 s; sung from 9.8 s (cut to 1.0 s) | 112 s, made in 13 s; sung from 0.4 s | not made | not made |
| Aligner unsure of | 0 of 28 lines | 0 of 35 lines | | |
| Cost | $0.39 + 1,167 credits | $0.46 + 1,139 credits | $0.72 (to locate) | $0.48 (to locate) |

- **Eleven Music times far better than Lyria.** Each song came back in about 12 s, exactly
  its planned length (its sections are pinned), and the aligner was sure of every line,
  where it was unsure of 4 to 9 per Lyria song. It too sang one song from 9.8 s, which the
  intro cut caught. Its answer carries no cost header, so the bill shows the song unpriced.
- **SAM found what it could by name and colour.** No structure in rounds 3 and 4 came from
  the helper's box (in round 1, 13 of 30 did); the helper was asked only for what SAM could
  not find at all, mostly round 4's scattered molecules.
- **Cell scenes look like the textbook.** Asked for the look biology textbooks give
  membrane processes ("simple, smooth, rounded shapes with soft flat shading and thin dark
  outlines, not atomic detail; the membrane a phospholipid bilayer of round grey heads and
  yellow tails; each compartment a flat pale colour; ions as small spheres"), Muse Image
  drew the familiar figure in 6 pictures of 6 in a side test, every complex across the
  membrane and ATP synthase's head in the stroma. Round 3's "as a Campbell figure draws
  it" alone gave detailed ribbon proteins students do not know. The bundle prompt now
  gives each kind of diagram its look in those words.
- **Natural colours look better.** A green leaf, a yellow Sun and a blue Earth with its
  continents look like textbook art; a thing painted a colour it never has (an orange and
  pink Earth, a magenta water wheel) looks like a toy. The bundle prompt now keeps natural
  colours, and chooses colours only for what has none.
- **What is still wrong:**
  - *The hemisphere close-ups.* Asked for a globe with its halves "tinted", Muse Image
    still paints them solid, and neither round-4 close-up drew the axis at right angles to
    the equator; one tilted the north toward the Sun in December (the helper said so; the
    picture was kept anyway, gap 1). Textbooks rarely colour hemispheres at all: they draw
    the lit half of the globe and the lines of latitude. Drawing it that way, or mending a
    near-miss with Meta's `/v1/images/edits`, are the next things to try.
  - *Molecules as structures.* The round-4 bundle made water drops, oxygen bubbles and
    NADPH dots structures of their own (9 in one scene); SAM cannot pin scattered dots, so
    the scene failed the step. Small molecules should be drawn, not labelled.
  - *Odd colour choices.* A solid black Photosystem II and a pink stroma: the brain picks
    the colours, and nothing holds them to how textbooks colour those parts.

A test round is now one command a topic, and faster:

- `python -m studio plan ... --videos` writes the bundle, fact-checks it and makes the
  videos; `python -m studio make <job>` makes or resumes a local job's videos in `videos`'
  order, with no API, keeping runs.jsonl so `show` has each step's time, and printing each
  step's one line.
- The art step paints its scenes at the same time, locate finds them at the same time, and
  the audio step asks for the song, narration and exam question at once. Round 4 painted 5
  scenes in 66 s (rounds 1-2: 2-3.5 min) and located them in 27 s (1-3 min).
- Two jobs on one machine time their songs in turn: in round 3 both separated their vocals
  at once and each took 8 times as long (Demucs 5.3 min instead of 40 s), their threads
  fighting for the cores.
- A dropped connection to Meta's API is now asked again; it failed round 3's
  photosynthesis art once ("Connection reset by peer").

## Round 5: a song to remember, and figures redrawn from real ones

Set off by "Split the Water", an AP Bio song video on the same topic (3:20, a karaoke lyric
band over a scanned textbook figure and a slide of text), and the prompt that wrote its
lyrics. Its song sounds like a song; ours read like sentences. Its figure is the real
textbook one, correct and familiar, but small (about 18% of the screen), blurry and still.

### The song

Why ours read like sentences:

- **The brief made the song the lesson sung.** Every line a fact, "leave out jokes", and
  lines led by part names so the labels appear ("Cytochrome b6f pumps protons through").
  The brain wrote headlines with the small words dropped, rhymed 2 or 3 of its 10
  couplets (Split the Water: nearly all), and two lines broke ("NADPH forms to feed the
  Calvin").
- **Each line had two bars**: 4.9 s at 98 bpm, so a six-word line was drawn out. Split the
  Water sings a line a bar, about 2.3 s.

The song and the lesson video now do different jobs: the narration explains every step,
the song is what a learner replays to remember 3 to 5 things. The bundle prompt's song
brief follows the Split the Water prompt's shape: an intro whose first line is a short
title line, a verse, a pre-chorus that busts the misconception, a chant-like chorus that
opens and ends on the title line, a verse, a pre-chorus to the next topic, a bridge that
walks one failure's effects in order (one the facts describe, not the exam question's), a
last chorus with a summary couplet, an outro. Rhyming couplets, a story through the steps,
one metaphor throughout, one surprising fact, terms spelled as sung ("NAD plus"), and a
line need not name a part. Lines are one bar below 130 bpm, two from 130 (one would be
under 1.9 s). Sections may now be `intro`, `pre-chorus` and `outro` too.

| | Spark, photosynthesis | Spark, seasons | Wonder, photosynthesis | Wonder, seasons |
|---|---|---|---|---|
| Lines | 51 at 98 bpm (2:05) | 47 at 150 bpm (2:30) | 50 at 150 bpm (2:40) | 50 at 150 bpm (2:40) |
| Fact-check | 2 rounds, 2 lyric fixes | 2 rounds, 2 lyric fixes | 1 round, no fixes | 1 round, no fixes |
| Bundle + fact-check | $0.30 | $0.28 | $1.16, after a $0.82 try that failed | $1.26 |

- **It sounds like a song now.** Choruses chant ("Tilt to the Sun, it's summer light /
  Straight rays hit hard, the days run bright / Lean away now for the long cold night"),
  the pre-chorus busts the myth ("Closest in January, what a twist"), and the bridges walk
  a failure through ("What if a leak lets protons slip away... NADPH still forms... But
  Calvin starves with no ATP to spend").
- **Muse Spark still writes nonsense to reach a rhyme**: "Calvin builds the sugar from that
  text", "ATP rolls out, the cell's new toy", "Up to NADP plus into the sky". Muse Wonder
  writes clean, correct lines that rhyme, but plainer, nearer to sentences again, at about
  four times the price. Its first photosynthesis plan came back with every field but the
  song (no lyrics, no music styles) and failed validation; the second was fine.
- **Muse Spark wrote "Split the water, catch the light"**, the video's own title line, in 2
  of its 3 photosynthesis songs. The phrase is in none of our prompts.
- **The fact-check made a right line wrong.** The writer's bridge jammed ATP synthase and
  sang "NADPH piles up for a short time" (the standard answer); the fact-check "corrected"
  it to "No NADPH is made", borrowing a fact about a herbicide that blocks electron flow.
  The fact-check prompt now holds each line to its own case, and the facts now cover a
  second failure for the bridge. It also lets attitude through ("you see") and keeps a
  corrected line's rhyme word. (Split the Water's own prompt asked its writer to check
  itself, and still shipped "every hand-off pumps a proton".)

### The figures

The diagrams were wrong because Muse Image drew them from a prompt alone (see "Why the
diagrams come out wrong"). Given a real figure to redraw (Meta's `/v1/images/edits`, two
pictures each at $0.01):

| Asked for | What came back |
|---|---|
| The OpenStax light-reactions figure, redrawn without text | The figure: every complex in its place and order, sharp at 2016x1152; 1 of 2 added a wrong electron path into the interior |
| The same, one colour per complex | The same, colour-coded for SAM; 1 of 2 added a stray dotted loop |
| No figure, the prompt alone (as the pipeline does now) | Five generic proteins in a row, nothing a student would recognise |
| The IAU seasons diagram (its blank version), redrawn | The diagram, label boxes gone, every axis pointing the same way |
| The same, on white | The tilt kept, but some globes lit from the wrong side |
| The June Earth alone, large, from that diagram | Right: the axis toward the Sun, the north pole in daylight, the south in shadow, rays from the Sun's side (the close-up rounds 1 to 4 kept getting wrong) |
| Split the Water's image prompt: each part named with its colour, shape and place, left to right, "leave clear space around each protein for labels" | The parts in the right order and colours, far better than our prompt alone, but lumpy, detailed proteins rather than the flat figure students know |
| The same prompt with the OpenStax figure to redraw | Muse followed the prompt's look and dropped the figure's |
| Split the Water's Photosystem II close-up prompt | Ribbon-model proteins under a glowing beam: striking, not a figure a student would recognise |

A test job (`mock-ps1`) put the colour-coded redraw through locate, timeline and render with
five Split the Water lines at its pace: SAM found all five complexes (one with the
helper's box), the camera moved to each as it was sung, and particles ran from the
cytochrome complex to Photosystem I to NADP+ reductase. One fault: the redraw gave
Photosystem II's core Photosystem I's teal, so it lit up with Photosystem I. "Each complex
one colour throughout" belongs in the redraw prompt.

To make it the pipeline: a figure for each scene (a small library of openly licensed
figures for the demo topics, OpenStax CC BY and the IAU's CC BY diagrams, or an image search
for the rest), the credit on the droplet, a check that the redraw kept the science (the
helper, or the better of two), and the art step's edit call in place of its paint call.
Split the Water's prompt has two things worth taking into the redraw prompt: each part's
colour by name, and "leave clear space around each protein for labels".

**None of it looks like Campbell Biology.** Round 4's prompt asked for "soft flat shading and
thin dark outlines", and a redraw keeps its reference's style, so everything so far looks
like OpenStax: flat, outlined, dotted lobes. Campbell's figures are polished, airbrushed 3D:
the membrane a slab with depth seen slightly from above, sculpted glossy proteins lit from
one side, soft colour washes. Asked for that look in words (two pictures each):

| Asked for | What came back |
|---|---|
| Campbell's look in words, the prompt alone | The look, at last; but the science drifts: proteins sit on the membrane instead of passing through it, no small carriers |
| Campbell's look, redrawing the OpenStax figure | The figure kept its flat OpenStax style, a little shading added |
| Two pictures to the edit call: the OpenStax redraw for what goes where, a Campbell-look picture for the look | 1 of 2 both: the textbook layout, every complex through the membrane in order, in the 3D glossy look; the other stayed flat |
| The same, the membrane as one wide strip | Both copied the style picture, layout and all |

So the look needs a style picture of our own, made once per kind of diagram and approved by
eye (never a Campbell figure itself, which is copyrighted), passed with each scene's layout
reference, two tries a scene and the better kept.

## Not tested

- `python -m studio run`, `videos`, `wait`, `recover` and the upload step: they post to the
  API in `POPSMOSIS_API_URL`, which here is production, and Supabase is not set up.
- `BRAIN_PROVIDER=claude` (the SVG art) as the studio runs it. Muse Wonder as the brain
  only for the bundle and fact-check (round 5), not through the videos.
- The planner's Inkmory record (`INKMORY_ENABLED` is not set).

## Running a round again

```bash
python -m studio plan --topic "..." --course "..." --request "..." --job <id> --brief --videos
python -m studio make <id>        # resume after a failure
python -m studio show <id> --brief
```

For the art alone: `plan`, then `python -m studio step art <id>` and `step locate <id>`.

`ART_PROVIDER` picks who makes the art apart from the brain: `BRAIN_PROVIDER=claude
ART_PROVIDER=muse STUDIO_BRAIN_MODEL=` writes the lesson with Claude and paints it with Muse
Image (the empty `STUDIO_BRAIN_MODEL` clears a Muse model the environment may set). Tried
live on round 5's seasons plan: Claude Haiku checked each picture for under a tenth of a
cent, and SAM found 28 of 29 structures.

The jobs from these rounds were `r1-*` and `r2-*` in `jobs/` (git-ignored, gone with the
container). To look at a video, take a still every few seconds (`ffmpeg -ss <t> -i
out/song_h.mp4 -frames:v 1 still.png`); to check what was located, lay each
`masks/<scene>/<structure>.png` over its `art/<scene>.png`.
