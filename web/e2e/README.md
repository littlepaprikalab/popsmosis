# Browser tests

End-to-end tests with Playwright against the real stack: Postgres, the API on
`:3001`, the web app on `:3000`. They sign in by minting a session cookie with
`JWT_SECRET` (there is no Google in CI) and drive the teacher through the agent
API with a real key, so they cover the same paths a person and a runner use.

Run locally with the dev servers up:

```
npm run e2e            # from the repo root; seeds sample data first
```

`e2e/global-setup.ts` runs `seed:dev`, which creates Ada (admin), Ben, and the
`popsmosis-teacher` agent, and stores their ids and the agent key for the tests.
