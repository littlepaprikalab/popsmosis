import { FastifyReply, FastifyRequest } from 'fastify';

export const SESSION_COOKIE = 'zoey_session';

/**
 * The cookie name used before the project was renamed. Read, never set: a
 * browser can hold one of these for up to SESSION_TTL_S after the rename, and
 * the token inside it still verifies, so honouring it is what keeps signed-in
 * people signed in across the rebrand. The first authenticated request that
 * carries one swaps it for SESSION_COOKIE (see migrateLegacySessionCookie) and
 * logout clears both. Drop this once SESSION_TTL_S has elapsed since the
 * rename, by which point no live cookie can carry the old name.
 */
export const LEGACY_SESSION_COOKIE = 'bong_session';

type RequestCookies = Record<string, string | undefined> | undefined;

/** The session token on a request, under either cookie name. */
export function sessionCookieToken(cookies: RequestCookies): string | undefined {
  return cookies?.[SESSION_COOKIE] ?? cookies?.[LEGACY_SESSION_COOKIE];
}

/**
 * Whether the request carries a session cookie at all. Every check that asks
 * "is this request cookie-authenticated?" — CSRF, rate-limit tier — must go
 * through this, or a request on the legacy name would slip past it.
 */
export function hasSessionCookie(cookies: RequestCookies): boolean {
  return sessionCookieToken(cookies) !== undefined;
}

/** True when the token came from the old cookie and needs moving across. */
export function usesLegacySessionCookie(cookies: RequestCookies): boolean {
  return cookies?.[SESSION_COOKIE] === undefined && cookies?.[LEGACY_SESSION_COOKIE] !== undefined;
}

// The JWT expiry and the cookie maxAge must stay in sync: the browser drops
// the cookie exactly when the token inside it would stop verifying.
//
// Sessions are sliding: every authenticated request past the halfway point
// re-issues a fresh 30-day token, so anyone who visits at least once every
// 30 days is never asked to sign in again. Only a full 30 days of absence, an
// explicit logout, or a deleted account ends a session.
export const SESSION_TTL_S = 30 * 24 * 60 * 60; // 30 days
export const SESSION_EXPIRES_IN = '30d';

// Sliding-session renewal kicks in once less than half the lifetime remains.
export const RENEW_THRESHOLD_S = SESSION_TTL_S / 2;

export function setSessionCookie(reply: FastifyReply, token: string) {
  reply.setCookie(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
    path: '/api',
    maxAge: SESSION_TTL_S,
  });
}

/**
 * Drop any session cookie already queued on this reply, so a later decision
 * about the session is the only one the browser sees.
 */
function dropQueuedSessionCookies(reply: FastifyReply) {
  const existing = reply.getHeader('set-cookie');
  if (!existing) return;
  const list = (Array.isArray(existing) ? existing : [existing]).map(String);
  const kept = list.filter(
    (c) => !c.startsWith(`${SESSION_COOKIE}=`) && !c.startsWith(`${LEGACY_SESSION_COOKIE}=`),
  );
  if (kept.length === list.length) return;
  reply.removeHeader('set-cookie');
  if (kept.length) reply.header('set-cookie', kept);
}

/**
 * Clears both names, so logout ends a session started under either one. Any
 * session cookie already queued goes first: a request that arrived on the
 * legacy name has a migration cookie pending (see migrateLegacySessionCookie),
 * and logout must not leave a freshly issued token beside the clear.
 */
export function clearSessionCookie(reply: FastifyReply) {
  dropQueuedSessionCookies(reply);
  reply.clearCookie(SESSION_COOKIE, { path: '/api' });
  reply.clearCookie(LEGACY_SESSION_COOKIE, { path: '/api' });
}

/**
 * Move a session started under the old cookie name onto the current one: issue
 * a fresh token under SESSION_COOKIE and drop the legacy cookie, so every later
 * request is an ordinary one. Best-effort for the same reason as renewal — the
 * request is already authenticated and must not fail because the swap did; the
 * next request simply tries again.
 */
export function migrateLegacySessionCookie(
  request: FastifyRequest,
  reply: FastifyReply,
  decoded: { sub: string },
) {
  try {
    const fresh = request.server.jwt.sign({ sub: decoded.sub }, { expiresIn: SESSION_EXPIRES_IN });
    setSessionCookie(reply, fresh);
    reply.clearCookie(LEGACY_SESSION_COOKIE, { path: '/api' });
  } catch (err) {
    request.log.warn({ err }, 'Could not move the session onto the current cookie name; keeping the existing one');
  }
}

// Re-issue the token once it is past the halfway point of its lifetime.
// Best-effort: the request is already authenticated, so a renewal failure
// must never fail the request.
export function renewSessionIfNeeded(
  request: FastifyRequest,
  reply: FastifyReply,
  decoded: { sub: string; exp?: number },
) {
  try {
    if (!decoded.exp) return;
    const remainingS = decoded.exp - Math.floor(Date.now() / 1000);
    if (remainingS > RENEW_THRESHOLD_S) return;
    const fresh = request.server.jwt.sign({ sub: decoded.sub }, { expiresIn: SESSION_EXPIRES_IN });
    setSessionCookie(reply, fresh);
  } catch (err) {
    request.log.warn({ err }, 'Session renewal failed; keeping existing token');
  }
}

// A response that sets a session cookie is per-user and must never be stored
// by a shared cache. Returns the Cache-Control value to enforce, or null.
export function cacheControlForCookieResponse(current: unknown): string | null {
  const cc = String(current ?? '');
  return /\bpublic\b|\bs-maxage\b/i.test(cc) ? 'no-store' : null;
}
