-- The brain an admin picked for a topic on /learn (api/src/lib/brains.ts): claude, muse-wonder or muse-spark.
-- Null, as every topic before it: the teacher's own setting.
-- AlterTable
ALTER TABLE "questions" ADD COLUMN     "brain" TEXT;
