-- Play counts (api/src/routes/plays.ts): how far the plays of each droplet video got, and how often a hook clip was
-- shared and downloaded. Totals per droplet and video, and nothing else: no row stands for a person, a device or a
-- visit, and nothing in it says who watched or when. A new table, so the API that does not know it runs on as before.

-- CreateEnum
CREATE TYPE "DropletVideo" AS ENUM ('SONG', 'LESSON', 'HOOK');

-- CreateTable
CREATE TABLE "droplet_plays" (
    "answerId" TEXT NOT NULL,
    "video" "DropletVideo" NOT NULL,
    "started" INTEGER NOT NULL DEFAULT 0,
    "reached25" INTEGER NOT NULL DEFAULT 0,
    "reached50" INTEGER NOT NULL DEFAULT 0,
    "reached75" INTEGER NOT NULL DEFAULT 0,
    "finished" INTEGER NOT NULL DEFAULT 0,
    "shared" INTEGER NOT NULL DEFAULT 0,
    "downloaded" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "droplet_plays_pkey" PRIMARY KEY ("answerId","video")
);

-- AddForeignKey
ALTER TABLE "droplet_plays" ADD CONSTRAINT "droplet_plays_answerId_fkey" FOREIGN KEY ("answerId") REFERENCES "answers"("id") ON DELETE CASCADE ON UPDATE CASCADE;
