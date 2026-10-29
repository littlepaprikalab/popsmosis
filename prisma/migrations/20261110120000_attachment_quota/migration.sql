-- The bytes of files each request carried, for the daily byte quota on attachments (api/src/lib/usage.ts). Rows from
-- before this start at 0, which only gives the next 24 hours a fresh count; what is held is counted from the files.
ALTER TABLE "usage_events" ADD COLUMN "bytes" INTEGER NOT NULL DEFAULT 0;
-- True from the take until its message's files are written, so the bytes held count a message on its way.
ALTER TABLE "usage_events" ADD COLUMN "filesPending" BOOLEAN NOT NULL DEFAULT false;

-- The retention sweep reads the files still held, oldest first, without walking every file it already let go.
CREATE INDEX "attachments_purgedAt_createdAt_idx" ON "attachments"("purgedAt", "createdAt");
