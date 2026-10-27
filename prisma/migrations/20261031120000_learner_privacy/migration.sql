-- A learner can take back a request that has a lesson, or delete their account: their
-- words, files and follow-ups go, the lesson stays, and the request no longer names them.
-- AlterTable
ALTER TABLE "questions" ALTER COLUMN "userId" DROP NOT NULL;

-- Deleting an account finds the person's follow-ups by their author; without this index
-- the cascade reads the whole replies table. The hourly follow-up limit counts by it too.
-- CreateIndex
CREATE INDEX "replies_userId_createdAt_idx" ON "replies"("userId", "createdAt");
