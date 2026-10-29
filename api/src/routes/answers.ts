import { FastifyInstance, FastifyReply } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireAdmin, requireAgent } from '../middleware/auth.js';
import { VIDEO_FIELDS, VIDEO_STATUSES, VIDEO_ERROR_MAX_LENGTH, serializeVideo, videoUrlError, type VideoField, type VideoStatus } from '../lib/droplet.js';
import { secretError } from '../lib/validation.js';
import { BUCKETS, bucketFileError, dropletFileUrlError, fileError, signUpload, storageConfig, STORAGE_SETTINGS, type Bucket } from '../lib/storage.js';
import { breakdownError } from '../lib/breakdown.js';
import { bundleTags } from '../lib/tags.js';
import { Prisma } from '../generated/prisma/client.js';

// The agent's half of a droplet's videos (docs/PLAN.md, section 10), for the agent's
// own answers only: a key can finish its own droplets, never another agent's. And
// the admin's switch that takes a droplet off the public Droplets tab.
export default async function answerRoutes(app: FastifyInstance) {
  // The answer, or null once the error has been sent.
  async function ownAnswer(id: string, agentId: string, reply: FastifyReply) {
    const answer = await prisma.answer.findUnique({
      where: { id },
      select: { id: true, agentId: true, songVideoUrl: true, lessonVideoUrl: true, videoStatus: true },
    });
    if (!answer) {
      sendError(reply, 'NOT_FOUND', 'Answer not found');
      return null;
    }
    if (answer.agentId !== agentId) {
      sendError(reply, 'FORBIDDEN', 'You can only change your own answers');
      return null;
    }
    return answer;
  }

  // The tags are copied out of the bundle when the lesson is posted (routes/questions.ts). A
  // lesson posted to an API older than the tags (a deploy in between) has them only in its
  // bundle: these are the ones to copy as it joins the tab, or null when there is nothing to
  // copy. Reads only the bundle's tags, and only for READY.
  async function tagsToCopy(id: string) {
    const rows = await prisma.$queryRaw<{ tags: unknown }[]>`
      SELECT bundle->'tags' AS tags FROM answers
      WHERE id = ${id} AND jsonb_typeof(bundle->'tags') = 'object'
        AND COALESCE(cardinality("songTags"), 0) = 0 AND COALESCE(cardinality("lessonTags"), 0) = 0
    `;
    if (!rows[0]) return null;
    const tags = bundleTags({ tags: rows[0].tags });
    return tags.songTags.length || tags.lessonTags.length ? tags : null;
  }

  // PATCH /api/answers/:id/video — { status, songVideoUrl?, lessonVideoUrl?,
  // songPosterUrl?, lessonPosterUrl?, error?, attempt?, breakdown? }. RENDERING when the media steps
  // start (and again as each video lands), READY when they are done, FAILED with one
  // line on why. A URL left out keeps the one already saved, so the song can be posted
  // before the lesson video exists. With the attempt a run holds (routes/videos.ts) it
  // applies only while that attempt is the current render: a run whose render went
  // quiet and was handed to another cannot mark its stale videos READY over the new one.
  // With READY, the breakdown of the media steps (lib/breakdown.ts) is stored beside the lesson's.
  app.patch('/api/answers/:id/video', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const body = (request.body ?? {}) as Record<string, unknown>;
    const { status, error } = body;
    const { songVideoUrl, lessonVideoUrl } = body;
    if (!VIDEO_STATUSES.includes(status as VideoStatus)) {
      return sendError(reply, 'VALIDATION_ERROR', `status must be one of ${VIDEO_STATUSES.join(', ')}`);
    }
    const urls: Partial<Record<VideoField, string>> = {};
    for (const field of VIDEO_FIELDS) {
      const value = body[field];
      if (value === undefined) continue;
      const urlError = videoUrlError(value, field) ?? dropletFileUrlError(value as string, field, id);
      if (urlError) return sendError(reply, 'VALIDATION_ERROR', urlError);
      urls[field] = value as string;
    }
    const { attempt } = body;
    if (attempt !== undefined && !(Number.isInteger(attempt) && (attempt as number) > 0)) {
      return sendError(reply, 'VALIDATION_ERROR', 'attempt must be a positive integer');
    }
    if (status === 'FAILED') {
      if (typeof error !== 'string' || !error.trim()) return sendError(reply, 'VALIDATION_ERROR', 'error is required when status is FAILED');
      if (error.length > VIDEO_ERROR_MAX_LENGTH) return sendError(reply, 'VALIDATION_ERROR', `error must be ${VIDEO_ERROR_MAX_LENGTH} characters or less`);
      const leak = secretError('error', error);
      if (leak) {
        request.log.warn({ agentId: request.agent!.id, answerId: id }, 'agent text refused: it looks like a secret');
        return sendError(reply, 'VALIDATION_ERROR', leak);
      }
    } else if (error !== undefined) {
      return sendError(reply, 'VALIDATION_ERROR', 'error is only for status FAILED');
    }
    const { breakdown } = body;
    if (breakdown !== undefined && status !== 'READY') {
      return sendError(reply, 'VALIDATION_ERROR', 'breakdown is only for status READY');
    }
    const breakdownProblem = breakdownError(breakdown);
    if (breakdownProblem) return sendError(reply, 'VALIDATION_ERROR', breakdownProblem);

    const answer = await ownAnswer(id, request.agent!.id, reply);
    if (!answer) return reply;
    // READY is what lists a droplet on the public tab, so it must have something to play.
    // A URL is never cleared, so one read here still holds when the update lands.
    if (status === 'READY' && !(songVideoUrl ?? answer.songVideoUrl) && !(lessonVideoUrl ?? answer.lessonVideoUrl)) {
      return sendError(reply, 'VALIDATION_ERROR', 'READY needs songVideoUrl or lessonVideoUrl, sent now or before');
    }

    // Read before the write, so a failed read fails the request before anything changed.
    const tags = status === 'READY' ? await tagsToCopy(id) : null;

    // One conditional write, so the attempt check and the update cannot be split by another run. READY is final: a
    // droplet on the public tab keeps the videos it was listed with, whoever holds the key (a re-render is a new
    // attempt, which READY never gets).
    const current = attempt === undefined
      ? { OR: [{ videoStatus: null }, { videoStatus: { not: 'READY' as const } }] }
      : { videoAttempts: attempt as number, videoStatus: 'RENDERING' as const };
    const { count } = await prisma.answer.updateMany({
      where: { id, ...current },
      data: {
        videoStatus: status as VideoStatus,
        // Only the URLs this request sent: the two videos render in parallel and report
        // separately, and writing back the other's value as read would erase it.
        ...urls,
        videoError: status === 'FAILED' ? (error as string).trim() : null,
        videoUpdatedAt: new Date(),
        // READY carries the videos' whole bill. One sent without it (the studio left it out as too big or
        // unreadable, or a READY by hand) drops the bill so far the progress reports kept: shown as the total,
        // it would leave out the render and the upload.
        ...(status === 'READY' && { videoBreakdown: breakdown === undefined ? Prisma.DbNull : (breakdown as Prisma.InputJsonObject) }),
        ...tags,
      },
    });
    await prisma.agent.update({ where: { id: request.agent!.id }, data: { lastSeenAt: new Date() } });
    if (count === 0) {
      return sendError(reply, 'CONFLICT', attempt === undefined
        ? 'These videos are READY and listed on the Droplets tab; they can no longer change'
        : 'Another run has taken over these videos, or they are no longer rendering');
    }
    const updated = await prisma.answer.findUniqueOrThrow({
      where: { id },
      select: {
        videoStatus: true, songVideoUrl: true, lessonVideoUrl: true, songPosterUrl: true, lessonPosterUrl: true,
        videoError: true, videoUpdatedAt: true,
      },
    });
    return serializeVideo(updated);
  });

  // POST /api/answers/:id/upload-url — { bucket, file }: a signed URL the studio
  // PUTs one file to, at <bucket>/<answerId>/<file>, with the headers it names
  // (lib/storage.ts). The public bucket's answer also says where the file can be
  // read, for the video PATCH. Signing asks nothing of R2, so once the settings are
  // whole this answers at once; a key R2 refuses shows on the PUT itself.
  app.post('/api/answers/:id/upload-url', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { bucket, file } = (request.body ?? {}) as { bucket?: unknown; file?: unknown };
    if (!BUCKETS.includes(bucket as Bucket)) return sendError(reply, 'VALIDATION_ERROR', `bucket must be one of ${BUCKETS.join(', ')}`);
    const pathError = fileError(file) ?? bucketFileError(bucket as Bucket, file as string);
    if (pathError) return sendError(reply, 'VALIDATION_ERROR', pathError);

    const answer = await ownAnswer(id, request.agent!.id, reply);
    if (!answer) return reply;
    // The files of a listed droplet stay as they were listed: an upload URL would overwrite them in place.
    if (answer.videoStatus === 'READY') {
      return sendError(reply, 'CONFLICT', 'These videos are READY and listed on the Droplets tab; their files can no longer change');
    }
    const config = storageConfig();
    if (!config) return sendError(reply, 'UNAVAILABLE', `File storage is not configured on this API (${STORAGE_SETTINGS.join(', ')})`);
    if ('problem' in config) return sendError(reply, 'UNAVAILABLE', `File storage is misconfigured on this API: ${config.problem}`);
    return signUpload(config, bucket as Bucket, id, file as string);
  });

  // PATCH /api/answers/:id — admin: { hidden }. True takes the droplet off the public
  // Droplets tab, false puts it back. The lesson stays on /learn either way.
  app.patch('/api/answers/:id', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { hidden } = (request.body ?? {}) as { hidden?: unknown };
    if (typeof hidden !== 'boolean') return sendError(reply, 'VALIDATION_ERROR', 'hidden must be true or false');
    const answer = await prisma.answer.findUnique({ where: { id }, select: { hiddenAt: true } });
    if (!answer) return sendError(reply, 'NOT_FOUND', 'Answer not found');
    // Hiding again keeps the first time it was hidden.
    const hiddenAt = hidden ? (answer.hiddenAt ?? new Date()) : null;
    const updated = await prisma.answer.update({ where: { id }, data: { hiddenAt }, select: { id: true, hiddenAt: true } });
    request.log.info({ answerId: id, hidden, adminId: request.viewer!.id }, 'droplet visibility changed');
    return { id: updated.id, hidden: updated.hiddenAt !== null, hiddenAt: updated.hiddenAt?.toISOString() ?? null };
  });
}
