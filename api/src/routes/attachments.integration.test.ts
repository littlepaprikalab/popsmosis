/**
 * Attachments against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1, like the other integration files.
 *
 * Covers: a question and a human reply carrying files, as Zed's Zoey panel sends them -> the files listed on the
 * thread and in both of the advisor's queues, without their bytes -> each served to a reader as its bytes say it is,
 * never as it claimed -> the limits, an agent's reply refused files, and a stranger refused the bytes -> the retention
 * letting the bytes go and keeping the row -> the files going with their message.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('attachments', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./../lib/prisma.js'))['default'];
  let cookieA: string;
  let cookieB: string;
  let zedToken: string;
  let agentKey: string;
  const tag = `att-${Date.now()}`;

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const asBearer = (key: string) => ({ authorization: `Bearer ${key}` });
  const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64, 7)]);
  const b64 = (b: Buffer | string) => Buffer.from(b).toString('base64');

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    process.env.JWT_SECRET ||= 'integration-test-secret';
    const { buildApp } = await import('./../index.js');
    const { generateApiKey } = await import('./../lib/apikey.js');
    prisma = (await import('./../lib/prisma.js')).default;
    app = await buildApp();
    await app.ready();

    const approved = { status: 'APPROVED' as const, reviewedAt: new Date() };
    const userA = await prisma.user.create({ data: { email: `${tag}-a@example.com`, googleId: `${tag}-a`, name: 'Ada', ...approved } });
    const userB = await prisma.user.create({ data: { email: `${tag}-b@example.com`, googleId: `${tag}-b`, name: 'Ben', status: 'PENDING' } });
    const { SESSION_EXPIRES_IN } = await import('./../lib/session.js');
    cookieA = `zoey_session=${app.jwt.sign({ sub: userA.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    cookieB = `zoey_session=${app.jwt.sign({ sub: userB.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    const minted = await app.inject({ method: 'POST', url: '/api/tokens', headers: asUser(cookieA), payload: { name: 'zed panel' } });
    zedToken = minted.json().token;

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

  let questionId: string;
  let answerId: string;
  let shotUrl: string;
  let logUrl: string;
  let fakeUrl: string;

  it('takes a question with its files and lists them, without their bytes', async () => {
    const r = await app.inject({
      method: 'POST', url: '/api/questions', headers: asBearer(zedToken),
      payload: {
        content: `${tag} why is the torch dark here?`, client: 'zed', context: 'camera 1 2 3',
        attachments: [
          { name: 'pasted.png', mimeType: 'image/png', data: b64(png) },
          { name: 'D:/Abyss/Saved/Logs/zed.log', data: b64('Zed: warning: torch has no light\n') },
          { name: 'evil.html', mimeType: 'text/html', data: b64(Buffer.from([0x3c, 0x00, 0x3e])) },
        ],
      },
    });
    expect(r.statusCode).toBe(201);
    const q = r.json();
    questionId = q.id;
    expect(q.attachments.map((a: { name: string; mimeType: string; size: number; expired: boolean }) => [a.name, a.mimeType, a.size, a.expired])).toEqual([
      ['pasted.png', 'image/png', png.length, false],
      ['zed.log', 'text/plain', 33, false],
      ['evil.html', 'text/html', 3, false],
    ]);
    [shotUrl, logUrl, fakeUrl] = q.attachments.map((a: { url: string }) => a.url);
    expect(shotUrl).toMatch(/^\/api\/attachments\/\w+$/);
    expect(JSON.stringify(q)).not.toContain(b64(png)); // a card never carries the bytes
  });

  it('lists them in the advisor\'s question queue, and serves each to a reader as its bytes say', async () => {
    const queue = await app.inject({ method: 'GET', url: '/api/questions/queue?limit=20', headers: asBearer(agentKey) });
    const queued = queue.json().data.find((q: { id: string }) => q.id === questionId);
    expect(queued.attachments.map((a: { name: string }) => a.name)).toEqual(['pasted.png', 'zed.log', 'evil.html']);

    const shot = await app.inject({ method: 'GET', url: shotUrl, headers: asBearer(agentKey) });
    expect(shot.statusCode).toBe(200);
    expect(shot.headers['content-type']).toBe('image/png');
    expect(shot.headers['content-disposition']).toBe(`inline; filename="pasted.png"; filename*=UTF-8''pasted.png`);
    expect(shot.headers['content-security-policy']).toMatch(/sandbox/);
    expect(shot.rawPayload.equals(png)).toBe(true);

    const log = await app.inject({ method: 'GET', url: logUrl, headers: asBearer(zedToken) });
    expect(log.headers['content-type']).toBe('text/plain; charset=utf-8');
    expect(log.body).toBe('Zed: warning: torch has no light\n');

    const fake = await app.inject({ method: 'GET', url: fakeUrl, headers: asUser(cookieA) });
    expect(fake.headers['content-type']).toBe('application/octet-stream'); // never what it claimed
    expect(fake.headers['content-disposition']).toMatch(/^attachment;/);

    // Nor does a row that says image/png make its bytes one: what goes out is judged from the bytes as they go.
    await prisma.attachment.updateMany({ where: { name: 'evil.html', questionId }, data: { mimeType: 'image/png' } });
    const relabelled = await app.inject({ method: 'GET', url: fakeUrl, headers: asUser(cookieA) });
    expect(relabelled.headers['content-type']).toBe('application/octet-stream');
  });

  it('refuses the bytes to anyone not signed in and approved, and a file that is not there', async () => {
    expect((await app.inject({ method: 'GET', url: shotUrl })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: shotUrl, headers: asUser(cookieB) })).statusCode).toBe(403);
    expect((await app.inject({ method: 'GET', url: '/api/attachments/nope', headers: asBearer(zedToken) })).statusCode).toBe(404);
  });

  it('refuses a message whose files break a limit, before writing anything', async () => {
    const before = await prisma.question.count({ where: { content: { startsWith: tag } } });
    const over = await app.inject({
      method: 'POST', url: '/api/questions', headers: asBearer(zedToken),
      payload: { content: `${tag} too much`, attachments: Array.from({ length: 6 }, (_, i) => ({ name: `f${i}.txt`, data: b64('x') })) },
    });
    expect(over.statusCode).toBe(400);
    expect(over.json().error.message).toMatch(/at most 5 files/);
    expect(await prisma.question.count({ where: { content: { startsWith: tag } } })).toBe(before);
  });

  it('takes files on a human reply, lists them in the reply queue, and refuses them on an agent\'s', async () => {
    const answered = await app.inject({
      method: 'POST', url: `/api/questions/${questionId}/answer`, headers: asBearer(agentKey), payload: { content: 'Its light is off.' },
    });
    answerId = answered.json().id;
    const r = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(zedToken),
      payload: { content: 'Still dark - see this', client: 'zed', attachments: [{ name: 'again.png', data: b64(png) }] },
    });
    expect(r.statusCode).toBe(201);
    const humanReply = r.json();
    expect(humanReply.attachments.map((a: { name: string; mimeType: string }) => [a.name, a.mimeType])).toEqual([['again.png', 'image/png']]);

    const queue = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asBearer(agentKey) });
    const thread = queue.json().data.find((t: { answer: { id: string } }) => t.answer.id === answerId);
    expect(thread.replyTo.attachments.map((a: { name: string }) => a.name)).toEqual(['again.png']);
    expect(thread.question.attachments.map((a: { name: string }) => a.name)).toEqual(['pasted.png', 'zed.log', 'evil.html']);

    const agentReply = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(agentKey),
      payload: { content: 'Turned it on.', replyToId: humanReply.id, attachments: [{ name: 'x.txt', data: b64('x') }] },
    });
    expect(agentReply.statusCode).toBe(400);
    expect(agentReply.json().error.message).toMatch(/human replies only/);

    const card = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(card.json().answer.replies[0].attachments.map((a: { name: string }) => a.name)).toEqual(['again.png']);
  });

  it('lets the bytes go past the retention and keeps the row, which then answers 410', async () => {
    const { sweepExpiredAttachments } = await import('./../lib/attachments.js');
    await prisma.attachment.updateMany({ where: { questionId }, data: { createdAt: new Date(Date.now() - 400 * 24 * 3600 * 1000) } });
    expect(await sweepExpiredAttachments(prisma)).toBeGreaterThanOrEqual(3);
    const card = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(card.json().attachments.map((a: { name: string; expired: boolean }) => [a.name, a.expired])).toEqual([
      ['pasted.png', true], ['zed.log', true], ['evil.html', true],
    ]);
    expect(card.json().answer.replies[0].attachments[0].expired).toBe(false); // the reply's is still within it
    const gone = await app.inject({ method: 'GET', url: shotUrl, headers: asBearer(zedToken) });
    expect(gone.statusCode).toBe(410);
  });

  it('drops the files with their message', async () => {
    const r = await app.inject({
      method: 'POST', url: '/api/questions', headers: asBearer(zedToken),
      payload: { content: `${tag} a question to take back`, attachments: [{ name: 'a.txt', data: b64('a') }] },
    });
    const id = r.json().id;
    expect(await prisma.attachment.count({ where: { questionId: id } })).toBe(1);
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${id}`, headers: asBearer(zedToken) })).statusCode).toBe(204);
    expect(await prisma.attachment.count({ where: { questionId: id } })).toBe(0);
  });
});
