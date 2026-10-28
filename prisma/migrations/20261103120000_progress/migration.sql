-- Where the studio is while it makes a droplet (api/src/lib/progress.ts): the page shows it as the bill of
-- materials filling in, step by step, and holds the lesson text back until the videos are done.
-- AlterTable
ALTER TABLE "questions" ADD COLUMN     "progress" JSONB;

-- AlterTable
ALTER TABLE "answers" ADD COLUMN     "videoProgress" JSONB;
