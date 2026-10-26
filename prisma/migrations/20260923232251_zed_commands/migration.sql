-- AlterTable
ALTER TABLE "answers" ADD COLUMN     "commands" JSONB;

-- AlterTable
ALTER TABLE "questions" ADD COLUMN     "client" TEXT;

-- AlterTable
ALTER TABLE "replies" ADD COLUMN     "client" TEXT,
ADD COLUMN     "commands" JSONB;
