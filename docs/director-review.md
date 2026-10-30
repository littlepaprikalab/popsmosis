# The director's review (not built yet)

A step after the timeline that looks at the video the way a student does, before the full render, and fixes what
confuses from a fixed menu. Written up on Oct 30 for later; nothing here runs today.

## Why

The renderer is code: it does what the timeline says and cannot see the result. Every round since 17 a person (or
Claude, reviewing by eye) found frames that confuse a learner and that no step checks:

- a label pinned on the wrong part (round 19: "Mountain" on a band underground; fixed since by the label check),
- pills crowding a thin part (round 22: "$10 from interest" and "$100 from principal" on one thin strip of the
  stacked bar),
- a zoom that cuts off what the line is about (round 22: the top of the stacked bar against the frame's edge),
- motion that does not match the sentence (round 21: the gases ran along the capillary, not across the membrane;
  particles cut across the nephron's bends),
- motion where nothing should move (round 21: a dot on the hook's cliff).

Each was fixed in code once found, but finding them took a person watching every video.

## What it does

1. **Stills, not video.** After the timeline step, render one frame per beat, late in the beat (labels and values
   on, the camera settled): about 34 frames for a lesson and 57 for a song, a second or two of local rendering in
   all. No video is encoded.
2. **Look.** A vision model sees the stills in order, each with the line said over it, and answers, per beat, only
   from a closed list of problems: label on the wrong thing, labels overlapping, subject cut off, subject too small,
   motion that contradicts the line, motion that should not be there, a value or label unreadable.
3. **Fix from a menu.** Each problem maps to a timeline edit code can make and check: show the whole scene instead
   of zooming; zoom on another part; drop the beat's flow; draw a flow as hand-off arrows instead of particles; hide
   a label on this beat; hold the beat's picture longer (merge with the next on the same scene). The model never
   gives coordinates (vision models place points badly; every pixel-level answer we have tried needed a code check
   behind it): it picks from the menu, and code applies the pick.
4. **Verify in code, then render once.** A fix is kept only if a code check agrees it helped (after "show the whole
   scene", the spotlit part is fully in frame; after "hide a label", the pills no longer overlap). Then the full
   render runs as today.

## Cost: keep it cheap

From this repo's call ledger (round 22): one picture checked by Claude Sonnet costs about $0.007 (about 3,800
tokens in, mostly the picture, about 100 out); the whole compound-interest droplet cost $3.39. The review must stay
a small share of that.

| Design                                   | Calls per droplet | Roughly |
| ---------------------------------------- | ----------------- | ------- |
| One still per call, Sonnet               | ~90               | $0.65   |
| Contact sheets, 9 stills each, Sonnet    | ~11               | $0.10   |
| Contact sheets, Gemini Flash             | ~11               | $0.02   |
| Code lint first, sheets only where flagged | 2 to 5          | < $0.05 |

So, in order:

1. **Code lint first, free.** Much of the list is measurable without a model: pills overlapping each other or a
   value; a pill or value outside the frame; the spotlit part less than, say, 80% inside the frame; a particle route
   that leaves its parts' masks for more than a few cells; a part labelled on a beat where it is covered. Fix what
   code can and flag the rest.
2. **Contact sheets, not single frames.** Nine stills to an image (each about 640 x 360, the line printed under it)
   cost about as much as one full-size still, and the model sees the sequence, which is what catches a cut from
   nowhere or a scene changing too fast.
3. **The cheap model first.** Gemini Flash (about $0.50 per million tokens in) already judges our songs well
   (scratch song critic, rounds 18 to 22). Escalate to Sonnet only for a sheet Flash flags and a fix cannot settle.
4. **Only what changed.** Key each sheet by the hash of its beats; a re-render of the same timeline reviews
   nothing.
5. **A cap.** A budget per droplet (say $0.10); past it the review stops and says so in the step's line.

## How to roll it out

1. **Report only.** Run it over the scorecard's 12 topics, writing what it flags beside each video, and compare
   with a person's review of the same videos. Keep only the problems it finds reliably (high agreement, few false
   alarms).
2. **Then the safe fixes.** Start with the ones code can verify fully (show the whole scene, hide a crowding
   label), and add the others one at a time.
3. **Then the song.** The same, with the lyric under each still.

## Open questions

- Whether a still per beat is enough, or motion problems need two stills a second apart (the start and end of a
  flow's trip).
- Whether the brain should see the review's findings when it writes the next bundle, so a direction that keeps
  failing (a zoom on a thin part) is not written again.
