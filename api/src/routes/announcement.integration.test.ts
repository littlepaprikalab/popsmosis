/**
 * The announcement under the nav, against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: anyone reads it with no sign-in, only admins put it up or take it down, a change is checked and in force
 * on this instance at once, and it leaves the studio's settings as they were.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('announcement', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let adminCookie = '';
  let learnerCookie = '';

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const read = () => app.inject({ method: 'GET', url: '/api/announcement' });
  const put = (cookie: string, payload: object) =>
    app.inject({ method: 'PUT', url: '/api/announcement', headers: asUser(cookie), payload });
  const takeDown = (cookie: string) => app.inject({ method: 'DELETE', url: '/api/announcement', headers: asUser(cookie) });

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prisma = (await import('../lib/prisma.js')).default;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE settings, users RESTART IDENTITY CASCADE');
    await app.close();
    await prisma.$disconnect();
  });

  beforeEach(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE settings, users RESTART IDENTITY CASCADE');
    await (await import('../lib/announcement.js')).writeAnnouncement(null); // what this instance has in memory, as the table now is
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    const admin = await prisma.user.create({ data: { email: 'ada@example.com', googleId: 'g-ada', name: 'Ada', role: 'ADMIN' } });
    const learner = await prisma.user.create({ data: { email: 'ben@example.com', googleId: 'g-ben', name: 'Ben' } });
    const cookie = (id: string) => `popsmosis_session=${app.jwt.sign({ sub: id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    adminCookie = cookie(admin.id);
    learnerCookie = cookie(learner.id);
  });

  it('is none until an admin puts one up, and then anyone reads it with no sign-in', async () => {
    expect((await read()).json()).toEqual({ announcement: null });

    const res = await put(adminCookie, { message: 'Songs are off today: we are out of quota.', tone: 'warning' });
    expect(res.statusCode).toBe(200);
    const announcement = { message: 'Songs are off today: we are out of quota.', tone: 'warning', updatedAt: expect.any(String) };
    expect(res.json()).toEqual({ announcement });

    const shown = await read();
    expect(shown.statusCode).toBe(200);
    expect(shown.headers['set-cookie']).toBeUndefined();
    expect(shown.json()).toEqual({ announcement });
    expect(shown.json().announcement.updatedAt).toBe(res.json().announcement.updatedAt);
  });

  it('puts a new one in place of the one before, with a later time, so a banner someone closed shows again', async () => {
    const first = (await put(adminCookie, { message: 'Songs are off.' })).json().announcement;
    expect(first.tone).toBe('info');
    await new Promise((r) => setTimeout(r, 5));
    const second = (await put(adminCookie, { message: 'Songs are back.', tone: 'info' })).json().announcement;
    expect(second.message).toBe('Songs are back.');
    expect(Date.parse(second.updatedAt)).toBeGreaterThan(Date.parse(first.updatedAt));
    expect((await read()).json().announcement).toEqual(second);
  });

  it('takes it down for everyone at once', async () => {
    await put(adminCookie, { message: 'Songs are off.' });
    const res = await takeDown(adminCookie);
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ announcement: null });
    expect((await read()).json()).toEqual({ announcement: null });
    expect(await prisma.setting.count({ where: { key: 'announcement' } })).toBe(0);
    expect((await takeDown(adminCookie)).json()).toEqual({ announcement: null }); // with none up, nothing to do
  });

  it('lets only admins change it', async () => {
    expect((await put(learnerCookie, { message: 'hi' })).statusCode).toBe(403);
    expect((await takeDown(learnerCookie)).statusCode).toBe(403);
    expect((await app.inject({ method: 'PUT', url: '/api/announcement', payload: { message: 'hi' } })).statusCode).toBe(401);
    expect((await read()).json()).toEqual({ announcement: null });
  });

  it('refuses one it cannot show, and keeps the one up', async () => {
    await put(adminCookie, { message: 'Songs are off.' });
    for (const [payload, message] of [
      [{ message: '   ' }, 'message is required; DELETE /api/announcement takes the announcement down'],
      [{ message: 'x'.repeat(501) }, 'message must be 500 characters or less'],
      [{ message: 'hi', tone: 'danger' }, 'tone must be one of info, warning'],
    ] as const) {
      const res = await put(adminCookie, payload);
      expect(res.statusCode).toBe(400);
      expect(res.json().error.message).toBe(message);
    }
    expect((await read()).json().announcement.message).toBe('Songs are off.');
  });

  it('is a row of its own: the studio settings neither show it nor change it', async () => {
    await put(adminCookie, { message: 'Songs are off.' });
    const studio = await app.inject({ method: 'GET', url: '/api/studio/settings', headers: asUser(adminCookie) });
    expect(studio.json().updatedAt).toBeNull();
    expect(studio.json()).not.toHaveProperty('announcement');
    await app.inject({ method: 'PUT', url: '/api/studio/settings', headers: asUser(adminCookie), payload: { song: { provider: 'gemini' } } });
    expect((await read()).json().announcement.message).toBe('Songs are off.');
  });
});
