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

1. **A picture the helper says is wrong is used anyway.** After its last try (two since Oct 28) the art step
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
16. *(Fixed: `art/<scene>.json` keeps each try's check under `tries`.)* **A refused picture's check is not kept**: only the next try's prompt quotes it, so
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

## Round 6: Claude writes, Muse Image paints, on dark slate

The brain back on Claude (`BRAIN_PROVIDER=claude ART_PROVIDER=muse`) to save Muse tokens,
the art prompt rebuilt from a study-guide look (soft gradients, semi-realistic organelles,
colour coding, a plain dark slate blue background, space left for labels) plus a sentence on
where each step happens, art only, on AP Bio cellular respiration and the light reactions.

| | Cellular respiration | Light reactions |
|---|---|---|
| Scenes | 5 | 6 |
| Located | 14 of 18; after the repaint below, 16 of 18 | 20 of 21; after, 20 of 21 |
| Cost | $1.71: Claude $1.49 (bundle, fact-check), Muse Image $0.13, SAM $0.08, Haiku's checks $0.01 | $1.71 |

- **Claude could not write a bundle at all.** Its structured outputs refused the bundle's
  schema: "The compiled grammar is too large". Every field compiles alone and the first 12
  together; with `direction` and `tags` the whole does not. No round had run Claude as the
  brain since those were added. A schema refused for that is now asked for in the prompt
  and validated as before.
- **An extra chorus failed a $1.25 bundle.** Claude sang the chorus once more than the
  prompt's order, and `music.sections` did not list it. The song is timed from the lyrics,
  so the list now follows them; that bundle was validated again and used, not rewritten.
- **The overviews are right now.** Respiration: glycolysis in the cytoplasm, the Krebs cycle
  a ring in the matrix, the chain and ATP synthase on the cristae. Light reactions: grana
  stacks and the Calvin cycle in the stroma. Rounds 1 to 4 drew neither reliably.
- **The dark background works in the video.** Parts not yet sung fade into the slate, the
  spotlight dims the rest to it, and the white labels read well.
- **"Semi-realistic proteins" came back as ribbon models** in every close-up. Repainted
  with "proteins and molecules are smooth, rounded, softly shaded shapes, never ribbons,
  coils or clusters of atoms" in the art step's style line: 5 of 6 close-ups came back
  smooth and textbook-like; the ATP synthase close-up turned into a lumpy cluster.
- **The mitochondrion sits beside its cell**, a dumbbell, in both paintings: the scene's
  own prompt put "the cytoplasm on the left" and "a mitochondrion in the middle and on the
  right", and a style line saying parts sit inside what holds them did not override it. The
  bundle prompt now asks for that in the scene's own words.
- **Compartments and small markers are hard to find**: the matrix and the intermembrane
  space (whole regions) and the cyanide and herbicide markers went unlocated.
- **Claude's chorus, too, was "Split the water, catch the light"**: three brains of three
  have now written Split the Water's title line.

## Round 7: the art checker on Sonnet, and a picture without actions

No new lessons: round 6's 28 pictures (kept and refused) checked again by the helper, now
Sonnet, with the old check prompt and the new one ($0.45), and one scene painted from a
prompt Gemini suggested ($0.02, and $0.01 to check).

- **Haiku refused both analogies for not being the topic** ("a dam, not cellular
  respiration") on every try. The checker is now told each scene's kind (hook, overview,
  analogy or close-up), that what a scene leaves out is not missing, and that the video
  animates the flows. On Sonnet, no complaint was about an analogy not being the topic;
  what it listed about them was real (the water held in a bubble beside the dam).
- **Sonnet finds 2 to 8 problems in nearly every picture**, where Haiku passed some. Many
  are real (round 6's respiration overview: the oxygen outside the mitochondrion, the
  pyruvate arrows ending in the intermembrane space). Some are wrong: it called the
  herbicide scene's ATP synthase upside down when its head is in the stroma, where it
  belongs. So nearly every scene now takes its two pictures, about $0.04 a scene.
- **A picture with no actions in it comes out clean.** Round 6's chain close-up asked for
  ions pumped up, crowding, flowing back and oxygen turning into water, and came back
  crowded with gold dots. Gemini's rewrite asks only for the layout: the membrane across
  the middle, a blue space above and a pink matrix below, three purple complexes and a
  green ATP synthase in it. Both of its pictures were clean and well spaced, with room for
  labels and for the video's moving dots. Sonnet passed one and found one problem in the other.
- **But what the prompt leaves out, the painter invents.** That prompt does not say which
  way ATP synthase faces, and both pictures put its mushroom head in the intermembrane
  space instead of the matrix. A still layout needs the facts of where things are and
  which way they face; it is the actions that crowd it.

## Round 8: pictures that hold still

Round 6's two lessons kept, with only their scenes' picture prompts rewritten by Claude under
a new rule ($0.18): a picture is a still stage and the video does the acting, so a prompt
says where everything is (the regions, each part in its region, which way it faces) and
nothing it does. Repainted on Muse Image with two pictures a scene, located with SAM, and
rendered silent. About $1.00 for both, with the repaint and second locate below.

| | Cellular respiration | Light reactions |
|---|---|---|
| Pictures | 14 for 5 scenes (2 scenes painted twice) | 15 for 6 |
| Located | 16 of 18 (round 6: 16) | 20 of 21 (round 6: 20) |
| Cost | $0.51 | $0.49 |

- **The diagrams are clean.** No pumped ions, tangled arrows or crowds of dots: the
  membranes are bands, the proteins stand apart with room for labels, and ATP synthase
  faces the right way in 4 of 5 close-ups (its head in the matrix or the stroma). The
  mitochondrion sits inside its cell in the overview, which round 6 drew beside it.
- **The style line had put bubbles and molecules into everyday scenes.** "Every part sits
  inside whatever holds it" wrapped round 6's dam and pond in cell-like bubbles, and
  "proteins and molecules are smooth, rounded shapes" scattered molecules round the
  sprinter and through the reservoir. The first is gone (each scene's prompt says where
  its parts sit), and the second is now only in a diagram's style. Repainted, the hooks
  and analogies are a plain sprinter, a pond, and two dams that look like dams.
- **Muse Image paints a colour in its own shade.** Asked for green, it painted ATP synthase
  sage; plain Lab distance put its pale highlights nearer the white oxygen than the
  bundle's bright green, so locate dropped it in two scenes, and the pale pink matrix and
  light blue intermembrane space with it. Colours are now compared by CIE94 with the
  structure's colour as the standard and its hue counted in full: a paler or duller shade
  of a colour counts as it, another hue does not. On every part located this round the old
  rule would have dropped 5 right matches, the new one none; but it drops the white oxygen
  in the chain close-up, whose pale blue shading now counts as the intermembrane space (42
  to 46% its own colour, under the 50% it needs).
- **A dark part sinks into the background.** Claude's rewrite asked for the lumen "a deep,
  rich blue", though its colour is light blue; painted navy, 90% of it read as the
  background and it was left out. The bundle prompt now says to name the colour
  `color_hex` gives, never a deeper one.
- **A match with none of a part's colour was labelled.** SAM's "green turbine" came back as
  a band of brown earth across the dam picture, and the label "turbine" pointed at it: a
  match with too little of any part's colour was let through unjudged, a rule meant for a
  picture painted in other colours. Now it is let through only when the picture has the
  part's colour nowhere; otherwise it is dropped, and the helper's box found the turbine.
- **In the silent videos the pictures work and the motion is thin.** The labels sit on the
  right parts and read well on the slate, and in the light reactions the dots go from the
  stroma down through cytochrome b6f into the lumen. But the dots are small, few, and look
  like the ions painted in the picture; they run straight between the parts' label points
  (across the dam rather than along its pipe); they say nothing of what they are (an
  electron, a proton); and a flow loses its start when a part is left out (the lumen in the
  ATP synthase close-up). This is the motion engine's work: routes, molecule markers drawn
  by the renderer, motion on the art.
- **The second picture rarely helps.** Sonnet found something in every first picture (2 to
  5 problems), so all 11 scenes were painted twice, and 9 of 11 kept their first picture
  anyway, the second scoring no better; one passed on its second. What it lists now is
  mostly the picture's own faults (a chute opening too high, bubbles above the water), not
  flows or the analogy. So a picture is now painted again only for writing in it or a part
  missing or out of place (`misplaced`); anything else wrong is recorded. Checked again
  that way ($0.09), 5 of this round's 11 first pictures would be repainted, each for a
  fault of placement (the cyanide outside its complex, the rotor ring in the lumen, the
  herbicide above the membrane): about $0.05 saved a lesson.

## Round 9: marked particles

The motion engine's first piece, on round 8's two lessons and pictures. Each line's direction
now says what its particles carry (`carries`), and the video writes it on the lead particle:
a badge in that kind's colour ("H⁺" gold, "e⁻" cyan, "ATP", a pill for "pyruvate"). Claude
filled in `carries` for the two lessons' 40 moving lines without writing them again ($0.13
for two tries), and both were rendered silent.

