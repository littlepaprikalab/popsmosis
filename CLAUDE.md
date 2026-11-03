# Notes for Claude

These notes are for **coding sessions** on this repository: changing the code, test rounds, the scorecard. They do
not apply to a heartbeat of the teacher's routine, the run the API trigger or the schedule starts with
docs/popsmosis-agent-skill.md: that run follows the skill file alone, and makes each droplet's videos on the API keys
as its Step 2b says (the owner, Nov 3). Nothing below changes what a heartbeat does.

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

## Test drawings run in the session too, never on the API key

When you make or remake a job's videos in a coding session (a test round, a remake), the art step never draws on
the API. A subagent
draws each SVG scene from `art.PROMPT` and `art.user_prompt(bundle, scene, previous)`. Judge its
answer with `art.judge`, fixing only what it refuses (at most `art.MAX_TRIES` tries). Then save it as
the scene's kept drawing (`art._marker(bundle, scene)` and the SVG, at `art._kept(job, scene)`), so the
step finds it done. Anything else the brain would write for the job is a subagent too.

Run the media steps after that with every API key unset, so a step that would still call out fails
instead of billing. Paint, music or a voice on the API (Muse Image, Gemini) only when the user asks
for it in this conversation, with its price said first. This is the owner's standing decision
(Oct 31), after a redraw billed $0.76 of drawer calls.

## Rules in the prompts earn their place

The writer's brief is long, and the writer does not follow every rule in it (Nov 1: `held` was never set in two
scorecard runs, panels were asked for in every run). The owner's standing decision (Nov 1):

- A new prompt rule is tested before it stays: a subagent writer (free) on the topics it is for. If it does not
  change what the writer does, take it back out rather than leave it to pile up.
- Prefer code that makes the outcome sure over a rule that asks for it: a default, a guard that repairs, or the
  fact-check dropping what is wrong (as `held` became a default, and an analogy scene is left out by a guard).
- Trim what stops applying when the thing it was for goes away.

## The scorecard runs in the session, never on the API key

Always run the scorecard ([docs/scorecard.md](docs/scorecard.md)) in the Claude Code session,
with subagents standing in for every model call. Never run `python -m studio scorecard run`,
not even for a few topics or a `--rejudge`: it calls the Anthropic API on the owner's key
(about $20 for all 12 topics). This is the owner's standing decision.

How to run it, for each topic in `studio/scorecards/topics.json`:

- **The brain:** one subagent (on the session's model, as the brain is Opus) writes the script,
  then the bundle, as two separate answers. Give it the prompts the code builds:
  `script.system_prompt(brief)` and the brief itself (`studio/prompts/bundle.md`), the user
  prompt `bundle.user_prompt(topic.as_request(), style_for(topic.id))` (with
  `script.as_prompt(script)` after it for the bundle), and the schemas from
  `brain.strict_schema(Script)` and `brain.strict_schema(Bundle)`. It validates each answer
  against `Script` / `Bundle` and fixes only what fails validation, never for quality.
- **The checks:** run the bundle step's code checks on its answer, in `bundle.run`'s order:
  `checks.held_to_guide` on the script's facts before the bundle prompt is built, then
  `script.follow`, `checks.review`, `checks.repair_once` (with the topic's study guide),
  `checks.review` again when that changed anything (keeping the first report's problems), and
  the problem functions it adds. Free.
- **The judge:** one Opus subagent (the session's model, the owner's choice on Oct 31; it was Sonnet before,
  and runs are compared only on one judge) grades the droplet as `scorecard.droplet_text` prints it,
  against `studio/prompts/rubric.md`, blind beside the latest real run's droplet for the same
  topic (labelled X and Y at random), and says which it prefers.
- **Read the droplets yourself** as well, lesson, song and narration, and report what you
  found with quotes, not only the scores.

`tools/insession/score.py` does the code's part of all this (prompts, answer checks, the code checks, the blind
pairing and the summary), and `tools/insession/SCORE_WRITER.md` and `SCORE_JUDGE.md` are the subagents' briefs.

It costs no API money but a lot of session usage (about 24 subagents, 40 minutes), so when a
change you make could move its scores, offer a run once and wait for the answer. That means a
change to the bundle prompt (`studio/prompts/bundle.md`), the script prompt
(`studio/prompts/script.md`), the rubric, the code checks (`studio/checks.py`), the bundle step,
or the brain, its model or its effort (`studio/brain.py`). The narration edit is skipped; say so.
