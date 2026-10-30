import { FastifyInstance } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireUserOrAgent } from '../middleware/auth.js';
import { servedAs, contentDisposition, sniffMimeType, pastRetention, purgeAttachment } from '../lib/attachments.js';
import { canReadThread } from '../lib/privacy.js';

export default async function attachmentRoutes(app: FastifyInstance) {
  // GET /api/attachments/:id — a file sent with a question or reply, to whoever may read the thread it is in: the
  // learner who sent it, an admin, and the teacher (lib/privacy.ts). Anyone else is told it is not there. Served as
  // lib/attachments.ts decides, in a sandbox, so a file can only ever be looked at or saved. A file past the retention
  // is gone, whether or not the sweep has come to it yet: its bytes go on the spot. The bytes are read only once the
  // caller may have them, so a refused or expired read never pulls 4 MB out of the database.
  app.get('/api/attachments/:id', { preHandler: [requireUserOrAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const attachment = await prisma.attachment.findUnique({
      where: { id },
      select: {
        name: true,
        purgedAt: true,
        createdAt: true,
        question: { select: { userId: true, claimedById: true, answers: { select: { agentId: true } } } },
        reply: { select: { userId: true, answer: { select: { agentId: true, question: { select: { userId: true } } } } } },
      },
    });
    // The teacher reads the threads it works on: a topic's files while it holds the topic or once it answered it,
    // a follow-up's under its own lesson. A person reads the ones lib/privacy.ts gives them.
    const agentId = request.agent?.id;
    const readable = !!attachment && (agentId
      ? attachment.question
        ? attachment.question.claimedById === agentId || attachment.question.answers.some((a) => a.agentId === agentId)
        : attachment.reply?.answer.agentId === agentId
      : canReadThread(
        request.viewer!,
        attachment.question?.userId ?? attachment.reply?.answer.question.userId ?? null,
        attachment.reply?.userId ?? null,
      ));
    if (!attachment || !readable) return sendError(reply, 'NOT_FOUND', 'Attachment not found');
    const gone = () => sendError(reply, 'GONE', 'This file is past the time attachments are kept');
    if (attachment.purgedAt) return gone();
    if (pastRetention(attachment.createdAt)) {
      await purgeAttachment(prisma, id).catch((err) => request.log.warn({ err }, 'attachment purge on read failed'));
      return gone();
    }
    const stored = await prisma.attachment.findUnique({ where: { id }, select: { data: true } });
    if (!stored?.data) return gone(); // let go between the two reads
    const data = Buffer.from(stored.data);
    const { contentType, disposition } = servedAs(sniffMimeType(data) ?? 'application/octet-stream'); // the bytes decide, whatever the row says
    reply.header('Content-Type', contentType);
    reply.header('Content-Disposition', contentDisposition(disposition, attachment.name));
    reply.header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; sandbox");
    reply.header('Cache-Control', 'private, max-age=86400'); // an id's bytes never change, but they may be let go
    return reply.send(data);
  });
}
