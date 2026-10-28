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
