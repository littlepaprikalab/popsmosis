import { FastifyInstance } from 'fastify';
import prisma, { runTransaction } from '../lib/prisma.js';
import { sendError, AppError, isAppError, isUniqueViolation } from '../lib/errors.js';
import { requireUser, requireAgent, requireUserOrAgent } from '../middleware/auth.js';
import { validateString, containsPersonalInfo, parseLimit } from '../lib/validation.js';
import { replyInclude, serializeReply, serializeAnswer, answerInclude, attachmentsInclude, serializeAttachment } from '../lib/serialize.js';
import { notifyRoutine, FIRE_REASON_REPLY } from '../lib/routine.js';
import { parseAttachments, attachmentsCreate, sweepExpiredAttachments, MESSAGE_BODY_LIMIT } from '../lib/attachments.js';
import { followUpContext } from '../lib/droplet.js';

export const REPLY_MAX_LENGTH = 2000;
const REPLIES_PER_USER_PER_HOUR = parseInt(process.env.REPLIES_PER_USER_PER_HOUR || '30', 10);

export default async function replyRoutes(app: FastifyInstance) {
  // GET /api/replies/queue — agent: threads under this agent's answers whose
  // latest reply is from a human (so the agent owes a response), oldest first.
  app.get('/api/replies/queue', { preHandler: [requireAgent] }, async (request) => {
    const agentId = request.agent!.id;
    const { limit } = request.query as Record<string, string | undefined>;
    const take = parseLimit(limit, 10, 50);

    // Answers by this agent whose *newest* reply is from a human, oldest such
    // reply first. One statement, driven by the (answerId, createdAt) index, so
    // a thread with several unanswered human replies in a row cannot crowd out
    // other threads and an answered thread never reappears.
    const rows = await prisma.$queryRaw<{ id: string }[]>`
      SELECT a.id
      FROM answers a
      JOIN LATERAL (
        SELECT r."userId", r."createdAt"
        FROM replies r
        WHERE r."answerId" = a.id
        ORDER BY r."createdAt" DESC, r.id DESC
        LIMIT 1
      ) last ON true
      WHERE a."agentId" = ${agentId} AND last."userId" IS NOT NULL
      ORDER BY last."createdAt" ASC
      LIMIT ${take}
    `;
    if (rows.length === 0) return { data: [] };

    const answerIds = rows.map((r) => r.id);
    const answers = await prisma.answer.findMany({
      where: { id: { in: answerIds } },
      include: { ...answerInclude, question: { include: { user: { select: { id: true, name: true, avatarUrl: true } }, attachments: attachmentsInclude } } },
    });
    const byId = new Map(answers.map((a) => [a.id, a]));

    const data = answerIds
      .map((id) => byId.get(id))
      .filter((a): a is NonNullable<typeof a> => !!a)
      .map((a) => {
        const serialized = serializeAnswer(a);
        const last = a.replies[a.replies.length - 1];
        return {
          question: {
            id: a.question.id, content: a.question.content,
            attachments: a.question.attachments.map(serializeAttachment), createdAt: a.question.createdAt.toISOString(), user: a.question.user,
          },
          answer: {
            id: serialized.id, content: serialized.content, createdAt: serialized.createdAt,
            // The droplet as the studio planned it, without the scenes and camera directions; null for a
            // lesson posted without a bundle.
            context: followUpContext(a.bundle),
          },
          thread: a.replies.map((r) => serializeReply(r)),
          // The reply the agent must respond to: the newest human reply.
          replyTo: last && !last.agentId ? serializeReply(last) : null,
        };
      })
      .filter((t) => t.replyTo !== null);

    await prisma.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
    return { data };
  });

  // POST /api/answers/:id/replies — human (any signed-in user) or agent (author of the answer). A human's may carry
  // `attachments` (lib/attachments.ts) - checked for before the body is read, since that makes it large.
  app.post('/api/answers/:id/replies', { onRequest: [requireUserOrAgent], bodyLimit: MESSAGE_BODY_LIMIT }, async (request, reply) => {
    const { id: answerId } = request.params as { id: string };
    const { content, replyToId, attachments: rawAttachments } = (request.body ?? {}) as {
      content?: unknown; replyToId?: unknown; attachments?: unknown;
    };

    const contentError = validateString(content, 'content', { required: true, minLength: 1, maxLength: REPLY_MAX_LENGTH });
    if (contentError) return sendError(reply, 'VALIDATION_ERROR', contentError);
    const text = (content as string).trim();

    const answer = await prisma.answer.findUnique({ where: { id: answerId }, select: { id: true, agentId: true } });
    if (!answer) return sendError(reply, 'NOT_FOUND', 'Answer not found');

    if (request.agent) {
      // Agent branch: must own the answer and name the human reply being answered.
      if (answer.agentId !== request.agent.id) {
        return sendError(reply, 'FORBIDDEN', 'You can only reply under your own answers');
      }
      if (typeof replyToId !== 'string' || !replyToId) {
        return sendError(reply, 'VALIDATION_ERROR', 'replyToId is required for agent replies');
      }
      if (rawAttachments !== undefined && rawAttachments !== null) {
        return sendError(reply, 'VALIDATION_ERROR', 'attachments are for human replies only');
      }
      try {
        const created = await runTransaction(async (tx) => {
          const target = await tx.reply.findUnique({ where: { id: replyToId }, select: { id: true, answerId: true, userId: true } });
          if (!target || target.answerId !== answerId) throw new AppError('NOT_FOUND', 'replyToId does not belong to this answer');
          if (!target.userId) throw new AppError('VALIDATION_ERROR', 'replyToId must point at a human reply');
          const row = await tx.reply.create({
            data: { answerId, agentId: request.agent!.id, replyToId, content: text },
            include: replyInclude,
          });
          await tx.agent.update({ where: { id: request.agent!.id }, data: { lastSeenAt: new Date() } });
          return row;
        });
        reply.status(201);
        return serializeReply(created);
      } catch (err) {
        if (isUniqueViolation(err)) return sendError(reply, 'CONFLICT', 'You have already replied to that message');
        if (isAppError(err)) return sendError(reply, err.code, err.message);
        throw err;
      }
    }

    // Human branch
    const userId = request.viewer!.id;
    const { attachments, error: attachmentsError } = parseAttachments(rawAttachments);
    if (attachmentsError) return sendError(reply, 'VALIDATION_ERROR', attachmentsError);
    if (containsPersonalInfo(text)) {
      return sendError(reply, 'VALIDATION_ERROR', 'Please do not include email addresses or phone numbers in a reply');
    }
    const since = new Date(Date.now() - 60 * 60 * 1000);
    const recent = await prisma.reply.count({ where: { userId, createdAt: { gte: since } } });
    if (recent >= REPLIES_PER_USER_PER_HOUR) {
      return sendError(reply, 'RATE_LIMITED', `Max ${REPLIES_PER_USER_PER_HOUR} replies per hour`, { retryable: false });
    }
    const created = await prisma.reply.create({
      data: { answerId, userId, content: text, attachments: attachmentsCreate(attachments) },
      include: replyInclude,
    });
    if (attachments.length > 0) {
      sweepExpiredAttachments(prisma).catch((sweepErr) => request.log.warn({ err: sweepErr }, 'attachment retention sweep failed'));
    }
    // A human reply always puts this thread on the reply queue, so wake the
    // teacher. The agent branch above deliberately does not: an agent reply
    // takes the thread back off the queue, and firing there would have the
    // teacher wake itself in a loop.
    notifyRoutine(FIRE_REASON_REPLY, request.log);
    reply.status(201);
    return serializeReply(created, userId);
  });

  // DELETE /api/replies/:id — author only (humans). Cascade removes the agent's reply to it.
  app.delete('/api/replies/:id', { preHandler: [requireUser] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const row = await prisma.reply.findUnique({ where: { id }, select: { id: true, userId: true } });
    if (!row) return sendError(reply, 'NOT_FOUND', 'Reply not found');
    if (row.userId !== request.viewer!.id) return sendError(reply, 'FORBIDDEN', 'You can only delete your own replies');
    await prisma.reply.delete({ where: { id } });
    return reply.status(204).send();
  });
}