- **Claude's choices were right on every line but the analogies**: electrons from
  photosystem II to cytochrome b6f, protons from the stroma through b6f into the lumen and
  back through ATP synthase, pyruvate from glycolysis to the Krebs cycle, ATP to the Calvin
  cycle, nothing marked on sunlight. It marked the dams' water "H₂O", which in the analogy
  stands for the protons; the rule now leaves an analogy's particles unmarked.
- **The badges read at full size** once they were made 44 pixels across: at 36 the "⁺" and
  "⁻" were hard to see.
- **The marked motions are now fact-checked** in the same call as the lines, each distinct
  motion once ("H⁺ travels from Stroma to Cytochrome b6f to Thylakoid lumen"); one that
  fails is left out of the video. Checked live on its own, it passed 12 of the two
  lessons' 13 and caught one the writer got wrong: "pyruvate travels from glycolysis to
  the Krebs cycle", where the facts say pyruvate becomes acetyl CoA first and only its
  acetyl group enters the cycle. Inside the fact-check's one call that is about $0.02 a
  lesson more (alone, $0.04, mostly the facts sent again).

## Round 10: spaces that fill and empty

The motion engine's second piece, on the same two lessons and pictures. The bundle now says
what piles up where (`builds_up`); the timeline works out how full each space is on every
line (filling while marked particles end there, emptying while they start from it, never
below 15%, carried on across scenes), and the renderer fills the space with that kind's
dots, one in three marked "H⁺", and a tint. Asked for `builds_up` on its own, Claude named
exactly H⁺ in the intermembrane space and H⁺ in the thylakoid lumen ($0.02 for both).

