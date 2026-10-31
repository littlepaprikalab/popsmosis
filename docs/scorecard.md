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
