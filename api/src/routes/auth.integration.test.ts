/**
 * Google sign-in against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1, like the other integration files.
 *
 * Google itself is stubbed: the authorization code is the identity token's payload as JSON. Everything after it is real,
 * so this covers the route that turns a Google account into a user and a session: a first sign-in can use the site at
 * once, ADMIN_EMAILS makes an admin, and an account linked by email (a new googleId) still gets the role.
 */
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';

vi.mock('google-auth-library', () => ({
  OAuth2Client: class {
    async getToken(code: string) {
      return { tokens: { id_token: code } };
    }
    async verifyIdToken({ idToken }: { idToken: string }) {
      return { getPayload: () => JSON.parse(idToken) };
    }
  },
}));

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('Google sign-in', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./../lib/prisma.js'))['default'];
  const tag = `signin-${Date.now()}`;
  const before = { id: process.env.GOOGLE_CLIENT_ID, secret: process.env.GOOGLE_CLIENT_SECRET, admins: process.env.ADMIN_EMAILS };

  // `omit` leaves acceptTerms out of the body altogether, as an old web app would. `from` is the caller's address:
  // sign-in allows 10 tries a minute from one, and the refusals below would use up this file's share.
  const signIn = (payload: { sub: string; email: string; name?: string }, acceptTerms: unknown = true, from?: string) =>
    app.inject({
      method: 'POST', url: '/api/auth/google', ...(from && { remoteAddress: from }),
      payload: { code: JSON.stringify(payload), ...(acceptTerms === 'omit' ? {} : { acceptTerms }) },
    });
  const asUser = (res: { cookies: { name: string; value: string }[] }) => {
    const session = res.cookies.find((c) => c.name === 'popsmosis_session');
    expect(session).toBeDefined();
    return { cookie: `popsmosis_session=${session!.value}`, 'x-requested-with': 'XMLHttpRequest' };
  };

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    process.env.JWT_SECRET ||= 'integration-test-secret';
    process.env.GOOGLE_CLIENT_ID = 'test-client';
    process.env.GOOGLE_CLIENT_SECRET = 'test-secret';
    process.env.ADMIN_EMAILS = `${tag}-root@example.com,${tag}-linked@example.com`;
    const { buildApp } = await import('./../index.js');
    prisma = (await import('./../lib/prisma.js')).default;
    app = await buildApp();
    await app.ready();
  });

  afterAll(async () => {
    await prisma.user.deleteMany({ where: { email: { startsWith: tag } } });
    for (const [key, value] of [['GOOGLE_CLIENT_ID', before.id], ['GOOGLE_CLIENT_SECRET', before.secret], ['ADMIN_EMAILS', before.admins]] as const) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    await app.close();
    await prisma.$disconnect();
  });

  it('lets a Google account nobody has seen before in at once, as a regular user', async () => {
    const res = await signIn({ sub: `${tag}-g-new`, email: `${tag}-New@Example.com`, name: 'Newcomer' });
    expect(res.statusCode).toBe(200);
    expect(res.json().user).toMatchObject({ email: `${tag}-new@example.com`, name: 'Newcomer', role: 'USER' });
    const headers = asUser(res);

    expect((await app.inject({ method: 'GET', url: '/api/questions', headers })).statusCode).toBe(200);
    const ask = await app.inject({ method: 'POST', url: '/api/questions', headers, payload: { content: 'Supply and demand: what sets a price?' } });
    expect(ask.statusCode).toBe(201);
    expect((await app.inject({ method: 'GET', url: '/api/users', headers })).statusCode).toBe(403);

    // Signing in again is the same account, not a second one.
    expect((await signIn({ sub: `${tag}-g-new`, email: `${tag}-new@example.com`, name: 'Newcomer' })).json().user.id).toBe(res.json().user.id);
  });

  it('signs nobody in without the Terms agreed, and records the version and time for those who agree', async () => {
    const who = { sub: `${tag}-g-terms`, email: `${tag}-terms@example.com`, name: 'Terms' };
    for (const acceptTerms of ['omit', null, false, 'yes', 1]) {
      const refused = await signIn(who, acceptTerms, '203.0.113.7');
      expect(refused.statusCode).toBe(400);
      expect(refused.json().error.message).toMatch(/Agree to the Terms of Use/);
      expect(refused.cookies.find((c) => c.name === 'popsmosis_session')).toBeUndefined();
    }
    expect(await prisma.user.count({ where: { email: who.email } })).toBe(0); // no account made without it

    const { TERMS_VERSION } = await import('./../lib/terms.js');
    const before = Date.now();
    const res = await signIn(who);
    expect(res.statusCode).toBe(200);
    expect(res.json().user).toMatchObject({ termsCurrent: true });
    const row = await prisma.user.findUniqueOrThrow({ where: { email: who.email } });
    expect(row.termsVersion).toBe(TERMS_VERSION);
    expect(row.termsAcceptedAt!.getTime()).toBeGreaterThanOrEqual(before - 1000);
  });

  it('asks an account with an old or missing record to agree again, and records it', async () => {
    const res = await signIn({ sub: `${tag}-g-old-terms`, email: `${tag}-old-terms@example.com`, name: 'Old Terms' });
    const headers = asUser(res);
    const id = res.json().user.id as string;
    // The Terms move on: this account's record is now an older version.
    await prisma.user.update({ where: { id }, data: { termsVersion: '2000-01-01' } });
    const { invalidateUserCache } = await import('./../middleware/auth.js');
    invalidateUserCache(id);
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers })).json().termsCurrent).toBe(false);

    expect((await app.inject({ method: 'POST', url: '/api/auth/terms', headers, payload: {} })).statusCode).toBe(400);
    expect((await app.inject({ method: 'POST', url: '/api/auth/terms', payload: { accept: true } })).statusCode).toBe(401);
    const agreed = await app.inject({ method: 'POST', url: '/api/auth/terms', headers, payload: { accept: true } });
    expect(agreed.statusCode).toBe(200);
    expect(agreed.json()).toMatchObject({ id, termsCurrent: true });
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers })).json().termsCurrent).toBe(true);
  });

  it('makes an ADMIN_EMAILS address an admin at sign-in', async () => {
    const res = await signIn({ sub: `${tag}-g-root`, email: `${tag}-root@example.com` });
    expect(res.json().user.role).toBe('ADMIN');
    expect((await app.inject({ method: 'GET', url: '/api/users', headers: asUser(res) })).statusCode).toBe(200);
  });

  it('links a known email under a new Google id, and still applies ADMIN_EMAILS to it', async () => {
    const existing = await prisma.user.create({ data: { email: `${tag}-linked@example.com`, googleId: `${tag}-g-old`, name: 'Old' } });
    const res = await signIn({ sub: `${tag}-g-replacement`, email: `${tag}-linked@example.com`, name: 'Linked' });
    expect(res.statusCode).toBe(200);
    expect(res.json().user).toMatchObject({ id: existing.id, role: 'ADMIN', name: 'Linked' });
    expect((await prisma.user.findUnique({ where: { id: existing.id } }))!.googleId).toBe(`${tag}-g-replacement`);
  });
});
