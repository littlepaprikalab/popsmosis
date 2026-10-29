import { createHmac, randomBytes } from 'crypto';
import { isIPv6 } from 'net';
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

/**
 * The address a limit counts a visitor by: an IPv4 address as it is, an IPv6 address by its /64, the block one home
 * or one network is given. Keyed by the whole IPv6 address, one machine could take a fresh key for every request.
 */
export function addressGroup(address: string): string {
  const v4 = /^(?:::ffff:)?(\d{1,3}(?:\.\d{1,3}){3})$/i.exec(address);
  if (v4) return v4[1];
  if (!isIPv6(address)) return address;
  const [head, tail = ''] = address.toLowerCase().split('::');
  const left = head ? head.split(':') : [];
  const right = tail ? tail.split(':') : [];
  // An IPv4 tail ("::1.2.3.4") is two groups, past the /64 either way.
  const missing = 8 - left.length - right.length - right.filter((g) => g.includes('.')).length;
  const groups = address.includes('::') ? [...left, ...Array(Math.max(missing, 0)).fill('0'), ...right] : left;
  return `${groups.slice(0, 4).map((g) => g.replace(/^0+(?=.)/, '')).join(':')}::/64`;
}

/**
 * A fixed window per key: at most `max` takes for each key until the window ends, then all start again. A key is
 * kept only as a keyed hash of its parts, under a secret drawn afresh each window, so what it holds cannot be read
 * back to an address, nor matched across windows. At most `maxKeys` keys a window: past that, a new key is refused,
 * so a flood of new keys cannot grow it without bound.
 */
export class KeyedWindowCounter {
  private windowStart = 0;
  private counts = new Map<string, number>();
  private secret = randomBytes(32);

  constructor(private readonly max: number, private readonly windowMs: number, private readonly maxKeys = 100_000) {}

  /** True when this one fits under the key's limit in the current window. */
  take(parts: string[], now = Date.now()): boolean {
    if (now - this.windowStart >= this.windowMs) {
      this.windowStart = now;
      this.counts = new Map();
      this.secret = randomBytes(32);
    }
    const key = createHmac('sha256', this.secret).update(parts.join('\n')).digest('base64url');
    const n = this.counts.get(key) ?? 0;
    if (n >= this.max || (n === 0 && this.counts.size >= this.maxKeys)) return false;
    this.counts.set(key, n + 1);
    return true;
  }
}
