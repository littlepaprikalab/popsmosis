-- A learner can take back a request that has a lesson, or delete their account: their
-- words, files and follow-ups go, the lesson stays, and the request no longer names them.
-- AlterTable
ALTER TABLE "questions" ALTER COLUMN "userId" DROP NOT NULL;
