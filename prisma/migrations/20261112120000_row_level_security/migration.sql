-- Row-level security on every table, with no policy: a role that is not the table's owner reads and writes no row,
-- whatever its grants. That is Supabase's Data API (PostgREST, as anon or authenticated with the project's
-- publishable key), should it expose this schema: popsmosis never uses it, and a leaked key must reach nothing.
--
-- The API is untouched: Prisma connects as the role that ran these migrations and owns the tables (postgres on
-- Supabase), and a table's owner is not held to its row-level security unless the table FORCEs it, which none does.
-- The counting triggers (20261031120000_droplet_engagement) run as the role that fired them, so as the owner too.
--
-- A new table needs the same line: api/src/rls.integration.test.ts fails while one has none.
ALTER TABLE "users" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "usage_events" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "agents" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "questions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "answers" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "replies" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "attachments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "droplet_likes" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "droplet_comments" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "droplet_plays" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "settings" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "_prisma_migrations" ENABLE ROW LEVEL SECURITY;
