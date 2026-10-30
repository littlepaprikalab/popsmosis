-- What the studio's checks found, for an admin reviewing a droplet (GET /api/answers/:id/review): the code checks on the
-- bundle, sent with the lesson, and the art checker's look at each picture, sent with READY. Null for a droplet made
-- before they were sent, and for a lesson posted without a bundle.
ALTER TABLE "answers" ADD COLUMN "checks" JSONB;
ALTER TABLE "answers" ADD COLUMN "artChecks" JSONB;
