# The droplet scorecard

The scorecard tells you whether a change made droplets better overall, not just on the one
topic you were looking at. It is not a test: nothing runs it automatically, it has no pass or
fail. We run it in a Claude Code session with subagents standing in for the model calls, so a
run costs session usage, not API money (`CLAUDE.md` says how). Run it when one of the reasons
below applies.

It runs a fixed set of 12 topics (`studio/scorecards/topics.json`) through the bundle step only:
the brain writes each bundle, the learner test reads its lesson, and the code checks run. There is
no fact-check, art or audio. Each droplet is then scored three ways:

- **Checks:** the problems the code checks leave for the prompt, by rule.
- **Learner test:** whether a first-time learner got the core idea and the why chain's links.
- **Judge:** Claude Opus 5.5 (Claude Sonnet 5.5 until Oct 31) reads the droplet against a written rubric
  (`studio/prompts/rubric.md`). It scores hook, frame, why chain, key lines, accuracy,
  singability and lesson clarity from 1 to 5, gives a reason for each, and lists the claims it
  would correct.

The real pipeline (`run`, `plan`, `videos`) never calls the scorecard or its judge.

## When to run it

- Before merging a change to the bundle prompt (`studio/prompts/bundle.md`), the code checks
  (`studio/checks.py`) or the learner test (`studio/steps/learner.py`).
- When the brain or its effort changes, or a new model comes out.
- After several small changes that were each tried on only a few topics: together they can
  move the others.

To try a change quickly first, run a few topics; then run all 12 before merging.

## How to run it

**We run the scorecard in a Claude Code session, with subagents standing in for every model
call, never with the API key.** `CLAUDE.md` ("The scorecard runs in the session") says how.
The command below is kept for reference only: it bills the owner's Anthropic account.


In a Claude Code session, ask: "Run the scorecard and compare it with the latest run in
`studio/scorecards/runs`, then commit the run." Or run it yourself:

```bash
python -m studio scorecard run                                    # all 12 topics, about 45 minutes
python -m studio scorecard run --topics light-reactions,derivative # a few, to try a change
python -m studio scorecard compare <earlier-run> <new-run>        # two runs side by side
```

- It needs `POPSMOSIS_ANTHROPIC_API_KEY`. Without it, it stops before spending anything.
- It refuses to run while `STUDIO_VIDEOS` is `lesson`: those droplets have no song, and the rubric
  judges one. Its scores are for the droplet with its song.
- `--budget 40` (the default): no topic starts once the run has spent that much.
- `--brain muse-spark` (or `muse-wonder`) and `--effort` run it on another brain.
- `--resume <run>` finishes a run that stopped (a container restart), redoing only what is missing.
- `--resume <run> --rejudge` judges a run's droplets again after the rubric changes, keeping its
  bundles (about $0.40). It never makes a bundle, so a topic whose bundle failed stays failed.

**Commit the run folder.** A cloud container does not keep files, and the next comparison needs
the run.

## Reading the results

Each run is a folder in `studio/scorecards/runs/`, named for when it started and its commit:

- `scorecard.md`: start here. The summary, a table by topic, and the reason behind every score.
- `scorecard.json`: the same data, which `compare` reads.
- A folder per topic: the whole droplet (`bundle.json`), its checks, the learner test, the
  judge's scores and every call's cost.

`compare` shows each criterion's mean and each topic's scores before and after. A change is
worth merging when the means go up without other topics going down.

Each topic is made once a run, so scores also move by chance. Treat a mean that moves by less than
about half a point, or one topic moving by one point, as noise. Running the same commit twice
shows how much scores move on their own.

Runs are comparable only on the same rubric and judge; `compare` says when they differ. Changing
the topic list is fine, but `compare` uses only the topics both runs have.

## In-session run, Oct 31

