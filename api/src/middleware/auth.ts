import { FastifyRequest, FastifyReply } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { hashApiKey, isAgentToken, isWellFormedAgentToken, bearerToken } from '../lib/apikey.js';
import { sessionCookieToken, renewSessionIfNeeded } from '../lib/session.js';
import { TtlCache } from '../lib/ttl-cache.js';
import { needsAdminRole } from '../lib/access.js';

// Per-process caches so the page's polling does not cost a user lookup per
// request. Short TTLs bound how long a disabled agent or deleted user keeps
// working: at most AGENT_TTL_MS / USER_TTL_MS after the change (per instance).
const AGENT_TTL_MS = 15_000;
const USER_TTL_MS = 30_000;
const agentCache = new TtlCache<AgentAuth | null>(1000);
const userCache = new TtlCache<UserAuth | null>(5000);

/** Forget a cached agent (call after disable/rotate so the change is immediate on this instance). */
export function invalidateAgentCache(apiKeyHash?: string) {
  if (apiKeyHash) agentCache.delete(apiKeyHash); else agentCache.clear();
}
export function invalidateUserCache(userId: string) {
  userCache.delete(userId);
}

export interface UserAuth {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  role: 'USER' | 'ADMIN';
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

const USER_AUTH_SELECT = { id: true, email: true, name: true, avatarUrl: true, role: true } as const;

/**
 * Give an ADMIN_EMAILS address the role the config says it has (lib/access.ts),
 * so adding an address takes effect on a session that is already open, with no
 * new sign-in. This runs on a cache miss only and writes only when there is
 * something to fix, so the ordinary path is one comparison.
 */
async function healRole(user: UserAuth, request: FastifyRequest): Promise<UserAuth> {
  if (!needsAdminRole(user)) return user;
  try {
    const healed = await prisma.user.update({ where: { id: user.id }, data: { role: 'ADMIN' }, select: USER_AUTH_SELECT });
    request.log.info({ userId: user.id }, 'Granted the admin role from ADMIN_EMAILS');
    return healed;
  } catch (err) {
    // The row moved, or the write failed. The config decides, not the row, so
    // honour it for this request and let the next cache miss try the write again.
    request.log.warn({ err, userId: user.id }, 'Could not persist the admin role from ADMIN_EMAILS; applying it for this request only');
    return { ...user, role: 'ADMIN' };
  }
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
    if (user) user = await healRole(user, request);
    userCache.set(decoded.sub, user, USER_TTL_MS);
  }
  if (!user) return 'unknown-user';
  renewSessionIfNeeded(request, reply, decoded);
  return user;
}

/** Human endpoints: a valid session cookie; agent keys are rejected. */
export async function requireUser(request: FastifyRequest, reply: FastifyReply) {
  if (request.viewer) return;
  if (isAgentToken(bearerToken(request.headers.authorization))) {
    return sendError(reply, 'UNAUTHORIZED', 'Agent keys cannot access user endpoints');
  }
  const result = await resolveSessionCookie(request, reply);
  if (result === 'missing') return sendError(reply, 'UNAUTHORIZED', 'Missing authorization token');
  if (result === 'invalid') return sendError(reply, 'UNAUTHORIZED', 'Invalid token');
  if (result === 'unknown-user') return sendError(reply, 'UNAUTHORIZED', 'User not found');
  request.viewer = result;
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

/** Session cookie if present; never fails. Skips agent requests entirely. */
export async function optionalUser(request: FastifyRequest, reply: FastifyReply) {
  if (request.viewer) return;
  if (isAgentToken(bearerToken(request.headers.authorization))) return;
  const result = await resolveSessionCookie(request, reply);
  if (typeof result !== 'string') request.viewer = result;
}

/** Admin endpoints: a signed-in ADMIN. */
export async function requireAdmin(request: FastifyRequest, reply: FastifyReply) {
  await requireUser(request, reply);
  if (!request.viewer) return; // requireUser already replied
  if (request.viewer.role !== 'ADMIN') {
    return sendError(reply, 'FORBIDDEN', 'Admin access required');
  }
}
