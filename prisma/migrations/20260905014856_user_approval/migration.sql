-- CreateEnum
CREATE TYPE "UserStatus" AS ENUM ('PENDING', 'APPROVED', 'DENIED');

-- AlterTable
ALTER TABLE "users" ADD COLUMN     "reviewedAt" TIMESTAMP(3),
ADD COLUMN     "reviewedById" TEXT,
ADD COLUMN     "status" "UserStatus" NOT NULL DEFAULT 'PENDING';

-- Backfill: everyone who signed in before approvals existed passed the
-- ALLOWED_EMAIL_DOMAINS / ALLOWED_EMAILS gate at the time. Keep them in;
-- only sign-ins after this migration wait for an admin.
UPDATE "users" SET "status" = 'APPROVED', "reviewedAt" = CURRENT_TIMESTAMP;

-- CreateIndex
CREATE INDEX "users_status_createdAt_idx" ON "users"("status", "createdAt");

-- AddForeignKey
ALTER TABLE "users" ADD CONSTRAINT "users_reviewedById_fkey" FOREIGN KEY ("reviewedById") REFERENCES "users"("id") ON DELETE SET NULL ON UPDATE CASCADE;
