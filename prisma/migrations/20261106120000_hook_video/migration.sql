-- The hook cut (studio/steps/timeline.py hook_cut): an upright clip of the song's pre-chorus and first chorus,
-- the part students share. Null for a droplet made before it, or a song with no chorus.
-- AlterTable
ALTER TABLE "answers" ADD COLUMN "hookVideoUrl" TEXT,
ADD COLUMN "hookPosterUrl" TEXT;

-- The Droplets tab's Clips feed (GET /api/droplets?clips=1): only READY, shown droplets with a hook cut, newest
-- first. Partial, so it holds just those rows: the feed reads one page of them however rare they are, where the
-- (videoStatus, createdAt, id) index would walk every finished droplet to find them.
-- CreateIndex
CREATE INDEX "answers_clips_idx" ON "answers"("createdAt" DESC, "id" DESC) WHERE "hookVideoUrl" IS NOT NULL AND "videoStatus" = 'READY' AND "hiddenAt" IS NULL;
