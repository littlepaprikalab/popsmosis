# Notes for Claude

## CI

GitHub Actions isn't running for this repo right now: every job ends in seconds with
no logs, on `main` too. Don't investigate, re-run or comment on red CI checks.
Check changes locally instead:

- `npm --workspace web run typecheck` and `npm --workspace web run build`
- `npm --workspace api run typecheck`
- the Playwright suite against the real stack (`web/e2e/README.md`)

Remove this section once Actions runs again.
