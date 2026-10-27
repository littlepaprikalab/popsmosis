-- Anyone with a Google account can use the site: there is no approval queue,
-- so the access status and its review columns go, and every account that was
-- waiting or denied is in. Personal access tokens go with it.

-- DropForeignKey
ALTER TABLE "user_tokens" DROP CONSTRAINT "user_tokens_userId_fkey";

-- DropForeignKey
ALTER TABLE "users" DROP CONSTRAINT "users_reviewedById_fkey";

-- DropIndex
DROP INDEX "users_status_createdAt_idx";

-- AlterTable
ALTER TABLE "users" DROP COLUMN "reviewedAt",
DROP COLUMN "reviewedById",
DROP COLUMN "status";

-- DropTable
DROP TABLE "user_tokens";

-- DropEnum
DROP TYPE "UserStatus";
