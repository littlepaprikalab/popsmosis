import { FastifyInstance, FastifyReply } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireAgent } from '../middleware/auth.js';
import { VIDEO_STATUSES, VIDEO_ERROR_MAX_LENGTH, serializeVideo, videoUrlError, type VideoStatus } from '../lib/droplet.js';
import { BUCKETS, fileError, signUpload, storageConfig, StorageError, type Bucket } from '../lib/storage.js';

// The agent's half of a droplet's videos (docs/PLAN.md, section 10). Both routes
// are for the agent's own answers only: a key can finish its own droplets, never
// another agent's.
export default async function answerRoutes(app: FastifyInstance) {
  // The answer, or null once the error has been sent.
  async function ownAnswer(id: string, agentId: string, reply: FastifyReply) {
    const answer = await prisma.answer.findUnique({
      where: { id },
      select: { id: true, agentId: true, songVideoUrl: true, lessonVideoUrl: true },
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

  // PATCH /api/answers/:id/video — { status, songVideoUrl?, lessonVideoUrl?, error? }.
  // RENDERING when the media steps start (and again as each video lands), READY
  // when they are done, FAILED with one line on why. A URL left out keeps the one
  // already saved, so the song can be posted before the lesson video exists.
  app.patch('/api/answers/:id/video', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { status, songVideoUrl, lessonVideoUrl, error } = (request.body ?? {}) as {
      status?: unknown; songVideoUrl?: unknown; lessonVideoUrl?: unknown; error?: unknown;
    };
    if (!VIDEO_STATUSES.includes(status as VideoStatus)) {
      return sendError(reply, 'VALIDATION_ERROR', `status must be one of ${VIDEO_STATUSES.join(', ')}`);
    }
    for (const [field, value] of [['songVideoUrl', songVideoUrl], ['lessonVideoUrl', lessonVideoUrl]] as const) {
      const urlError = value === undefined ? null : videoUrlError(value, field);
      if (urlError) return sendError(reply, 'VALIDATION_ERROR', urlError);
    }
    if (status === 'FAILED') {
      if (typeof error !== 'string' || !error.trim()) return sendError(reply, 'VALIDATION_ERROR', 'error is required when status is FAILED');
      if (error.length > VIDEO_ERROR_MAX_LENGTH) return sendError(reply, 'VALIDATION_ERROR', `error must be ${VIDEO_ERROR_MAX_LENGTH} characters or less`);
    } else if (error !== undefined) {
      return sendError(reply, 'VALIDATION_ERROR', 'error is only for status FAILED');
    }

    const answer = await ownAnswer(id, request.agent!.id, reply);
    if (!answer) return reply;
    const song = (songVideoUrl as string | undefined) ?? answer.songVideoUrl;
    const lesson = (lessonVideoUrl as string | undefined) ?? answer.lessonVideoUrl;
    // READY is what lists a droplet on the public tab, so it must have something to play.
    if (status === 'READY' && !song && !lesson) {
      return sendError(reply, 'VALIDATION_ERROR', 'READY needs songVideoUrl or lessonVideoUrl, sent now or before');
    }

    const updated = await prisma.answer.update({
      where: { id },
      data: {
        videoStatus: status as VideoStatus,
        songVideoUrl: song,
        lessonVideoUrl: lesson,
        videoError: status === 'FAILED' ? (error as string).trim() : null,
        videoUpdatedAt: new Date(),
      },
      select: { videoStatus: true, songVideoUrl: true, lessonVideoUrl: true, videoError: true, videoUpdatedAt: true },
    });
    await prisma.agent.update({ where: { id: request.agent!.id }, data: { lastSeenAt: new Date() } });
    return serializeVideo(updated);
  });

  // POST /api/answers/:id/upload-url — { bucket, file }: a signed URL the studio
  // PUTs one file to, at <bucket>/<answerId>/<file> (lib/storage.ts). The public
  // bucket's answer also says where the file can be read, for the video PATCH.
  app.post('/api/answers/:id/upload-url', { preHandler: [requireAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { bucket, file } = (request.body ?? {}) as { bucket?: unknown; file?: unknown };
    if (!BUCKETS.includes(bucket as Bucket)) return sendError(reply, 'VALIDATION_ERROR', `bucket must be one of ${BUCKETS.join(', ')}`);
    const pathError = fileError(file);
    if (pathError) return sendError(reply, 'VALIDATION_ERROR', pathError);

    if (!(await ownAnswer(id, request.agent!.id, reply))) return reply;
    const config = storageConfig();
    if (!config) return sendError(reply, 'UNAVAILABLE', 'File storage is not configured on this API (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)');
    try {
      return await signUpload(config, bucket as Bucket, id, file as string);
    } catch (err) {
      if (!(err instanceof StorageError)) throw err;
      request.log.warn({ err, bucket, answerId: id }, 'signed upload URL failed');
      return sendError(reply, 'UNAVAILABLE', 'File storage did not give an upload URL; try again', { retryable: true });
    }
  });
}
