import { FastifyInstance } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireAgent } from '../middleware/auth.js';
import { FAILED_REASON_MAX_LENGTH, serializeVideo, videoStaleMinutes } from '../lib/droplet.js';
import { videoMaxAttempts } from '../lib/video-claim.js';
import { parseLimit, validateString } from '../lib/validation.js';

// The claim on a droplet's videos (lib/video-claim.ts): who is rendering them, which
// attempt this is, and the queue that hands a render whose machine died to a later run.
// For the agent's own answers only, like the video PATCH.

const answerSelect = {
  id: true, questionId: true, agentId: true, bundle: true, factcheck: true, videoAttempts: true,
  videoStatus: true, songVideoUrl: true, lessonVideoUrl: true, videoError: true, videoUpdatedAt: true,
} as const;

type ClaimedAnswer = { id: string; questionId: string; bundle: unknown; factcheck: unknown; videoAttempts: number } &
  Parameters<typeof serializeVideo>[0];

// What a run needs to make the videos on a machine that has never seen the topic.
function serializeClaim(a: ClaimedAnswer) {
  return {
    answerId: a.id,
    questionId: a.questionId,
    attempt: a.videoAttempts,
    bundle: a.bundle,
    factcheck: a.factcheck,
    video: serializeVideo(a),
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
    if (attempt !== null) {
      claimed = await prisma.$queryRaw<{ id: string }[]>`
        UPDATE answers SET "videoUpdatedAt" = now()
        WHERE id = ${id} AND "agentId" = ${agentId} AND "videoStatus" = 'RENDERING' AND "videoAttempts" = ${attempt}
        RETURNING id
      `;
    }
    // A run whose attempt was taken over may still start a new one, if the one that
    // took over has itself gone quiet: the same rule as a claim without an attempt.
    if (claimed.length === 0) {
      claimed = await prisma.$queryRaw<{ id: string }[]>`
        UPDATE answers
        SET "videoStatus" = 'RENDERING', "videoUpdatedAt" = now(), "videoError" = NULL,
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
    return serializeClaim(answer as ClaimedAnswer);
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
      SET "videoStatus" = 'RENDERING', "videoUpdatedAt" = now(), "videoError" = NULL,
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
    return { data: answers.map((a) => serializeClaim(a as ClaimedAnswer)) };
  });

  // POST /api/answers/:id/video/fail — { reason, attempt }: give the videos up after a
  // second failure. The lesson stays; the droplet shows the reason. Only the run that
  // holds the current attempt may, so a run that was taken over cannot fail the render
  // that replaced it.
  app.post('/api/answers/:id/video/fail', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agentId = request.agent!.id;
    const body = (request.body ?? {}) as { reason?: unknown; attempt?: unknown };
    const reasonError = validateString(body.reason, 'reason', { required: true, minLength: 1, maxLength: FAILED_REASON_MAX_LENGTH });
    if (reasonError) return sendError(reply, 'VALIDATION_ERROR', reasonError);
    const attempt = attemptOf(body.attempt);
    if (attempt === null || attempt === 'invalid') return sendError(reply, 'VALIDATION_ERROR', 'attempt must be a positive integer');

    const failed = await prisma.$queryRaw<{ id: string }[]>`
      UPDATE answers
      SET "videoStatus" = 'FAILED', "videoError" = ${(body.reason as string).trim()}, "videoUpdatedAt" = now()
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
    return serializeVideo(answer);
  });
}
