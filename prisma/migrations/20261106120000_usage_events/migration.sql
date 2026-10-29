-- What each learner has asked for lately, for the daily topic and hourly follow-up limits (api/src/lib/usage.ts).
-- Kept apart from the requests, so taking one back or deleting it no longer hands back the quota it used.

-- CreateEnum
CREATE TYPE "UsageKind" AS ENUM ('TOPIC', 'REPLY');

-- CreateTable
CREATE TABLE "usage_events" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "kind" "UsageKind" NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "usage_events_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "usage_events_userId_kind_createdAt_idx" ON "usage_events"("userId", "kind", "createdAt");

-- CreateIndex
CREATE INDEX "usage_events_kind_createdAt_idx" ON "usage_events"("kind", "createdAt");

-- CreateIndex
CREATE INDEX "usage_events_createdAt_idx" ON "usage_events"("createdAt");

-- AddForeignKey
ALTER TABLE "usage_events" ADD CONSTRAINT "usage_events_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- What the old limits counted at the moment of the deploy carries over, so nobody gets a fresh day or hour from it.
INSERT INTO "usage_events" ("id", "userId", "kind", "createdAt")
SELECT gen_random_uuid()::text, "userId", 'TOPIC', "createdAt"
FROM "questions"
WHERE "userId" IS NOT NULL AND "createdAt" > now() - interval '1 day';

INSERT INTO "usage_events" ("id", "userId", "kind", "createdAt")
SELECT gen_random_uuid()::text, "userId", 'REPLY', "createdAt"
FROM "replies"
WHERE "userId" IS NOT NULL AND "createdAt" > now() - interval '1 hour';
