-- The admin Users page loads users a page at a time by cursor, admins first and then newest first
-- (api/src/routes/users.ts). This index lets each page start where the last one ended instead of sorting the table.
-- Every statement here is IF NOT EXISTS: Prisma does not run a migration as one transaction, so one that stops part
-- way leaves what it made, and the retry (after `prisma migrate resolve --rolled-back`) must step over it.
-- CreateIndex
CREATE INDEX IF NOT EXISTS "users_role_createdAt_id_idx" ON "users"("role" DESC, "createdAt" DESC, "id" DESC);

-- Its search matches any part of an email or a name (ILIKE '%…%'). A b-tree cannot serve that; a trigram index can,
-- so a search does not read every user. pg_trgm ships with Postgres and is a trusted extension, so the database's
-- owner can enable it. Supabase keeps extensions in a schema of their own, "extensions", which a migration's
-- search_path may not hold: the operator class is named with the schema pg_trgm is in, wherever that is, so this
-- works on a database that already has it there. One DO block, so the extension and the two indexes go together.
DO $$
DECLARE
  trgm TEXT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
      CREATE EXTENSION pg_trgm WITH SCHEMA extensions;
    ELSE
      CREATE EXTENSION pg_trgm;
    END IF;
  END IF;
  SELECT n.nspname INTO trgm FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm';

  -- CreateIndex
  EXECUTE format('CREATE INDEX IF NOT EXISTS "users_email_idx" ON "users" USING GIN ("email" %I.gin_trgm_ops)', trgm);
  -- CreateIndex
  EXECUTE format('CREATE INDEX IF NOT EXISTS "users_name_idx" ON "users" USING GIN ("name" %I.gin_trgm_ops)', trgm);
END $$;
