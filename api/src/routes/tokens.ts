import { FastifyInstance } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireCookieUser, requireUser, invalidateUserTokenCache } from '../middleware/auth.js';
import { generateUserToken } from '../lib/apikey.js';
import { validateString } from '../lib/validation.js';

// A person with a hundred tokens has lost track of them, which is the state
// this is meant to prevent rather than a workload worth supporting.
const MAX_LIVE_TOKENS_PER_USER = 10;

function publicToken(t: { id: string; name: string; createdAt: Date; lastUsedAt: Date | null; revokedAt: Date | null }) {
  return {
    id: t.id,
    name: t.name,
    createdAt: t.createdAt.toISOString(),
    lastUsedAt: t.lastUsedAt?.toISOString() ?? null,
    revokedAt: t.revokedAt?.toISOString() ?? null,
  };
}

export default async function tokenRoutes(app: FastifyInstance) {
  // GET /api/tokens/me — what a token is, from the token's own point of view.
  // The one route a token is expected to call with nothing else in mind: a
  // client checks here that its token still works and who it speaks for.
  app.get('/api/tokens/me', { preHandler: [requireUser] }, async (request) => {
    const { id, email, name, avatarUrl, role } = request.viewer!;
    return { id, email, name, avatarUrl, role, auth: request.viewerAuth ?? 'cookie' };
  });

  // GET /api/tokens — the caller's own tokens, newest first. Revoked ones stay
  // listed: "did I already kill that one?" is the question this page answers.
  app.get('/api/tokens', { preHandler: [requireCookieUser] }, async (request) => {
    const tokens = await prisma.userToken.findMany({
      where: { userId: request.viewer!.id },
      orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
    });
    return { data: tokens.map(publicToken) };
  });

  // POST /api/tokens — mint one. The raw token is returned exactly once and is
  // not recoverable afterwards; only its sha256 is stored.
  app.post('/api/tokens', { preHandler: [requireCookieUser] }, async (request, reply) => {
    const { name } = (request.body ?? {}) as { name?: unknown };
    const err = validateString(name, 'name', { required: true, minLength: 2, maxLength: 60 });
    if (err) return sendError(reply, 'VALIDATION_ERROR', err);

    const live = await prisma.userToken.count({ where: { userId: request.viewer!.id, revokedAt: null } });
    if (live >= MAX_LIVE_TOKENS_PER_USER) {
      return sendError(reply, 'RATE_LIMITED', `Max ${MAX_LIVE_TOKENS_PER_USER} active tokens — revoke one first`, { retryable: false });
    }

    const { raw, hashed } = generateUserToken();
    const token = await prisma.userToken.create({
      data: { userId: request.viewer!.id, name: (name as string).trim(), tokenHash: hashed },
    });
    reply.status(201);
    return { ...publicToken(token), token: raw };
  });

  // DELETE /api/tokens/:id — revoke. Kept as a row so the name stays readable;
  // the cache is dropped here so the token stops working on this instance at
  // once rather than at the end of its TTL.
  app.delete('/api/tokens/:id', { preHandler: [requireCookieUser] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const token = await prisma.userToken.findUnique({ where: { id } });
    if (!token || token.userId !== request.viewer!.id) return sendError(reply, 'NOT_FOUND', 'Token not found');
    if (token.revokedAt !== null) return publicToken(token);
    const updated = await prisma.userToken.update({ where: { id }, data: { revokedAt: new Date() } });
    invalidateUserTokenCache(token.tokenHash);
    return publicToken(updated);
  });
}
