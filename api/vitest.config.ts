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
  },
});
