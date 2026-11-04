-- The song style a learner asks for on /learn, in its own field rather than a "Style:" line in the topic. Null: none,
-- and the studio's rotation picks one. Erased with the topic's words when the request is taken back.
ALTER TABLE "questions" ADD COLUMN "songStyle" TEXT;
