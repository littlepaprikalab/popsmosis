/**
 * Who a request is counted as by the API's rate limit (index.ts). Browsers all reach the API through the web app's
 * proxy, so keying anonymous requests by the connecting address put every visitor in one bucket, and anyone could
 * use up sign-in for everyone. And keying by a cookie or key nobody checked gave a made-up one a fresh bucket on
 * every request.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';

describe.runIf(RUN)('rate-limit keys', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./lib/prisma.js'))['default'];
  const tag = `rl-${Date.now()}`;
  const SECRET = 'rate-limit-proxy-secret';
  let prev: string | undefined;
  let agentKey = '';

  // A request as the web app's proxy forwards it: its secret, and the visitor's address as the edge saw it.
  const viaProxy = (address: string, extra: Record<string, string> = {}) => ({ 'x-proxy-token': SECRET, 'cf-connecting-ip': address, ...extra });
  const signIn = (address: string) =>
    app.inject({ method: 'POST', url: '/api/auth/google', headers: viaProxy(address), payload: {} });
  const me = (headers: Record<string, string>) => app.inject({ method: 'GET', url: '/api/auth/me', headers });

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prev = process.env.PROXY_SECRET;
    process.env.PROXY_SECRET = SECRET;
    prisma = (await import('./lib/prisma.js')).default;
    app = await (await import('./index.js')).buildApp();
    await app.ready();
    const { generateApiKey } = await import('./lib/apikey.js');
    const k = generateApiKey();
    agentKey = k.raw;
    await prisma.agent.create({ data: { name: `${tag}-agent`, apiKeyHash: k.hashed } });
  });

  afterAll(async () => {
    await prisma.agent.deleteMany({ where: { name: { startsWith: tag } } });
    await app.close();
    if (prev === undefined) delete process.env.PROXY_SECRET; else process.env.PROXY_SECRET = prev;
  });

  it('gives each visitor their own sign-in limit, so one cannot lock everyone out', async () => {
    for (let i = 0; i < 30; i++) expect((await signIn('203.0.113.1')).statusCode).toBe(400); // no code: refused, but counted
    expect((await signIn('203.0.113.1')).statusCode).toBe(429);
    expect((await signIn('203.0.113.2')).statusCode).toBe(400); // another visitor is untouched
  });

  it('gives a forged session cookie no bucket of its own', async () => {
    // A well-formed HS256 token, each with a different subject, signed with a secret that is not the API's.
    const { createHmac } = await import('crypto');
    const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url');
    const forged = (i: number) => {
      const head = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: `made-up-${i}`, exp: Math.floor(Date.now() / 1000) + 3600 })}`;
      return `popsmosis_session=${head}.${createHmac('sha256', 'not-the-secret').update(head).digest('base64url')}`;
    };
    const codes: number[] = [];
    for (let i = 0; i < 121; i++) codes.push((await me(viaProxy('198.51.100.7', { cookie: forged(i) }))).statusCode);
    expect(codes.slice(0, 120).every((c) => c === 401)).toBe(true);
    expect(codes[120]).toBe(429);
  });

  it('gives a made-up agent key no bucket of its own, nor a way past the proxy', async () => {
    const codes: number[] = [];
    for (let i = 0; i < 121; i++) {
      codes.push((await me(viaProxy('198.51.100.8', { authorization: `Bearer sk_pop_${i}` }))).statusCode);
    }
    expect(codes[120]).toBe(429);

    // A key in the right shape that is no agent's is refused at the gate, even on a page anyone may read.
    const fake = `Bearer sk_pop_${'e'.repeat(64)}`;
    const direct = await app.inject({ method: 'GET', url: '/api/droplets', headers: { authorization: fake } });
    expect(direct.statusCode).toBe(401);
    // The real key still reaches the API directly.
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: { authorization: `Bearer ${agentKey}` } })).statusCode).toBe(200);
  });

  it('answers a database blip on the key lookup with a 5xx the studio retries, never a 401 that stops the run', async () => {
    const { generateApiKey } = await import('./lib/apikey.js');
    const k = generateApiKey(); // never looked up, so not cached
    await prisma.agent.create({ data: { name: `${tag}-blip`, apiKeyHash: k.hashed } });
    const { vi } = await import('vitest');
    const spy = vi.spyOn(prisma.agent, 'findUnique').mockRejectedValue(new Error('connection terminated unexpectedly'));
    try {
      const res = await app.inject({ method: 'GET', url: '/api/agents/me', headers: { authorization: `Bearer ${k.raw}` } });
      expect(res.statusCode).toBeGreaterThanOrEqual(500);
    } finally {
      spy.mockRestore();
    }
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: { authorization: `Bearer ${k.raw}` } })).statusCode).toBe(200);
  });
});
