import { FastifyReply, FastifyRequest } from 'fastify';

export const SESSION_COOKIE = 'popsmosis_session';

type RequestCookies = Record<string, string | undefined> | undefined;

/** The session token on a request. */
export function sessionCookieToken(cookies: RequestCookies): string | undefined {
  return cookies?.[SESSION_COOKIE];
}

/**
 * Whether the request carries a session cookie at all. Every check that asks
 * "is this request cookie-authenticated?" — CSRF, rate-limit tier — goes
 * through this, so they all agree on what a cookie request is.
 */
export function hasSessionCookie(cookies: RequestCookies): boolean {
  return sessionCookieToken(cookies) !== undefined;
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

export function clearSessionCookie(reply: FastifyReply) {
  reply.clearCookie(SESSION_COOKIE, { path: '/api' });
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
