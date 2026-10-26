import { FastifyRequest, FastifyReply } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { hashApiKey, isAgentToken, isWellFormedAgentToken, isUserToken, isWellFormedUserToken, bearerToken } from '../lib/apikey.js';
import { sessionCookieToken, renewSessionIfNeeded } from '../lib/session.js';
import { TtlCache } from '../lib/ttl-cache.js';
import { accessFix, accessFixData } from '../lib/access.js';

// Per-process caches so the page's polling does not cost a user lookup per
// request. Short TTLs bound how long a disabled agent or deleted user keeps
// working: at most AGENT_TTL_MS / USER_TTL_MS after the change (per instance).
const AGENT_TTL_MS = 15_000;
const USER_TTL_MS = 30_000;
// Shorter than a cookie's: a revoked token has to stop working promptly, and a
// token is the credential most likely to be sitting in a config file somewhere.
// This is also the bound on revocation. The revoking instance drops its own
// entry at once, but each cache is per-process, so another instance that
// authenticated the token just before can keep accepting it for up to this
// long. Same tradeoff the agent and user caches already make; killing it needs
// shared invalidation, not a shorter number.
const USER_TOKEN_TTL_MS = 10_000;
const agentCache = new TtlCache<AgentAuth | null>(1000);
const userCache = new TtlCache<UserAuth | null>(5000);
const userTokenCache = new TtlCache<string | null>(1000); // token hash -> owner id, or null for "no live token"

// How often a token's lastUsedAt is written. A poller hits the API every few
// seconds and the column is only ever read by a human deciding whether a token
// is still in use, so a write per request would be pure load for no extra
// information.
const TOKEN_LAST_USED_WRITE_MS = 60_000;
const tokenLastUsedWrittenAt = new Map<string, number>();

/** Forget a cached agent (call after disable/rotate so the change is immediate on this instance). */
export function invalidateAgentCache(apiKeyHash?: string) {
  if (apiKeyHash) agentCache.delete(apiKeyHash); else agentCache.clear();
}
export function invalidateUserCache(userId: string) {
  userCache.delete(userId);
}
/** Forget a cached personal access token (call after revoking one so it stops working immediately on this instance). */
export function invalidateUserTokenCache(tokenHash?: string) {
  if (tokenHash) {
    userTokenCache.delete(tokenHash);
    tokenLastUsedWrittenAt.delete(tokenHash);
  }
  else {
    userTokenCache.clear();
    tokenLastUsedWrittenAt.clear();
  }
}

export interface UserAuth {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  role: 'USER' | 'ADMIN';
  status: 'PENDING' | 'APPROVED' | 'DENIED';
}

export interface AgentAuth {
  id: string;
  name: string;
  enabled: boolean;
}

declare module 'fastify' {
  interface FastifyRequest {
    viewer?: UserAuth;
    agent?: AgentAuth;
    // How the viewer proved who they are. Token-authenticated callers are the
    // same human with the same reach, except that they may not manage tokens.
    viewerAuth?: 'cookie' | 'token';
  }
}

// Resolve an agent bearer token to an agent, or a typed error. Single choke
// point so every guard agrees on what a valid key is.
export async function resolveAgentToken(
  token: string,
): Promise<{ agent: AgentAuth } | { errorCode: 'UNAUTHORIZED' | 'FORBIDDEN'; message: string }> {
  if (!isWellFormedAgentToken(token)) {
    return { errorCode: 'UNAUTHORIZED', message: 'Invalid API key' };
  }
  const hashed = hashApiKey(token);
  let agent = agentCache.get(hashed);
  if (agent === undefined) {
    agent = await prisma.agent.findUnique({
      where: { apiKeyHash: hashed },
      select: { id: true, name: true, enabled: true },
    });
    agentCache.set(hashed, agent, AGENT_TTL_MS);
  }
  if (!agent) return { errorCode: 'UNAUTHORIZED', message: 'Invalid API key' };
  if (!agent.enabled) return { errorCode: 'FORBIDDEN', message: 'Agent is disabled' };
  return { agent };
}

const USER_AUTH_SELECT = { id: true, email: true, name: true, avatarUrl: true, role: true, status: true } as const;

/**
 * Bring a row whose role and access status disagree with each other, or with
 * ADMIN_EMAILS / the auto-approve lists, back in line — lib/access.ts has the
 * rules and why a row can end up saying ADMIN + PENDING, which otherwise wedges
 * that account on the waiting page for good. This runs on a cache miss only and
 * writes only when there is something to fix, so the ordinary path is one
 * comparison. The waiting page's own /auth/me polling is what triggers it: the
 * page lets itself in within USER_TTL_MS of the config or the row changing, with
 * no new sign-in.
 */
