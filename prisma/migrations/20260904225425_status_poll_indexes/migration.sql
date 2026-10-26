-- CreateIndex
CREATE INDEX "answers_createdAt_idx" ON "answers"("createdAt" DESC);

-- CreateIndex
CREATE INDEX "replies_createdAt_idx" ON "replies"("createdAt" DESC);
