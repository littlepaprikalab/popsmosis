import { FastifyInstance } from 'fastify';
import prisma, { runTransaction } from '../lib/prisma.js';
import { Prisma } from '../generated/prisma/client.js';
import { sendError, AppError, isAppError, isUniqueViolation } from '../lib/errors.js';
import { requireUser, requireAgent, accountGone } from '../middleware/auth.js';
import { validateString, containsPersonalInfo, parseLimit, secretError } from '../lib/validation.js';
import { decodeCreatedAtIdCursor, encodeCreatedAtIdCursor } from '../lib/cursor.js';
import { questionInclude, serializeQuestion, serializeQuestionForAgent } from '../lib/serialize.js';
import { notifyRoutine, FIRE_REASON_QUESTION } from '../lib/routine.js';
import { claimMinutes } from '../lib/claim.js';
import { parseAttachments, attachmentsCreate, sweepExpiredAttachments, MESSAGE_BODY_LIMIT } from '../lib/attachments.js';
import { dropletJsonError, FAILED_REASON_MAX_LENGTH } from '../lib/droplet.js';
import { breakdownError } from '../lib/breakdown.js';
import { progressError, storedProgress } from '../lib/progress.js';
import { bundleTags } from '../lib/tags.js';
import { readableQuestions, detachQuestions } from '../lib/privacy.js';
import { parseBrain } from '../lib/brains.js';
import { studioForAgent } from '../lib/studio-settings.js';
import { takeTopic, pruneUsage } from '../lib/usage.js';

export const QUESTION_MIN_LENGTH = 3; // a topic can be one short word: "DNA"
export const QUESTION_MAX_LENGTH = 2000;
export const ANSWER_MAX_LENGTH = 5000;

