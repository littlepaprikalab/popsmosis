import type { FastifyRequest } from 'fastify';

/**
 * Rate limits for the endpoints anyone may call with no sign-in: the Droplets tab
 * (docs/PLAN.md, section 10).
 *
 * The API's own limit keys a request by its agent key, then by the session cookie's
 * subject, then by its IP. None of that works here. The cookie is not verified by the
 * limiter, so a made-up one would be a fresh key each time. And a browser's request
 * comes through the web app's proxy, so its IP is the web server's: every visitor
 * would share one bucket. So a public request is keyed by the visitor's address as
 * the edge in front of the web app saw it, and all of them together also share one
 * ceiling per API instance, which a forged address cannot get around.
 */

export const PUBLIC_PER_CLIENT_PER_MINUTE = 60;
export const PUBLIC_PER_INSTANCE_PER_MINUTE = 1200;

const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value)?.split(',')[0]?.trim();

/**
 * Who is asking. The forwarded headers are believed only on a request that came
 * through the web app (it carries the proxy secret), where the edge set them;
 * Cloudflare's and Render's own header win over X-Forwarded-For, which a client
 * can start for itself. Anything else is keyed by the address that connected.
 */
export function clientAddress(request: FastifyRequest, proxySecret = process.env.PROXY_SECRET): string {
  const proxied = !!proxySecret && request.headers['x-proxy-token'] === proxySecret;
  // The web app's middleware (web/src/middleware.ts) reads the visitor's address where the edge first set it and
  // passes it on: when the web app reaches the API by its public URL, the edge's own headers on this hop name the web
  // server instead. The edge's headers here are the fallback for a web app older than that.
  const forwarded = proxied
    ? first(request.headers['x-popsmosis-client-ip']) || first(request.headers['cf-connecting-ip'])
      || first(request.headers['true-client-ip']) || first(request.headers['x-forwarded-for'])
    : undefined;
  return forwarded || request.ip;
}

export function publicClientKey(request: FastifyRequest, proxySecret = process.env.PROXY_SECRET): string {
  return `public:${clientAddress(request, proxySecret)}`;
}

/** A fixed one-minute window shared by every public request on this instance. */
export class WindowCounter {
  private windowStart = 0;
  private count = 0;

  constructor(private readonly max: number, private readonly windowMs = 60_000) {}

  /** True when this request fits in the current window. */
  take(now = Date.now()): boolean {
    if (now - this.windowStart >= this.windowMs) {
      this.windowStart = now;
      this.count = 0;
    }
    if (this.count >= this.max) return false;
    this.count += 1;
    return true;
  }
}
