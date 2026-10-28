import { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import prisma from '../lib/prisma.js';
import { Prisma } from '../generated/prisma/client.js';
import { sendError, isForeignKeyViolation, isUniqueViolation } from '../lib/errors.js';
import { containsPersonalInfo, parseLimit, validateString } from '../lib/validation.js';
import { decodeCreatedAtIdCursor, decodeCursor, encodeCreatedAtIdCursor } from '../lib/cursor.js';
import { EXCERPT_SOURCE_LENGTH, publicDropletSelect, publicDropletWhere, serializeDroplet, serializeDropletCard } from '../lib/droplet.js';
import {
  COMMENT_MAX_LENGTH, COMMENTS_ON_CARD, COMMENTS_PAGE_MAX, COMMENTS_PAGE_SIZE, commentSelect, commentsPerUserPerHour, hasVisibleText, idempotencyKeyOf, serializeComment,
} from '../lib/engagement.js';
import { PUBLIC_PER_CLIENT_PER_MINUTE, PUBLIC_PER_INSTANCE_PER_MINUTE, WindowCounter, publicClientKey } from '../lib/public-limit.js';
import { requireUser, sessionSubject } from '../middleware/auth.js';
import { tagQueryOf } from '../lib/tags.js';

// Likes are one row each way and cost little, but each is a write on the droplet's row, so one
// person flipping a thumb as fast as they can click is held to a pace no real use reaches.
const LIKES_PER_USER_PER_MINUTE = 60;
const COMMENT_BODY_LIMIT = 16 * 1024;

/**
 * The Droplets tab (docs/PLAN.md, section 10): every finished droplet, for anyone,
 * with no sign-in, and the thumbs-up and comments under each.
 *
 * The reads need no sign-in and say nothing about the learner: no name or avatar, not
 * the request in their own words, no attachments, no follow-ups. The fields that go out
 * are in lib/droplet.ts (publicDropletSelect) and lib/engagement.ts. When the visitor is
 * signed in, a read also says which of it is theirs (`liked`, a comment's `mine`); the
 * session is only verified for that, never renewed, so a public response sets no cookie.
 *
 * Liking and commenting need a signed-in person, and act only on a droplet that is on
 * the tab: READY and not hidden. Taking your own back (unliking, deleting your comment)
 * works whatever the droplet's state.
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

  // A public read differs by who is signed in, so no shared cache may hand one person's to another.
  const viewerOf = (request: FastifyRequest, reply: FastifyReply) => {
    const viewer = sessionSubject(request);
    const vary = reply.getHeader('vary');
    reply.header('vary', vary ? `${vary}, Cookie` : 'Cookie');
    if (viewer) reply.header('cache-control', 'private');
    return viewer;
  };

  // GET /api/droplets — READY and not hidden, newest first; ?cursor=, ?limit= (default 12, max 24);
  // ?tag= only those whose song or lesson video carries that hashtag ("#APBio" and "apbio" alike).
  // Each card brings its newest comments (COMMENTS_ON_CARD), so the list shows them in one request.
  app.get('/api/droplets', { config, preHandler: [shared] }, async (request, reply) => {
    const viewer = viewerOf(request, reply);
    const { limit, cursor } = request.query as Record<string, string | undefined>;
    const { tag } = request.query as Record<string, unknown>; // a string, or a list when named twice
    const take = parseLimit(limit, 12, 24);
    const after = decodeCursor(cursor ?? '');
    const at = typeof after?.createdAt === 'string' ? new Date(after.createdAt) : null;
    const from = at && !Number.isNaN(at.getTime())
      ? (typeof after?.id === 'string'
        ? Prisma.sql`AND ("createdAt" < ${at} OR ("createdAt" = ${at} AND id < ${after.id}))`
        : Prisma.sql`AND "createdAt" < ${at}`)
      : Prisma.empty;
    // Each video's tags have a GIN index, so a search reads only the droplets that carry it.
    const searched = tagQueryOf(tag);
    const tagged = searched
      ? Prisma.sql`AND ("songTags" @> ARRAY[${searched}]::text[] OR "lessonTags" @> ARRAY[${searched}]::text[])`
      : Prisma.empty;
    // The viewer's own thumbs-up, one primary-key probe per card, in the same statement.
    const liked = viewer
      ? Prisma.sql`EXISTS (SELECT 1 FROM droplet_likes l WHERE l."answerId" = answers.id AND l."userId" = ${viewer})`
      : Prisma.sql`false`;
    // A card needs two fields of the bundle and the start of the lesson; the bundle can be
    // hundreds of KB, so Postgres hands back only those (the same fields publicDropletSelect
    // names). Walks the (videoStatus, createdAt, id) index.
    const rows = await prisma.$queryRaw<(Parameters<typeof serializeDropletCard>[0] & { liked: boolean })[]>`
      SELECT id, left(content, ${EXCERPT_SOURCE_LENGTH}) AS content, "createdAt",
             jsonb_build_object('course', bundle->'course', 'topic', bundle->'topic') AS bundle,
             "songVideoUrl", "lessonVideoUrl", "songPosterUrl", "lessonPosterUrl",
             "likeCount", "commentCount", "songTags", "lessonTags", ${liked} AS liked
      FROM answers
      WHERE "videoStatus" = 'READY' AND "hiddenAt" IS NULL ${tagged} ${from}
      ORDER BY "createdAt" DESC, id DESC
      LIMIT ${take}
    `;
    // The comments are extra to the list: when they cannot be read, the cards go out without
    // them, and a card reads its own when it is opened, as it does from an API older than them.
    let comments: Awaited<ReturnType<typeof cardComments>> | null = null;
    try {
      comments = await cardComments(rows, viewer);
    } catch (err) {
      request.log.warn({ err }, 'the cards went out without their comments');
    }
    return {
      data: rows.map((row) => ({ ...serializeDropletCard(row, row.liked), ...(comments && { comments: comments(row) }) })),
      nextCursor: encodeCreatedAtIdCursor(rows, take),
    };
  });

  // The newest few comments of each card on a page of the list, so a card can show them
  // without a request of its own. One statement for the page: for each droplet that has any,
  // a probe of the (answerId, createdAt DESC, id DESC) index. A card's cursor is where its
  // comments route carries on, and null once it shows them all.
  async function cardComments(cards: { id: string; commentCount: number }[], viewer: string | null) {
    const ids = cards.filter((c) => c.commentCount > 0).map((c) => c.id);
    const rows = ids.length === 0 ? [] : await prisma.$queryRaw<
      { answerId: string; id: string; content: string; createdAt: Date; userId: string; name: string | null }[]
    >`
      SELECT c."answerId", c.id, c.content, c."createdAt", c."userId", u.name
      FROM unnest(ARRAY[${Prisma.join(ids)}]::text[]) AS d(id)
      CROSS JOIN LATERAL (
        SELECT id, "answerId", content, "createdAt", "userId" FROM droplet_comments
        WHERE "answerId" = d.id
        ORDER BY "createdAt" DESC, id DESC
        LIMIT ${COMMENTS_ON_CARD}
      ) c
      JOIN users u ON u.id = c."userId"
      ORDER BY c."createdAt" DESC, c.id DESC
    `;
    const byDroplet = new Map<string, typeof rows>();
    for (const row of rows) byDroplet.set(row.answerId, [...(byDroplet.get(row.answerId) ?? []), row]);
    return (card: { id: string; commentCount: number }) => {
      const newest = byDroplet.get(card.id) ?? [];
      return {
        data: newest.map((c) => serializeComment({ ...c, user: { name: c.name } }, viewer)),
        nextCursor: newest.length > 0 && card.commentCount > newest.length ? encodeCreatedAtIdCursor(newest, newest.length) : null,
      };
    };
  }

  // A page of a droplet's comments, newest first. Walks the (answerId, createdAt DESC, id DESC) index.
  async function commentsPage(answerId: string, viewer: string | null, take: number, cursor?: string) {
    const after = decodeCreatedAtIdCursor(cursor);
    const rows = await prisma.dropletComment.findMany({
      where: { answerId, ...(after ? { AND: [after] } : {}) },
      orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
      take,
      select: commentSelect,
    });
    return { data: rows.map((c) => serializeComment(c, viewer)), nextCursor: encodeCreatedAtIdCursor(rows, take) };
  }

  // GET /api/droplets/:id — one droplet, by its answer id, with the first page of its comments;
  // 404 unless it is on the tab. One request for the whole page: every public request spends the
  // instance's shared ceiling, so a page view that needed two would halve how many it can serve.
  app.get('/api/droplets/:id', { config, preHandler: [shared] }, async (request, reply) => {
    const viewer = viewerOf(request, reply);
    const { id } = request.params as { id: string };
    const row = await prisma.answer.findFirst({ where: { id, ...publicDropletWhere }, select: publicDropletSelect });
    if (!row) return sendError(reply, 'NOT_FOUND', 'Droplet not found');
    const liked = !!viewer && !!(await prisma.dropletLike.findUnique({
      where: { answerId_userId: { answerId: id, userId: viewer } }, select: { userId: true },
    }));
    return { ...serializeDroplet(row, liked), comments: await commentsPage(id, viewer, COMMENTS_PAGE_SIZE) };
  });

  // GET /api/droplets/:id/comments — newest first; ?cursor=, ?limit= (default 20, max 50): the
  // pages after the one GET /api/droplets/:id brings. The count comes back with every page, so
  // loading more also brings the count up to date.
  app.get('/api/droplets/:id/comments', { config, preHandler: [shared] }, async (request, reply) => {
    const viewer = viewerOf(request, reply);
    const { id } = request.params as { id: string };
    const { limit, cursor } = request.query as Record<string, string | undefined>;
    const droplet = await prisma.answer.findFirst({ where: { id, ...publicDropletWhere }, select: { commentCount: true } });
    if (!droplet) return sendError(reply, 'NOT_FOUND', 'Droplet not found');
    const page = await commentsPage(id, viewer, parseLimit(limit, COMMENTS_PAGE_SIZE, COMMENTS_PAGE_MAX), cursor);
    return { ...page, commentCount: droplet.commentCount };
  });

  // PUT /api/droplets/:id/like — the viewer's thumbs-up; DELETE takes it back. Both are
  // idempotent: sending one twice changes nothing, so a retry or a double click is safe.
  // Both return { liked, likeCount }. One route for the two methods, so they share one
  // rate limit: flipping back and forth counts every flip.
  app.route({
    method: ['PUT', 'DELETE'],
    url: '/api/droplets/:id/like',
    preHandler: [requireUser],
    config: { rateLimit: { max: LIKES_PER_USER_PER_MINUTE, timeWindow: '1 minute' } },
    handler: async (request, reply) => {
      const { id } = request.params as { id: string };
      const userId = request.viewer!.id;

      if (request.method === 'DELETE') {
        // Works on a droplet that has since left the tab: what is yours is yours to take back.
        await prisma.dropletLike.deleteMany({ where: { answerId: id, userId } });
        const row = await prisma.answer.findUnique({ where: { id }, select: { likeCount: true } });
        if (!row) return sendError(reply, 'NOT_FOUND', 'Droplet not found');
        return { liked: false, likeCount: row.likeCount };
      }

      try {
        // Only a droplet on the tab, decided in the same statement as the insert. The trigger
        // counts it (prisma/migrations/20261031120000_droplet_engagement).
        await prisma.$executeRaw`
          INSERT INTO droplet_likes ("answerId", "userId")
          SELECT id, ${userId} FROM answers WHERE id = ${id} AND "videoStatus" = 'READY' AND "hiddenAt" IS NULL
          ON CONFLICT DO NOTHING
        `;
      } catch (err) {
        // The droplet (or the account) was deleted between the check and the insert.
        if (isForeignKeyViolation(err)) return sendError(reply, 'NOT_FOUND', 'Droplet not found');
        throw err;
      }
      const row = await prisma.answer.findFirst({ where: { id, ...publicDropletWhere }, select: { likeCount: true } });
      if (!row) return sendError(reply, 'NOT_FOUND', 'Droplet not found');
      return { liked: true, likeCount: row.likeCount };
    },
  });

  // POST /api/droplets/:id/comments — { content } 1..1000 chars, plain text. Send an
  // Idempotency-Key header to make it safe to retry: the same key returns the comment it
  // first made (200) instead of posting it twice.
  // Checked for a sign-in before the body is read, as the other message routes are.
  app.post('/api/droplets/:id/comments', { onRequest: [requireUser], bodyLimit: COMMENT_BODY_LIMIT }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const viewer = request.viewer!;
    const { content } = (request.body ?? {}) as { content?: unknown };
    const contentError = validateString(content, 'content', { required: true, minLength: 1, maxLength: COMMENT_MAX_LENGTH });
    if (contentError) return sendError(reply, 'VALIDATION_ERROR', contentError);
    const text = (content as string).trim();
    if (!hasVisibleText(text)) return sendError(reply, 'VALIDATION_ERROR', 'content must have something to read in it');
    if (containsPersonalInfo(text)) {
      return sendError(reply, 'VALIDATION_ERROR', 'Comments are public: please leave out email addresses and phone numbers');
    }
    const key = idempotencyKeyOf(request.headers['idempotency-key']);
    if (key === null) return sendError(reply, 'VALIDATION_ERROR', 'Idempotency-Key must be 8 to 100 letters, digits or _.:-');

    const droplet = await prisma.answer.findFirst({ where: { id, ...publicDropletWhere }, select: { id: true } });
    if (!droplet) return sendError(reply, 'NOT_FOUND', 'Droplet not found');

    // The comment this key already made, if any. Before the hourly limit, so the retry of a
    // comment that went through is never refused for counting itself.
    const replay = async () => {
      if (!key) return null;
      const existing = await prisma.dropletComment.findUnique({
        where: { userId_idempotencyKey: { userId: viewer.id, idempotencyKey: key } },
        select: { ...commentSelect, answerId: true },
      });
      if (!existing) return null;
      if (existing.answerId !== id || existing.content !== text) {
        return sendError(reply, 'CONFLICT', 'That Idempotency-Key was already used for a different comment');
      }
      reply.status(200);
      return serializeComment(existing, viewer.id);
    };
    const replayed = await replay();
    if (replayed) return replayed;

    const limit = commentsPerUserPerHour();
    const recent = await prisma.dropletComment.count({ where: { userId: viewer.id, createdAt: { gte: new Date(Date.now() - 3600_000) } } });
    if (recent >= limit) return sendError(reply, 'RATE_LIMITED', `Max ${limit} comments per hour`, { retryable: false });

    try {
      const created = await prisma.dropletComment.create({
        data: { answerId: id, userId: viewer.id, content: text, idempotencyKey: key ?? null },
        select: commentSelect,
      });
      reply.status(201);
      return serializeComment(created, viewer.id);
    } catch (err) {
      // Two sends of one key at once: the other one won, so this one is its replay.
      if (isUniqueViolation(err)) {
        const again = await replay();
        if (again) return again;
      }
      if (isForeignKeyViolation(err)) return sendError(reply, 'NOT_FOUND', 'Droplet not found');
      throw err;
    }
  });

  // DELETE /api/droplets/:id/comments/:commentId — its author, or an admin. 204, and 204
  // again if it is already gone by the time this runs.
  app.delete('/api/droplets/:id/comments/:commentId', { preHandler: [requireUser] }, async (request, reply) => {
    const { id, commentId } = request.params as { id: string; commentId: string };
    const viewer = request.viewer!;
    const row = await prisma.dropletComment.findUnique({ where: { id: commentId }, select: { answerId: true, userId: true } });
    if (!row || row.answerId !== id) return sendError(reply, 'NOT_FOUND', 'Comment not found');
    if (row.userId !== viewer.id && viewer.role !== 'ADMIN') return sendError(reply, 'FORBIDDEN', 'You can only delete your own comments');
    await prisma.dropletComment.deleteMany({ where: { id: commentId } });
    if (row.userId !== viewer.id) request.log.info({ answerId: id, commentId, adminId: viewer.id }, 'droplet comment removed by an admin');
    return reply.status(204).send();
  });
}