The first run in a session (no API money): the 12 topics' bundles written by subagents on that day's
prompts (the trimmed brief, meanings, the graph rules), the code checks run, the learner test and the
narration edit skipped. Each droplet was judged blind beside the latest real run's droplet for
its topic (`20261030-0015-f9b2d85`, the "baseline" below).

| Judge | Preferred the new droplet | New, mean | Baseline, mean | Errors listed, new / baseline |
|---|---|---|---|---|
| Claude Sonnet 5.5 | 12 of 12 | 4.21 | 3.58 | |
| Claude Opus 5.5 | 12 of 12 | 4.45 | 3.73 | 9 / 15 |

Opus scores both arms about 0.2 higher than Sonnet and sees the same gap, so runs judged by one
are not compared with runs judged by the other. By criterion, on Opus (new / baseline): hook
4.50 / 4.50, frame 4.67 / 3.67, why chain 4.42 / 3.92, key lines 4.17 / 3.00, accuracy 4.42 /
4.08, singability 4.08 / 3.00, lesson clarity 4.92 / 3.92. The hook did not move; the gains are
in the key lines, the singing and the lesson.

Opus's errors in the new droplets are fine points the fact-check is there to catch: the wedding in
*Romeo and Juliet* happens offstage ("We see the wedding"); a lunar eclipse is red for at most
about an hour and three quarters, not "a few hours"; the clergy and nobles paid little tax, not
none ("the penthouses pay none"); highs and lows also sit where there is no derivative ("Where
the tangent line lies flat, they're caught"); Spanish keeps *que* with one subject after doubt
("with only one subject, there's no que at all").

### Again, after the day's song changes

Run again in the session after the title examples ("Cash in the carriers"), the intro and chorus loosened, and
the exam-review rule for rapped verses (none of the 12 topics draws a rapped style, so that rule went untested
here), judged by Opus beside the same baseline droplets:

| Run | Preferred the new droplet | New, mean | Baseline, mean | Errors listed, new / baseline |
|---|---|---|---|---|
| Earlier on Oct 31 | 12 of 12 | 4.45 | 3.73 | 9 / 15 |
| After the song changes | 12 of 12 | 4.52 | 3.69 | 6 / 13 |

Within the noise of one run, nothing went down. The hook, new against baseline, went from 4.50 / 4.50 to
4.75 / 4.17, but the same baseline droplets' hook moved by a third of a point between the two judgings, so read
that as a hint. Accuracy went from 4.42 to 4.67. Read through:

- **Every intro now opens on a line that leads to the title**, most of them a question or a scene: "No time, no
  distance, still a speed?", "Nine out of ten, is it skill or luck?", "Trays piled high or a line out the door".
  None opened on the title line, which the brief still allows.
- **11 of the 12 choruses run 5 lines**, title, three lines, title, as Split the Water's does. The brief allows 4
  or 5; the 5 has become the habit.
- **The title example bends on its own topic:** the light reactions sang "Split the water, cash the light", the
  example turned to its money frame, where the run before sang it word for word.
- **The errors are song lines and key lines** the fact-check is there for: "The queen's whole court? Just six
  percent" (the whole royal court), "you weigh less than before" (mass), "Till gravity pulls, you carry on" (no
  gravity in space until something switches it on), an eclipse sung as if every full moon had one, a key line
  saying the new moon has "no lit half", and classical conditioning's frame making the learned response a choice
  ("reaching for the umbrella").

## In-session run, Nov 1

After the day's changes: the core idea says what a thing is and what it is for (cause and effect for a topic about
why something happens), a fact says what it is for, step lines follow the song's order, crowds that are always
there (`held`), no analogy picture, and the brief trimmed of its analogy leftovers. Judged by Opus beside the same
baseline droplets:

| Run | Preferred the new droplet | New, mean | Baseline, mean | Errors listed, new / baseline |
|---|---|---|---|---|
| Oct 31, after the song changes | 12 of 12 | 4.52 | 3.69 | 6 / 13 |
| Nov 1 | 12 of 12 | 4.44 | 3.71 | 12 / 9 |

