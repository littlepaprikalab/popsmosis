-- The hook cut (studio/steps/timeline.py hook_cut): an upright clip of the song's pre-chorus and first chorus,
-- the part students share. Null for a droplet made before it, or a song with no chorus.
-- AlterTable
ALTER TABLE "answers" ADD COLUMN "hookVideoUrl" TEXT,
ADD COLUMN "hookPosterUrl" TEXT;
