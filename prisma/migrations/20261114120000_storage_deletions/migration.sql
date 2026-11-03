-- A learner deleting a topic with everything in it (DELETE /api/questions/:id/everything): the droplet's files in
-- R2 still to be cleared, one row per deleted droplet (api/src/lib/storage-delete.ts). Only the answer's id, and no
-- foreign key, since the answer is gone. A new table, so an API that does not know it runs on as before.

-- CreateTable
CREATE TABLE "storage_deletions" (
    "answerId" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "clearedAt" TIMESTAMP(3),
    "tries" INTEGER NOT NULL DEFAULT 0,
    "lastError" TEXT,

    CONSTRAINT "storage_deletions_pkey" PRIMARY KEY ("answerId")
);

-- As on every table (20261112120000_row_level_security): no role but its owner reads or writes a row.
ALTER TABLE "storage_deletions" ENABLE ROW LEVEL SECURITY;
