-- CreateTable
CREATE TABLE "attachments" (
    "id" TEXT NOT NULL,
    "questionId" TEXT,
    "replyId" TEXT,
    "name" TEXT NOT NULL,
    "mimeType" TEXT NOT NULL,
    "size" INTEGER NOT NULL,
    "data" BYTEA,
    "purgedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "attachments_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "attachments_questionId_idx" ON "attachments"("questionId");

-- CreateIndex
CREATE INDEX "attachments_replyId_idx" ON "attachments"("replyId");

-- CreateIndex
CREATE INDEX "attachments_createdAt_idx" ON "attachments"("createdAt");

-- AddForeignKey
ALTER TABLE "attachments" ADD CONSTRAINT "attachments_questionId_fkey" FOREIGN KEY ("questionId") REFERENCES "questions"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "attachments" ADD CONSTRAINT "attachments_replyId_fkey" FOREIGN KEY ("replyId") REFERENCES "replies"("id") ON DELETE CASCADE ON UPDATE CASCADE;
