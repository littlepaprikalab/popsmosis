/**
 * The byte limits on files and the retention, against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: a learner's day of bytes, which deleting a request does not give back -> what popsmosis holds of a learner's
 * files, which deleting a request does free -> a body too large for what is left refused before it is read -> a file
 * past the retention gone on read before the sweep comes, and listed as expired -> the sweep letting go of more than
 * one batch in a run.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;
const MB = 1024 * 1024;

d('attachment quota and retention', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./../lib/prisma.js'))['default'];
  let cookie: string;
  let userId: string;
  const tag = `quota-${Date.now()}`;
  const saved = { ...process.env };
  const LIMITS = ['ATTACHMENT_MB_PER_USER_PER_DAY', 'ATTACHMENT_MB_HELD_PER_USER', 'ATTACHMENT_RETENTION_DAYS', 'QUESTIONS_PER_USER_PER_DAY',
    'TOPICS_PER_DAY'];

  const asUser = () => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  // Plain text the size asked for, so the file is stored as text/plain.
  const file = (bytes: number, name = 'notes.txt') => ({ name, data: Buffer.alloc(bytes, 'a').toString('base64') });
  const ask = (content: string, attachments: object[] = []) =>
    app.inject({ method: 'POST', url: '/api/questions', headers: asUser(), payload: { content: `${tag} ${content}`, attachments } });

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    process.env.JWT_SECRET ||= 'integration-test-secret';
    const { buildApp } = await import('./../index.js');
    prisma = (await import('./../lib/prisma.js')).default;
    app = await buildApp();
    await app.ready();
  });

  beforeEach(async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '100';
    process.env.TOPICS_PER_DAY = '0';
    const user = await prisma.user.create({ data: { email: `${tag}-${Math.random()}@example.com`, googleId: `${tag}-${Math.random()}`, name: 'Quinn' } });
    userId = user.id;
    const { SESSION_EXPIRES_IN } = await import('./../lib/session.js');
    cookie = `popsmosis_session=${app.jwt.sign({ sub: user.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
  });

  afterEach(() => {
    for (const name of LIMITS) {
      if (saved[name] === undefined) delete process.env[name]; else process.env[name] = saved[name];
    }
  });

  afterAll(async () => {
    await prisma.user.deleteMany({ where: { email: { startsWith: tag } } });
    await app.close();
    await prisma.$disconnect();
  });

  it('caps a learner\'s files a day, and deleting a request gives none of it back', async () => {
    process.env.ATTACHMENT_MB_PER_USER_PER_DAY = '1';
    process.env.ATTACHMENT_MB_HELD_PER_USER = '0'; // this test is about the day alone
    const first = await ask('first', [file(0.6 * MB)]);
    expect(first.statusCode).toBe(201);
    const second = await ask('second', [file(0.6 * MB)]);
    expect(second.statusCode).toBe(429);
    expect(second.json().error.message).toMatch(/You can send 1 MB of files a day and have sent 0\.6 MB in the last 24 hours/);
    expect((await ask('no files, still fine')).statusCode).toBe(201);
    expect((await ask('a small one fits', [file(0.3 * MB)])).statusCode).toBe(201);

    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${first.json().id}`, headers: asUser() })).statusCode).toBe(204);
    expect((await ask('after deleting', [file(0.6 * MB)])).statusCode).toBe(429);
    // A refused take writes nothing: the day holds the two that went through.
    const day = await prisma.usageEvent.aggregate({ where: { userId }, _sum: { bytes: true } });
    expect(day._sum.bytes).toBe(Math.floor(0.6 * MB) + Math.floor(0.3 * MB));
  });

  it('caps what popsmosis holds of a learner\'s files, and deleting a request frees its files', async () => {
    process.env.ATTACHMENT_MB_PER_USER_PER_DAY = '0';
    process.env.ATTACHMENT_MB_HELD_PER_USER = '1';
    const first = await ask('first', [file(0.6 * MB)]);
    expect(first.statusCode).toBe(201);
    const refused = await ask('second', [file(0.6 * MB)]);
    expect(refused.statusCode).toBe(429);
    expect(refused.json().error.message).toMatch(/keeps up to 1 MB of your files at a time and is keeping 0\.6 MB/);

    // A follow-up's files count too.
    const agent = await prisma.agent.create({ data: { name: `${tag}-agent-${Math.random()}` } });
    const answer = await prisma.answer.create({ data: { questionId: first.json().id, agentId: agent.id, content: 'A lesson.' } });
    await prisma.question.update({ where: { id: first.json().id }, data: { status: 'ANSWERED' } });
    const followUp = await app.inject({
      method: 'POST', url: `/api/answers/${answer.id}/replies`, headers: asUser(), payload: { content: 'and this?', attachments: [file(0.5 * MB)] },
    });
    expect(followUp.statusCode).toBe(429);

    // Files past the retention are not held, whether or not the sweep has come to them.
    await prisma.attachment.updateMany({ where: { questionId: first.json().id }, data: { createdAt: new Date(Date.now() - 31 * 24 * 3600 * 1000) } });
    expect((await ask('once the old one is past its time', [file(0.6 * MB)])).statusCode).toBe(201);
    await prisma.agent.delete({ where: { id: agent.id } }).catch(() => {});
  });

  it('counts what a learner holds across two messages sent at once', async () => {
    process.env.ATTACHMENT_MB_PER_USER_PER_DAY = '0';
    process.env.ATTACHMENT_MB_HELD_PER_USER = '1';
    const { takeTopic } = await import('./../lib/usage.js');
    // Two takes before either message's files are written: the second sees the first's bytes on its way.
    const results = await Promise.allSettled([takeTopic(userId, Math.floor(0.6 * MB)), takeTopic(userId, Math.floor(0.6 * MB))]);
    expect(results.map((r) => r.status).sort()).toEqual(['fulfilled', 'rejected']);
    // Through the route: two messages at once, either alone fits beside the 0.6 MB on its way, both together do not.
    const both = await Promise.all([ask('one', [file(0.3 * MB)]), ask('two', [file(0.3 * MB)])]);
    expect(both.map((r) => r.statusCode).sort()).toEqual([201, 429]);
    // Once a message is written its take is no longer pending: its files count once, from the files.
    expect(await prisma.usageEvent.count({ where: { userId, filesPending: true } })).toBe(1); // the bare take above
  });

  it('refuses a body too large for what is left before reading it', async () => {
    process.env.ATTACHMENT_MB_PER_USER_PER_DAY = '1';
    expect((await ask('fills the day', [file(0.9 * MB)])).statusCode).toBe(201);
    // 3 MB of files as base64: the open check refuses it on its length alone.
    const big = await ask('too much', [file(3 * MB)]);
    expect(big.statusCode).toBe(429);
    expect(big.json().error.message).toMatch(/1 MB of files a day/);
  });

  it('serves no file past the retention, letting its bytes go on the spot, and lists it as expired', async () => {
    process.env.ATTACHMENT_RETENTION_DAYS = '30';
    const asked = await ask('old notes', [file(1000)]);
    const [listed] = asked.json().attachments;
    expect((await app.inject({ method: 'GET', url: listed.url, headers: asUser() })).statusCode).toBe(200);

    await prisma.attachment.update({ where: { id: listed.id }, data: { createdAt: new Date(Date.now() - 31 * 24 * 3600 * 1000) } });
    const card = await app.inject({ method: 'GET', url: `/api/questions/${asked.json().id}`, headers: asUser() });
    expect(card.json().attachments[0].expired).toBe(true); // before any sweep
    expect((await app.inject({ method: 'GET', url: listed.url, headers: asUser() })).statusCode).toBe(410);
    const row = await prisma.attachment.findUniqueOrThrow({ where: { id: listed.id } });
    expect(row.data).toBeNull();
    expect(row.purgedAt).not.toBeNull();
  });

  it('sweeps more than one batch in a run, oldest first, and leaves files within the retention', async () => {
    process.env.ATTACHMENT_RETENTION_DAYS = '30';
    const q = await prisma.question.create({ data: { userId, content: `${tag} many files` } });
    const old = new Date(Date.now() - 40 * 24 * 3600 * 1000);
    await prisma.attachment.createMany({
      data: Array.from({ length: 230 }, (_, i) => ({ questionId: q.id, name: `${i}.txt`, mimeType: 'text/plain', size: 1, data: new Uint8Array([97]), createdAt: old })),
    });
    await prisma.attachment.create({ data: { questionId: q.id, name: 'new.txt', mimeType: 'text/plain', size: 1, data: new Uint8Array([97]) } });
    const { sweepExpiredAttachments } = await import('./../lib/attachments.js');
    expect(await sweepExpiredAttachments(prisma)).toBeGreaterThanOrEqual(230);
    expect(await prisma.attachment.count({ where: { questionId: q.id, purgedAt: null } })).toBe(1);
    expect(await sweepExpiredAttachments(prisma)).toBe(0);
  });
});
