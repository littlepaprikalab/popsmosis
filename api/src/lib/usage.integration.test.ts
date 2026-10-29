/**
 * The limits on what learners ask for (lib/usage.ts), against a real database: taking a request back or deleting it
 * hands nothing back, parallel requests cannot all pass the same count, and the site-wide ceiling counts everyone.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';

describe.runIf(RUN)('usage limits', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./prisma.js'))['default'];
  const tag = `usage-${Date.now()}`;
  const saved = { ...process.env };

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const ask = (cookie: string, content = 'ATP synthesis') =>
    app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookie), payload: { content } });

  async function learner(name: string) {
    const { SESSION_EXPIRES_IN } = await import('./session.js');
    const row = await prisma.user.create({ data: { email: `${tag}-${name}@example.com`, googleId: `${tag}-${name}`, name } });
    return { id: row.id, cookie: `popsmosis_session=${app.jwt.sign({ sub: row.id }, { expiresIn: SESSION_EXPIRES_IN })}` };
  }

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prisma = (await import('./prisma.js')).default;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });

  afterAll(async () => {
    await prisma.user.deleteMany({ where: { email: { startsWith: tag } } });
    await prisma.agent.deleteMany({ where: { name: { startsWith: tag } } });
    await app.close();
    await prisma.$disconnect();
  });

  beforeEach(async () => {
    await prisma.usageEvent.deleteMany({});
  });

  afterEach(() => {
    for (const name of ['QUESTIONS_PER_USER_PER_DAY', 'TOPICS_PER_DAY', 'REPLIES_PER_USER_PER_HOUR']) {
      if (saved[name] === undefined) delete process.env[name];
      else process.env[name] = saved[name];
    }
  });

  it('a topic taken back or deleted does not hand its place in the day back', async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '2';
    const ada = await learner('ada');
    const first = await ask(ada.cookie);
    const second = await ask(ada.cookie);
    expect([first.statusCode, second.statusCode]).toEqual([201, 201]);

    // One deleted while pending, one answered and then taken back: the day is still used up.
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${first.json().id}`, headers: asUser(ada.cookie) })).statusCode).toBe(204);
    const { generateApiKey } = await import('./apikey.js');
    const agent = await prisma.agent.create({ data: { name: `${tag}-agent`, apiKeyHash: generateApiKey().hashed } });
    await prisma.answer.create({ data: { questionId: second.json().id, agentId: agent.id, content: 'A lesson.' } });
    await prisma.question.update({ where: { id: second.json().id }, data: { status: 'ANSWERED' } });
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${second.json().id}`, headers: asUser(ada.cookie) })).statusCode).toBe(204);
    expect(await prisma.question.count({ where: { userId: ada.id } })).toBe(0);

    const third = await ask(ada.cookie);
    expect(third.statusCode).toBe(429);
    expect(third.json().error).toMatchObject({ code: 'RATE_LIMITED', retryable: false });

    // A day later the place comes back.
    await prisma.usageEvent.updateMany({ where: { userId: ada.id }, data: { createdAt: new Date(Date.now() - 25 * 3_600_000) } });
    expect((await ask(ada.cookie)).statusCode).toBe(201);
  });

  it('requests sent all at once cannot all pass the same count', async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '3';
    const ben = await learner('ben');
    const results = await Promise.all(Array.from({ length: 8 }, (_, i) => ask(ben.cookie, `Topic number ${i}`)));
    expect(results.filter((r) => r.statusCode === 201)).toHaveLength(3);
    expect(results.filter((r) => r.statusCode === 429)).toHaveLength(5);
    expect(await prisma.question.count({ where: { userId: ben.id } })).toBe(3);
  });

  it("stops new topics once the site's day is used up, whoever asks", async () => {
    process.env.TOPICS_PER_DAY = '3';
    const [cy, di] = [await learner('cy'), await learner('di')];
    expect((await ask(cy.cookie)).statusCode).toBe(201);
    expect((await ask(cy.cookie)).statusCode).toBe(201);
    expect((await ask(di.cookie)).statusCode).toBe(201);
    const over = await ask(di.cookie);
    expect(over.statusCode).toBe(429);
    expect(over.json().error.message).toMatch(/all the droplets it can for today/);

    process.env.TOPICS_PER_DAY = '0'; // 0: no ceiling
    expect((await ask(di.cookie)).statusCode).toBe(201);
  });

  it('lets go of rows no limit reads any more, and keeps the rest', async () => {
    const fay = await learner('fay');
    await prisma.usageEvent.createMany({ data: [
      { userId: fay.id, kind: 'TOPIC', createdAt: new Date(Date.now() - 3 * 86_400_000) },
      { userId: fay.id, kind: 'REPLY', createdAt: new Date(Date.now() - 49 * 3_600_000) },
      { userId: fay.id, kind: 'TOPIC', createdAt: new Date(Date.now() - 23 * 3_600_000) },
    ] });
    const { pruneUsage } = await import('./usage.js');
    expect(await pruneUsage(prisma)).toBe(2);
    expect(await prisma.usageEvent.count({ where: { userId: fay.id } })).toBe(1);
  });

  it('a follow-up deleted does not hand its place in the hour back, and parallel ones cannot overshoot', async () => {
    process.env.REPLIES_PER_USER_PER_HOUR = '2';
    const eve = await learner('eve');
    const { generateApiKey } = await import('./apikey.js');
    const agent = await prisma.agent.create({ data: { name: `${tag}-agent-2`, apiKeyHash: generateApiKey().hashed } });
    const q = await prisma.question.create({ data: { userId: eve.id, content: 'Tides', status: 'ANSWERED' } });
    const answer = await prisma.answer.create({ data: { questionId: q.id, agentId: agent.id, content: 'The Moon pulls the oceans.' } });
    const followUp = (content: string) =>
      app.inject({ method: 'POST', url: `/api/answers/${answer.id}/replies`, headers: asUser(eve.cookie), payload: { content } });

    const first = await followUp('Why two tides a day?');
    expect(first.statusCode).toBe(201);
    expect((await app.inject({ method: 'DELETE', url: `/api/replies/${first.json().id}`, headers: asUser(eve.cookie) })).statusCode).toBe(204);
    const rest = await Promise.all([followUp('And spring tides?'), followUp('And neap tides?'), followUp('And the Sun?')]);
    expect(rest.filter((r) => r.statusCode === 201)).toHaveLength(1);
    expect(rest.filter((r) => r.statusCode === 429)).toHaveLength(2);
  });
});
