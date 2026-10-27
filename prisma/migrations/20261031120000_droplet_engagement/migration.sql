-- AlterTable
ALTER TABLE "answers" ADD COLUMN     "commentCount" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN     "likeCount" INTEGER NOT NULL DEFAULT 0;

-- CreateTable
CREATE TABLE "droplet_likes" (
    "answerId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "droplet_likes_pkey" PRIMARY KEY ("answerId","userId")
);

-- CreateTable
CREATE TABLE "droplet_comments" (
    "id" TEXT NOT NULL,
    "answerId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "idempotencyKey" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "droplet_comments_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "droplet_likes_userId_idx" ON "droplet_likes"("userId");

-- CreateIndex
CREATE INDEX "droplet_comments_answerId_createdAt_id_idx" ON "droplet_comments"("answerId", "createdAt" DESC, "id" DESC);

-- CreateIndex
CREATE INDEX "droplet_comments_userId_createdAt_idx" ON "droplet_comments"("userId", "createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "droplet_comments_userId_idempotencyKey_key" ON "droplet_comments"("userId", "idempotencyKey");

-- AddForeignKey
ALTER TABLE "droplet_likes" ADD CONSTRAINT "droplet_likes_answerId_fkey" FOREIGN KEY ("answerId") REFERENCES "answers"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "droplet_likes" ADD CONSTRAINT "droplet_likes_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "droplet_comments" ADD CONSTRAINT "droplet_comments_answerId_fkey" FOREIGN KEY ("answerId") REFERENCES "answers"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "droplet_comments" ADD CONSTRAINT "droplet_comments_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;


-- The two counts on answers are kept here, not by the API. Every way a like or a comment
-- comes or goes passes through these: the routes, the cascade when a user or a droplet is
-- deleted, and SQL run by hand. One UPDATE per statement, grouped by droplet, so a bulk
-- delete (a user with a thousand likes) touches each droplet's row once.
--
-- Transition tables cannot serve a trigger with more than one event, so each table has an
-- INSERT trigger and a DELETE trigger over one function; TG_OP gives the sign. Neither table
-- moves a row between droplets, so no UPDATE trigger is needed.
--
-- To check them by hand (both should return no rows):
--   SELECT id FROM answers a WHERE "likeCount"    <> (SELECT count(*) FROM droplet_likes    WHERE "answerId" = a.id);
--   SELECT id FROM answers a WHERE "commentCount" <> (SELECT count(*) FROM droplet_comments WHERE "answerId" = a.id);

CREATE FUNCTION "droplet_likes_count"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE "answers" AS a
  SET "likeCount" = a."likeCount" + CASE WHEN TG_OP = 'INSERT' THEN c.n ELSE -c.n END
  FROM (SELECT "answerId", count(*)::int AS n FROM changed GROUP BY "answerId") AS c
  WHERE a.id = c."answerId";
  RETURN NULL;
END
$$;

CREATE TRIGGER "droplet_likes_counted_insert" AFTER INSERT ON "droplet_likes"
  REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION "droplet_likes_count"();
CREATE TRIGGER "droplet_likes_counted_delete" AFTER DELETE ON "droplet_likes"
  REFERENCING OLD TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION "droplet_likes_count"();

CREATE FUNCTION "droplet_comments_count"() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE "answers" AS a
  SET "commentCount" = a."commentCount" + CASE WHEN TG_OP = 'INSERT' THEN c.n ELSE -c.n END
  FROM (SELECT "answerId", count(*)::int AS n FROM changed GROUP BY "answerId") AS c
  WHERE a.id = c."answerId";
  RETURN NULL;
END
$$;

CREATE TRIGGER "droplet_comments_counted_insert" AFTER INSERT ON "droplet_comments"
  REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION "droplet_comments_count"();
CREATE TRIGGER "droplet_comments_counted_delete" AFTER DELETE ON "droplet_comments"
  REFERENCING OLD TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION "droplet_comments_count"();
