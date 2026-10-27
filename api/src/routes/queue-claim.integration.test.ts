/**
 * The question queue's claim lease, against a real Postgres (DATABASE_URL).
 * Skipped unless INTEGRATION=1.
 *
 * The lease rides on FOR UPDATE SKIP LOCKED, which a unit test cannot exercise
 * at all: the whole behaviour is what two concurrent transactions do to each
 * other. The failure it prevents is silent — nothing errors, two runs just each
 * write an answer for the same question and one is discarded.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('question queue claim lease', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let agentA = '';
  let agentB = '';
  let userId = '';

  const asAgent = (key: string) => ({ authorization: `Bearer ${key}` });
  const queue = (key: string, limit = 5) =>
    app.inject({ method: 'GET', url: `/api/questions/queue?limit=${limit}`, headers: asAgent(key) });
  const ids = (res: Awaited<ReturnType<typeof queue>>) =>
    (res.json().data as { id: string }[]).map((q) => q.id).sort();

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prisma = (await import('../lib/prisma.js')).default;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    await app.close();
    await prisma.$disconnect();
  });

  beforeEach(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    const { generateApiKey } = await import('../lib/apikey.js');
    const ka = generateApiKey();
    const kb = generateApiKey();
    agentA = ka.raw;
    agentB = kb.raw;
    await prisma.agent.create({ data: { name: 'a', apiKeyHash: ka.hashed } });
    await prisma.agent.create({ data: { name: 'b', apiKeyHash: kb.hashed } });
    const u = await prisma.user.create({ data: { email: 'a@b.c', googleId: 'g1', name: 'Ada' } });
    userId = u.id;
    for (let i = 1; i <= 6; i++) {
      await prisma.question.create({
        data: { userId, content: `Question number ${i} about meshes`, createdAt: new Date(Date.now() + i * 1000) },
      });
    }
  });

  it('hands two callers disjoint sets instead of the same oldest five', async () => {
    const [a, b] = await Promise.all([queue(agentA, 3), queue(agentB, 3)]);
    const overlap = ids(a).filter((id) => ids(b).includes(id));
    expect(overlap).toEqual([]);
    expect([...ids(a), ...ids(b)].length).toBe(6);
  });

  it('does not re-hand out a question whose claim is still live', async () => {
    const first = ids(await queue(agentA, 6));
    expect(first.length).toBe(6);
    // Everything is claimed, so a second caller has nothing to take.
    expect(ids(await queue(agentB, 6))).toEqual([]);
  });

  it('releases a dead run\'s questions once the lease expires', async () => {
    const taken = ids(await queue(agentA, 6));
    expect(taken.length).toBe(6);
    expect(ids(await queue(agentB, 6))).toEqual([]);

    // agentA's run dies without answering. Nothing clears the claim; it ages out.
    await prisma.question.updateMany({
      where: { id: { in: taken } },
      data: { claimedAt: new Date(Date.now() - 60 * 60 * 1000) },
    });

    expect(ids(await queue(agentB, 6))).toEqual(taken);
  });

  it('answering removes the question regardless of who holds the claim', async () => {
    const [target] = ids(await queue(agentA, 1));
    const ans = await app.inject({
      method: 'POST', url: `/api/questions/${target}/answer`,
      headers: asAgent(agentA), payload: { content: 'here you go' },
    });
    expect(ans.statusCode).toBe(201);
    await prisma.question.update({ where: { id: target }, data: { claimedAt: new Date(Date.now() - 60 * 60 * 1000) } });
    // The expired claim does not bring an answered question back to the queue.
    expect(ids(await queue(agentB, 6))).not.toContain(target);
  });

  it('a claimed question is not work the trailing fire should wake a run for', async () => {
    const { queuesHaveWork } = await import('../lib/routine.js');
    expect(await queuesHaveWork()).toBe(true);
    await queue(agentA, 6);                       // a live run takes them all
    expect(await queuesHaveWork()).toBe(false);   // nothing left for anyone else
    await prisma.question.updateMany({ data: { claimedAt: new Date(Date.now() - 60 * 60 * 1000) } });
    expect(await queuesHaveWork()).toBe(true);    // the run died; it is work again
  });
});
