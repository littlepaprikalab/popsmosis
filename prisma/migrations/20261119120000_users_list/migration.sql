-- The admin Users page loads users a page at a time by cursor, admins first and then newest first
-- (api/src/routes/users.ts). This index lets each page start where the last one ended instead of sorting the table.
-- CreateIndex
CREATE INDEX "users_role_createdAt_id_idx" ON "users"("role" DESC, "createdAt" DESC, "id" DESC);

-- Its search matches any part of an email or a name (ILIKE '%…%'). A b-tree cannot serve that; a trigram index can,
-- so a search does not read every user. pg_trgm ships with Postgres and is a trusted extension (Supabase allows it).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- CreateIndex
CREATE INDEX "users_email_idx" ON "users" USING GIN ("email" gin_trgm_ops);

-- CreateIndex
CREATE INDEX "users_name_idx" ON "users" USING GIN ("name" gin_trgm_ops);
