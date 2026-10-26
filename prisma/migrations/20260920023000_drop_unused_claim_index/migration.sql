-- The planner never chose this index: the claim scan reads
-- questions_createdAt_id_idx in createdAt order and applies the claim
-- predicate as a filter, because `claimedAt IS NULL OR claimedAt < …` is not
-- a range condition it can seek on. Measured over 40 real queue calls against
-- 55k rows: 0 scans, while being the largest index on the table.
DROP INDEX IF EXISTS "questions_status_claimedAt_createdAt_idx";
