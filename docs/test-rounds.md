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

## Round 17: three topics drawn at random, each sung by Eleven Music and by Lyria

Oct 30. Three topics drawn at random from 24 that no round and no scorecard topic had used:
**World History: why the Roman Republic fell** ("How did Rome go from a republic to an
empire?"), the first history topic through the videos; **6th grade science: the rock cycle**
("How does one kind of rock turn into another?"); and **Anatomy and Physiology: how the kidneys
filter blood** ("How does a nephron make urine?"). Each ran `plan` on Claude Opus 5.5 at high
effort, then `make` with what `/admin/studio` has now written to the job's `settings.json`
(Gemini 3.1 Flash Image painting, Eleven Music singing). The finished job was then copied, the
copy's song switched to Lyria 3.5, and `make` run on it again: the song, its timing, the
timeline and the videos were made again on the same lesson, pictures and narration, so the two
singers are compared on the same lyrics. All three topics ran at once on one 4-core container;
nothing was posted.

| | Rome | Rock cycle | Kidneys |
|---|---|---|---|
| Audience | high school | 6th grade | high school |
| Fact-check | passed in 2 rounds, 1 line fixed; 1 motion left out | passed in 1 round | passed in 1 round |
| Pictures | 7 for 6 scenes | 5 for 5 | 4 for 4 |
| Located | 15 of 17, 2 by the helper's box | 15 of 16 | 13 of 14, 3 by the box |
| Narration | 188 s | 182 s | 181 s |
| Lesson (bundle, audit, fact-check) | $2.11, 14 min | $1.83, 12 min | $1.79, 11 min |
| Videos | $0.63 + 1,137 credits | $0.44 + 1,126 credits | $0.41 + 1,122 credits |
| Topic to both videos | 32 min | 27 min | 20 min |
| Sung again by Lyria, to both videos | 17 min, $0.08 | 19 min, $0.08 | 18 min, $0.08 |

The videos' dollars are the pictures (Gemini, about 7 cents each with its check) and SAM; the
credits are the narration's. Eleven Music sends no cost header, so its songs are unpriced; a
Lyria song is $0.08. The times are long because the three topics' timing steps ran at once:
Demucs took 3 to 7 minutes a song, and the timing step 4 to 14.

### The two singers

| | Eleven Music: Rome | Rocks | Kidneys | Lyria: Rome | Rocks | Kidneys |
|---|---|---|---|---|---|---|
| Made in | 16 s | 16 s | 22 s | 46 s | 38 s | 43 s |
| Length, planned 127 / 125 / 132 s | 126.6 s | 125.0 s | 132.0 s | 131.6 s | 130.8 s | 133.3 s |
| Aligner unsure of | 7 of 58 lines | 0 of 50 | 0 of 55 | 9 of 58 | 12 of 50 | 16 of 55 |
| Mean aligner score | 0.55 | 0.67 | 0.67 | 0.61 | 0.55 | 0.43 |
| Chorus drift, at most | 3.33 s | 0.04 s | 0.06 s | 1.11 s | 2.67 s | 1.09 s |

Both sang from the first second, at the tempo asked (a rough beat count of each song), and
Lyria sang every line as written in two of three songs.

- **Eleven Music timed far better on two of three.** On the rock cycle and the kidneys the
  aligner was sure of every line and the choruses repeated to within 0.06 s, as in round 3. Its
  sections are pinned, so it keeps the planned length to the tenth of a second.
- **Rome was the exception, on both.** Its lines are long and full of names and dates ("And the
  Senate named him Augustus in twenty-seven BC"), one bar each at 110 bpm. Eleven Music's
  last chorus came out with one line timed at 5.4 s and the next at 0.8 s, the outro's "Cross
  the Rubicon, there's no way back home" at no length at all (it never shows in the band), and
  the second verse's first line, ten words, in 0.3 s. The aligner scored that line 0.34,
  just above the 0.3 that counts as unsure, so nothing flagged it; a check on words a second
  would. Lyria's Rome drifted less and timed every line at 0.56 s or more.
- **Lyria left the kidney song's outro out, and the timing step does not know.** Lyria's answer
  carries a `text` of what it sang (kept in `audio/song.plan.json`): for the kidneys it stops
  after the last chorus, without the three outro lines, and its vocals go quiet at 130 s. The
  aligner, asked to find the outro after the last chorus, put the chorus's last four lines and
  the outro's three into the song's last 7.4 s, about a second each (scores 0.02 to 0.10), so
  the band flashes lines that are never sung. For Eleven Music the step marks a section with no
  singing as not sung; for Lyria, whose sections are not pinned, that check is skipped. Reading
  Lyria's text and marking the lines it leaves out as not sung would fix this.
- **Lyria says when it sang each line, sometimes.** Two of its three texts give each line a
  start time on the bar grid (`[12.5:] Deep underground, the magma cools so slow`). They
  match the aligner: every rock-cycle line within 0.9 s of where it was aligned (most 0.1 to
  0.5 s after, the voice coming in after the bar), every Rome line within 1.7 s, chorus lines
  the aligner scored unsure among them. They could give the aligner each line's window, or
  check it. The kidney text had no times.
- **Lyria makes the song in 40 s or so, Eleven Music in 20.** Lyria ran 1 to 6 s past the
  planned length.

### The pictures

- **Fixed: a label on the wrong part, from naming colours wrong** (`studio/steps/art.py`,
  `colour_word`). The art step asks for each part's colour by name, and named Material Pink
  (#F06292, hue 340) "red" and Material Purple (#9C27B0, hue 291) "pink": purple stopped at 290
  degrees, pink at 335. Rome's overview, a timeline the bundle asked for as blue, pink and
  purple, was asked for as "Roman Republic blue, Civil wars red, Roman Empire light pink", and
  painted that way; SAM's "pink bar" then found the Empire, and the label "Civil wars" went on
  it for the whole video. Purple now runs to 310, pink to 345, and a light red is called pink
  (#FFC0CB was "light red"). Of 159 colours in this round's and the scorecard's bundles, 9 are
  named differently, each now as it looks. Repainted with the fix ($0.08 for the picture and
  its check), the timeline came back blue, pink and purple, and the two labels sat on their own
  sections. Its blue came back a deep navy, far darker than asked, and the Republic was left
  out, as round 8's navy lumen was.
- **A map of Caesar's time drawn with today's borders.** The bundle asked for "Gaul, the land
  of modern France" and "the Italian peninsula", and Gemini drew modern France and modern Italy
  (its first try had every European border of today and was refused, for a stray dot). The
  Rubicon, which in 49 BC divided Italy from Cisalpine Gaul, Caesar's own province, sits inside
  "Italy"; the song sings, rightly, that he crossed it into Italy. The checker passed the map.
  The Rubicon, a short cyan squiggle, was not located, so the army's route runs from Gaul's
  label to Italy's across the sea west of Corsica. History maps need the period's borders, and
  saying so to the checker.
- **A box mask with holes shows as speckle.** SAM could not find the kidney close-up's Bowman's
  capsule, a pale ring and the pale space inside it, by any phrase; the helper's box gave a
  mask with holes in the pale space, and each time the song spotlit the capsule the holes
  darkened to grey blotches around the glomerulus (round 1's speckle, on a pale space).
- **The science and the everyday scenes look like the textbook.** The kidneys, ureters and
  bladder in the body; the glomerulus in Bowman's capsule; a nephron with its loop, the
  capillaries around it and a collecting duct; the rock cycle as block diagrams a 6th grader
  can read, with a volcano, a magma chamber and a purple metamorphic band. The everyday scenes
  are clean (a backpack, a bed and a bin; crayon shavings, a pressed block, a swirled block and
  melted wax; an office, a boss with pay envelopes and his workers), and so are Rome's: small
  farms beside a walled estate, a general on horseback between the Senate and his legions,
  Augustus as the Prima Porta statue.
- **Rome's overview was a bare bar.** A timeline in three colours is a diagram with nothing
  in it to look at, on screen for 22 of the song's lines (50 s); a topic told in time needs its
  overview to be a scene, or several.

### The lessons

All three passed the fact audit in one call and the fact-check, 97 to 102 lines supported and
none unsupported. The rock cycle was written for the 6th grade its course named; the kidneys,
asked for in an anatomy course with no grade, for high school. The fact-check's one fix
(Rome) turned "You've seen this pattern before" into "The same pattern shows up later", since
the facts do not say the learner has seen it, and it left out one marked motion, an army
travelling from the legions to the Senate. The songs carry their metaphors through
(cleaning out a backpack; a crayon shaved, pressed and melted; a boss who pays the workers), and
each bridge walks a case the facts give: diabetes filling the filtrate with more glucose than
the carriers can take back; limestone to marble to sediment without ever being igneous; Sulla
marching on Rome.

## Round 18: the same three topics, sung, and watched as a student would

Oct 30. Round 17's three topics again, after the changes its videos asked for: the songs'
lines short and sung (6 to 9 syllables, verses of 8 lines, devices from songwriting rather
than sentences), each section given the time its lines take to sing and the song as long as
that makes it (90 to 240 s), Lyria's own account of what it sang used to find a line it left
out; labels that stay where their part is, while what moves is a particle (a glucose
hexagon, a water drop, a short symbol such as H⁺ printed on its dot) named once in a legend;
the pictures asked for no label lines; the particles taking each leg of a flow through the
parts themselves where their outlines join (`studio/render/routing.py`), with no arrows
drawn; and a narration editor (`studio/steps/narration.py`). Same set-up as round 17: `plan`
on Claude Opus 5.5 at high effort, Gemini painting, Eleven Music singing, then a copy sung by
Lyria. Nothing was posted.

| | Rome | Rock cycle | Kidneys |
|---|---|---|---|
| Lesson (bundle, audit, fact-check) | $2.12 | $1.68 | $1.89 |
| Pictures and SAM | $0.55 | $0.96, one painted again twice | $0.68, one painted again |
| Song, Eleven Music / Lyria | 158 s / 182 s | 154 s / 154 s | 158 s / 173 s |
| Narration | 180 s | 178 s | 191 s |

The kidneys' Lyria song was blocked once (PROHIBITED_CONTENT) and the studio sang it with
Eleven Music instead, silently: a round has to read `provider` in `song.plan.json` before
calling a song Lyria's. Asked again, Lyria sang it. Two pictures came back with painted
pointer lines where labels would hang (the "sticks"): `NO_TEXT` now rules them out and the
picture check lists them, and the repainted pictures were clean.

### The songs

Measured on the vocals Demucs separated, round 17's songs were sung at 4 to 6 words a second,
a rap's pace, and round 18's at 2.5 to 3.3. Eleven Music's sections are all about as loud as
each other (the kidneys': verse -20 dB, chorus -19, bridge -17), where Lyria's chorus stands
out (-19 against -21 to -23), which may be why Eleven Music still sounds like shouting. Rome's
song sang its story out of order: Augustus, then "Eighty-eight BCE, rewind" for Sulla in the
bridge. The kidneys' song video cut to a picture for a single two-second line six times.

### Watched as a student

Each lesson video was watched a frame a line, asking at each whether a student who has not
met the topic would know what they are looking at and why it comes next:

- **No question to answer.** The kidneys' hook gave five facts (where the kidneys sit, 180
  liters, the bladder, 99 % taken back, a million nephrons) and never asked anything; Rome's
  showed the US Capitol. The question was on screen, in the key line, but the voice did not
  ask it.
- **Talking about what is not there.** "Rome elected two consuls each year" over four
  generals; "lava that cools fast at the surface makes rock with tiny crystals" over a
  volcano with no lava and no igneous rock drawn at all; "each kidney holds about a million
  nephrons" over a body with no nephron in it, then a cut to a nephron from nowhere.
- **The correction before what it corrects.** Rome's "a common mistake is to think Julius
  Caesar was Rome's first emperor" came 32 seconds in, a minute before Caesar did, as the
  prompt asked ("right after the overview").
- **Side roads.** Pumice that floats, granite countertops, "the same osmosis your own cells
  know", Kaiser and Czar: each true, each a turn off the path.
- **The ending three times.** The kidneys' lesson told diabetes, then the core idea, then
  went back to the body for "blood flows in, and urine flows out".
- **Dots for people.** Rome's armies marched as purple dots marked "troops", which read as
  sparks next to the drawn soldiers.
- **A crossing named but not shown.** The kidneys' close-up named secretion with nothing
  moving: the fact-check had cut its motion, since no fact put secretion in the proximal
  tubule the close-up was of.

The narration editor, tried on the kidneys' narration by itself ($0.15), fixed the order: it
opened on "So how does your body keep what it needs and get rid of its waste?", told the
diabetes case after the path, and ended once. It cannot fix a sentence about something no
picture draws, since the pictures are planned before it reads anything. Round 19 writes the
lesson first.

## Round 19: the lesson written first, then everything around it

Oct 30. The same three topics through the new bundle step: the script first (`studio/steps/
script.py`), the bundle call handed it and held to it, then the narration editor. In the
videos, particles take each leg through the parts, a cut into a close-up pushes in on the part
first, and a picture shown for a single sung line gives way to the one before it.

| | Rome | Rock cycle | Kidneys |
|---|---|---|---|
| Script | $0.36, 2.6 min | $0.19, 1.3 min | $0.29, 2.7 min |
| Bundle | $1.55 | $1.37 | $1.92, 14 min |
| Narration edit | $0.07: 6 of 35 reworded, none moved | $0.11: 11 of 33 | $0.07: 4 of 31 |
| Kept of the script | all: essence, facts, 35 sentences | all, 33 | all, 31 |
| Fact-check | passed, 1 round, no fixes | passed, 1 round | passed, 1 round |
| Narration | 176 s | 155 s | 181 s (stand-in voice) |

Every bundle kept its script whole: the facts and the essence word for word, every sentence on
the scene the script put it, none reworded. The editor then moved no sentence, since the script
already had them in order, and only smoothed words. All three fact-checks passed in one round
with nothing to fix, against round 18's fixes and a motion cut.

### The lessons

Read and watched as a student would, each is now one explanation. Each hook ends on the
question ("So how does one kind of rock turn into another?"); the analogy is laid out as
signposts the path then uses ("That's putting things back in the backpack: reabsorption";
"just like the pressed crayon shavings"); each step is signposted ("Now for real rock,
starting with the volcano", "Next, look at the mountain", "Back in the nephron"); the predict
question comes mid-way; the misconception comes where it forms (Caesar corrected right after
his death, not before he appears); a worked case comes after the path (a granite mountain to
sandstone to quartzite; diabetes); and each ends once, on the core idea. Rome's story runs in
time order, year by year, from the Gracchi to Augustus, and its song does too.

What was still wrong:

- **The rock cycle never labelled igneous or metamorphic rock.** The script's overview asked
  for "a zone of deep rock" rather than metamorphic rock, so no structure carried the words
  the narration says. The script now asks for each key term that is a thing to be drawn and
  named in the narration's words.
- **A label on the wrong thing.** SAM answered "grey mountain" with the mountain and a pale
  purple band underground (the picture was too muted for the colour check to tell them
  apart), and "Mountain" was pinned on the band; "Volcano" took the whole ground. The locate
  step now has the helper look at each scene with every label's pin drawn on it, and finds a
  wrong one again in the helper's box: on the same picture it moved both onto the right
  things, for under 3 cents a topic.
- **A part SAM could not trace.** The kidneys' glomerulus, a knot of thin pink tubes, was no
  one shape to SAM even boxed, and its close-up went unlabelled. A structure SAM cannot trace
  in the helper's box is now the pixels there in its own colour.
- **Rome's overview was a painted map,** and it came back wrong (the Senate house in the sea,
  Marius in Sicily), with the art check's notice of it on screen for the whole overview. A
  story in time now lines its people up left to right, like a timeline, instead.
- **Rome's song corrected Caesar before singing him,** in the first pre-chorus, which the brief
  gives the misconception. It now moves to the second when the first verse has not reached it.
- **The editor drew one sentence out to 25 words,** and the caption shrank to fit. A sentence
  it lengthens past 22 words keeps the writer's words.

### The songs and the pictures behind them

No song cut to a picture for less than 4.3 s (round 18's kidney song did so six times for 2 s).
The songs sit on the overview for most of their length (Rome's for 48 s and then 71 s), the
camera moving over it. Timing Lyria's songs is still the weak point: the rock cycle's had 20 of
50 lines the aligner was unsure of, against 5 for Eleven Music's, since Lyria is aligned over
the whole song at once and sings backing vocals and echoes the lyrics do not have.

### ElevenLabs ran out

The kidneys' Eleven Music song was refused: the ElevenLabs quota (131,000 credits) was spent,
45 left, the song needing 2,197. Narration is ElevenLabs too, so the kidneys were made with
Lyria's song and, for this round only, a stand-in voice: Gemini TTS (gemini-3.8-flash-tts) a
sentence at a time, timed by the timing step's MMS aligner and written as the character
timings the ElevenLabs call returns (a scratch script, not a studio feature). Gemini TTS reads
any instruction aloud ("Say warmly:" made a 5.5 s sentence 9.7 s), so it is given the
sentences alone; one call for the whole narration never came back.

## Round 20: the same three topics, with round 19's fixes

Oct 30. The script asked to draw the key terms and to line a story in time up as a timeline
of its people; the locate step checking where each label is pinned and finding an untraceable
part by its colour; a young audience's song asked for the pop they play. ElevenLabs had no
credits left, so every song is Lyria's and the narration the stand-in voice (round 19), one job
a topic.

| | Rome | Rock cycle | Kidneys |
|---|---|---|---|
| Lesson and pictures | $2.67 | $2.74 | $3.19 |
| Script kept | all, 32 sentences | all, 31 | all, 31 |
| Fact-check | passed in 2 rounds, 1 fix | passed in 1 | passed in 1 |
| Located | 26 of 26, 14 in the helper's box | 21 of 23 | 27 of 27 |

- **Rome** opens on July and August, named for Caesar and Augustus ("So how did a republic
  that hated kings end up with an emperor?"), lays the club analogy out as signposts (the
  committee is the Senate, the captains the consuls, the bouncers the legions: "once a captain
  pays them, they obey him"), and walks a timeline line-up of the Senate, Marius, Sulla, Caesar
  and Augustus, each with his legions, the camera moving along it as each is named. The one
  thing that moved, the pay, travels from Marius to his legions. Caesar is corrected right
  after the Ides of March, and the song's second pre-chorus corrects him after the verse that
  sings him.
- **The rock cycle** labels igneous, sedimentary and metamorphic rock where each forms (the
  igneous rock was painted too faintly for SAM, the helper's box or its colour to find, and
  went unlabelled), maps each kind to a crayon (shavings, pressed block, bent block, melted
  into a mold), and works Hawaii's black sand as its case.
- **The kidneys** show the nephron as "one of about a million tiny tubes like this", go into
  each close-up from the step that needs it and back ("Back on the main path"), and every part
  was located, the glomerulus included.

Still in the way: the art check's notice card, which lists what a picture still gets wrong
beside it, stood beside the Rome analogy for the whole scene, for four committee members where
the analogy had five. An analogy's picture now discloses nothing: it cannot teach the topic
wrong. Elsewhere the card is a choice the product made, and it is on screen whenever a picture
keeps a mistake (the rock cycle's metamorphic rock "far above the magma").

## Round 21: three topics the studio had never seen

Oct 30. Three topics drawn from the pool round 17 drew from, none tuned on: **AP World
History: how the Silk Road connected the world**, **7th grade life science: how the stomach
and intestines digest food** and **Anatomy and Physiology: how the lungs exchange gases**. The
draw first gave a second history topic (the causes of World War I); it was drawn again from
outside history, for a third subject. Lyria and the stand-in voice, as round 20.

| | Silk Road | Digestion | Lungs |
|---|---|---|---|
| Lesson and pictures | $2.96 | $2.89 | $2.90 |
| Audience | high school | 7th grade | high school |
| Script kept | all, 31 sentences | all, 29 | all, 32 |
| Fact-check | passed in 1 round | passed in 2, 2 fixes | passed in 1 |

Each is one explanation, in the shape the script asks for: a hook that ends on the question
(paper and silk from China; a sandwich gone in a second that your cells cannot use; the lungs'
70 square meters), an analogy whose parts become signposts (a relay race whose handoff zones
are oasis cities; a LEGO model taken apart into bricks small enough for the "mail slot" of the
intestine's wall; a train at a crowded platform, its open doors the respiratory membrane), the
steps in order with "First", "Next", "Back on the map", a predict question (what happens to the
price of silk at each stop?), the misconception where it forms (one road travelled end to end;
the stomach doing most of the digestion; the lungs pumping oxygen), a worked case after the
path (the Black Death riding the relay; lactose intolerance; pneumonia) and one ending.

### The songs, heard by Gemini

None of these songs could be heard here, so Gemini (gemini-3.8-flash) listened to each, as a
producer and as a 16-year-old (a scratch script, under a cent a song). It is one model's ears,
so its numbers are for comparing versions of a song, not alone, but it tells a bad song from a
good one: round 17's kidney song, sung at 4 to 5 words a second, it heard as "spoken or
rapped", melody 4 of 10, overall 6.5, and it caught round 17's Rome song stepping back in time.

| | Vocals | Melody | Chorus sticks | Would play it | Overall |
|---|---|---|---|---|---|
| Round 17, kidneys, Eleven Music | spoken or rapped | 4 | 8 | 5 | 6.5 |
| Round 18, kidneys, Eleven Music | sung | 8 | 8 | 7 | 7.5 |
| Round 18, kidneys, Lyria | sung | 8 | 8 | 7 | 8 |
| Round 18, kidneys, Eleven Music with section cues | sung | 8 | 9 | 7 | 8 |
| Round 19, rock cycle, Eleven Music / Lyria | sung | 7 / 8 | 8 / 8 | 4 / 5 | 7 / 7 |
| Round 19, Rome, Eleven Music / Lyria | mostly sung / sung | 7 / 8 | 8 / 9 | 6 / 7 | 7 / 8 |
| Round 20, Rome / rock cycle, Lyria | sung | 8 / 8 | 9 / 9 | 7 / 6 | 8 / 8 |
| Round 21, Silk Road / digestion, Lyria | sung | 8 / 8 | 8 / 8 | 7 / 6 | 8 / 7.5 |

Lyria's songs heard a little better than Eleven Music's on the same lyrics, as the owner heard
them. The rock cycle's round 19 song, asked for "playful pop", it heard as "an elementary school
curriculum jingle" (would play it: 4); round 20's, written before the young style changed, 6.
What it still marks down: bridges crammed with facts, and a corny shouted line in the 7th grade
song ("Bloated belly!").

The section cues are a scratch test on Eleven Music (each section's chunk given its own
delivery: soft and close in the verses, full in the chorus, stripped back in the bridge, and
shouting, rap and rushing avoided), made from round 18's kidney lyrics, with a second version
given more time a line; neither is in the studio, and they wait for the owner's ears.

### Then, from what rounds 20 and 21 showed

- **A note that taught a falsehood.** The helper's note beside round 20's kidney close-up said its glucose
  carriers belonged on the capillary's side, which is false for the carriers that take glucose out of the
  filtrate: the card meant to disclose a flawed picture taught something wrong itself. A note is now shown
  only once the brain, given the picture and the lesson's facts, has not found it false or about something the
  picture does not show (`confirmed_notes`). Asked to keep only notes it was sure of, it dropped 7 of 8 and
  flipped on one between two asks, so it keeps what it cannot settle: the video would rather disclose a doubt
  than hide a mistake. Told so, it kept 5 of 7 notes on rounds 20 and 21 and still dropped the false one.
- **Too much on screen.** Round 21's digestion overview, for a 7th grader, showed seven labels, two reaction
  pills ("food → chyme") and a five-row legend at once. The timeline now records the parts each line says,
  and past four labels on screen, a label (and its reaction pill) rests faint unless this line or the one
  before names its part, it is spotlit or framed, or particles travel to it. Every label stays where its part
  is; the eye goes to the one the voice means.
- **A stale banner over the chorus.** The song showed the last verse's key line over its first two choruses
  ("Gracchi killed → politics settled by force"). Every chorus now shows the core idea it sings.

Rounds 20 and 21 were drawn again with all of these (timeline and render only, no paid calls).

## Round 22: three more new topics, one of them abstract

Oct 30. Every fix above, on three topics drawn from subjects not yet tried: **AP Biology: how
natural selection works** ("Is evolution the same thing as natural selection?"), **Environmental
Science: how greenhouse gases warm the Earth**, and **Personal Finance: how compound interest
grows money**. The draw first gave plate tectonics, too near the rock cycle; it was drawn again
between the two topics with no process to draw, the Pythagorean theorem and compound interest.
Lyria and the stand-in voice; $9.17 for the three ($3.04, $3.05, $3.09), every script kept whole,
the fact-checks passed (natural selection's with 2 fixes in 2 rounds).

- **Natural selection** is the night's clearest video: a vending machine restocked by its sales
  as the analogy, three generations of beetles painted side by side with a bar under each
  shifting from green to brown, a bird's close-up where the green beetle stands out ("Which
  beetles do you think it spots first?"), the misconception right after (no beetle changed
  colour), antibiotic resistance as the case in two dishes, and a rockfall for drift, so the
  ending can answer the question: selection is one cause of evolution, not the same thing.
- **Greenhouse gases**: a bathtub with its drain partly plugged; the close-up of the air brings
  nitrogen, oxygen, carbon dioxide, water vapour and methane forward one at a time as each is
  named, the rest resting.
- **Compound interest** reads well (a snowball rolling down the hill of time; $1,000 at 10 %
  worked year by year; a close-up of the $110 that shows the $10 interest earned) but its
  painted chart contradicts it: simple-interest bars taller than the compound ones and every
  year's interest the same height, the four mistakes the check found listed beside it for the
  whole overview. A painter cannot draw numbers to scale; a scene with axes needs drawing (the
  studio's SVG art), not painting. Its hook also gave the answer away before the question,
  which the script asks it not to.

Gemini heard the three songs as sung: overall 8 (natural selection, greenhouse) and 7 (compound
interest, "cluttered with numbers").

### Then: the math drawn, and its numbers written

Compound interest again, from a copy of round 22's job, with its four chart scenes drawn by
Claude as SVG (the overview's bars, the stacked bar, the two savers' curves, the credit card's
bars) and the hook and analogy kept as Gemini painted them; the stacked bar marked `to_scale` by
hand, since the bundle came before the field. Each drawing took one try, about $0.07, and came
out to the facts' numbers exactly: $1,000 of principal as 550 px, the years' interest as 55, 60.5
and 66.55 px ($100, $110, $121), simple interest's totals as 605, 660 and 715 px. No notice card:
nothing was wrong to disclose. Two things the first render showed, both fixed: the graph look
flattened each block's outline into its own colour, so the three years of interest ran together
as one gold slab (now a darker outline, svg.LOOK 2); and a chart read only by its bars is half
read. Drawn again, the drawer marked its values ("$1,210" over the year-2 bar, "Year 2" under
it), and each amount now appears on the word that says it: "$1,100" at 76.9 s, "$1,210" at 90.8 s,
"$1,331" at 126.6 s, "$10,290" and "$5,740" as the two savers are compared. Still to fix: the close-up
wrote "One hundred dollars" (its sentence spells it out; the drawer now writes figures), and its
pills crowd the thin top blocks, which is what a director's review would catch
(docs/director-review.md).

## In-session tests, Oct 31: the fact-check, the songwriter and title lines

No API calls: subagents stood in for every model call (CLAUDE.md), on the 12 scorecard topics' droplets written
in-session on the day's prompts.

**The fact-check is not degrading the songs.** Each droplet's round-1 call, built by the code
(`check_bundle`) and answered by a subagent on `prompts/factcheck.md`, failed 22 of about 1,500 lines and 3 of
228 song couplets (the audit step was skipped: the facts as the brain wrote them). Its fixes were mostly right
("Spanish verbs come in two moods" is wrong: there is the imperative too; "The secret's shared with only us" became
"The suitors miss what's plain to us", rhyme kept). Two findings:

- Four droplets wrote "Most people say yes", copied from the script prompt's example, and the checker softened each
  to "Many people": the example now says "Many people", and why.
- Its fix to the sodium pump's chorus, "Open a gate, the rush begins / Sodium out, potassium in", flipped the
  title line to "Potassium out, sodium in", so the chorus no longer ended on its first line.

**The songwriter** (`prompts/songwriter.md`), on those 3 couplets and 6 the scorecard judge had flagged, the old
prompt against a new one carrying the song brief's craft and an appeal, judged blind by a Sonnet subagent: every
rewrite of both true, the new one preferred 3 to 1 (5 ties), singing 3.67 to 3.44 of 5. The checker was right on 7
of 9; on the two it was wrong about, the new one did better: it defended "The gas is light, it shoots out quick",
the facts' own words, where the old one sang "Hot gas at high pressure shoots out quick". Both flipped the sodium
pump's title line until the new prompt said never to change one and to rewrite the line beside it ("Gates let it
rush, the pump still spins": the title line kept, though "spins" is no picture of that pump). Shipped, with the
appeal.

**Title lines**, the intro and chorus written from each topic's script, judged blind against the hook anchors:

| Wording | Title line, mean | Preferred | Weak (3 or lower) |
|---|---|---|---|
| The examples as they are ("Open the gates, let sodium in", "Split the water, catch the light") | 3.92 / 4.17 | 7 / 8 of 12 | 2 / 1 |
| Examples from three subjects ("The Moon pulls, the oceans rise", "Buy on credit, watch the banks fall") and "a stranger can guess the topic" | 3.50 | 4 | 6 |
| "Split the water…", "Pull the sea, then let it go", "Print the page, spread the word" | 4.00 | 4 | 2 |

The first row is one set of title lines judged twice: 3.92 and 4.17, so about 0.2 is the judge's own noise. The
statement-shaped example ("The Moon pulls…") was copied as a shape ("The catalyst lowers the hump"); commands come
out as chants ("Ring the tone, then bring the bowl"). Neither change helped. The owner then dropped "Open the gates,
let sodium in" (it reads like a sentence, and sings sodium the way the pump does not move it), so the examples are
the third row's, which tested within the judge's noise of the first. On
its own topic an example is copied word for word (the light reactions sang "Split the water, catch the light" in
both arms that had it): it teaches the shape everywhere else.

Then the chorus example, which had been "Open the gates, let sodium in / Flip the charge and the spike begins", each
arm against the old brief (judged by Sonnet, then by Opus from the fifth test on):

| Title examples, chorus example | New | Old brief | Preferred new | Weak, new / old |
|---|---|---|---|---|
| Split, Pull, Print; "Moon overhead, the sea climbs high / Moon moves on, the sand runs dry" (Sonnet) | 3.67 | 4.08 | 2 of 12 (1 tie) | 6 / 2 |
| Split, Pull, Print; "Spot the germ, lock it tight / Call the cells, win the fight" (Opus) | 4.17 | 4.67 | 5 of 12 | 1 / 0 |
| Split, Pull, "Cash in the carriers"; "Pull the sea, then let it go / High tide, low tide, to and fro" (Opus, against the old brief written afresh) | 4.00 | 4.17 | 5 of 12 | 4 / 1 |

The old brief's lead was mostly one lucky draw: written again, its title lines scored 4.17 where its first set had
scored 4.67, so a brief's own runs differ by about half a point on 12 topics, more than the judge's 0.2. These tests
cannot tell apart briefs closer than that. The last row, the owner's picks, is within that of the old brief and ships:
the title examples "Split the water, catch the light", "Pull the sea, then let it go" and "Cash in the carriers" (a
phrase that is also the science, from the owner's favourite song), and a chorus example that opens on a title example
and answers it, as the old one did. The Moon chorus, the one example that was not a command, did worst: statement
examples are copied as statements. Its writer sang "Split the water, bank the sun" on the light reactions, rather
than copy the example.

The owner also loosened the song's shape (Oct 31): the intro need not open on the title line, as a song that always
starts on its title is predictable, and the chorus may run 5 lines, as Split the Water's does (docs/reference-songs.md).

**Rapped verses that review for the exam**, after the owner's favourite, Cash in the Carrier (docs/reference-songs.md):
five AP topics (cellular respiration, the light reactions, the sodium pump, supply and demand, the derivative) sung
in its style, rapped verses and a sung chorus, by the brief as it was and by one with a rule that rapped verses
carry the course's counts, numbers and names in order, judged blind by Opus:

| | Exam value | Singability | Accuracy | Preferred |
|---|---|---|---|---|
| The brief as it was | 3.4 | 4.4 | 5.0 (no errors) | 1 of 5 |
| Rapped verses review for the exam, up to about 16 syllables | 4.4 | 3.4 | 4.6 (3 errors) | 4 of 5 |

The review rap sang what the exam asks ("Three sodium inside lock into the pump's pockets tight", "Sodium's ten times
thicker outside, potassium thirty times in", "Inside's minus seventy"), where the brief as it was never gave the pump's
3 to 2. Its lines ran long (median 15 syllables, 11 of 80 over 16), and its errors were overreach: "Each hop powers a
pump" (only three complexes pump), "Same chain" for the chloroplast's and the mitochondrion's. Held to 14 syllables
against 16, judged by Opus told that a rapped bar at 100 bpm carries 16 or more, the two tied: preferred 3 to 2,
singability 4.0 each, exam value 4.0 and 4.2. Told that, the judge scored the 16-syllable songs' singing 4.0, where it
had scored it 3.4: what it had marked down was length, not flow. Shipped at 16, as the owner asked (rap is fast).

**Then undone, after the owner watched both** (Nov 1). The sodium pump's two songs were sung by Lyria in Cash in the
Carrier's style ($0.16) and made into song videos with the same pictures (the earlier droplet's paintings, a water
tower drawn in the session for the analogy). The owner chose the brief as it was: the review rap had "too much
detail", more than a student will retain from a song, whatever the judge scored for exam value. Rapped verses keep
the sung song's rules (one number at most, about 14 syllables). Lyria's first take of the review rap also left out
its bridge and last chorus; sung again, it sang every line.

## In-session tests, Nov 1: the pump video, its analogy, the core idea, step lines and crowds

All in the session, with every API key unset, except one Muse Image painting the owner asked for ($0.01: the club,
below) and a Lyria song for it.

### The analogy picture

The owner read the pump song's lyrics and understood them, but not the video: "trying to reconcile the bounce".
The water tower (an SVG drawing) was "very confusing", with a tower, a pump and a house to map. A nightclub
(bouncer, door, street), painted by Muse Image, read no better: "the analogy doesn't quite work for this topic".
The song then stopped playing over the analogy scene (`checks._song_off_analogy`), and the club song was directed
again over the real pictures only (job sp-f). A dam was drawn too (SVG, passed the picture check on its first try)
but not shown. The owner's decision: no analogy picture for any topic. The analogy stays in words, said over the
real picture, each part matched to the real part as it is named. That is the next change.

### What it is and why it's needed

The pump lesson said what the pump is in one phrase ("a protein in the cell membrane") and why it runs ("so the
gradients would fade if nothing pushed them back"), but not what the gradients are for: nerve signals were only in
the song ("And that spark races down your nerve"), and its core idea was a step, "Each ATP flips the pump's shape,
carrying three sodium out and two potassium in, as fast as they leak back." The core idea rule now has two kinds: a
thing or an idea with a job says what it is and what it is for, then how; a topic about why something happens says
its cause and effect. A subagent writing under the new rule, from the same droplet's facts, wrote "A pump in the
cell membrane keeps sodium out and potassium in, using ATP to push back what leaks": it left out "so nerves can
fire", since those facts never say what the gradients are for. Only a fresh droplet, whose facts are written under
the rule, will show whether it says why.

A "THE BIG IDEA" tag on the key line band was tried and undone the same hour: at the end of the lesson the band
already repeated the narration word for word. The core idea is now written quietly across the top of every frame
instead, and the band shows only steps.

### Step lines on the wrong lyric

Five of the pump song's six key lines sat on lyrics singing another step ("2 K⁺ binding drops phosphate → pump
snaps back inward" over "Potassium leaks out, takes its plus charge down the hill"). The brief asked for both the
why chain's order and the song's, and the song sang the pump's cycle before the leak. The song's order now wins.
A check by shared words was tried on the pump song and the 12 scorecard songs of Oct 31: it found the pump's three
clear cases, but on the 12 it flagged six lines, most of them right where they were, sung through a metaphor (the
catalyst song's "Hit too soft and you roll back down" is its activation energy step) or one line from the line
that repeats the words. It records a hint (`checks.misplaced_key_lines`) and moves nothing.

### Crowds of dots

The owner saw no K⁺ outside the cell, and too few dots. Each crowd started with a few dots and filled only while
the pump ran, the leak drained the K⁺ inside to two, and the cytoplasm's outline (from SAM) was the band just under
the membrane, room for four dots, and hidden until the song named "inside" at 66 s. Now a held difference starts
full and only dips, a few of each kind show where it is scarce, a held crowd shows half pale before its space is
named, and a thin outline seats its crowd in its whole box.

### A lyric the motion contradicts

The club song's pre-chorus sang "Side door swings open, the street floods in / That's how a nerve signal begins"
over Na⁺ moving through the leak channels: as if leak channels started nerve signals. The fact-check judges each
lyric and each motion on its own, so nothing asks whether a lyric matches what moves under it. A subagent stood in
for the checker on the droplet's whole round-one call (136 lines: 111 SUPPORTED, 24 FILLER, 1 UNSUPPORTED) and did
fail the couplet, but only because the song had said "Side door's cracked, the leak channels let the sodium in" a
verse before, so it read the side door as the leak channels, which stay open. Its correction, "Other doors swing
open", would still be sung over Na⁺ moving through the leak channels: the motion passed on its own ("Na⁺ travels
from Outside of the cell to Leak channels to Inside of the cell", f1). Checking each sung line together with what
moves under it would catch both; a pair that fails would lose its motion, not its words.

### Rap sections

K-pop and Latin pop now rap their second verse, and dance-pop its bridge; the rest is sung (the owner: "it doesn't
mean the whole song is rap"). Two writers in the session wrote derivative (Latin pop) and supply and demand
(dance-pop) from the new brief: each rapped only that section, said so in its style ("the first verse sung and the
second verse rapped in quick, crisp bars"), and kept the chorus sung; the rapped lines ran 9 to 15 syllables against
6 to 10 sung, in couplets like the rest ("Slide that second point in, make the gap get thin / Every secant line
swings and the slopes close in"). Lyria sang both ($0.16). Timed by the studio's own aligner, the rapped section ran
about half again as fast as the sung lines (6.4 and 6.0 syllables a second, against 4.1 and 4.0), though the aligner
was unsure of 30 and 19 lines; the owner listened and approved both.

### Panels

Classical conditioning asked for panels in both of the day's scorecard runs ("before, during and after, three
panels"), and the rocket for one picture in air and in space side by side. A rule in the brief and the script's
prompt now says a picture is one moment, never panels: moments of one process are its stages, and a comparison is
two scenes. Two writers in the session wrote both topics under it. Neither asked for a panel: classical
conditioning drew "before", "pairing" and "after" as three stages of the same lab, and the rocket "in-space" as a
scene of its own. The rule stays, and the stages' older "never several stages side by side" is now said in it.

### A trim of the writer's brief

The brief was read for what it says twice, or says for something code now does. It lost 500 words (9,298 to
8,795), and nothing it asks changed:

- Its narration section: the script's narration is spoken word for word (`script.follow`), so those rules were read
  only when the script call failed, and had drifted from the script's (350 to 450 words against 300 to 420). With
  no script, the bundle writer now gets the script prompt's narration rules (`script.narration_rules`).
- What code does: `music.sections` (set from the lyrics), the lesson never playing a song picture
  (`_settle_song_scenes`).
- Rules said twice: key terms in the lesson, the song and the narration; a structure's name and colour; the song's
  first scene; the chorus's scene; the fact-check, now named once for everything it reads; the key lines' order.
- "A line that sums up holds still", against "the chorus and the summary lines show the whole pathway": in the two
  scorecard runs, the writer showed the whole pathway wherever something travels (31 of 34 chorus lines for the
  light reactions, 28 of 31 for the pump) and held still where nothing does. The first went.

## In-session round, Nov 1: five topics end to end

The first five scorecard topics (`studio/scorecards/topics.json`), each from its request to its finished song,
lesson and upright hook videos, with no Anthropic API call at all: every call the pipeline makes to Claude was
answered by a subagent in the Claude Code session, from the exact system prompt, prompt, schema and images the code
built. The owner approved the paid calls and their prices: Muse Image ($0.01 a picture), SAM 3.1 ($0.0025 an image),
Lyria 3.5 ($0.08 a song) and Gemini TTS (about $0.04 a narration). The Anthropic and ElevenLabs keys were unset, so a
call nobody approved would fail rather than bill. The owner had first asked for all 12 topics and then cut the round
to five.

How it ran (`tools/insession/`): each topic's job (`jobs/<topic id>/`, so the song style rotates as in the
scorecard) ran the studio's own steps, `bundle`, `audit` and `factcheck`, then the videos as `make` runs them, with
`settings.json` set to `{"song": {"provider": "gemini"}, "art": {"claude": "muse"}}`. Their `ask` wrote each call to a
folder and waited until a subagent saved an answer, which `answer.py` checked against the call's schema and the
studio's own model. The brain's calls were answered on the session's model (the writer did the script and then the
bundle), the helper's on Sonnet as the pipeline runs them. 157 calls in all.

| Topic | Spend | What the fact audit and the fact-check changed | Pictures repainted, and why | What looked wrong in the videos |
|---|---|---|---|---|
| The sodium-potassium pump | $0.31 | Audit: a stroke is "the most common kind" that a clot causes, not every stroke. Fact-check: the two lines that still said every stroke (narration, lesson); 132 of 134 passed round 1 | The facing-in close-up once: ATP floated apart from the pump and the K⁺ arrows pointed the wrong way. Kept with one wrong label ("ATP Phosphate group"), flagged | Choruses mistimed and the hook cut short (below). ATP dots ran through the pump to the outside of the cell, Na⁺ ran sideways (fixed, below). The facing-out picture also opens to the inside, and only a sticky note says so. The gut picture's route ends in a mitochondrion. The upright hook crops the painted labels and its flag. The picture's own ions beside the video's Na⁺/K⁺ dots |
| The light reactions | $0.44 | Audit: all correct. Fact-check (3 rounds): photosystem I does not make NADPH itself; "your own cells" became mitochondria; 3 motions dropped (NADPH from photosystem I; photosystem I or the cycle turning things into NADPH or sugar) | None | The last key line stays through the whole weed-killer case. The overview stays pale until its 15 parts are named. Arrow one-liners over painted labels. SAM cost $0.27: 7 of 43 parts never found |
| What a derivative means | $0.13 | Audit: a derivative is zero at a peak or valley, not at an endpoint. Fact-check: nothing failed | None: its hook was painted once, and its 3 graphs, drawn as SVG, were each refused once (their dots too small, below) and redrawn | Text on the graph collides: two parts' meanings run together, and the arrow's one-liner sits on "Point Q" |
| Why the French Revolution happened | $0.24 | Audit: England had "a similar trigger", not "the same causes". Fact-check (2 rounds, 8 lines): the bridge (the songwriter's "Same broke king, but church fights stir the pot" and "In France, that's when the lid gives way"), the narration and lesson on England, "the lid blows off" moved to August, and 3 picture descriptions that named a real king | None | The corrected England line is 31 words, and the lyric band shrinks it to unreadable type. The overview is pale for long stretches and the camera crops its labels. The last key line stays through the England case |
| Dramatic irony in Romeo and Juliet | $0.24 | Audit: three facts (suspense or humour; the wedding is offstage; Paris is arranged in Act 3). Fact-check: 7 lines, the core idea among them ("so we often watch") | None (the tomb lacks its vault, logged as minor) | The last key line stays from the potion to the end. The camera's follow crops painted labels ("rents", "us"). Nothing flows in it, so no arrows |

Every learner test passed first time (the core idea and every link), so no lesson was rewritten. The narration edits
added linking words, and twice moved a passage so the lesson ends on its core idea. The round cost $1.48: the five
topics, and $0.12 for repainting the pump's close-ups in the overview's look (below).

### Fixed on this branch

- **Particles went sideways and through the membrane** ([still](test-rounds-nov1/pump-atp-dots-cross-membrane.jpg)). A
  flow's stops were its parts' label points. The inside of the cell had its label point at the picture's far left
  edge, so the pump's crossing read as left to right and the Na⁺ ran sideways. In the close-up, the pump's label
  point was in its lobes above the membrane, and ATP crossed the membrane to reach it. Now a crossing's direction comes
  from where its ends lie beside the part, and a part at a path's end is met at its face toward the stop beside it.
- **Flows are arrows that move** (the owner). Off a graph, each flow is a faint dotted track, with a short arrow (a
  third of the route, at most 170 px) gliding along it, and its particle rides the arrow's head (the owner's idea). It
  fades at the head and waits at the tail through a short pause
  ([stills, 0.4 s apart](test-rounds-nov1/pump-arrows-with-riding-particles.jpg)). The pump was re-rendered both ways.
- **Close-ups in the overview's look** (the owner, after the pump's pictures came out in different styles: "not sure
  if they are intended to convey something different"). A scene that shares a part with the overview is now painted
  after it, redrawn from the overview's picture. Tried on the pump only ($0.12): the set reads as one, but the
  facing-in pocket reads less clearly, and the stroke picture came back with three wrong labels, each flagged
  ([before and after](test-rounds-nov1/pump-close-ups-before-after-style.jpg)). The other four topics kept their first
  pictures.

### Still wrong, worst first

1. **The choruses are mistimed, in all five songs.** The forced aligner (MMS_FA on Demucs's vocals; no model
   writes the timings) puts several chorus lines in under a second, then leaves the singer singing over a blank
   band for 6 to 15 s ([still](test-rounds-nov1/pump-chorus-blank-band.jpg)): the pump's first chorus is sung from
   54 to about 69 s, timed 54 to 60 s. Chorus lines under a second: pump 5 of 17, derivative 4 of 14, French
   Revolution 4 of 17, light reactions 1, dramatic irony 1. Each song passed the step, the aligner being unsure of 20
   to 23 lines of 47 to 50, under its limit. The hook cut is timed from it too, so the pump's ends a third of the way
   into its chorus. Lyria returns its words (it sang the lyrics as written) but no timings.
2. **Crackle in the songs.** Lyria's songs are mastered loud: the pump's goes past full scale 79 times, in its
   choruses. The audio step then re-encodes Lyria's MP3 to MP3, and the render to AAC, with no headroom. Proposed:
   keep Lyria's MP3 as it comes, and lower it a dB or two before the render (not done, for the owner's word).
   Done on Nov 2: Lyria's MP3 is kept as sent; each song goes into the mix turned down so its loudest sample sits
   at 0.85 (`encode.HEADROOM`), under a limiter at -1 dBFS (`media.PEAK_LIMIT`); and the video's audio is encoded
   with ffmpeg's two-loop AAC coder at 320k (`media.AAC`), whose errors stay further under the music. Later that
   day a crackle heard in a new rocket song moved between plays of the same raw Lyria file (24-25 s, then 11-14 s,
   then gone): it came from the playback, not the file. Before taking a crackle for a fault, play the spot again.
3. **The last key line stays for a minute or more** when a lesson ends on a case (the stroke, the weed killer,
   England, the tomb): the band holds the last step of the why chain over the whole case
   ([still](test-rounds-nov1/irony-key-line-held-over-tomb.jpg)).
4. **A fact-check correction can lengthen a narration line past the band** (the England line, 31 words, in tiny
   type: [still](test-rounds-nov1/french-long-line-tiny-band.jpg)). The narration editor holds lines to 22 words, but
   the checker's corrections are not held to anything.
5. **A graph dot that is a part of its own is refused every first time.** The drawing instructions give a point
   `r="12"` (about 650 px drawn), and the code refuses a part under 1,500 px (`svg.MIN_AREA`). All three derivative
   graphs came back once, and the redraws used r ≈ 33. Proposed: draw a point that is a part of its own at r ≥ 24 in
   `svg.style`.
6. **Text collides on a graph** ([still](test-rounds-nov1/derivative-text-collides.jpg)): two meanings at the top run
   into each other, and the arrow's one-liner sits on "Point Q".
7. **Painted labels are cropped**: by the camera's follow and zoom on every topic
   ([still](test-rounds-nov1/french-pale-overview-cropped-labels.jpg)), and by the upright hook, which also drops the
   sticky note of a flagged label ([still](test-rounds-nov1/pump-hook-crops-labels.jpg)). A painted picture labels
   itself, so the video cannot move its labels clear.
8. **A painting that contradicts its scene is not repainted.** The pump's facing-out stage also opens to the inside.
   The checker listed it under "wrong" (recorded, not repainted), not "misplaced", so it was only disclosed in a
   sticky note.
9. **Pale for long**: a picture's parts are ghosted until named, and an overview with many parts (the light
   reactions' 15, the French timeline's 8) stays mostly pale for 30 s or more.
10. **The arrows' one-liners sit on painted labels** ([still](test-rounds-nov1/light-arrow-note-on-labels.jpg)): they
    keep clear of the video's labels, which a painted picture has none of.
11. Smaller: the pump's hook scene labelled "Body" and "Couch". The picture's own painted ions beside the video's
    Na⁺/K⁺ dots, in other colours. Three French picture descriptions failed the public-text rule for naming real
    kings (Louis XVI, Charles I), though the facts and the labels name them.

### The round itself

- Two drivers were killed for memory (4 GB each) while a third topic rendered, on this 15 GB machine. Run two or three
  topics at once at most. They were restarted from the commit before the art change, so their pictures were kept. A
  repair the killed run had made in place changed one picture, so 6 SAM calls were billed again ($0.015).
- When a label check fails, locate asks the helper for the same box again, with the same picture and prompt. The
  driver answered it with the first answer (the pump's "Small intestine"). The live helper would likely answer it
  alike: the second call learns nothing from the failed dot.
- SAM is the largest and least predictable cost: $0.005 for the derivative, whose graphs locate themselves, and
  $0.27 for the light reactions, whose overview has 15 parts. Each part is asked in up to three phrasings, then by the
  helper's box.

## Round, Nov 2: the kidneys on Meta's models, end to end

One topic, locally, to check quality before the hackathon demo, with every model call on the real APIs and none of
them Claude's: **AP Biology: how the kidneys filter blood** ("How do the kidneys filter blood? For AP Biology", R&B),
`plan --brain muse-wonder --videos` (job `kid-mw1`). The owner approved the paid calls; nothing was posted.

| | |
|---|---|
| Brain and helper | Muse Wonder 1.0 writes (script, bundle, audit, narration edit, fact-check, its look at each first picture); Muse Spark 1.3 checks (learner test, picture checks, the locate step's boxes and label checks, the arrows) |
| Media | Muse Image paints, SAM 3.1 finds the parts, Lyria 3.5 sings, Gemini 3.8 Flash TTS reads, and MMS times both; Demucs and MMS run here, free |
| Keys | `MODEL_API_KEY` and `GEMINI_API_KEY` only: the Anthropic and ElevenLabs keys, the production API's, and the retired `BRAIN_PROVIDER`, `STUDIO_BRAIN_MODEL`, `SONG_PROVIDER` and `ART_PROVIDER` unset; `STUDIO_CLAUDE_DRAWS=brain` |
| Settings | No `settings.json`, so the Muse brain's defaults; one holding only `{"spend": {"topicUsd": 5}}` was added after the lesson, as a ceiling, which changes no default |

| Part | Cost | Time |
|---|---|---|
| Script | $0.42 | 2.6 min |
| Bundle | $1.00 | 4.8 min |
| Narration edit | $0.59 | 3.8 min |
| Fact audit (twice), learner test | $0.18 | 1.4 min |
| Fact-check, 2 rounds | $0.54 | 2.9 min |
| **The lesson** | **$2.73** | **15.5 min** |
| Art: 6 pictures, 6 checks by Spark, 4 looks by Wonder | $0.49 | 4.3 min |
| Locate: 73 SAM calls ($0.18), 28 Spark boxes and label checks ($0.32) | $0.50 | 3.4 min |
| Song (173 s) and narration (124 s) | $0.11 | 0.5 min |
| Timing, timeline, render | $0 | 5.8 min |
| Arrows: 18 Spark calls | $0.28 | 2.3 min |
| **The videos** | **$1.38** | **12.6 min** |
| **Topic to both videos** | **$4.11** | **28 min** |

The bill lists `muse-wonder-1.0`, `muse-spark-1.3`, `muse-image-1.0`, `sam-3.1`, `lyria-3.5` and
`gemini-3.8-flash-tts`, and the free `mms-fa` and `htdemucs`: nothing else was called. Muse Wonder is priced as Opus
5.5 is ($4 / $20 per million) and writes long: 20K output tokens for the script, 47K for the bundle, 29K for the
narration edit, which cost more than the two fact-check rounds. Its looks at the pictures were cheap ($0.05 to $0.12).

### What worked

- **The facts and the fact-check.** The audit judged all four facts correct in one call. The fact-check passed in two
  rounds (78 supported, 11 filler, none unsupported) and repaired the narration editor's worst line: "Then high
  pressure forces water from the blood, carrying salts with it as glucose and urea follow" became "Then high
  pressure forces water, glucose and salts from the blood into Bowman's capsule". It also took "a tight ball of
  capillaries" out of n7, since no fact says it.
- **The pictures.** Five scenes in seven tries, in one textbook look: a nephron with its glomerulus, coil, loop,
  collecting duct and capillaries; Bowman's capsule in cross-section with blood cells and proteins inside the
  capillaries and water, glucose, salts and urea in the capsule space; the tubule wall beside a capillary; the same
  wall with glucose piled up for the diabetes case. No wrong label and no note on any kept picture. The filter's first
  try had proteins in the capsule space, and the check's note said so truly ("proteins never leave the blood during
  filtration"); Muse Wonder rewrote the scene and the repaint was clean. Muse Image refused the hook's first prompt
  (content policy; it asked for a cup of urine) and painted the second.
- **Round 17's Bowman's capsule.** Found, by SAM in the helper's box, and every mask of the close-up on its own part
  (water, glucose, salts and urea each on its own coloured balls). 29 of 30 parts located; the proteins, inside the
  glomerulus, were left out.
- **Round 17's missing outro.** Lyria's text listed all 45 lines, outro included, and sang them: the outro's lines
  were heard at 0.60 to 0.70. Every lyric still shows the word being sung. Chorus drift was 2.0 s at most; the
  aligner was unsure of 7 lines, four of them the last chorus (0.12 to 0.23), spaced as the other choruses are.
- **Round 20's glucose note.** No note was written on a kept picture, and the one written was true.
- **Each flow goes the right way.** Filtration: water, glucose and salt from the glomerulus into the capsule space.
  Reabsorption: glucose and water from the tubule across the wall into the capillary. The loop: water and salt out
  into the tissue. The collecting duct: urine down to the pool.
- **The lesson's shape.** The hook ends on its question ("So what happens to the other 178 liters or so?"), the
  sorting-line analogy is said over the nephron, the prediction comes where the misconception forms ("if glucose
  leaves the blood, is it lost in your pee?"), the diabetes case comes after the path, and the lesson ends on its core
  idea.

### What went wrong

1. **`make` stopped before the videos** (fixed): "Claude Opus 5.5 needs POPSMOSIS_ANTHROPIC_API_KEY". `make` checked
   the default brain's keys, not the job's.
2. **No secretion** (rule added, not re-run). The facts cover filtration, reabsorption and the loop; nothing is
   secreted anywhere, so an AP Biology lesson on the nephron skips one of its three processes, and no picture or flow
   shows it. A free subagent writer on the same script prompt left it out too.
3. **The diabetes case over the hook** (fixed, re-rendered): the script put n19 to n22 on the hook, so "So the
   proximal tubule cannot take back all that extra glucose" played over a boy holding a water jug, while the bundle
   had painted the case for the song alone ([before and after](test-rounds-nov2-kidneys/case-on-hook-before-after.jpg)).
4. **The hook clip ended on the next verse** (fixed): its last frames showed "The loop of Henle dips", the verse after
   the chorus timed straight after its last word ([before and after](test-rounds-nov2-kidneys/hook-last-frame-before-after.jpg)).
5. **The narration editor garbled a clear line.** The script's "Next, the loop of Henle pulls more water out into
   salty tissue" became "Back in the nephron, the loop of Henle leaves the salty tissue saltier deeper down, so more
   water leaves" ([still](test-rounds-nov2-kidneys/stale-key-line-and-garbled-n15.jpg)): "Back in the nephron" is its
   prompt's own example, though the close-up before it was in the nephron too. The fact-check passed it.
6. **A key line held over the next scene**: "High pressure in glomerulus → filtrate forms" stays over the prediction
   and the misconception (n11, n12) on the tubule close-up, until the tubule's own key line. Clearing the band at every
   cut would also clear the lesson's question off the overview; left for the owner.
7. **The fact-check and the filtrate.** It dropped "filtrate travels from Glomerulus to Filtrate" (three motions) as
   travelling "to itself": the particle and the capsule space share the name. It passed the lyric "Salts and urea are
   crossing too" and dropped urea's motion, since no fact names urea, though urea is one of the key terms.
8. **An arrow read backwards.** Spark's look said the water arrow in the close-up ran from the capsule space into the
   glomerulus, though its own placement said the reverse, and the arrow was left out; 4 of 14 arrows were.
9. **The overview's loop.** The proximal tubule's orange runs down the loop's first arm, so the painted "Loop of
   Henle" label sits beside an orange tube, and there is no distal tubule, though f1 names "a further winding
   section". The check passed it.
10. **Rhymes for their own sake**: "Deeper tissue wears a saltier crown", "High pressure sends water and glucose to
    flight", "So glucose stays, the stream's not the same"; and "The proximal tubule pulls it out", which can be heard
    as taking glucose away.
11. Smaller: the hook is a young child whose see-through jug shows his kidneys and pelvis; the upright hook crops the
    overview's painted labels ("imal tubule", "Collecting du"); no arrow has a one-liner (35 `says` hints).

### Fixed on this branch

- **`make` checks the job's own brain** (`studio/videos.py`): `local()` checks the keys inside `job_chosen()`, as
  `videos` and `background` already did.
- **A case told on the hook plays over the picture that draws it** (`checks._case_off_the_hook`, rule `case-on-hook`).
  Sentences that come back to the hook to talk about parts it does not draw, up to the one that says the core idea,
  play over the scene that draws the most of those parts, the song's own picture first on a tie; the song-scene repair
  then makes it the lesson's too. The close stays on the hook.
- **The hook cut stops at the next line** (`timeline.hook_cut`): its 0.8 s tail never reaches the line after the
  chorus.
- **Every stage the course names** (`prompts/bundle.md`, the facts): "A process the course teaches as named stages has
  every stage it names in the facts, each in its place, even one the learner's question leaves out (the four phases of
  mitosis, the three stages of cellular respiration), and the narration walks each as a step." Tested on this topic
  with free subagent writers, its examples from other topics so that a writer applies it rather than copies it: without
  it, secretion was in 0 of 2 scripts (the Claude writer's and Muse Wonder's); with the facts half alone, in the facts
  of 2 of 2 and the narration of none; as shipped, walked in the narration of 2 of 2, over the tubule close-up ("The
  tubule also does secretion, moving extra acid and some drugs from blood into the filtrate").

The job was checked again and its timeline and videos made again with the first three (`step check`, `step
timeline`, `step render`: no paid call; the arrows already pointed were kept, the moved sentences carrying no flow).
The brief's rule needs a new lesson, so it is not in this droplet.

### The topic again, with the stages rule

The owner approved a second job (`kid-mw2`, $6 ceiling) on the same command with every fix above. Its first bundle call
dropped ("muse-wonder-1.0: Connection error", unbilled; the proxy logged nothing), after $0.57 of script and audit;
`step bundle`, `audit` and `factcheck`, then `make`, ran it again. $4.74 in all: $0.57 lost, $2.16 the lesson, $1.70
the videos with the arrows pointed twice ($0.31 the second time, below).

- **Secretion is taught**, in plain words rather than the course's: "Then, in the later coiled tubule, salts are
  adjusted and extra wastes are added from the blood into the filtrate for removal", and sung ("Later tubule tunes the
  salts just right / Extra wastes are added to the flight"). The key terms took antidiuretic hormone, not secretion.
- **But it is not shown.** Its arrow, pointed by the helper at the vessel beside the tubule, was left out by the
  renderer's gate as starting "away from Blood vessels": SAM's mask of the vessels was only those by their label, 583
  px off (fixed below). Pointed again, it passed the gate and failed the helper's look, "It starts at the later coiled
  tubule edge and ends out in the blood vessels", though the still it was shown runs from the vessel into the coil.
  The look failed filtration's arrow too ("starts and ends in the yellow filtrate", leaving the glomerulus's edge):
  6 of 15 arrows left out. Muse Spark misreads short arrows (about 40 px in the 960 px still), as it read the first
  job's water arrow backwards.
- **Worse elsewhere.** The overview is a crowded diagram cut off at the top, the loop not joined to the proximal
  tubule, membrane beads along every tube, and antidiuretic hormone painted as two chains of yellow beads; the check
  passed it. The narration ran to 434 words, over 420. The diabetes case plays over the overview, its own picture the
  song's alone. 27 of 34 parts located; the filter close-up's water, glucose, salts and nitrogen waste were not.
- **Better**: the hook is a plain figure with his kidneys; the fact-check passed with 87 lines supported, none
  unsupported; the song drifted 1.0 s at most.

Fixed on this branch from it: **a pointed arrow is not judged by the boxes** (`Renderer._wrong_arrow`): the box of a
part painted in many places is only the copy SAM found, and the arrows step looks at a pointed arrow as drawn, as the
gate already did not guess its way. Too short or leaving the picture still leaves it out. Still open: the look itself,
which needs the arrow larger in its still, or a stronger model, to read which way it goes.

## Nov 2: natural selection on Meta's models, end to end

Round 22's natural selection, made again before the hackathon demo, locally, on Meta's models wherever the studio
uses one, on the real APIs: `python -m studio plan --topic "natural selection" --course "AP Biology" --request "Is
evolution the same thing as natural selection? For AP Biology" --style "pop-punk" --brain muse-wonder --videos`. The
owner approved the paid calls and their prices. The environment held `MODEL_API_KEY` and `GEMINI_API_KEY` only: the
Anthropic and ElevenLabs keys unset, so a call to Claude or ElevenLabs would fail rather than bill;
`POPSMOSIS_API_URL` and `POPSMOSIS_API_KEY` unset, so nothing could be posted; `STUDIO_CLAUDE_DRAWS=brain`, so Muse
Image paints any chart. No `settings.json`: Muse Image paints, Lyria sings, Gemini TTS reads, and MMS times the
narration as well as the song.

Muse Wonder never got past the lesson: two of its four big answers were broken. On the owner's word the topic was
then made on Muse Spark (`--brain muse-spark`), which made the whole droplet.

| | Muse Wonder (`ns-wonder`) | Muse Spark (`ns-spark`) |
|---|---|---|
| Spend | $2.80, the lesson only | $1.08: the lesson $0.56, the videos $0.52 |
| How far | Try 1: the bundle refused (no lyrics). Try 2, after the fixes below: a script about something else, its facts in the bundle; stopped in the fact-check | Lesson, song (182 s), narration (142 s), both videos and the hook clip (28 s) |
| Time | Try 2's bundle step 10 min | Lesson 14 min (bundle 9, fact-check 4); videos 10 min |
| Bill | muse-wonder-1.0, muse-spark-1.3 (the learner test) | muse-spark-1.3 $0.85, Muse Image $0.05 (5 pictures), SAM 3.1 $0.07, Lyria 3.5 $0.08, Gemini 3.8 Flash TTS $0.03; Demucs and MMS on the machine. No other model |
| Fact audit, fact-check | Audit passed four facts about a club (below) | Audit: all four CORRECT. Fact-check passed in 2 rounds, 2 fixes |

The session spent $3.88 in all. A fact-check call cut off when the Muse Wonder run was stopped is not on its bill.

### Muse Wonder

- **Try 1: a song with no lyrics.** Its script was the best writing of the night: green and brown beetles on
  brown soil, a bird on a branch, evolution defined before selection ("Evolution is change in allele frequency, the
  share of one allele, in a population over time. Natural selection is one process that can drive that change in a
  particular direction."), the mistake asked where it forms, over the bird's close-up ("So will a surviving green
  beetle turn brown during its own life?"), a later population in a scene of its own, and an antibiotic case. It
  gave all four facts the id "[generated]". The bundle call ($0.73, 3 min) then planned a 37-line song (a direction
  for each of s1 to s37, key lines on s3, s7, s17 and s19, a pop-punk style) and sent `"lyrics": []`, and the step
  failed. Every object in that answer had its keys in alphabetical order, not the schema's: `direction` before
  `lyrics`, `axes` before `id`. Written in that order, a line's direction comes before the line. Whether Meta's
  structured output orders them so was not tested (a paid call).
- **Try 2: a script about something else.** The question "Question", one scene ("hook": "Tokyo"), three lines of
  narration ("What's the deal with Seattle weather?", "Is evolution the same thing as natural selection?", "5th
  grade, AP Biology"), a why chain ending "PreparedAmtrak Empire passengers should check their seat assignment on
  their smartphones", and four facts on a club's interviews, a C++ tournament, a 28-person cap and a lab procedure
  ("Dimethoxychlor (DMC) binds irreversibly to CDK"). The audit judged each UNSURE ("I cannot confirm this club's 4v4
  tournament history since 2023 ... from established AP Biology course knowledge") and passed them, as an UNSURE
  fact is kept. The bundle took the script's facts word for word, as told. Its own lesson was good (rock pocket mice
  on dark lava, an owl, and the only one of the three that named drift, mutation and gene flow), but every line
  cited a fact about the club. It was stopped in the fact-check, before any picture.

### Muse Spark

What worked: the bill, every call Meta's, Lyria's or Gemini TTS's; four pictures in five tries, each labelling its
own parts with every word spelled right ([the pictures](test-rounds-nov2/natural-selection-spark-pictures.jpg)); SAM
found all 19 parts (3 by the helper's box); the narration aligned with no line unsure; and the song's lines this
time sit where the singer sings them (the vocals stop at 135 s, where the last chorus is timed to end). The lesson
video holds together as one path: the pills, the beetles, the birds, the next generation, the bacteria.

Read as a learner, worst first:

1. **The ending does not answer the question.** The last line is "So evolution is not the same as natural
   selection because variation goes in, selection sorts it, and adaptation comes out"
   ([still](test-rounds-nov2/natural-selection-ending.jpg)): how selection works, not why the two differ. Fact f1
   names mutation, gene flow and drift as other causes; no line says them. The core idea on screen the whole time,
   "Evolution is inherited change over generations, and natural selection drives it by favoring traits that improve
   survival", reads as if selection were the cause. The song asks "Evolution or selection?" twice and never answers.
   Round 22 showed drift as a rockfall, and Muse Wonder's mice named all three.
2. **No picture of the generations.** The population's change is told over the overview, which always shows three
   light beetles and three dark ones: "Back in the group, the next generation holds more dark offspring and fewer
   light offspring" ([still](test-rounds-nov2/natural-selection-next-generation-same-picture.jpg)). So there were no
   counts to keep from one picture to the next, and no chart for Muse Image to get wrong. Round 22 painted three
   generations side by side with a bar under each; Muse Wonder's script had a later population of its own.
3. **The misconception is corrected late**: "So did hungry birds make individual beetles turn dark because they
   needed it?" comes after the allele shift and adaptation (n16 to n19), not at the birds, where it forms.
4. **The overview contradicts the narration.** Its light beetles sit on the leaves, its dark ones on the soil,
   while the narration says "hungry birds hunt over the dark soil, so light beetles stand out". The leaves came out
   teal, under a song that sings "leaves so green" (fixed for later bundles, below).
5. **Filler lyrics, passed by the fact-check**: "High fitness lays more eggs at night", "More dark kids hatch with
   tiny legs", "Whole population claims the spark", "Resistant bugs own the situation"; "Heritable tint from mom and
   dad / Kids copy it, good or bad" makes inheritance sound like copying; "It ain't need that paints them dark" opens
   the hook clip. The fact-check's two fixes were right ("Tough winners always win" became "Here tough winners
   win"), but Muse Spark passed every other lyric. The chorus ("Birds eat light, dark lives on / Light fades out, dark
   carries on") is catchy and true; the code checks noted it "says too little of the core idea".
6. **A sticky note that teaches nothing.** The helper found the close-up's light beetle "buried in soil" (it sits a
   little into the painted ground), the repaint did not satisfy it, and the note shown is "Light beetles stay on dark
   soil" ([still](test-rounds-nov2/natural-selection-false-note.jpg)), which reads as a fact about beetles.
7. **A 27-second instrumental break.** Lyria added an empty section (`[[E6]]` in its own text of the song) between
   the last chorus and the outro, and the video holds the overview with a blank band from 129 s to 158 s. With the
   two choruses before it, the song stays on that one picture from 87 s to 158 s.
8. Smaller: the hook paints bacteria the size of tennis balls beside the pills; the bacteria case reads as a blood
   vessel with capsules floating in it; "Last winter ... this winter the same antibiotic pills barely work" casts one
   person's two infections as one population; the upright hook crops the left bird.

### Fixed on this branch

1. **A refused bundle is asked again** (`studio/steps/bundle.py`). Muse Wonder's songless bundle failed the step,
   and a retry of the step writes the script again too. The call is now asked a second time with what was wrong
   ("lyrics and narration must not be empty"), pydantic's echo of the answer left out.
2. **Facts are numbered** (`studio/steps/script.py`). The script's facts get ids f1, f2, ... unless they have
   them. With "[generated]" on all four, the bundle numbered them itself, so it no longer kept the script's facts and
   the early audit was thrown away; the audit, which corrects a fact by its id, would have given all four one text.
3. **A script about something else is not followed.** A script whose facts name no word of the topic ("selected"
   is not "selection"), or with fewer than 3 scenes, is treated as no script: the bundle is written without one, as
   when the script call fails. None of the 36 saved scorecard bundles' facts would be refused. The in-session scorer
   checks scripts the same way.
4. **Green runs to a hue of 170** (`studio/checks.py`). The writer's "light green leaves" (#43AA8B, hue 162) were
   named teal, the picture's words made to agree, and painted teal. Teal itself is 180; one saved part moves.
5. **The audit is billed once.** The bundle step audits the script's facts beside the bundle call and writes
   audit.json, so the audit step finds itself done, but `run` and `plan` ran every step after the bundle: a second
   audit on every topic with a script ($0.012 and 19 s here, more on Opus or Muse Wonder).

Two more were fixed on main the same night, the same way: `make` checked the keys on the default brain, so
`plan --brain muse-spark --videos` wrote and fact-checked the lesson, then stopped with "Claude Opus 5.5 needs
POPSMOSIS_ANTHROPIC_API_KEY" (d22e623); and seven video tests failed since the art step began asking the writer to
look at each first picture (f08578b).

No prompt rule was added. What went wrong in Muse Spark's lesson is the writer's, and a rule tried on a subagent
writer (Claude) would say nothing about Muse Spark.

### Still wrong, worst first

1. **Muse Wonder is not reliable enough to run unwatched**: two broken answers in four, each now caught (a second
   ask; no script), at up to $0.85 a bundle call. Whether Meta's structured output writes keys in alphabetical order,
   which would put the song's direction before its lyrics, is one cheap test away.
2. **Muse Spark's lesson does not answer this question**, and its fact-check passes filler a stricter checker
   would not (above).
3. **An audit that cannot confirm any fact still passes.** All four UNSURE, about a club, passed; the script check
   now catches that case first.
4. **The helper's notes are not reviewed** (since Nov 2), and a false one is shown as a disclosure.
5. **A long instrumental break holds one picture.** Proposed: add "long instrumental break" to the song's avoid list
   (`audio.AVOID`, beside "long instrumental intro"); it needs a paid song to try ($0.08).

Not good enough to show judges as it is: the pictures, timing and karaoke are clean, but the ending misses the
question the learner asked, and round 22's droplet answered it.

## Round, Nov 2: the lungs on Muse Wonder, end to end

The owner's hackathon demo topic, **AP Biology: how the lungs get oxygen into the blood**, asked for in K-pop, run
locally on Meta's models wherever the studio can, all on the real APIs, the paid calls approved for this run:

```bash
python -m studio plan --topic "how the lungs get oxygen into the blood" --course "AP Biology" \
  --request "How do the lungs get oxygen into the blood? For AP Biology" --style "K-pop" \
  --brain muse-wonder --videos
```

Muse Wonder 1.0 wrote (script, bundle, fact audit, narration edit, fact-check, picture rewrites), Muse Spark 1.3
checked (learner test, picture checks, label checks, boxes, arrow looks), Muse Image painted, SAM 3.1 found the parts,
Lyria 3.5 sang, Gemini 3.8 Flash TTS read, and MMS and Demucs timed both, on this machine. `MODEL_API_KEY` and
`GEMINI_API_KEY` set; `POPSMOSIS_ANTHROPIC_API_KEY`, `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY` unset, so a call to
Claude or ElevenLabs would fail rather than bill; `STUDIO_CLAUDE_DRAWS=brain`, so nothing was Claude's to draw. A
local job has no `settings.json`: Muse Image painted and Google made the audio by default. No call went anywhere else.
It ran beside the kidneys above, the same evening, on another branch.

| | |
|---|---|
| Spend | $3.04 for the finished job (`lungs-wonder-2`): the lesson $2.02 (Muse Wonder $2.00), art $0.38, locate $0.33, arrows $0.18, audio $0.12. The first try (`lungs-wonder`) logged $0.32 before its bundle call was cut (below), and the probes of Meta's API about $0.02. Not in the ledger: whatever Meta billed for that cut bundle call's three tries, which it does not report |
| Time | 2 h 15 min from the first command to the last frame, most of it lost to Meta's API and the fixes below. The lesson took 12.5 min (script 125 s, bundle 221 s, narration edit 209 s, fact-check 100 s); the videos would have taken about 14 (audio 40 s and timing 6.3 min beside the art's 3.6 min and locate's 5, then arrows 142 s and render 158 s) |
| Fact audit | 4 facts, all correct, by course knowledge |
| Fact-check | passed in 1 round: 86 lines supported, 8 filler, 0 unsupported, nothing corrected |
| Song | Lyria, 3:00, every line sung as written; 45 lines timed, the aligner unsure of 14 (every chorus line), chorus drift at most 1.3 s |
| Pictures | 5 scenes in 6 paintings; 17 of 21 parts located (10 in the helper's box) |

### What worked

- **The lesson reads as one explanation.** A hook from the learner's world ("When you sprint, your leg muscles use
  oxygen much faster than at rest. You breathe harder, yet inhaled air itself never enters your blood"), an analogy
  that says where it stops ("Think of an alveolus as a loading station and hemoglobin as its shuttle"; the lesson:
  "The comparison stops at the wall: the shuttle does not carry oxygen across it"), partial pressure defined before
  it is used, the steps signposted, the case after the path (emphysema: fewer, larger sacs, less surface, less
  oxygen during exercise) and an ending that answers the question: "So fresh air and oxygen-poor blood go in, and
  oxygen-rich blood and carbon-dioxide-rich air go out."
- **The misconception is corrected where it forms**, on the alveolus close-up, right after the gradient is taught
  and before any dot moves: "If both sides reached equal partial pressure, would oxygen still keep entering blood?
  A common answer is yes because the body needs it, but need cannot move gases." Muse Wonder chose the body's need
  pulling oxygen in as the mistake, not round 21's lungs pumping it; the song's pre-chorus sings it ("It's not your
  need that pulls it through").
- **O₂ and CO₂ cross in opposite directions**, where the narration says each: O₂ rides its arrow from the air in the
  alveolus down across the wall into the capillary at 86 s, and CO₂ its arrow up into the alveolus at 93 s
  ([stills](test-rounds-nov2/lungs-o2-down-co2-up.jpg)). The arrows look placed both right. Round 21's gases ran along
  the capillary, not across the membrane.
- **The labels are on their parts** in all five pictures: alveolus, pulmonary capillary, red blood cell, oxygen and
  carbon dioxide on the close-up; windpipe, branching airways, lungs, heart, alveolus and capillary web on the
  overview.
- **The rapped verses are timed.** Verse lines take about 2 s each at 3 to 4 words a second, the sung chorus lines 3
  to 5 s. Every line's window has singing in it on Demucs's vocal track, and no gap over 1.5 s is sung through for
  more than 1.8 s: none of the Nov 1 songs' 6 to 15 s of singing over a blank band. The aligner was unsure of every
  chorus line all the same.
- **The last key line does not hang over the case**: the band is empty through the emphysema passage.

### What went wrong, worst first

1. **A picture contradicts the voice.** Muse Image painted five oxygens on each hemoglobin, twice, though the scene
   asks for four; the narrator says "Hemoglobin is a protein that can bind up to four oxygen molecules" and the song
   sings "Four at a time on every ride" over it, beside the sticky note that says so
   ([still](test-rounds-nov2/lungs-four-at-a-time-five-oxygens.jpg), [the picture](test-rounds-nov2/lungs-hemoglobin-five-oxygens.jpg)).
   Its middle "Hemoglobin" label sits on an oxygen. Kept as it is (the owner): a repaint costs about $0.60, since
   locate and the arrows run again on every scene, and may miscount again.
2. **The choruses play over a still overview** ([still](test-rounds-nov2/lungs-chorus-over-still-overview.jpg)).
   SAM never located the overview's pulmonary capillary, a thin green web over pink lung: every match was mostly the
   lung's colour and was dropped (73 in the step). So the overview's O₂ into the blood, and the blood's way to the
   heart, have nothing to run between; and the arrows' look left out the air's arrow from the windpipe to the alveoli
   as pointing the wrong way ("It starts in the lung and ends at the windpipe"), though it was placed from the fork
   outward. "Oxygen moves from air to blood" is sung four times over a picture where nothing moves.
3. **The checker passes what is wrong.** Muse Spark found nothing in the overview (a red vessel runs out to the
   right arm, from the scene's own words "a blood vessel from the heart toward one arm"; the face is cropped at the
   top) or in the emphysema picture, whose "broken alveolar walls" float inside the sac as pink scraps.
4. **The lyrics are plain and some lines are padding**: "Hemoglobin carries it to you" (three choruses), "Breathe in
   deep, fresh air floods", rhymes that miss ("high" / "blood", "ride" / "low", "sac" / "heart"). "Equal pressures,
   nothing moves for you" says no movement where the lesson says no net diffusion, and the fact-check let it by.
5. **The upright hook crops labels** in its first frame ("Pu…", "Red blood c…") and opens on the verse's last line
   with its band still empty ([still](test-rounds-nov2/lungs-hook-first-frame-crops-labels.jpg)), as Nov 1 found.
6. **Pale for long**: on the song's verses the alveolus close-up stays washed out on most lines, as each spotlights a
   part, and the CO₂ line's zoom puts the "Carbon dioxide" label out of frame.

### Fixed on this branch

- **Muse Wonder's bundle call was cut after 15 minutes** ("Connection error."). Muse reasons in silence and answers
  only at the end, streamed or not (a probe: 599 reasoning tokens in silence, then the whole answer in a few chunks), so a
  long think holds a connection with nothing on it; the 15 minutes are about three tries cut at 5 minutes each,
  the SDK's two retries unseen, each perhaps billed; the kidneys' second job lost its bundle the same way. The
  writer's calls now run in the background on Meta's Responses API and are polled every 3 s
  (`brain.META_BACKGROUND`): the retried bundle came back in 221 s.
- **In the background, Meta left about a third of Muse Spark's calls queued, never started**: two picture checks for
  17 minutes while a fresh call beside them finished in 6 s, then 8 of locate's boxes and label checks, sent again
  after 5 minutes; most were answered in about 30 s, and the hook's label check stuck twice and was skipped. None of Muse Wonder's 11 background calls stuck. So the helper's calls (8 to 100 s)
  are one request each again, as on chat completions, and a writer's call still queued after 90 s is cancelled and
  sent again, as is one in progress past 30 minutes, up to three tries (`META_QUEUED_S`, `META_WAIT_S`,
  `META_TRIES`); a call still running says so every 2 minutes, with its id. After the change the arrows step's 10
  helper calls each came back first time. A Meta call ends "completed", which the calls table no longer flags.
- **`plan --videos` and `make` checked the keys on the default brain**, so the Muse job stopped before its videos
  asking for `POPSMOSIS_ANTHROPIC_API_KEY`, as the kidneys' did; the same fix came in from their branch first.
- **Fact ids that were not short.** The first script gave each fact a phrase of Muse Wonder's own for its id ("fact:
  the following facts are the ONLY truth for the droplet: ventilation and structure"), which every line would have
  cited; the second gave "F1" to "F4". This branch renumbered them f1, f2, ... before the bundle was asked for; main's
  `script.numbered`, from the natural selection run's "[generated]" ids, does the same, and replaced it in the rebase.
- **A phospholipid bilayer where there is no membrane.** The look the brief gives a cell scene ends "the membrane a
  phospholipid bilayer"; the hemoglobin close-up has no membrane among its parts, and its first picture came back with
  the capillary's wall painted as a bilayer. Code now leaves the clause out of a scene whose parts name no membrane
  (`art.without_bilayer`); the writer's rewrite put it back, and the painter was sent the scene without it.
- **30 false problems in the code checks.** Muse Wonder marked its O₂ and CO₂ flows "travels", and `checks.arrow`
  counted each as an arrow with no one-liner; the renderer draws a symbol as particles whatever it is marked, so the
  video had none of them. The check now decides as the renderer does.
- Also: the old variables `BRAIN_PROVIDER`, `STUDIO_BRAIN_MODEL`, `STUDIO_BRAIN_EFFORT` and `SONG_PROVIDER` are set in
  this cloud environment, and stop `make` (`brain.retired_problem()`); the run unset them. The videos were rendered
  before this branch was rebased on main's sticky-note change. No scorecard was run (the owner): the checks, brain
  and script changes do not touch the prompts or what the judge reads.

### Against round 21's lungs

Round 21 (Claude Opus, $2.90, "how the lungs exchange gases") had a train at a crowded platform for its analogy, the
70 square meters for its hook, pneumonia for its case, and its gases ran along the capillary. This one is about
oxygen alone but still carries CO₂ the other way, its gases cross the wall, and its misconception and analogy are
placed as the script asks; its song is plainer and its overview holds still. Muse Wonder cost about what Opus did.

### Good enough for the demo?

The lesson video, yes, with the caveat of the hemoglobin picture: it teaches the path in order, the crossing is
right and the misconception lands. The song video is weaker for a demo: the choruses sit over a still overview, and
the one hemoglobin line sings "four" over five. Showing the lesson video and a verse of the song avoids both.

## Not tested

- `python -m studio run`, `videos`, `wait`, `recover` and the upload step: they post to the
  API in `POPSMOSIS_API_URL`, which here is production, and Supabase is not set up.
- The SVG art (`ART_PROVIDER=svg`) through the videos: round 16 drew one plan's scenes with it,
  but made no videos from them. Muse Spark as the brain through the videos (Muse Wonder went
  all the way twice on Nov 2, the kidneys and the lungs; natural selection did not get past its lesson, above).

## Running a round again

```bash
python -m studio plan --topic "..." --course "..." --request "..." --job <id> --brief --videos
python -m studio make <id>        # resume after a failure
python -m studio show <id> --brief
```

In this cloud environment `BRAIN_PROVIDER`, `STUDIO_BRAIN_MODEL`, `STUDIO_BRAIN_EFFORT` and `SONG_PROVIDER` are
still set; they stop `make` (and `plan --videos` before its videos) until they are unset for the command, as on Nov 2.

For the art alone: `plan`, then `python -m studio step art <id>` and `step locate <id>`.

To run a topic on Meta's models alone, as on Nov 2: unset `POPSMOSIS_ANTHROPIC_API_KEY`, `ANTHROPIC_API_KEY`,
`ELEVENLABS_API_KEY`, `POPSMOSIS_API_URL` and `POPSMOSIS_API_KEY`, set `STUDIO_CLAUDE_DRAWS=brain`, keep
`MODEL_API_KEY` and `GEMINI_API_KEY`, and `plan ... --brain muse-spark` (or `muse-wonder`). A call to Claude or
ElevenLabs then fails rather than bills, and nothing can be posted.

To run topics with every Claude call answered by subagents in a Claude Code session, as the Nov 1 round did, see
`tools/insession/README.md`: `tools/insession/start.sh <topic id>`, then answer what `answer.py groups` lists.

To hear a lesson sung by the other singer (round 17), copy the finished job, set
`{"song": {"provider": "gemini"}}` (or `"elevenlabs"`) in the copy's `settings.json`, and run
`make` on the copy: it makes the song, its timing, the timeline and the videos again, and
keeps the rest.

These rounds set the brain and the painter with environment variables (`BRAIN_PROVIDER`,
`ART_PROVIDER`), which the studio no longer reads: an admin sets both on `/admin/studio`
now. A local job is written by the brain `plan --brain` names (Claude Opus 5.5 by default)
and painted by that brain's default painter, Gemini 3.1 Flash Image for Claude's, unless the
job's `settings.json` says otherwise: `{"art": {"claude": "muse"}}` has Muse Image paint it,
`{"art": {"claude": "svg"}}` has Claude draw it. Write it after `plan` and before the art.
Rounds 5 to 16 were Claude's lesson painted by Muse Image this way.

The jobs from these rounds were `r1-*` and `r2-*` in `jobs/` (git-ignored, gone with the
container). To look at a video, take a still every few seconds (`ffmpeg -ss <t> -i
out/song_h.mp4 -frames:v 1 still.png`); to check what was located, lay each
`masks/<scene>/<structure>.png` over its `art/<scene>.png`.
