-- How many machines have started on a topic (POST /api/questions/:id/claim with newRun). A topic whose every run
-- ended before its lesson was posted is given up once it reaches QUESTION_MAX_RUNS, rather than started on yet another
-- machine and paid for again (api/src/lib/claim.ts). A topic already waiting starts at 0.
ALTER TABLE "questions" ADD COLUMN "runs" INTEGER NOT NULL DEFAULT 0;