async function healAccess(user: UserAuth, request: FastifyRequest): Promise<UserAuth> {
  const fix = accessFix(user);
  if (!fix) return user;
  try {
    const healed = await prisma.user.update({ where: { id: user.id }, data: accessFixData(fix), select: USER_AUTH_SELECT });
    request.log.info({ userId: user.id, fix }, 'Access reconciled with the configured role and approval rules');
    return healed;
  } catch (err) {
    // The row moved, or the write failed. For these three cases the config and
    // the role decide, not the row, so honour the fix for this request and let
    // the next cache miss try the write again.
    request.log.warn({ err, userId: user.id }, 'Could not persist the access fix; applying it for this request only');
    return { ...user, ...fix };
  }
}

/**
 * Resolve a pk_pop_ bearer token to the human who owns it. The token proves
 * only which account is calling: whether that account may do anything is
 * requireUser's question, asked of the same row a cookie would have loaded, so
 * a token cannot outrun an approval that was revoked.
 */
async function resolveUserToken(token: string, request: FastifyRequest): Promise<UserAuth | 'invalid'> {
  if (!isWellFormedUserToken(token)) return 'invalid';
  const hashed = hashApiKey(token);

  let ownerId = userTokenCache.get(hashed);
  if (ownerId === undefined) {
    const row = await prisma.userToken.findUnique({ where: { tokenHash: hashed }, select: { userId: true, revokedAt: true } });
    ownerId = row && row.revokedAt === null ? row.userId : null;
    userTokenCache.set(hashed, ownerId, USER_TOKEN_TTL_MS);
  }
  if (ownerId === null) return 'invalid';

  let user = userCache.get(ownerId);
  if (user === undefined) {
    user = await prisma.user.findUnique({ where: { id: ownerId }, select: USER_AUTH_SELECT });
    if (user) user = await healAccess(user, request);
    userCache.set(ownerId, user, USER_TTL_MS);
  }
  // The row is gone but the token's foreign key cascades on delete, so this
  // only happens in the window between the two reads. Treat it as invalid.
  if (!user) return 'invalid';

  // Throttled: see TOKEN_LAST_USED_WRITE_MS. Not awaited — nothing the caller
  // asked for depends on it, and a failed write costs only a stale column.
  const now = Date.now();
  const lastWrite = tokenLastUsedWrittenAt.get(hashed) ?? 0;
  if (now - lastWrite >= TOKEN_LAST_USED_WRITE_MS) {
    tokenLastUsedWrittenAt.set(hashed, now);
    prisma.userToken
      .update({ where: { tokenHash: hashed }, data: { lastUsedAt: new Date(now) } })
      .catch((err: unknown) => request.log.warn({ err }, 'Could not record a token\'s lastUsedAt'));
  }
  return user;
}

async function resolveSessionCookie(request: FastifyRequest, reply: FastifyReply): Promise<UserAuth | 'missing' | 'invalid' | 'unknown-user'> {
  const token = sessionCookieToken(request.cookies);
  if (!token) return 'missing';
  let decoded: { sub: string; exp?: number };
  try {
    decoded = request.server.jwt.verify<{ sub: string; exp?: number }>(token);
  } catch {
    return 'invalid';
  }
  let user = userCache.get(decoded.sub);
  if (user === undefined) {
    user = await prisma.user.findUnique({ where: { id: decoded.sub }, select: USER_AUTH_SELECT });
    if (user) user = await healAccess(user, request);
    userCache.set(decoded.sub, user, USER_TTL_MS);
  }
  if (!user) return 'unknown-user';
  renewSessionIfNeeded(request, reply, decoded);
  return user;
}

/**
 * Who is signed in, whatever their approval status. Only /api/auth/me and
 * logout use this directly: a PENDING user needs to see that they are pending.
 * Everything else goes through requireUser, which also demands APPROVED.
 */
export async function requireSession(request: FastifyRequest, reply: FastifyReply) {
  if (request.viewer) return;
  const headerToken = bearerToken(request.headers.authorization);
  if (isAgentToken(headerToken)) {
    return sendError(reply, 'UNAUTHORIZED', 'Agent keys cannot access user endpoints');
  }
  // A personal access token stands in for the cookie a non-browser caller does
  // not have. Checked before the cookie so a request carrying both is the
  // caller the header names, which is what an explicit credential should mean.
  if (isUserToken(headerToken)) {
    const viaToken = await resolveUserToken(headerToken, request);
    if (viaToken === 'invalid') return sendError(reply, 'UNAUTHORIZED', 'Invalid access token');
    request.viewer = viaToken;
    request.viewerAuth = 'token';
    return;
  }
  const result = await resolveSessionCookie(request, reply);
  if (result === 'missing') return sendError(reply, 'UNAUTHORIZED', 'Missing authorization token');
  if (result === 'invalid') return sendError(reply, 'UNAUTHORIZED', 'Invalid token');
  if (result === 'unknown-user') return sendError(reply, 'UNAUTHORIZED', 'User not found');
  request.viewer = result;
  request.viewerAuth = 'cookie';
}