By criterion, against Oct 31: hook 4.92 (4.75), frame 4.75 (4.58), singability 4.25 (4.08), lesson 4.83 (4.92),
why chain 4.33 (4.58), key lines 3.67 (4.08), accuracy 4.33 (4.67). Read through, and beside the judge:

- **No droplet drew the analogy**, and every core idea took its kind: what it is and what it is for ("The
  sodium-potassium pump spends ATP pushing sodium out and potassium in, so nerves can fire and cells don't swell";
  "A p-value is how often luck alone would give results at least as extreme as yours; small makes luck doubtful"),
  cause and effect for the Moon, the revolution, the rocket and prices. Lessons say what the thing is for early:
  "Catalysts let reactions that would be far too slow run fast at ordinary temperatures, so life and much of
  industry depend on them."
- **Key lines dipped for a known reason in part:** the rubric reads them "in order", and the rocket's, placed as
  its song sings them, put "No air in the pair → slightly more thrust in space" first (scored 2). The light
  reactions and the p-value lost a point for a question as the first line and a last line on the next stage.
- **The errors doubled** (6 to 12), none of them in what changed: song lines that overclaim ("Small enough, the
  court rules luck out", "She can really taste it after all"; "a republic grows" sung as if straight after 1789),
  a bridge saying |x|'s secant slopes "climb" and "fall", a misconception that is itself wrong (air "only drags",
  so a rocket pushes harder in space), "rolling forward is the thrust", and the pump's "Three sodium climb in from
  inside", heard as sodium entering the cell. One run's spread; the fact-check, which this run leaves out, is
  there for these.
- **`held` was missed where it mattered:** the pump wrote sodium outside and potassium inside as `held: false`,
  the rule's own example of true.
