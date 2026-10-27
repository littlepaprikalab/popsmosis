import { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import prisma from '../lib/prisma.js';
import { Prisma } from '../generated/prisma/client.js';
import { sendError } from '../lib/errors.js';
import { parseLimit } from '../lib/validation.js';
import { decodeCursor, encodeCreatedAtIdCursor } from '../lib/cursor.js';
import { EXCERPT_SOURCE_LENGTH, publicDropletSelect, publicDropletWhere, serializeDroplet, serializeDropletCard } from '../lib/droplet.js';
import { PUBLIC_PER_CLIENT_PER_MINUTE, PUBLIC_PER_INSTANCE_PER_MINUTE, WindowCounter, publicClientKey } from '../lib/public-limit.js';

/**
 * The Droplets tab (docs/PLAN.md, section 10): every finished droplet, for anyone,
 * with no sign-in. The first endpoints that need none, so they read nothing about
 * the caller and say nothing about the learner: no name or avatar, not the request
 * in their own words, no attachments, no follow-ups. The fields that do go out are
 * in lib/droplet.ts (publicDropletSelect).
 */
export default async function dropletRoutes(app: FastifyInstance) {
  const ceiling = new WindowCounter(PUBLIC_PER_INSTANCE_PER_MINUTE);
  const config = {
    rateLimit: { max: PUBLIC_PER_CLIENT_PER_MINUTE, timeWindow: '1 minute', keyGenerator: (req: FastifyRequest) => publicClientKey(req) },
  };
  // After the per-visitor limit, so a visitor already over theirs does not spend the shared one.
  const shared = async (_request: FastifyRequest, reply: FastifyReply) => {
    if (!ceiling.take()) return sendError(reply, 'RATE_LIMITED', 'Too many requests');
  };

  // GET /api/droplets — READY and not hidden, newest first; ?cursor=, ?limit= (default 12, max 24).
  app.get('/api/droplets', { config, preHandler: [shared] }, async (request) => {
    const { limit, cursor } = request.query as Record<string, string | undefined>;
    const take = parseLimit(limit, 12, 24);
    const after = decodeCursor(cursor ?? '');
    const at = typeof after?.createdAt === 'string' ? new Date(after.createdAt) : null;
    const from = at && !Number.isNaN(at.getTime())
      ? (typeof after?.id === 'string'
        ? Prisma.sql`AND ("createdAt" < ${at} OR ("createdAt" = ${at} AND id < ${after.id}))`
        : Prisma.sql`AND "createdAt" < ${at}`)
      : Prisma.empty;
    // A card needs two fields of the bundle and the start of the lesson; the bundle can be
    // hundreds of KB, so Postgres hands back only those (the same fields publicDropletSelect
    // names). Walks the (videoStatus, createdAt, id) index.
    const rows = await prisma.$queryRaw<Parameters<typeof serializeDropletCard>[0][]>`
      SELECT id, left(content, ${EXCERPT_SOURCE_LENGTH}) AS content, "createdAt",
             jsonb_build_object('course', bundle->'course', 'topic', bundle->'topic') AS bundle,
             "songVideoUrl", "lessonVideoUrl", "songPosterUrl", "lessonPosterUrl"
      FROM answers
      WHERE "videoStatus" = 'READY' AND "hiddenAt" IS NULL ${from}
      ORDER BY "createdAt" DESC, id DESC
      LIMIT ${take}
    `;
    return { data: rows.map(serializeDropletCard), nextCursor: encodeCreatedAtIdCursor(rows, take) };
  });

  // GET /api/droplets/:id — one droplet, by its answer id; 404 unless it is on the tab.
  app.get('/api/droplets/:id', { config, preHandler: [shared] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const row = await prisma.answer.findFirst({ where: { id, ...publicDropletWhere }, select: publicDropletSelect });
    if (!row) return sendError(reply, 'NOT_FOUND', 'Droplet not found');
    return serializeDroplet(row);
  });
}
