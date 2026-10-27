-- CreateEnum
CREATE TYPE "VideoStatus" AS ENUM ('RENDERING', 'READY', 'FAILED');

-- AlterEnum
ALTER TYPE "QuestionStatus" ADD VALUE 'FAILED';

-- AlterTable
ALTER TABLE "answers" ADD COLUMN     "bundle" JSONB,
ADD COLUMN     "factcheck" JSONB,
ADD COLUMN     "lessonVideoUrl" TEXT,
ADD COLUMN     "songVideoUrl" TEXT,
ADD COLUMN     "videoError" TEXT,
ADD COLUMN     "videoStatus" "VideoStatus",
ADD COLUMN     "videoUpdatedAt" TIMESTAMP(3);

-- AlterTable
ALTER TABLE "questions" ADD COLUMN     "failedReason" TEXT;
