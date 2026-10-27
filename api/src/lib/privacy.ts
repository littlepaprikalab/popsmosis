/**
 * What is the learner's and what is everyone's (docs/PLAN.md, section 10, and the /privacy page).
 *
 * The lesson the teacher makes is public: it is on the Droplets tab for anyone. Everything the learner sent is
 * theirs: the request in their own words, the files with it, and the follow-up thread under the lesson. Only they
 * see it, plus the teacher that answers it and the admins who run the site.
 *
 * When a learner takes a request back, or deletes their account, their side of it goes and the lesson stays. The
 * lesson is the AI's work and already carries nothing about them, so it goes on serving whoever comes after them.
 */
import type { Prisma } from '../generated/prisma/client.js';
import type { TxClient } from './prisma.js';

interface Viewer {
  id: string;
  role: 'USER' | 'ADMIN';
}

/** The questions a signed-in person may read: their own, or, for an admin, everyone's. */
export function readableQuestions(viewer: Viewer): Prisma.QuestionWhereInput {
  return viewer.role === 'ADMIN' ? {} : { userId: viewer.id };
}

/**
 * Whether a signed-in person may read a thread: the request, its files, the lesson's follow-ups and theirs.
 * `ownerId` is who asked for it (null once taken back); `authorId` who wrote the message, for a follow-up.
 */
export function canReadThread(viewer: Viewer, ownerId: string | null, authorId: string | null = null): boolean {
  return viewer.role === 'ADMIN' || (ownerId !== null && ownerId === viewer.id) || (authorId !== null && authorId === viewer.id);
}

/**
 * Take the learner out of requests that have a lesson: their words, their files and the whole follow-up thread
 * (the teacher's replies quote them) go, and the request no longer names anyone. The lesson, its videos and its
 * place on the Droplets tab are left as they are. Run inside the transaction that holds the questions' row locks.
 */
export async function detachQuestions(tx: TxClient, questionIds: string[]): Promise<void> {
  if (questionIds.length === 0) return;
  await tx.attachment.deleteMany({ where: { questionId: { in: questionIds } } });
  // A follow-up's files go with it (attachments cascade on replyId).
  await tx.reply.deleteMany({ where: { answer: { questionId: { in: questionIds } } } });
  await tx.question.updateMany({ where: { id: { in: questionIds } }, data: { userId: null, content: '' } });
}
