# The droplet scorecard

The scorecard tells you whether a change made droplets better overall, not just on the one
topic you were looking at. It is not a test: nothing runs it automatically, it has no pass or
fail, and every run spends real money (about $20). Run it by hand when one of the reasons below
applies.

It runs a fixed set of 12 topics (`studio/scorecards/topics.json`) through the bundle step only:
the brain writes each bundle, the learner test reads its lesson, and the code checks run. There is
no fact-check, art or audio. Each droplet is then scored three ways:

- **Checks:** the problems the code checks leave for the prompt, by rule.
- **Learner test:** whether a first-time learner got the core idea and the why chain's links.
- **Judge:** Claude Sonnet 5.5 reads the droplet against a written rubric
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

To try a change cheaply first, run a few topics (about $1.50 each); then run all 12 before merging.

## How to run it

In a Claude Code session, ask: "Run the scorecard and compare it with the latest run in
`studio/scorecards/runs`, then commit the run." Or run it yourself:

```bash
python -m studio scorecard run                                    # all 12 topics, about 45 minutes
python -m studio scorecard run --topics light-reactions,derivative # a few, to try a change
python -m studio scorecard compare <earlier-run> <new-run>        # two runs side by side
```

- It needs `POPSMOSIS_ANTHROPIC_API_KEY`. Without it, it stops before spending anything.
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