- **The crowds show the gradient**: the lumen fills from 15% to 73% on the line where
  cytochrome b6f pumps, and the intermembrane space fills to 73% and runs down to 38% as
  protons flow back through ATP synthase. The light reactions' lumen never empties in
  this lesson: in the ATP synthase close-up, where they leave, the lumen was not located.
- **The spotlight hid them at first**: the pumping line spotlights the pump, which dimmed
  the crowd it was filling. A crowd now stays bright while particles fill or empty it.
- **Painted ions look like them**: round 8's chain close-up has a sprinkle of painted gold
  ions in the intermembrane space, the crowd's own gold, so the crowd reads only by its
  marked dots there. The bundle prompt now asks for no loose ions in a picture (a space is
  a tint), so new pictures will not have them; these two lessons keep theirs until their
  close-ups are painted again.
- **A gold tint turns a blue space olive** at 0.35; it is 0.18 now.
- **Labels stay on** (asked for after this round): a structure named once in a video is
  labelled on every later scene that draws it, from that scene's first line, where before
  each scene started pale and unlabelled until its parts were named again. The light
  reactions' herbicide close-up now opens with photosystem II, cytochrome b6f, photosystem I
  and ATP synthase labelled. A spotlight may fall on anything named earlier in the video.
- **"White spheres" found the membrane**: in the cyanide close-up the oxygen's label points
  at the bilayer, whose lipid heads are white spheres too, so the colour check passes it.
  Open: a `sam_phrase` that also describes the membrane needs a check, or the oxygen a
  colour of its own.

