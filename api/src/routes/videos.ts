import { FastifyInstance } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireAgent } from '../middleware/auth.js';
import { FAILED_REASON_MAX_LENGTH, videoStaleMinutes } from '../lib/droplet.js';
import { videoMaxAttempts } from '../lib/video-claim.js';
import { parseLimit, validateString } from '../lib/validation.js';
import { breakdownError } from '../lib/breakdown.js';
import { progressError, storedProgress } from '../lib/progress.js';

// The claim on a droplet's videos (lib/video-claim.ts): who is rendering them, which
// attempt this is, and the queue that hands a render whose machine died to a later run.
// For the agent's own answers only, like the video PATCH.

// Only what these routes read: the page's view of the videos (lib/droplet.ts) is not theirs.
const answerSelect = {
  id: true, questionId: true, agentId: true, bundle: true, factcheck: true,
  videoAttempts: true, videoStatus: true, videoError: true,
} as const;

type ClaimedAnswer = {
  id: string; questionId: string; bundle: unknown; factcheck: unknown; videoAttempts: number; videoStatus: string | null;
};

// What a run needs to make the videos on a machine that has never seen the topic. A renewal,
// the heartbeat before every step, leaves out the bundle and fact-check its run already has.
function serializeClaim(a: ClaimedAnswer, renewal = false) {
  return {
    answerId: a.id,
    questionId: a.questionId,
    attempt: a.videoAttempts,
    status: a.videoStatus,
    ...(!renewal && { bundle: a.bundle, factcheck: a.factcheck }),
  };
}

function attemptOf(value: unknown): number | null | 'invalid' {
  if (value === undefined || value === null) return null;
  return Number.isInteger(value) && (value as number) > 0 ? (value as number) : 'invalid';
}

