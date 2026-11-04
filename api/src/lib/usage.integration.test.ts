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

  it('tells a learner how many topics they have left, and when the next comes back', async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '3';
    const ivy = await learner('ivy');
    const allowance = async () => {
      const res = await app.inject({ method: 'GET', url: '/api/questions/allowance', headers: asUser(ivy.cookie) });
      expect(res.statusCode).toBe(200);
      return res.json();
    };
    const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000);
    const inHours = (seconds: number) => seconds / 3600;

    // None used: all left, nothing coming back.
    expect(await allowance()).toEqual({ perDay: 3, left: 3, nextInSeconds: null, siteFull: false, siteOpensInSeconds: null, sitePerDay: 30 });

    // Two used: one left, and the older's place comes back first, 24 hours after it was taken. A row past the
    // window, or someone else's, counts for nothing.
    const other = await learner('ivy-other');
    await prisma.usageEvent.createMany({ data: [
      { userId: ivy.id, kind: 'TOPIC', createdAt: hoursAgo(20) },
      { userId: ivy.id, kind: 'TOPIC', createdAt: hoursAgo(2) },
      { userId: ivy.id, kind: 'TOPIC', createdAt: hoursAgo(25) },
      { userId: ivy.id, kind: 'REPLY', createdAt: hoursAgo(23) },
      { userId: other.id, kind: 'TOPIC', createdAt: hoursAgo(23) },
    ] });
    const some = await allowance();
    expect(some).toMatchObject({ perDay: 3, left: 1, siteFull: false });
    expect(inHours(some.nextInSeconds)).toBeCloseTo(4, 1);

    // One more through the API: none left, and the next is still the 20-hours-ago one's.
    expect((await ask(ivy.cookie)).statusCode).toBe(201);
    const none = await allowance();
    expect(none.left).toBe(0);
    expect(inHours(none.nextInSeconds)).toBeCloseTo(4, 1);

    // An admin lowers the limit under what is used: asking opens again only once enough have gone.
    process.env.QUESTIONS_PER_USER_PER_DAY = '2';
    const lowered = await allowance();
    expect(lowered.left).toBe(0);
    expect(inHours(lowered.nextInSeconds)).toBeCloseTo(22, 1); // the 2-hours-ago one, the second oldest
    process.env.QUESTIONS_PER_USER_PER_DAY = '0'; // 0: nobody may ask; nothing comes back
    expect(await allowance()).toMatchObject({ left: 0, nextInSeconds: null });

    // The site's day used up: said whatever the learner has left, with when it takes topics again.
    process.env.QUESTIONS_PER_USER_PER_DAY = '10';
    process.env.TOPICS_PER_DAY = '4';
    const full = await allowance();
    expect(full).toMatchObject({ left: 7, siteFull: true, sitePerDay: 4 });
    expect(inHours(full.siteOpensInSeconds)).toBeCloseTo(1, 1); // the other learner's, 23 hours ago
    process.env.TOPICS_PER_DAY = '100000';
    expect(await allowance()).toMatchObject({ siteFull: false, siteOpensInSeconds: null });
    // 0 is an admin's pause: full, with no time it opens.
    process.env.TOPICS_PER_DAY = '0';
    expect(await allowance()).toMatchObject({ left: 7, siteFull: true, siteOpensInSeconds: null, sitePerDay: 0 });

    expect((await app.inject({ method: 'GET', url: '/api/questions/allowance' })).statusCode).toBe(401);
  });

  it('an admin has no topic limit of their own, but counts toward the site\'s and is held by it', async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '1';
    process.env.TOPICS_PER_DAY = '3';
    const ola = await learner('ola');
    await prisma.user.update({ where: { id: ola.id }, data: { role: 'ADMIN' } });
    const allowance = async () =>
      (await app.inject({ method: 'GET', url: '/api/questions/allowance', headers: asUser(ola.cookie) })).json();

    expect(await allowance()).toEqual({ perDay: 1, left: null, nextInSeconds: null, siteFull: false, siteOpensInSeconds: null, sitePerDay: 3 });
    expect((await ask(ola.cookie)).statusCode).toBe(201);
    expect((await ask(ola.cookie)).statusCode).toBe(201); // past the learners' 1
    expect(await allowance()).toMatchObject({ left: null, siteFull: false });

    // Their topics are the site's too: a learner's makes three, and then nobody may ask, the admin neither.
    const pia = await learner('pia');
    expect((await ask(pia.cookie)).statusCode).toBe(201);
    const refused = await ask(ola.cookie);
    expect(refused.statusCode).toBe(429);
    expect(refused.json().error.message)
      .toBe('Popsmosis has reached its daily limit of 3 new topics. The limit is shared by everyone, not just you. Please try again later.');
    expect(await allowance()).toMatchObject({ left: null, siteFull: true });

    // The role taken away, the learners' limit holds them from the next request.
    process.env.TOPICS_PER_DAY = '100000';
    await prisma.user.update({ where: { id: ola.id }, data: { role: 'USER' } });
    expect((await ask(ola.cookie)).statusCode).toBe(429);
    expect(await allowance()).toMatchObject({ perDay: 1, left: 0 });
  });

  it('requests sent all at once cannot all pass the same count', async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '3';
    const ben = await learner('ben');
    const results = await Promise.all(Array.from({ length: 8 }, (_, i) => ask(ben.cookie, `Topic number ${i}`)));
    expect(results.filter((r) => r.statusCode === 201)).toHaveLength(3);
    expect(results.filter((r) => r.statusCode === 429)).toHaveLength(5);
    expect(await prisma.question.count({ where: { userId: ben.id } })).toBe(3);
  });

  it("topics from many learners sent at once cannot all pass the site's count", async () => {
    process.env.TOPICS_PER_DAY = '3';
    const learners = await Promise.all(Array.from({ length: 8 }, (_, i) => learner(`crowd${i}`)));
    const results = await Promise.all(learners.map((l) => ask(l.cookie)));
    expect(results.filter((r) => r.statusCode === 201)).toHaveLength(3);
    expect(results.filter((r) => r.statusCode === 429)).toHaveLength(5);
    expect(await prisma.usageEvent.count({ where: { kind: 'TOPIC' } })).toBe(3);
  });

  it("stops new topics once the site's day is used up, whoever asks", async () => {
    process.env.TOPICS_PER_DAY = '3';
    const [cy, di] = [await learner('cy'), await learner('di')];
    expect((await ask(cy.cookie)).statusCode).toBe(201);
    expect((await ask(cy.cookie)).statusCode).toBe(201);
    expect((await ask(di.cookie)).statusCode).toBe(201);
    const over = await ask(di.cookie);
    expect(over.statusCode).toBe(429);
    expect(over.json().error.message)
      .toBe('Popsmosis has reached its daily limit of 3 new topics. The limit is shared by everyone, not just you. Please try again later.');

    process.env.TOPICS_PER_DAY = '100000'; // no value lifts it; a high one never binds
    expect((await ask(di.cookie)).statusCode).toBe(201);
  });

  it('pauses new topics for everyone at 0, admins too, and says so', async () => {
    process.env.TOPICS_PER_DAY = '0';
    const rae = await learner('rae');
    const pam = await learner('pam');
    await prisma.user.update({ where: { id: pam.id }, data: { role: 'ADMIN' } });
    for (const who of [rae, pam]) {
      const paused = await ask(who.cookie);
      expect(paused.statusCode).toBe(429);
      expect(paused.json().error.message)
        .toBe('Popsmosis has paused new topics for now. This is for everyone, not just you. Please try again later.');
    }
    expect(await prisma.usageEvent.count({ where: { kind: 'TOPIC' } })).toBe(0);

    process.env.TOPICS_PER_DAY = '5'; // resumed
    expect((await ask(rae.cookie)).statusCode).toBe(201);
  });

  it('turns away a learner at the limit before reading what they sent', async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '1';
    const hal = await learner('hal');
    expect((await ask(hal.cookie)).statusCode).toBe(201);
    // A body past MESSAGE_BODY_LIMIT would be a 413 if it were read: the 429 says it never was.
    const { MESSAGE_BODY_LIMIT } = await import('./attachments.js');
    const huge = await app.inject({
      method: 'POST', url: '/api/questions', headers: { ...asUser(hal.cookie), 'content-type': 'application/json' },
      payload: JSON.stringify({ content: 'x', pad: 'y'.repeat(MESSAGE_BODY_LIMIT + 1) }),
    });
    expect(huge.statusCode).toBe(429);
  });

  it('gives a take back when the request it was for is never written', async () => {
    process.env.QUESTIONS_PER_USER_PER_DAY = '1';
    const gus = await learner('gus');
    const { takeTopic, giveBack } = await import('./usage.js');
    const taken = await takeTopic(gus.id);
    await expect(takeTopic(gus.id)).rejects.toMatchObject({ code: 'RATE_LIMITED' });
    await giveBack(taken);
    expect((await ask(gus.cookie)).statusCode).toBe(201); // the place came back
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
