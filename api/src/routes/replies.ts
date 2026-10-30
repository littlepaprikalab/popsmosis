import { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import prisma, { runTransaction } from '../lib/prisma.js';
import { sendError, AppError, isAppError, isUniqueViolation } from '../lib/errors.js';
import { requireUser, requireAgent, requireUserOrAgent, accountGone } from '../middleware/auth.js';
import { validateString, containsPersonalInfo, parseLimit, secretError } from '../lib/validation.js';
import { replyInclude, serializeReply, serializeReplyForAgent, serializeAnswer, answerInclude, attachmentsInclude, serializeAttachment } from '../lib/serialize.js';
import { notifyRoutine, FIRE_REASON_REPLY } from '../lib/routine.js';
import { parseAttachments, attachmentsCreate, MESSAGE_BODY_LIMIT } from '../lib/attachments.js';
import { followUpContext } from '../lib/droplet.js';
import { takeReply, giveBack, replyAllowed, filesAllowed, filesWritten } from '../lib/usage.js';

export const REPLY_MAX_LENGTH = 2000;

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
      omit: { factcheck: true }, // only the bundle feeds the follow-up context
      // Never who asked: the teacher is given what it needs to teach (lib/serialize.ts, serializeReplyForAgent).
      include: { ...answerInclude, question: { include: { attachments: attachmentsInclude } } },
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
            attachments: a.question.attachments.map(serializeAttachment), createdAt: a.question.createdAt.toISOString(),
          },
          answer: {
            id: serialized.id, content: serialized.content, createdAt: serialized.createdAt,
            // The droplet as the studio planned it, without the scenes and camera directions; null for a
            // lesson posted without a bundle.
            context: followUpContext(a.bundle),
          },
          thread: a.replies.map(serializeReplyForAgent),
          // The reply the agent must respond to: the newest human reply.
          replyTo: last && !last.agentId ? serializeReplyForAgent(last) : null,
        };
      })
      .filter((t) => t.replyTo !== null);

    await prisma.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
    return { data };
  });

  // POST /api/answers/:id/replies — human (the learner who asked for the lesson) or agent (author of the answer). The
  // thread is the learner's own (lib/privacy.ts). A human's may carry `attachments` (lib/attachments.ts) - checked for
  // before the body is read, since that makes it large.
  // A learner already at the hour's limit is told so before the body (up to MESSAGE_BODY_LIMIT) is read and parsed.
  const replyOpen = async (request: FastifyRequest, reply: FastifyReply) => {
    if (!request.viewer) return; // an agent, or requireUserOrAgent replied
    const refusal = (await replyAllowed(request.viewer.id))
      ?? (await filesAllowed(request.viewer.id, Number(request.headers['content-length']) || 0));
    if (refusal) return sendError(reply, refusal.code, refusal.message, refusal.extra);
  };
  app.post('/api/answers/:id/replies', { onRequest: [requireUserOrAgent, replyOpen], bodyLimit: MESSAGE_BODY_LIMIT }, async (request, reply) => {
    const { id: answerId } = request.params as { id: string };
    const { content, replyToId, attachments: rawAttachments } = (request.body ?? {}) as {
      content?: unknown; replyToId?: unknown; attachments?: unknown;
    };

    const contentError = validateString(content, 'content', { required: true, minLength: 1, maxLength: REPLY_MAX_LENGTH });
    if (contentError) return sendError(reply, 'VALIDATION_ERROR', contentError);
    const text = (content as string).trim();

    const answer = await prisma.answer.findUnique({
      where: { id: answerId },
      select: { id: true, agentId: true, question: { select: { userId: true } } },
    });
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
      // The thread is where a learner who talked the teacher into pasting a key would read it.
      const leak = secretError('content', text);
      if (leak) {
        request.log.warn({ agentId: request.agent.id, answerId }, 'agent text refused: it looks like a secret');
        return sendError(reply, 'VALIDATION_ERROR', leak);
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
        return serializeReplyForAgent(created);
      } catch (err) {
        if (isUniqueViolation(err)) return sendError(reply, 'CONFLICT', 'You have already replied to that message');
        if (isAppError(err)) return sendError(reply, err.code, err.message);
        throw err;
      }
    }

    // Human branch: only the learner the lesson was made for. An admin reads every thread but does not post in one.
    const userId = request.viewer!.id;
    if (answer.question.userId !== userId) {
      return sendError(reply, 'FORBIDDEN', 'You can only ask follow-ups under your own topics');
    }
    const { attachments, error: attachmentsError } = parseAttachments(rawAttachments);
    if (attachmentsError) return sendError(reply, 'VALIDATION_ERROR', attachmentsError);
    if (containsPersonalInfo(text)) {
      return sendError(reply, 'VALIDATION_ERROR', 'Please do not include email addresses or phone numbers in a reply');
    }
    // The learner's hour and bytes of files (lib/usage.ts), taken before the follow-up is written and kept after: one
    // deleted later does not hand its place back. Its own short transaction, so the files' write below holds no lock or pooled connection.
    let usageId: string;
    try {
      usageId = await takeReply(userId, attachments.reduce((sum, a) => sum + a.size, 0));
    } catch (err) {
      if (isAppError(err)) return sendError(reply, err.code, err.message, err.extra);
      if (await accountGone(err, userId)) return sendError(reply, 'UNAUTHORIZED', 'User not found');
      throw err;
    }
    let created;
    try {
      created = await prisma.reply.create({
        data: { answerId, userId, content: text, attachments: attachmentsCreate(attachments) },
        include: replyInclude,
      });
    } catch (err) {
      await giveBack(usageId).catch(() => {}); // never written, so it used nothing
      if (await accountGone(err, userId)) return sendError(reply, 'UNAUTHORIZED', 'User not found');
      throw err;
    }
    if (attachments.length > 0) {
      await filesWritten(usageId).catch((err) => request.log.warn({ err }, 'usage files not marked written'));
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