export default async function videoRoutes(app: FastifyInstance) {
  // POST /api/answers/:id/video/claim — { attempt? }. Without an attempt: start one,
  // when the videos were never started, were given up, or went quiet past the stale
  // time, and fewer than VIDEO_MAX_ATTEMPTS have started; it marks them RENDERING.
  // With the attempt a run already holds: renew it, the heartbeat before each step.
  // 409 when another run holds them, they are READY, or the attempts are used up.
  app.post('/api/answers/:id/video/claim', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const attempt = attemptOf((request.body as { attempt?: unknown } | undefined)?.attempt);
    if (attempt === 'invalid') return sendError(reply, 'VALIDATION_ERROR', 'attempt must be a positive integer');

    let claimed: { id: string }[] = [];
    let renewed = false;
    if (attempt !== null) {
      claimed = await prisma.$queryRaw<{ id: string }[]>`
        UPDATE answers SET "videoUpdatedAt" = now()
        WHERE id = ${id} AND "agentId" = ${agentId} AND "videoStatus" = 'RENDERING' AND "videoAttempts" = ${attempt}
        RETURNING id
      `;
      renewed = claimed.length > 0;
    }
    // A run whose attempt was taken over may still start a new one, if the one that
    // took over has itself gone quiet: the same rule as a claim without an attempt.
    if (claimed.length === 0) {
      claimed = await prisma.$queryRaw<{ id: string }[]>`
        UPDATE answers
        SET "videoStatus" = 'RENDERING', "videoUpdatedAt" = now(), "videoError" = NULL, "videoProgress" = NULL,
            "videoAttempts" = "videoAttempts" + 1
        WHERE id = ${id} AND "agentId" = ${agentId} AND bundle IS NOT NULL
          AND "videoAttempts" < ${videoMaxAttempts()}
          AND ("videoStatus" IS NULL OR "videoStatus" = 'FAILED'
               OR ("videoStatus" = 'RENDERING'
                   AND "videoUpdatedAt" < now() - make_interval(mins => ${videoStaleMinutes()})))
        RETURNING id
      `;
    }
    await prisma.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });

    const answer = await prisma.answer.findUnique({ where: { id }, select: answerSelect });
    if (!answer) return sendError(reply, 'NOT_FOUND', 'Answer not found');
    if (answer.agentId !== agentId) return sendError(reply, 'FORBIDDEN', 'You can only make videos for your own answers');
    if (claimed.length === 0) {
      if (answer.bundle === null) return sendError(reply, 'CONFLICT', 'This lesson has no bundle to make videos from');
      if (answer.videoStatus === 'READY') return sendError(reply, 'CONFLICT', 'The videos are already made');
      if (answer.videoStatus === 'RENDERING') {
        return sendError(reply, 'CONFLICT', 'Another run is making these videos', { attempt: answer.videoAttempts });
      }
      return sendError(reply, 'CONFLICT', `The videos were given up after ${answer.videoAttempts} attempt(s)`);
    }
    return serializeClaim(answer, renewed);
  });

  // GET /api/videos/queue?limit=N — agent: its own droplets whose render went quiet
  // past the stale time (the machine died), oldest first, each claimed for this caller
  // as its next attempt, with what a fresh machine needs to make the videos. A render
  // that was given up (FAILED) is not retried here: two failures in a row are not
  // what a new machine fixes.
  app.get('/api/videos/queue', { preHandler: [requireAgent] }, async (request) => {
    const { limit } = request.query as Record<string, string | undefined>;
    const take = parseLimit(limit, 1, 5);
    const agentId = request.agent!.id;
    // The same select-and-claim as the question queue: SKIP LOCKED hands two overlapping
    // runs different droplets.
    const claimed = await prisma.$queryRaw<{ id: string }[]>`
      UPDATE answers
      SET "videoStatus" = 'RENDERING', "videoUpdatedAt" = now(), "videoError" = NULL, "videoProgress" = NULL,
          "videoAttempts" = "videoAttempts" + 1
      WHERE id IN (
        SELECT id FROM answers
        WHERE "agentId" = ${agentId} AND bundle IS NOT NULL
          AND "videoStatus" = 'RENDERING'
          AND "videoUpdatedAt" < now() - make_interval(mins => ${videoStaleMinutes()})
          AND "videoAttempts" < ${videoMaxAttempts()}
        ORDER BY "createdAt" ASC, id ASC
        LIMIT ${take}
        FOR UPDATE SKIP LOCKED
      )
      RETURNING id
    `;
    await prisma.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
    if (claimed.length === 0) return { data: [] };
    const answers = await prisma.answer.findMany({
      where: { id: { in: claimed.map((r) => r.id) } },
      select: answerSelect,
      orderBy: [{ createdAt: 'asc' }, { id: 'asc' }],
    });
    return { data: answers.map((a) => serializeClaim(a)) };
  });

  // PUT /api/answers/:id/video/progress — { attempt?, running: [{ step, seconds }], failed?, breakdown? }: where
  // the render is (lib/progress.ts), sent at the start and the end of each media step. Its breakdown is the
  // videos' bill so far, until READY or the give-up sends the last one. Only while the videos are RENDERING, and
  // with an attempt, only for that attempt, so a run that was taken over cannot write over the one that replaced
  // it. It is not the heartbeat: the claim before each step is.
  app.put('/api/answers/:id/video/progress', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const problem = progressError(request.body, 'videos');
    if (problem) return sendError(reply, 'VALIDATION_ERROR', problem);
    const report = request.body as Record<string, unknown>;
    const attempt = attemptOf(report.attempt);
    if (attempt === 'invalid') return sendError(reply, 'VALIDATION_ERROR', 'attempt must be a positive integer');
    const breakdown = report.breakdown === undefined ? null : JSON.stringify(report.breakdown);

    const updated = await prisma.$queryRaw<{ id: string }[]>`
      UPDATE answers
      SET "videoProgress" = ${JSON.stringify(storedProgress(report))}::jsonb,
          "videoBreakdown" = COALESCE(${breakdown}::jsonb, "videoBreakdown")
      WHERE id = ${id} AND "agentId" = ${agentId} AND "videoStatus" = 'RENDERING'
        AND (${attempt}::int IS NULL OR "videoAttempts" = ${attempt}::int)
      RETURNING id
    `;
    // No lastSeenAt here: the claim before each step keeps it (routes/questions.ts says why).
    if (updated.length === 0) {
      const answer = await prisma.answer.findUnique({ where: { id }, select: answerSelect });
      if (!answer) return sendError(reply, 'NOT_FOUND', 'Answer not found');
      if (answer.agentId !== agentId) return sendError(reply, 'FORBIDDEN', 'You can only report on your own answers');
      return sendError(reply, 'CONFLICT', answer.videoStatus === 'RENDERING'
        ? 'Another run has taken over these videos' : `The videos are ${answer.videoStatus ?? 'not started'}, not rendering`);
    }
    return reply.status(204).send();
  });

  // POST /api/answers/:id/video/fail — { reason, attempt, breakdown? }: give the videos up
  // after a second failure. The lesson stays; the droplet shows the reason, and with the
  // breakdown, what the failed tries took and cost (lib/breakdown.ts). Only the run that
  // holds the current attempt may, so a run that was taken over cannot fail the render
  // that replaced it.
  app.post('/api/answers/:id/video/fail', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const body = (request.body ?? {}) as { reason?: unknown; attempt?: unknown; breakdown?: unknown };
    const reasonError = validateString(body.reason, 'reason', { required: true, minLength: 1, maxLength: FAILED_REASON_MAX_LENGTH });
    if (reasonError) return sendError(reply, 'VALIDATION_ERROR', reasonError);
    const attempt = attemptOf(body.attempt);
    if (attempt === null || attempt === 'invalid') return sendError(reply, 'VALIDATION_ERROR', 'attempt must be a positive integer');
    const breakdownProblem = breakdownError(body.breakdown);
    if (breakdownProblem) return sendError(reply, 'VALIDATION_ERROR', breakdownProblem);
    // Written in the same conditional UPDATE, so only the attempt that holds the render can; none sent keeps what is there.
    const breakdown = body.breakdown === undefined ? null : JSON.stringify(body.breakdown);

    const failed = await prisma.$queryRaw<{ id: string }[]>`
      UPDATE answers
      SET "videoStatus" = 'FAILED', "videoError" = ${(body.reason as string).trim()}, "videoUpdatedAt" = now(),
          "videoBreakdown" = COALESCE(${breakdown}::jsonb, "videoBreakdown")
      WHERE id = ${id} AND "agentId" = ${agentId} AND "videoStatus" = 'RENDERING' AND "videoAttempts" = ${attempt}
      RETURNING id
    `;
    await prisma.agent.update({ where: { id: agentId }, data: { lastSeenAt: new Date() } });
    const answer = await prisma.answer.findUnique({ where: { id }, select: answerSelect });
    if (!answer) return sendError(reply, 'NOT_FOUND', 'Answer not found');
    if (answer.agentId !== agentId) return sendError(reply, 'FORBIDDEN', 'You can only change your own answers');
    if (failed.length === 0) {
      return sendError(reply, 'CONFLICT', answer.videoStatus === 'RENDERING'
        ? 'Another run has taken over these videos' : `The videos are ${answer.videoStatus ?? 'not started'}, not rendering`);
    }
    return { status: answer.videoStatus, error: answer.videoError };
  });
}
