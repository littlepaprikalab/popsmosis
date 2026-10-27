-- AlterTable
ALTER TABLE "answers" ADD COLUMN     "hiddenAt" TIMESTAMP(3),
ADD COLUMN     "lessonPosterUrl" TEXT,
ADD COLUMN     "songPosterUrl" TEXT;

-- CreateIndex
CREATE INDEX "answers_videoStatus_createdAt_id_idx" ON "answers"("videoStatus", "createdAt" DESC, "id" DESC);