## Round 11: the close-ups painted again

The five membrane close-ups of round 8's lessons, painted before pictures were told to leave
out loose ions, were painted again: Claude rewrote only those five scenes' requests under the
current rules ($0.14), Muse Image painted them with the scene-aware checker (11 pictures with
the chain close-up's second go, $0.11, checks $0.09), and only those five were located again
($0.20; the other six kept their locations). About $0.54 in all.

- **The loose ions are gone** from four of the five (a few stray spheres stay in the
  thylakoid membrane close-up), so the H⁺ crowd is the only gold in a space: in the chain
  close-up it now reads plainly as it fills and runs down.
- **The cyanide now sits inside its complex**, and is located (it was not before); the
  light reactions' lumen is a light blue that is located in the ATP synthase close-up, so
  that lesson's lumen now drains too, from 73% to 38%.
- **A "soft light" tint reads as white.** Claude first asked for the chain close-up's spaces
  as "a soft light blue tint" and "a soft pink tint"; painted that pale, both counted as the
  white oxygen's colour and neither was located. The rules now ask for a space as an even
  wash of its own colour, as clear as its `color_hex`; painted again that way, the
  intermembrane space was located (the matrix was not).
- **ATP synthase stays upside down in the chain close-up**: four pictures across two rounds
  drew its mushroom head up in the intermembrane space, though every request put it in the
  matrix. Muse Image draws the mushroom upright; the rotor ring in the light reactions'
  close-up moved up into the membrane's lower edge, not into it.
- **"Hanging upside down" turned it over.** Asked for ATP synthase "as a mushroom hanging
  upside down from the membrane, its round cap below it in the matrix", both of two pictures
  drew it the right way up for the matrix, and the checker found nothing out of place
  ($0.04). The scene rules now say so in general: the illustrator draws a thing the way up
  it is usually pictured, so a scene that needs it the other way says it hangs upside down.
- **The checker listed a non-problem as misplaced** ("ATP synthase head in lumen side? no:
  head is in stroma, correct") and so painted the herbicide close-up a second time for
  nothing.

## Round 12: parts that turn

The motion engine's third piece, on the same two lessons. The bundle says what turns
(`spins`); the timeline eases each part up to 0.45 turns a second while particles flow into
or through it and down to a stop after, its turns carried on from line to line; the renderer
turns a wheel seen face-on in place and puts a curved arrow round anything else. Claude,
asked on its own, named ATP synthase and the turbine or water wheel in each lesson ($0.04).

- **Claude cannot know how a part looks in the picture**: it called the respiration dam's
  turbine "side" though it is painted face-on, a round wheel. The bundle no longer says;
  the renderer reads it off the picture.
- **Round is not enough**: the light reactions' ATP synthase close-up is about as wide as
  tall, and turned in place it would stand on its head every half turn. A part now turns in
  place only if its outline overlaps itself turned a quarter and a half turn by 60% or more:
  the respiration turbine did (0.79, 0.76); every ATP synthase (0.12 to 0.56) and the water
  wheel, painted at an angle on a wooden frame (0.50, 0.52), get the arrow instead, which
  still shows them turning.
- **The arrow lands well on the rotor**: in the ATP synthase close-up it wraps the rotor
  ring in the membrane while the H⁺ badge passes through.
- **It waits for its part's name**: a part not yet named is a pale ghost, and so is its
  arrow; in the chain close-up "ATP synthase" is said at the end of the line about it, so
  its arrow shows only then. The particles wait for their stops the same way.

## Round 13: blocked flows and parts that light up

The motion engine's last two pieces, on the same lessons. A failure scene's particles now
run to the blocker and pile up there under a red cross (`blocks`); a line can light a part
up (`glow`). Claude set both for the two lessons without writing them again ($0.25 for the
blocks and a first try at glows, $0.07 for the glows again).

- **The blocked flows are right**: "e⁻ flow from electron transport chain is stopped at
  cyanide" and "e⁻ flow from Photosystem II is stopped at Herbicide", both passed by the
  fact-check; in the cyanide close-up the electrons queue up behind the cyanide. The
  herbicide's route is short, so only two or three queue.
- **Asked first, Claude lit up a part on nearly every line**, as if the glow were the
  spotlight. The rule now says a glow is rare and only for an outside cause switching a
  part on. Asked again, it lit photosystem II where light excites it and photosystem I
  where light excites the electrons again (and four lines in respiration, two of them a
  stretch).
- **The fact-check caught a wrong sentence of the code's own**: a spin on a path "water pump
  -> reservoir -> turbine" read "turbine spins as particles flow into it from reservoir and
  water pump"; it is the reservoir's water that turns it. The sentence now names the stop
  just before the part.

## Round 14: repairing a picture in code before painting it again

Built after asking whether the model could repair a wrong picture itself: it cannot paint,
but code can change a picture using the outlines locate finds, and the helper can judge
the result. Two repairs, run in the locate step (`studio/repair.py`); tried live for $0.05.

- **Turning a part over works, and so does undoing it.** On round 11's chain close-up the
  helper named ATP synthase as upside down; code turned it over about the middle of its
  stalk and filled where it had been from the membrane beside it, cleanly. But that
  picture's ATP synthase has two heads, a mushroom cap above the membrane and a ball below,
  and turned over it still has two; the check still found it wrong, and the picture was
  put back as it was. A part drawn upside down can be fixed this way; one drawn wrong
  cannot.
- **Clearing specks first erased a space's own edge**: 64 "specks" in the light
  reactions' lumen were its anti-aliased lower edge, which came out ragged. A speck must
  now lie wholly inside the space. With that, the lumen had none to clear: the stray
  spheres in that picture sit on the background below it, which is left alone, since a
  small thing on the background may be a structure SAM did not find.

## Round 15: mitosis, end to end under every new rule

A new topic written from scratch ("Can you teach me mitosis for my AP Bio exam?"), through
every step but the upload: Claude's bundle and fact-check, Muse Image pictures with the
scene-aware checker and repair, SAM, the Eleven Music song and ElevenLabs narration,
Demucs and MMS timing, and both videos. $1.72 (Claude $1.54, Muse Image $0.08, SAM $0.10)
and about 1,500 ElevenLabs credits for the narration; 20 minutes.

| | |
|---|---|
| Fact-check | passed in 2 rounds, 1 fix: "Every copied chromosome's an X" became "A copied, condensed chromosome's an X" |
| Pictures | 9 for 5 scenes; 17 of 18 structures located (the metaphase close-up's centrosomes missed) |
| Song | pop-punk, 144 bpm, 150 s; all 45 lines timed, chorus drift at most 0.38 s |
| Videos | song 152 s, lesson 229 s |

- **The pictures are mostly strong.** The metaphase close-up is a clean textbook figure,
  chromosomes in a line with the spindle fibres meeting each one; the drug close-up and
  the hook (a scraped knee, its skin cells dividing under a magnifier) work too.
- **The overview's anaphase is wrong**: its fourth cell carries X-shaped chromosomes to the
  poles, where separated single chromatids are the point of the stage. The checker flagged
  it on both pictures, and two was the limit. Its cells' outer membranes are also painted
  the yellow the bundle gave the nuclear envelope.
- **The motion engine has nothing for parts that move.** Mitosis is chromosomes condensing,
  lining up, splitting and travelling to the poles; the engine moves dots between parts.
  So "the microtubules shorten, reeling sisters to opposite poles" shows dots hopping from
  a chromosome to a centrosome in the telophase picture, and the split itself is never
  seen. The drug scene's blocked pull (dots stopping at the drug under a cross) reads well.
  Topics in stages (mitosis, meiosis, the cell cycle, the moon's phases, a life cycle)
  need either a picture per stage the video moves through, or parts that move.
- **Labels carry across scenes as intended**: the drug close-up opens with "Centrosomes",
  "Spindle" and "Chromosomes" already labelled. A structure the overview draws five times
  over (one per cell) is labelled once, on one of them.
- **Nothing to repair**: the faults left were shapes and places, not a part upside down.

## Round 16: mitosis in stages, three ways to draw it, and saying what is wrong

Mitosis again (`r14-mitosis`), written fresh under the stage rule: each stage a scene of its
own, every stage after the first redrawn from the first stage's picture, and the video
fading from one stage into the next. Claude wrote seven scenes: the hook (a healing
scrape), prophase, the analogy (a tug-of-war), metaphase, anaphase, telophase and a
drug-blocked spindle. The four stages each open with the same framing sentence, word for
word, and there is no overview. The run cost $1.77 in metered calls (the song and the
voice are not priced), and SAM found 20 of 20 parts.

- **The stages fade into each other.** The song fades three times from one stage to the
  next, the lesson twice. The redraws kept the look (the same cell, the same colours), but
  not the framing or the science: anaphase came back a wider oval, so halfway through the
  fade two cell outlines show. It also came back with eight chromosomes on each side,
  where metaphase's four should give four. The check saw the anaphase chromosomes' bends
  pointing the wrong way, but not the count.
- **Going back a stage cut, rather than fading, after this round.** The song went from
  telophase back to anaphase over a fade, and the cell seemed to come back together.
- **Claude's SVG, from the same plan** (`ART_PROVIDER=svg`, `r14-svg`): every stage right,
  four chromosomes to each side in anaphase, the hook's new cells in the bottom of the top
  layer of skin, where Muse Image put them deeper. Every stage the same cell at the same
  size in the same place. Plainer than Muse Image's pictures, and its tug-of-war teams were
  stiff. $1.90 for the seven scenes, 14 minutes one after another; every part's outline
  comes with the drawing.
- **Claude's SVG restyled by Muse Image** (`/v1/images/edits`, the drawing as the
  reference, 7 pictures, $0.07): the parts stayed put. Lined up by the box round what is
  drawn, 81 to 97% of each part of the four stages was in its own colour where the drawing
  had it; the analogy's people moved more (33 to 53%). But the restyle added little: a
  dark background and softer shading.
- **OpenStax was not tried.** Biology 2e's mitosis figure is the stages done right, in one
  framing, but the book is CC BY-NC-SA and its page says it may not be "ingested into large
  language models or generative AI offerings without OpenStax's prior written permission".
- **What is left wrong is now said beside the picture.** The art check also writes, for
  each problem that would teach something false, a sentence for the student, and leaves out
  matters of look ("the cell is oval, not round"). Checked again with it (`r14-notice`,
  about 6 cents), three of the seven pictures had notes: the hook ("The purple new cells sit
  in the deeper skin as a loose row; in truth, dividing cells lie in the base of the top
  layer, just under the scrape."), prophase (the spindle already across the cell) and
  anaphase (the bends, and the fibres on the chromosomes' ends rather than at their
  centromeres). The video shows them on a white card at the right, under "AI-generated
  picture", for as long as the picture is on screen; the picture moves left to make room,
  and so do the other stages of its process, to keep one framing. Since the check can miss a
  mistake, as it missed the anaphase count, every other picture carries a small
  "AI-generated picture" tag at its bottom left, the SVG drawings too.

## Not tested

- `python -m studio run`, `videos`, `wait`, `recover` and the upload step: they post to the
  API in `POPSMOSIS_API_URL`, which here is production, and Supabase is not set up.
- The SVG art (`ART_PROVIDER=svg`) through the videos: round 16 drew one plan's scenes with it,
  but made no videos from them. Muse Wonder as the brain only for the bundle and fact-check
  (round 5), not through the videos.
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
