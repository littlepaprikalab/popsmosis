-- A droplet whose videos became READY while /admin/studio asks for review waits here, off the public Droplets tab,
-- until an admin approves it. Every droplet listed before this stays listed: the column starts null.
ALTER TABLE "answers" ADD COLUMN "awaitingReviewSince" TIMESTAMP(3);
