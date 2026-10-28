-- Each video's hashtags, the way a TikTok is tagged: the studio's brain writes them into the bundle, and the API
-- copies them here, cleaned, when the lesson is posted (api/src/lib/tags.ts). The Droplets tab searches by them.
-- AlterTable
ALTER TABLE "answers" ADD COLUMN     "lessonTags" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN     "songTags" TEXT[] DEFAULT ARRAY[]::TEXT[];

-- CreateIndex
CREATE INDEX "answers_songTags_idx" ON "answers" USING GIN ("songTags");

-- CreateIndex
CREATE INDEX "answers_lessonTags_idx" ON "answers" USING GIN ("lessonTags");