export default async function questionRoutes(app: FastifyInstance) {
  // GET /api/questions/status — cheap "anything new?" probe for polling.
  // Considers questions, answers, and replies so a page refreshes for any of them.
  // Scoped the same way GET /api/questions is (to the viewer's own questions,
  // unless an admin asks for everyone's), so the banner does not fire for a
  // stranger's activity that a refresh would not show.
  app.get('/api/questions/status', { preHandler: [requireUser] }, async (request, reply) => {
    const { mine, since } = request.query as Record<string, string | undefined>;
    const viewerId = request.viewer!.id;
    const scoped = mine === 'true' || request.viewer!.role !== 'ADMIN';

    // ?since=<ISO> — the caller's own watermark. A poller only needs to know
    // whether anything newer than that exists, and saying so costs an index
    // range over the rows written since its last tick. Without it every
    // subquery aggregates the whole history: cheap unscoped (MAX off the
    // descending createdAt indexes) but a full scan of answers and replies
    // once ?mine=true forces the join. The page omits it only on the initial
    // load, which reads agentLastSeenAt and discards latestAt.
    let after: Date | null = null;
    if (since !== undefined) {
      after = new Date(since);
      if (Number.isNaN(after.getTime())) return sendError(reply, 'VALIDATION_ERROR', 'since must be an ISO timestamp');
    }
    const newerQ = after ? Prisma.sql`AND q."createdAt" > ${after}` : Prisma.empty;
    const newerA = after ? Prisma.sql`AND a."createdAt" > ${after}` : Prisma.empty;
    const newerR = after ? Prisma.sql`AND r."createdAt" > ${after}` : Prisma.empty;

    // Every row a scoped viewer can see hangs off one of their questions, so
    // each subquery walks back to questions."userId". Both hops are indexed:
    // answers by questionId, replies by answerId.
    const latest = scoped
      ? Prisma.sql`GREATEST(
          (SELECT MAX(q."createdAt") FROM questions q
             WHERE q."userId" = ${viewerId} ${newerQ}),
          (SELECT MAX(a."createdAt") FROM answers a
             JOIN questions q ON q.id = a."questionId"
             WHERE q."userId" = ${viewerId} ${newerA}),
          (SELECT MAX(r."createdAt") FROM replies r
             JOIN answers a ON a.id = r."answerId"
             JOIN questions q ON q.id = a."questionId"
             WHERE q."userId" = ${viewerId} ${newerR})
        )`
      : Prisma.sql`GREATEST(
          (SELECT MAX(q."createdAt") FROM questions q WHERE TRUE ${newerQ}),
          (SELECT MAX(a."createdAt") FROM answers a WHERE TRUE ${newerA}),
          (SELECT MAX(r."createdAt") FROM replies r WHERE TRUE ${newerR})
        )`;
    const [row] = await prisma.$queryRaw<{ latestAt: Date | null; agentLastSeenAt: Date | null }[]>`
      SELECT
        ${latest} AS "latestAt",
        (SELECT MAX("lastSeenAt") FROM agents WHERE enabled) AS "agentLastSeenAt"
    `;
    return {
      // Null when nothing is newer than ?since, which is the answer the poller
      // wants: no banner.
      latestAt: row?.latestAt?.toISOString() ?? null,
      // When the teacher last called the API; the page shows it so a stalled
      // runner is visible to users, not just on the admin page. Never scoped
      // or bounded: a stalled runner is everyone's problem.
      agentLastSeenAt: row?.agentLastSeenAt?.toISOString() ?? null,
    };
  });

  // GET /api/questions/queue — agent: PENDING questions, oldest first, claimed
  // for this caller so an overlapping run is handed different ones.
  app.get('/api/questions/queue', { preHandler: [requireAgent] }, async (request) => {
    const { limit } = request.query as Record<string, string | undefined>;
    const take = parseLimit(limit, 5, 20);
    const agentId = request.agent!.id;

    // Select and claim in one statement. FOR UPDATE SKIP LOCKED is the whole
    // trick: a concurrent caller steps over rows this one has locked instead of
    // blocking on them or reading them anyway, so the two get disjoint sets.
    // A claim past its lease is treated as absent — see lib/claim.ts.
    const claimed = await prisma.$queryRaw<{ id: string }[]>`
      UPDATE questions
      SET "claimedAt" = now(), "claimedById" = ${agentId}
      WHERE id IN (
        SELECT id FROM questions
        WHERE status = 'PENDING'
          AND ("claimedAt" IS NULL OR "claimedAt" < now() - make_interval(mins => ${claimMinutes()}))
        ORDER BY "createdAt" ASC, id ASC
        LIMIT ${take}
        FOR UPDATE SKIP LOCKED
      )
      RETURNING id
    `;

    await prisma.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
    if (claimed.length === 0) return { data: [] };

    const questions = await prisma.question.findMany({
      where: { id: { in: claimed.map((r) => r.id) } },
      include: questionInclude,
      orderBy: [{ createdAt: 'asc' }, { id: 'asc' }],
    });
    return { data: questions.map((q) => serializeQuestionForAgent(q)) };
  });

  // POST /api/questions — signed-in user asks a question. `attachments` are
  // files sent with it (lib/attachments.ts), which is why the body may be
  // large — and why the caller is checked before it is read. An admin may
  // pick the `brain` that makes it (lib/brains.ts); anyone else's topic is
  // made by the teacher's own.
  app.post('/api/questions', { onRequest: [requireUser], bodyLimit: MESSAGE_BODY_LIMIT }, async (request, reply) => {
    const { content, attachments: rawAttachments, brain: rawBrain } = (request.body ?? {}) as {
      content?: unknown; attachments?: unknown; brain?: unknown;
    };
    const err = validateString(content, 'content', { required: true, minLength: QUESTION_MIN_LENGTH, maxLength: QUESTION_MAX_LENGTH });
    if (err) return sendError(reply, 'VALIDATION_ERROR', err);
    const { brain, error: brainError, forbidden } = parseBrain(rawBrain, request.viewer!.role === 'ADMIN');
    if (brainError) return sendError(reply, forbidden ? 'FORBIDDEN' : 'VALIDATION_ERROR', brainError);
    const { attachments, error: attachmentsError } = parseAttachments(rawAttachments);
    if (attachmentsError) return sendError(reply, 'VALIDATION_ERROR', attachmentsError);
    const text = (content as string).trim();
    if (containsPersonalInfo(text)) {
      return sendError(reply, 'VALIDATION_ERROR', 'Please do not include email addresses or phone numbers in a question');
    }

    // The learner's 24 hours and the site's (lib/usage.ts), taken in the same transaction as the topic: a topic
    // taken back or deleted later does not hand its place back.
    let question;
    try {
      question = await runTransaction(async (tx) => {
        await takeTopic(tx, request.viewer!.id);
        return tx.question.create({
          data: { userId: request.viewer!.id, content: text, brain, attachments: attachmentsCreate(attachments) },
          include: questionInclude,
        });
      });
    } catch (err) {
      if (isAppError(err)) return sendError(reply, err.code, err.message, err.extra);
      if (await accountGone(err, request.viewer!.id)) return sendError(reply, 'UNAUTHORIZED', 'User not found');
      throw err;
    }
    // Wake the teacher now rather than at its next scheduled firing. Not
    // awaited: the asker's 201 does not wait on Anthropic's API.
    notifyRoutine(FIRE_REASON_QUESTION, request.log);
    pruneUsage(prisma).catch((pruneErr) => request.log.warn({ err: pruneErr }, 'usage prune failed'));
    if (attachments.length > 0) {
      sweepExpiredAttachments(prisma).catch((sweepErr) => request.log.warn({ err: sweepErr }, 'attachment retention sweep failed'));
    }
    reply.status(201);
    return serializeQuestion(question, request.viewer!.id);
  });

  // GET /api/questions — the viewer's own questions, newest first, with answer + replies. An admin gets everyone's
  // (they keep the site running), or their own with ?mine=true. Other people's lessons are on the public Droplets
  // tab; their requests, files and follow-ups are theirs alone (lib/privacy.ts).
  app.get('/api/questions', { preHandler: [requireUser] }, async (request) => {
    const { limit, cursor, mine, status, ids } = request.query as Record<string, string | undefined>;
    const take = parseLimit(limit, 20, 50);
    const viewer = request.viewer!;

    // ?ids=a,b,c — the page re-fetches just the cards that are waiting on the
    // teacher, in one request. Capped so it cannot become a full-table pull.
    if (ids !== undefined) {
      const list = [...new Set(ids.split(',').map((s) => s.trim()).filter(Boolean))].slice(0, 50);
      if (list.length === 0) return { data: [], nextCursor: null };
      const rows = await prisma.question.findMany({ where: { id: { in: list }, ...readableQuestions(viewer) }, include: questionInclude });
      const byId = new Map(rows.map((r) => [r.id, r]));
      return {
        data: list.map((id) => byId.get(id)).filter((r): r is NonNullable<typeof r> => !!r).map((r) => serializeQuestion(r, viewer.id)),
        nextCursor: null,
      };
    }

    const where: Record<string, unknown> = mine === 'true' ? { userId: viewer.id } : { ...readableQuestions(viewer) };
    if (status === 'PENDING' || status === 'ANSWERED' || status === 'FAILED') where.status = status;
    const cursorFilter = decodeCreatedAtIdCursor(cursor);
    if (cursorFilter) where.AND = [cursorFilter];

    const questions = await prisma.question.findMany({
      where,
      include: questionInclude,
      orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
      take,
    });

    return {
      data: questions.map((q) => serializeQuestion(q, viewer.id)),
      nextCursor: encodeCreatedAtIdCursor(questions, take),
    };
  });

  // GET /api/questions/:id — the viewer's own, or anyone's for an admin; anyone else's is not found.
  app.get('/api/questions/:id', { preHandler: [requireUser] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const question = await prisma.question.findFirst({ where: { id, ...readableQuestions(request.viewer!) }, include: questionInclude });
    if (!question) return sendError(reply, 'NOT_FOUND', 'Question not found');
    return serializeQuestion(question, request.viewer!.id);
  });

  // DELETE /api/questions/:id — owner. One with no lesson yet (PENDING or FAILED) goes entirely. One with a lesson
  // is taken back: the learner's words, files and follow-ups go and the request no longer names them, while the
  // lesson stays for everyone else (lib/privacy.ts).
  app.delete('/api/questions/:id', { preHandler: [requireUser] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const userId = request.viewer!.id;

    try {
      await runTransaction(async (tx) => {
        // Locked as the answer route locks it, so a lesson cannot land between the look and the delete.
        const rows = await tx.$queryRaw<{ userId: string | null; status: string }[]>`
          SELECT "userId", status FROM questions WHERE id = ${id} FOR UPDATE
        `;
        const question = rows[0];
        if (!question) throw new AppError('NOT_FOUND', 'Question not found');
        if (question.userId !== userId) throw new AppError('FORBIDDEN', 'You can only delete your own questions');
        if (question.status === 'ANSWERED') await detachQuestions(tx, [id]);
        else await tx.question.delete({ where: { id } });
      });
    } catch (err) {
      if (isAppError(err)) return sendError(reply, err.code, err.message);
      throw err;
    }
    return reply.status(204).send();
  });

  // POST /api/questions/:id/claim — agent: take, or renew, the lease on one
  // question, and get it back with its attachments. The studio calls it before
  // each step that writes the lesson, so a long fact-check or a retried step
  // keeps the question out of an overlapping run's queue. The lease is the
  // queue's own (lib/claim.ts): the same agent may always renew it, another
  // agent only once it has lapsed.
  app.post('/api/questions/:id/claim', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const claimed = await prisma.$queryRaw<{ id: string }[]>`
      UPDATE questions
      SET "claimedAt" = now(), "claimedById" = ${agentId}
      WHERE id = ${id} AND status = 'PENDING'
        AND ("claimedAt" IS NULL OR "claimedById" = ${agentId}
             OR "claimedAt" < now() - make_interval(mins => ${claimMinutes()}))
      RETURNING id
    `;
    await prisma.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
    const question = await prisma.question.findUnique({ where: { id }, include: questionInclude });
    if (!question) return sendError(reply, 'NOT_FOUND', 'Question not found');
    if (claimed.length === 0) {
      if (question.status === 'ANSWERED') return sendError(reply, 'CONFLICT', 'Question already has an answer');
      if (question.status === 'FAILED') return sendError(reply, 'CONFLICT', 'Question was marked failed');
      return sendError(reply, 'CONFLICT', 'Another agent holds the claim on this question');
    }
    // With the studio's settings (lib/studio-settings.ts), so an admin's change reaches the run.
    return { ...serializeQuestionForAgent(question), studio: await studioForAgent() };
  });

  // PUT /api/questions/:id/progress — agent: where the studio is while it writes the lesson (lib/progress.ts):
  // { running: [{ step, seconds }], failed?, breakdown?, videos? }, sent at the start and the end of each step.
  // The topic's card shows it as the bill of materials filling in. Only the agent holding the claim, while the
  // topic has no lesson; it is not the lease, which only the claim renews.
  app.put('/api/questions/:id/progress', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const problem = progressError(request.body, 'lesson');
    if (problem) return sendError(reply, 'VALIDATION_ERROR', problem);
    const report = request.body as Record<string, unknown>;
    const stored = { ...storedProgress(report), ...(report.breakdown !== undefined && { breakdown: report.breakdown }) };
    // One write, and none to the agent's lastSeenAt: the claim before each step keeps that, and every run would
    // otherwise queue on the agent's one row.
    const updated = await prisma.$queryRaw<{ id: string }[]>`
      UPDATE questions SET progress = ${JSON.stringify(stored)}::jsonb
      WHERE id = ${id} AND status = 'PENDING' AND "claimedById" = ${agentId}
      RETURNING id
    `;
    if (updated.length === 0) {
      const question = await prisma.question.findUnique({ where: { id }, select: { status: true } });
      if (!question) return sendError(reply, 'NOT_FOUND', 'Question not found');
      if (question.status === 'ANSWERED') return sendError(reply, 'CONFLICT', 'Question already has an answer');
      if (question.status === 'FAILED') return sendError(reply, 'CONFLICT', 'Question was marked failed');
      return sendError(reply, 'CONFLICT', 'Only the agent holding the claim on this question can report its progress');
    }
    return reply.status(204).send();
  });

  // POST /api/questions/:id/fail — agent: give up on a question before it has a
  // lesson, with one line on why. It leaves the queue, so a topic that cannot
  // pass its fact-check is not paid for again on every sweep, and the card shows
  // the reason. Only the agent that last claimed it may, so a run cannot fail a
  // question another run has since picked up.
  app.post('/api/questions/:id/fail', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const { reason } = (request.body ?? {}) as { reason?: unknown };
    const reasonError = validateString(reason, 'reason', { required: true, minLength: 1, maxLength: FAILED_REASON_MAX_LENGTH });
    if (reasonError) return sendError(reply, 'VALIDATION_ERROR', reasonError);
    // The reason is shown on the learner's card.
    const leak = secretError('reason', reason);
    if (leak) {
      request.log.warn({ agentId, questionId: id }, 'agent text refused: it looks like a secret');
      return sendError(reply, 'VALIDATION_ERROR', leak);
    }

    try {
      await runTransaction(async (tx) => {
        const rows = await tx.$queryRaw<{ status: string; claimedById: string | null }[]>`
          SELECT status, "claimedById" FROM questions WHERE id = ${id} FOR UPDATE
        `;
        const question = rows[0];
        if (!question) throw new AppError('NOT_FOUND', 'Question not found');
        if (question.status === 'ANSWERED') throw new AppError('CONFLICT', 'Question already has an answer');
        if (question.status === 'FAILED') throw new AppError('CONFLICT', 'Question was already marked failed');
        if (question.claimedById !== agentId) throw new AppError('CONFLICT', 'Only the agent that claimed this question can mark it failed');
        await tx.question.update({ where: { id }, data: { status: 'FAILED', failedReason: (reason as string).trim(), progress: Prisma.DbNull } });
        await tx.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
      });
    } catch (err) {
      if (isAppError(err)) return sendError(reply, err.code, err.message);
      throw err;
    }
    const question = await prisma.question.findUniqueOrThrow({ where: { id }, include: questionInclude });
    return serializeQuestionForAgent(question);
  });

  // POST /api/questions/:id/answer — agent answers; exactly one answer per question.
  // The studio sends the bundle and fact-check the lesson came from with it
  // (lib/droplet.ts), and the breakdown of its steps so far (lib/breakdown.ts); a lesson
  // written by hand may leave all three out.
  app.post('/api/questions/:id/answer', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const { content, model, bundle, factcheck, breakdown } = (request.body ?? {}) as {
      content?: unknown; model?: unknown; bundle?: unknown; factcheck?: unknown; breakdown?: unknown;
    };

    const contentError = validateString(content, 'content', { required: true, minLength: 1, maxLength: ANSWER_MAX_LENGTH });
    if (contentError) return sendError(reply, 'VALIDATION_ERROR', contentError);
    if (model !== undefined) {
      const modelError = validateString(model, 'model', { maxLength: 100 });
      if (modelError) return sendError(reply, 'VALIDATION_ERROR', modelError);
    }
    const jsonError = dropletJsonError(bundle, factcheck) ?? breakdownError(breakdown);
    if (jsonError) return sendError(reply, 'VALIDATION_ERROR', jsonError);
    // The lesson, its lyrics and its tags go public: never a key the agent was talked into pasting.
    const leak = secretError('the lesson', content, bundle);
    if (leak) {
      request.log.warn({ agentId, questionId: id }, 'agent text refused: it looks like a secret');
      return sendError(reply, 'VALIDATION_ERROR', leak);
    }

    try {
      const answer = await runTransaction(async (tx) => {
        // Row lock so two concurrent answers to the same question serialize here.
        const rows = await tx.$queryRaw<{ id: string; status: string; videos: unknown; claimedById: string | null }[]>`
          SELECT id, status, progress->'videos' AS videos, "claimedById" FROM questions WHERE id = ${id} FOR UPDATE
        `;
        const question = rows[0];
        if (!question) throw new AppError('NOT_FOUND', 'Question not found');
        if (question.status === 'ANSWERED') {
          // Say whose: a caller whose 201 was lost in transit retries, and must be able to
          // tell its own lesson (and learn its id) from someone else's.
          const existing = await tx.answer.findFirst({ where: { questionId: id }, select: { id: true, agentId: true } });
          throw new AppError('CONFLICT', 'Question already has an answer',
            existing?.agentId === agentId ? { answerId: existing.id } : undefined);
        }
        if (question.status === 'FAILED') throw new AppError('CONFLICT', 'Question was marked failed');
        // Only the run holding the topic answers it: the queue and the claim lease it, and the studio renews the
        // lease before every step. An agent cannot post over a topic another run is making, nor answer one it never took.
        if (question.claimedById !== agentId) {
          throw new AppError('CONFLICT', 'Claim this question before answering it (POST /api/questions/:id/claim)');
        }

        const created = await tx.answer.create({
          data: {
            questionId: id, agentId, content: (content as string).trim(), model: typeof model === 'string' ? model : null,
            // Each video's hashtags come with the bundle, and are kept on their own to be searched (lib/tags.ts).
            ...(bundle !== undefined && {
              bundle: bundle as Prisma.InputJsonObject, factcheck: factcheck as Prisma.InputJsonObject, ...bundleTags(bundle),
            }),
            ...(breakdown !== undefined && { lessonBreakdown: breakdown as Prisma.InputJsonObject }),
          },
          include: { agent: { select: { id: true, name: true } }, replies: { include: { user: { select: { id: true, name: true, avatarUrl: true } }, agent: { select: { id: true, name: true } } } } },
        });
        // Of the lesson's progress only whether videos follow is still needed (lib/progress.ts): the answer carries
        // its own bill, and a list of cards should not drag the old one along for every answered topic.
        // Videos are made from the bundle: a lesson posted without one has none to wait for.
        const videos = bundle !== undefined ? question.videos : undefined;
        await tx.question.update({
          where: { id },
          data: { status: 'ANSWERED', progress: typeof videos === 'boolean' ? { videos } : Prisma.DbNull },
        });
        await tx.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
        return created;
      });
      reply.status(201);
      return {
        id: answer.id,
        questionId: answer.questionId,
        agent: answer.agent,
        content: answer.content,
        model: answer.model,
        createdAt: answer.createdAt.toISOString(),
      };
    } catch (err) {
      if (isUniqueViolation(err)) return sendError(reply, 'CONFLICT', 'You have already answered this question');
      if (isAppError(err)) return sendError(reply, err.code, err.message, err.extra);
      throw err;
    }
  });
}