- **The step-line hint fired 6 times, about one of them real** (supply and demand's equilibrium step on "Squeezed
  from the top, squeezed from the floor"): songs sing their steps in their own words.
- **The side-by-side check fired 6 times, half of them real:** classical conditioning's three panels, the Moon
  drawn three times, the catalyst's two panels; not two strawberry markets, nor Romeo and Juliet standing side by
  side.
- **The light reactions' old song is the better song**, "Split the water, catch the light", though the judge and
  the lesson preferred the new droplet.

### Again, after the day's later changes

Run again in the session after: a fact that says what the thing is for, `held` for crowds that are always there,
step lines that follow the song and hold only to the end of their section, the rap sections (K-pop and Latin pop
their second verse, dance-pop its bridge), the rule for crossing a membrane through its channel or pump, the
compressed fact-check prompt, and the side-by-side check tightened to panels and things drawn twice. Judged by Opus
beside the same baseline droplets:

| Run | Preferred the new droplet | New, mean | Baseline, mean | Errors listed, new / baseline |
|---|---|---|---|---|
| Oct 31, after the song changes | 12 of 12 | 4.52 | 3.69 | 6 / 13 |
| Nov 1, morning | 12 of 12 | 4.44 | 3.71 | 12 / 8 |
| Nov 1, evening | 12 of 12 | 4.43 | 3.79 | 9 / 12 |

By criterion, against the morning: key lines 4.17 (3.67), why chain 4.42 (4.33), lesson 4.92 (4.83), hook 4.67
(4.92), frame 4.58 (4.75), singability 4.00 (4.25), accuracy 4.25 (4.33). The key lines came back: none was read
out of order this time. Read through:

- **The rap sections held:** the three topics the rotation gives K-pop, Latin pop or dance-pop rapped exactly
  their one section, 8 lines each, and nothing else rapped.
- **What it is for reached the song:** the pump's chorus sings it ("So the nerves can fire, and the cell / Keeps its
  size, it doesn't swell"), its facts state it, and its core idea says it.
- **`held` was never set,** in this run or the morning's, not even for the pump's resting gradients the rule named:
  it was dropped that evening, and every crowd starts half full.
- **The pump still routed sodium in and potassium out through the bare membrane** on two of its fourteen routes,
  beside the sodium and potassium channels it drew and used elsewhere: the fact-check, which this run leaves out,
  fails both (tested on the morning's pump), and the video leaves them out.
- **Panels are asked for three times,** twice in classical conditioning (as in every run) and once in the rocket's
  air against space: the check records them, but they would still be painted.

### Again, after the brief's trim

Run again in the session after the writer's brief was trimmed (9,298 to 8,795 words: its narration rules left to
the script's prompt, rules said twice said once, and "a line that sums up holds still" cut, which contradicted "the
chorus shows the whole pathway"), after the panels rule, and with the rubric's singability anchor allowing the
section a style raps. Judged by Opus beside the same baseline droplets:

| Run | Preferred the new droplet | New, mean | Baseline, mean | Errors listed, new / baseline |
|---|---|---|---|---|
| Oct 31, after the song changes | 12 of 12 | 4.52 | 3.69 | 6 / 13 |
| Nov 1, morning | 12 of 12 | 4.44 | 3.71 | 12 / 8 |
| Nov 1, evening | 12 of 12 | 4.43 | 3.79 | 9 / 12 |
| Nov 1, after the trim | 12 of 12 | 4.52 | 3.75 | 3 / 12 |

By criterion, against the evening: accuracy 4.75 (4.25), why chain 4.67 (4.42), frame 4.75 (4.58), singability
4.08 (4.00), lesson 4.83 (4.92), hook 4.58 (4.67), key lines 4.00 (4.17). The baseline, the same droplets each
time, scored 3.75 against 3.79: about that much is the judge. Read through:

- **No panels:** none of the 12 asked for one. Classical conditioning drew "before", "pairing" and "after" as stages
  of one lab, and the rocket its air and space as two scenes; the evening run asked three times.
- **What it is for:** the pump's core idea is "A pump in the cell membrane spends ATP to keep sodium out and
  potassium in, so nerves can fire", and its lesson opens on "So what does a cell get for all that energy?". Every
  route of its ions goes through the pump, a channel or a carrier; only water crosses the membrane itself.
- **The rap sections** kept to their one section (the derivative's second verse, the bridges of supply and demand
  and the subjunctive); singability 4, 5 and 4 (4, 4 and 4 in the evening).
- **Three errors:** the catalyst's chorus sings "More collisions, faster chemistry" three times, when a catalyst
  makes more of the same collisions succeed; the derivative's lesson says "an average over an interval is not the
  rate at either point", which is not so for a line; the p-value's says it "lets you tell a real effect from a
  fluke of chance". The fact-check, which this run leaves out, is where these would be caught.
- **Key lines** lost a point in two droplets for opening on the question and not ending on the core idea, as the
  rubric's anchor asks ("from its first link to the core idea"), and for narration anchors out of the list's
  order. The brief now asks for the question first, keeps the core idea at the top of every frame and never on a
  key line, and each video places the key lines by its own lines: the anchor is out of date.
- **The lyric check that flags a line narrating the picture** fired on "Watch the replay, you know the score" and
  "Watch where the slopes are heading": a frame sung to the listener, not a camera direction. It now counts "watch"
  and "see" only after "now" or "let's", and "look" only when it points somewhere ("look at", "look inside").
- Chorus lines with particles: 79 of 198 (70 of 204 in the evening), with the summing-up rule gone.

## The baseline: `20261029-2019-ba83842`

Commit `ba83842`, Claude Opus 5.5 at high effort, $19.82, judged by the current rubric.

| Hook | Frame | Why chain | Key lines | Accuracy | Singability | Lesson |
|---|---|---|---|---|---|---|
| 4.17 | 3.92 | 4.33 | 3.00 | 4.08 | 3.08 | 3.75 |

- **The rules hold outside science.** History, literature, economics, maths, statistics and
  Spanish score about the same as biology.
- **Key lines are the weakest, the same way in every topic.** The first key line is the lesson's
  opening question rather than a step, one or two are analogy mappings ("Lamp = Sun, ball =
  Moon"), and the last is often about the bridge rather than the core idea.
- **Singability:** the choruses are strong everywhere, but the verses run long and read like
  textbook captions (13 lyric-style check problems, mostly lines over 12 words).
- **Checks:** 25 problems left for the prompt, about 2 a droplet.
- **The learner test passed 12 of 12 on the first read.** It is at its ceiling, so it cannot show
  an improvement; a harder transfer question would.
- **The judge's error list is still noisy:** about a third of its 22 entries say nothing needs
  correcting. Trust the accuracy score and its reason over the count.

### Again, after the principles brief

Run again in the session after the writers' prompts were rebuilt on seven teaching principles and cut back (commit
a6063f3): no "same trick in another topic" or "what comes next in the course" fact, one pre-chorus, a bridge only for
a system that can fail, a case only where the idea applies to something new, the everyday comparison optional and said
once, motion only where a line names it, and examples no longer drawn from these 12 topics. The learner test and the
narration edit were skipped, as in every in-session run. Judged by Opus beside the same baseline droplets:

| Run | Preferred the new droplet | New, mean | Baseline, mean | Errors listed, new / baseline |
|---|---|---|---|---|
| Nov 1, after the trim | 12 of 12 | 4.52 | 3.75 | 3 / 12 |
| Nov 1, principles brief | 12 of 12 | 4.33 | 3.64 | 3 / 14 |

By criterion, against the trim: frame 2.75 (4.75), key lines 4.75 (4.00), hook 4.58 (4.58), why chain 4.67 (4.67),
accuracy 4.75 (4.75), singability 4.00 (4.08), lesson 4.83 (4.83). The whole drop is the frame: the rubric rewards one
comparison carried through the song, the lesson and the narration, which this brief no longer asks for. Without the
frame the new droplets score 4.60 against the baseline's 3.57. The key lines went up most. Read through:

- **No second topic.** The French Revolution's core idea is "France's king went broke, and the Third Estate turned a
  fight over who pays into a fight over who rules"; no England, Charles I or pressure cooker. Its misconception is a
  real one ("did the royal court's luxury at Versailles empty the treasury?"), sung as "Not the court, its slice is
  small / War debt eats up half of it all". Romeo and Juliet's title is the play itself, "We see the end, but Romeo
  can't", where it was "Don't open that door, Romeo".
- **The comparison is used in 4 of 12, once each:** the pump's water tower, the light reactions' solar charger, the
  p-value's trial, Romeo and Juliet's surprise party. The judge marks the others down for having none (the French
  Revolution 1, the subjunctive and the Moon 2).
- **Bridges only where a system can fail:** the pump's stroke, the light reactions' weedkiller, the catalyst's boiled
  potato, the rocket's engine quitting, supply and demand's price cap. The other seven songs run 37 to 40 lines. The
  derivative dropped its corner of |x|; the subjunctive moved its rotation's rapped bridge to its second verse.
- **A false alarm:** on every song without a bridge the last two choruses run together, and song_shape_problems counts
  them as one ("the chorus is sung 2 times, not 3").
- **A new tic:** the French narration says "Back on the line" four times, about a timeline it never sets up, from the
  script prompt's "back to the overview between close-ups".
- **Three errors:** Juliet waking moments too late called situational irony as if a twist could only be that; the light
  reactions song singing "hydrogen" for hydrogen ions; the rocket coasting "because nothing pushes it", leaving out
  gravity. The fact-check, which this run leaves out, is where these would be caught.
- **Still long:** the lessons run 512 to 697 words against the 300 to 500 asked, and the narrations 366 to 417 of 420.
