/**
 * Personal access tokens against a real Postgres (DATABASE_URL). Skipped
 * unless INTEGRATION=1, like the other integration files.
 *
 * Covers: minting -> the token acting as its owner on the human endpoints ->
 * the limits that keep it from being more than its owner (no token
 * management, no agent endpoints, no unapproved account) -> revocation.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('personal access tokens', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./../lib/prisma.js'))['default'];
  let cookieA: string;
  let cookiePending: string;
  let agentKey: string;
  let userA: { id: string };
  let pending: { id: string };
  const tag = `tok-${Date.now()}`;

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const asToken = (token: string) => ({ authorization: `Bearer ${token}` });

  /** Mint through the API the way the site does, and hand back the raw token. */
  const mintToken = async (cookie: string, name: string): Promise<string> => {
    const r = await app.inject({ method: 'POST', url: '/api/tokens', headers: asUser(cookie), payload: { name } });
    expect(r.statusCode).toBe(201);
    return r.json().token as string;
  };

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    process.env.JWT_SECRET ||= 'integration-test-secret';
    const { buildApp } = await import('./../index.js');
    const { generateApiKey } = await import('./../lib/apikey.js');
    prisma = (await import('./../lib/prisma.js')).default;
    app = await buildApp();
    await app.ready();

    const approved = { status: 'APPROVED' as const, reviewedAt: new Date() };
    userA = await prisma.user.create({ data: { email: `${tag}-a@example.com`, googleId: `${tag}-a`, name: 'Ada', ...approved } });
    pending = await prisma.user.create({ data: { email: `${tag}-p@example.com`, googleId: `${tag}-p`, name: 'Pat' } });
    const { SESSION_EXPIRES_IN } = await import('./../lib/session.js');
    const mk = (id: string) => `zoey_session=${app.jwt.sign({ sub: id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    cookieA = mk(userA.id);
    cookiePending = mk(pending.id);

    const k = generateApiKey();
    agentKey = k.raw;
    await prisma.agent.create({ data: { name: `${tag}-agent`, apiKeyHash: k.hashed } });
  });

  afterAll(async () => {
    await prisma.user.deleteMany({ where: { email: { startsWith: tag } } });
    await prisma.agent.deleteMany({ where: { name: { startsWith: tag } } });
    await app.close();
    await prisma.$disconnect();
  });

  it('mints a token, shows the raw value exactly once, and stores only its hash', async () => {
    const r = await app.inject({ method: 'POST', url: '/api/tokens', headers: asUser(cookieA), payload: { name: 'zed panel' } });
    expect(r.statusCode).toBe(201);
    const raw = r.json().token as string;
    expect(raw).toMatch(/^pk_zoey_[0-9a-f]{64}$/);

    const listed = await app.inject({ method: 'GET', url: '/api/tokens', headers: asUser(cookieA) });
    expect(listed.statusCode).toBe(200);
    const row = listed.json().data.find((t: { id: string }) => t.id === r.json().id);
    expect(row.name).toBe('zed panel');
    expect(row.revokedAt).toBeNull();
    // The raw token is never served again, under any key.
    expect(JSON.stringify(listed.json())).not.toContain(raw);

    const stored = await prisma.userToken.findUnique({ where: { id: r.json().id } });
    expect(stored!.tokenHash).not.toBe(raw);
    expect(stored!.tokenHash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('validates the token name', async () => {
    expect((await app.inject({ method: 'POST', url: '/api/tokens', headers: asUser(cookieA), payload: { name: 'x' } })).statusCode).toBe(400);
    expect((await app.inject({ method: 'POST', url: '/api/tokens', headers: asUser(cookieA), payload: {} })).statusCode).toBe(400);
  });

  it('lets a token ask and read as its owner', async () => {
    const token = await mintToken(cookieA, 'asker');

    const me = await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken(token) });
    expect(me.statusCode).toBe(200);
    expect(me.json().id).toBe(userA.id);
    expect(me.json().auth).toBe('token');

    // A write with no cookie carries no CSRF header either: the bearer header
    // is the same-origin signal, so this must not be caught by that hook.
    const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asToken(token), payload: { content: `${tag} what does the token path do?` } });
    expect(asked.statusCode).toBe(201);
    expect(asked.json().user.id).toBe(userA.id);
    expect(asked.json().isOwner).toBe(true);

    // ...and the cookie session sees the same question as its own.
    const mine = await app.inject({ method: 'GET', url: '/api/questions?mine=true', headers: asUser(cookieA) });
    expect(mine.json().data.some((q: { id: string }) => q.id === asked.json().id)).toBe(true);
  });

  it('records lastUsedAt so a stale token is visible', async () => {
    const token = await mintToken(cookieA, 'used');
    expect((await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken(token) })).statusCode).toBe(200);
    // The write is fire-and-forget, so give it a turn of the loop to land.
    await new Promise((r) => setTimeout(r, 120));
    const listed = await app.inject({ method: 'GET', url: '/api/tokens', headers: asUser(cookieA) });
    const row = listed.json().data.find((t: { name: string }) => t.name === 'used');
    expect(row.lastUsedAt).toBeTruthy();
  });

  it('refuses a token that tries to manage tokens', async () => {
    const token = await mintToken(cookieA, 'self-serving');
    expect((await app.inject({ method: 'GET', url: '/api/tokens', headers: asToken(token) })).statusCode).toBe(403);
    expect((await app.inject({ method: 'POST', url: '/api/tokens', headers: asToken(token), payload: { name: 'successor' } })).statusCode).toBe(403);
    const victim = await app.inject({ method: 'GET', url: '/api/tokens', headers: asUser(cookieA) });
    const anyId = victim.json().data[0].id;
    expect((await app.inject({ method: 'DELETE', url: `/api/tokens/${anyId}`, headers: asToken(token) })).statusCode).toBe(403);
  });

  it("refuses an admin's token on the admin routes, so it cannot outlive its own revocation", async () => {
    // The routes that mint credentials outlasting the caller: an agent key, and
    // a promotion to ADMIN. Both survive revoking the token that made them, so a
    // token must not reach either however privileged its owner is.
    const admin = await prisma.user.create({
      data: { email: `${tag}-adm@example.com`, googleId: `${tag}-adm`, name: 'Ada', role: 'ADMIN', status: 'APPROVED', reviewedAt: new Date() },
    });
    const { SESSION_EXPIRES_IN } = await import('./../lib/session.js');
    const adminCookie = `zoey_session=${app.jwt.sign({ sub: admin.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    const adminToken = await mintToken(adminCookie, 'admin token');

    // The token is genuinely that admin — it just may not act as one.
    const me = await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken(adminToken) });
    expect(me.json().id).toBe(admin.id);
    expect(me.json().role).toBe('ADMIN');

    for (const call of [
      { method: 'GET' as const, url: '/api/agents' },
      { method: 'GET' as const, url: '/api/users' },
      { method: 'GET' as const, url: '/api/users/pending-count' },
    ]) {
      expect((await app.inject({ ...call, headers: asToken(adminToken) })).statusCode).toBe(403);
    }
    // The two that would entrench a stolen token.
    expect((await app.inject({ method: 'POST', url: '/api/agents', headers: asToken(adminToken), payload: { name: `${tag}-sneaky` } })).statusCode).toBe(403);
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${userA.id}`, headers: asToken(adminToken), payload: { role: 'ADMIN' } })).statusCode).toBe(403);
    expect((await prisma.agent.findUnique({ where: { name: `${tag}-sneaky` } }))).toBeNull();
    expect((await prisma.user.findUnique({ where: { id: userA.id } }))!.role).toBe('USER');

    // The same admin on a cookie is unaffected.
    expect((await app.inject({ method: 'GET', url: '/api/agents', headers: asUser(adminCookie) })).statusCode).toBe(200);
  });

  it('refuses a token on agent endpoints, and an agent key on the token endpoint', async () => {
    const token = await mintToken(cookieA, 'not-an-agent');
    expect((await app.inject({ method: 'GET', url: '/api/questions/queue', headers: asToken(token) })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asToken(token) })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken(agentKey) })).statusCode).toBe(401);
  });

  it('rejects malformed and unknown tokens', async () => {
    expect((await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken('pk_zoey_nope') })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken(`pk_zoey_${'f'.repeat(64)}`) })).statusCode).toBe(401);
  });

  it('gets an unapproved owner nowhere', async () => {
    // Minted while approved, then the account is put back in the queue: the
    // token must follow the account, not the state it was minted in.
    await prisma.user.update({ where: { id: pending.id }, data: { status: 'APPROVED', reviewedAt: new Date() } });
    const token = await mintToken(cookiePending, 'demoted');
    expect((await app.inject({ method: 'GET', url: '/api/questions', headers: asToken(token) })).statusCode).toBe(200);

    await prisma.user.update({ where: { id: pending.id }, data: { status: 'DENIED', reviewedAt: new Date() } });
    const { invalidateUserCache } = await import('./../middleware/auth.js');
    invalidateUserCache(pending.id); // the row's cache TTL, skipped so the test is not a 30s wait
    const after = await app.inject({ method: 'GET', url: '/api/questions', headers: asToken(token) });
    expect(after.statusCode).toBe(403);
    expect(after.json().error.code).toBe('ACCESS_DENIED');
  });

  it('stops working the moment it is revoked, and revoking is idempotent', async () => {
    const token = await mintToken(cookieA, 'doomed');
    expect((await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken(token) })).statusCode).toBe(200);

    const listed = await app.inject({ method: 'GET', url: '/api/tokens', headers: asUser(cookieA) });
    const id = listed.json().data.find((t: { name: string }) => t.name === 'doomed').id;

    const revoked = await app.inject({ method: 'DELETE', url: `/api/tokens/${id}`, headers: asUser(cookieA) });
    expect(revoked.statusCode).toBe(200);
    expect(revoked.json().revokedAt).toBeTruthy();
    // No TTL wait: the route drops the cache entry itself.
    expect((await app.inject({ method: 'GET', url: '/api/tokens/me', headers: asToken(token) })).statusCode).toBe(401);

    const again = await app.inject({ method: 'DELETE', url: `/api/tokens/${id}`, headers: asUser(cookieA) });
    expect(again.statusCode).toBe(200);
    expect(again.json().revokedAt).toBe(revoked.json().revokedAt);
    // Revoked, but still listed under its name so it can be told apart.
    const after = await app.inject({ method: 'GET', url: '/api/tokens', headers: asUser(cookieA) });
    expect(after.json().data.some((t: { id: string }) => t.id === id)).toBe(true);
  });

  it("will not let one user see or revoke another's token", async () => {
    const other = await prisma.user.create({ data: { email: `${tag}-o@example.com`, googleId: `${tag}-o`, name: 'Otto', status: 'APPROVED', reviewedAt: new Date() } });
    const { SESSION_EXPIRES_IN } = await import('./../lib/session.js');
    const cookieOther = `zoey_session=${app.jwt.sign({ sub: other.id }, { expiresIn: SESSION_EXPIRES_IN })}`;

    const mineList = await app.inject({ method: 'GET', url: '/api/tokens', headers: asUser(cookieA) });
    const someoneElsesId = mineList.json().data[0].id;

    const theirList = await app.inject({ method: 'GET', url: '/api/tokens', headers: asUser(cookieOther) });
    expect(theirList.json().data).toHaveLength(0);
    // Not 403: whether that id exists at all is not theirs to learn.
    expect((await app.inject({ method: 'DELETE', url: `/api/tokens/${someoneElsesId}`, headers: asUser(cookieOther) })).statusCode).toBe(404);
  });
});