/** Human endpoints: valid session cookie for an APPROVED user; agent keys are rejected. */
export async function requireUser(request: FastifyRequest, reply: FastifyReply) {
  await requireSession(request, reply);
  if (!request.viewer) return; // requireSession already replied
  const { status } = request.viewer;
  if (status === 'APPROVED') return;
  request.viewer = undefined; // no route may act for an unapproved user
  request.viewerAuth = undefined;
  return status === 'PENDING'
    ? sendError(reply, 'PENDING_APPROVAL', 'Your account is waiting for an admin to approve it')
    : sendError(reply, 'ACCESS_DENIED', 'Your account has been denied access');
}

/**
 * An approved human on a session cookie, never on a token. Guards the routes a
 * token must not reach: token management, and (through requireAdmin) the admin
 * routes. A stolen token can then ask questions as its owner, but it cannot
 * mint itself a successor, revoke the one the owner would use to kill it, or —
 * if its owner happens to be an admin — issue an agent key or promote an
 * account and so outlive its own revocation.
 */
export async function requireCookieUser(request: FastifyRequest, reply: FastifyReply) {
  await requireUser(request, reply);
  if (!request.viewer) return; // requireUser already replied
  if (request.viewerAuth !== 'cookie') {
    request.viewer = undefined;
    request.viewerAuth = undefined;
    return sendError(reply, 'FORBIDDEN', 'Access tokens cannot be used here — sign in on the site');
  }
}

/** Agent endpoints: valid sk_pop_ bearer key required; cookies are ignored. */
export async function requireAgent(request: FastifyRequest, reply: FastifyReply) {
  if (request.agent) return;
  const token = bearerToken(request.headers.authorization);
  if (!isAgentToken(token)) {
    return sendError(reply, 'UNAUTHORIZED', 'Missing or invalid agent API key');
  }
  const result = await resolveAgentToken(token);
  if ('errorCode' in result) return sendError(reply, result.errorCode, result.message);
  request.agent = result.agent;
}

/** Either a session cookie or an agent key. Sets request.viewer OR request.agent, never both. */
export async function requireUserOrAgent(request: FastifyRequest, reply: FastifyReply) {
  if (request.viewer || request.agent) return;
  const headerToken = bearerToken(request.headers.authorization);
  if (isAgentToken(headerToken)) {
    const result = await resolveAgentToken(headerToken);
    if ('errorCode' in result) return sendError(reply, result.errorCode, result.message);
    request.agent = result.agent;
    return;
  }
  return requireUser(request, reply);
}

/** Approved session cookie if present; never fails. Skips agent requests entirely. */
export async function optionalUser(request: FastifyRequest, reply: FastifyReply) {
  if (request.viewer) return;
  const headerToken = bearerToken(request.headers.authorization);
  if (isAgentToken(headerToken)) return;
  if (isUserToken(headerToken)) {
    const viaToken = await resolveUserToken(headerToken, request);
    if (viaToken !== 'invalid' && viaToken.status === 'APPROVED') {
      request.viewer = viaToken;
      request.viewerAuth = 'token';
    }
    return;
  }
  const result = await resolveSessionCookie(request, reply);
  if (typeof result !== 'string' && result.status === 'APPROVED') {
    request.viewer = result;
    request.viewerAuth = 'cookie';
  }
}

/**
 * Admin endpoints: an approved ADMIN on a session cookie, never on a token.
 *
 * Cookie-only for the same reason token management is. An admin's routes mint
 * credentials that outlive the caller: POST /api/agents issues an sk_pop_ key,
 * and PATCH /api/users/:id can promote an account to ADMIN. Either one survives
 * revoking the token that created it, so letting a token reach them would leave
 * a stolen token able to entrench itself after all — exactly what
 * requireCookieUser exists to prevent. Nothing needs them without a browser:
 * they are the /admin pages, and the shell scripts go via the database.
 */
export async function requireAdmin(request: FastifyRequest, reply: FastifyReply) {
  await requireCookieUser(request, reply);
  if (!request.viewer) return; // requireCookieUser already replied
  if (request.viewer.role !== 'ADMIN') {
    return sendError(reply, 'FORBIDDEN', 'Admin access required');
  }
}
