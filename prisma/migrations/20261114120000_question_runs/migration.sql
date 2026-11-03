-- How many runs have started on a topic, each on its own machine (POST /api/questions/:id/claim with a runId the topic
-- has not seen last), and the last run's id, so a run's renewals and retries count once. A topic whose every run
-- ended before its lesson was posted is given up once it reaches QUESTION_MAX_RUNS, rather than started on yet another
-- machine and paid for again (api/src/lib/claim.ts). A topic already waiting starts at 0, with no run.
ALTER TABLE "questions" ADD COLUMN "runs" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "questions" ADD COLUMN "lastRunId" TEXT;
