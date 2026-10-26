-- AlterTable
ALTER TABLE "questions" ADD COLUMN     "claimedAt" TIMESTAMP(3),
ADD COLUMN     "claimedById" TEXT;

-- CreateIndex
CREATE INDEX "questions_status_claimedAt_createdAt_idx" ON "questions"("status", "claimedAt", "createdAt");
