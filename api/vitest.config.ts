import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    // The integration files share one Postgres (DATABASE_URL) and reset it
    // between cases, so running files in parallel lets one file's TRUNCATE land
    // in the middle of another's assertions. Verified: with parallelism on, a
    // TRUNCATE loop in one integration file fails all 12 tests in the other.
    // The whole suite is ~2s, so serialising files costs nothing worth having.
    fileParallelism: false,
    // A passing run prints only the totals; a failing one prints the failed
    // tests, their errors and their console output. Pass `--reporter=default`
    // to list every file. The `minimal` reporter is what vitest picks by itself
    // when it detects an AI agent; naming it here makes that hold everywhere.
    reporters: process.env.GITHUB_ACTIONS === 'true' ? ['minimal', 'github-actions'] : ['minimal'],
    silent: 'passed-only',
    // adapter-pg sends a second query on a client that is still busy inside
    // Prisma's interactive transactions, and pg@8 prints a deprecation warning
    // for it once per test file. That is Prisma's to fix; `npm run dev` still
    // shows deprecations for our own code.
    execArgv: ['--no-deprecation'],
  },
});
