# Notes for Claude

## CI

GitHub Actions isn't running for this repo right now: every job ends in seconds with
no logs, on `main` too. Don't investigate, re-run or comment on red CI checks.
Check changes locally instead:

- `npm --workspace web run typecheck` and `npm --workspace web run build`
- `npm --workspace api run typecheck`
- the Playwright suite against the real stack (`web/e2e/README.md`)

Remove this section once Actions runs again.

## Live tests spend money

`studio/tests/test_live*.py` call the real Anthropic, Meta, Gemini and ElevenLabs APIs and
bill the owner's accounts. They run only with `STUDIO_LIVE=1` (`STUDIO_LIVE_AUDIO=1` for the
audio ones). Never set those unless the user asks for a live check in this conversation;
`python -m pytest studio` on its own makes no paid calls, whatever keys the environment has.

## The scorecard spends money too

`python -m studio scorecard run` ([docs/scorecard.md](docs/scorecard.md)) makes real Anthropic
calls: about $20 for all 12 topics, about $1.50 a topic. Never run it, not even for a few topics
or a `--rejudge`, unless the user approves that run in this conversation.

When a change you make could move its scores, offer a run once, with its cost, and wait for
the answer. That means a change to the bundle prompt (`studio/prompts/bundle.md`), the
rubric, the code checks (`studio/checks.py`), the learner test (`studio/steps/learner.py`),
the bundle step, or the brain, its model or its effort (`studio/brain.py`).
`scorecard compare` against the latest run in `studio/scorecards/runs/` is free. Commit a
run's folder once it ends.
