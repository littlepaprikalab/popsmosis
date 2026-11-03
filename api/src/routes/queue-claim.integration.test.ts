/**
 * The question queue's claim lease, against a real Postgres (DATABASE_URL).
 * Skipped unless INTEGRATION=1.
 *
 * The lease rides on FOR UPDATE SKIP LOCKED, which a unit test cannot exercise
 * at all: the whole behaviour is what two concurrent transactions do to each
 * other. The failure it prevents is silent — nothing errors, two runs just each
 * write an answer for the same question and one is discarded.
 *
 * Also the count of runs: a claim with a run id the topic has not seen last is one,
 * and a topic whose runs are used up is given up instead of started again
 * (lib/claim.ts).
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
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
  const claim = (id: string, key: string, payload?: object) =>
    app.inject({ method: 'POST', url: `/api/questions/${id}/claim`, headers: asAgent(key), payload });
  const lapse = (id: string) =>
    prisma.question.update({ where: { id }, data: { claimedAt: new Date(Date.now() - 60 * 60 * 1000) } });

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
    delete process.env.QUESTION_MAX_RUNS;
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

  it('never tells an agent who asked', async () => {
    const res = await queue(agentA, 6);
    expect(res.json().data.length).toBe(6);
    expect(res.json().data.every((q: object) => !('user' in q))).toBe(true);
    const claim = await app.inject({ method: 'POST', url: `/api/questions/${ids(res)[0]}/claim`, headers: asAgent(agentA) });
    expect(claim.statusCode).toBe(200);
    expect(claim.json()).not.toHaveProperty('user');
    expect(JSON.stringify(claim.json())).not.toContain('Ada');
  });

  it('only the run holding a question answers it, and the answer takes it out of every queue', async () => {
    const [target] = ids(await queue(agentA, 1));
    // The queue leased it to A: B cannot post over A's run, nor answer a question it never took.
    const stolen = await app.inject({
      method: 'POST', url: `/api/questions/${target}/answer`, headers: asAgent(agentB), payload: { content: 'mine now' },
    });
    expect(stolen.statusCode).toBe(409);
    expect(stolen.json().error.message).toMatch(/Claim this question/);
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

  it('counts each run once, by its id: not the hand-out, a renewal, or a retry after a lost reply', async () => {
    const [first] = (await queue(agentA, 1)).json().data;
    expect(first).toMatchObject({ runs: 0, maxRuns: 3 }); // handed out, not yet tried
    for (const runId of ['short', 'has spaces in it', 'x'.repeat(65), 42]) {
      expect((await claim(first.id, agentA, { runId })).statusCode).toBe(400);
    }
    expect((await claim(first.id, agentA, { runId: 'run-one-a1' })).json()).toMatchObject({ id: first.id, runs: 1, maxRuns: 3 });
    // The studio claims with its run's id before every step and every 5 minutes; a claim with no id is a renewal too.
    for (const payload of [{ runId: 'run-one-a1' }, { runId: 'run-one-a1' }, {}, undefined]) {
      expect((await claim(first.id, agentA, payload)).json()).toMatchObject({ id: first.id, runs: 1, maxRuns: 3 });
    }

    // Its machine goes away; the next sweep's run starts it over. The claim counts it and is committed, but the
    // reply never reaches the studio, which tries again with the same id: one run, not two.
    await lapse(first.id);
    expect((await queue(agentB, 1)).json().data[0]).toMatchObject({ id: first.id, runs: 1 });
    const lookup = vi.spyOn(prisma.question, 'findUnique').mockRejectedValueOnce(new Error('the connection was reset'));
    expect((await claim(first.id, agentB, { runId: 'run-two-b2' })).statusCode).toBe(500);
    lookup.mockRestore();
    expect((await prisma.question.findUniqueOrThrow({ where: { id: first.id } })).runs).toBe(2);
    expect((await claim(first.id, agentB, { runId: 'run-two-b2' })).json()).toMatchObject({ runs: 2 });

    // Another agent's run is turned away while B holds the lease, and counts nothing.
    expect((await claim(first.id, agentA, { runId: 'run-three-c3' })).json().error.message).toMatch(/Another agent/);
    expect((await prisma.question.findUniqueOrThrow({ where: { id: first.id } })).runs).toBe(2);
  });

  it('turns a new run away at the most while the last run holds the lease, and gives the topic up once it lapses', async () => {
    const [topic] = (await queue(agentA, 1)).json().data;
    for (const [i, runId] of ['run-one-a1', 'run-two-b2', 'run-three-c3'].entries()) {
      if (i > 0) await lapse(topic.id);
      expect((await claim(topic.id, agentA, { runId })).json()).toMatchObject({ runs: i + 1 });
    }
    // The last run renews its lease at the most as any run does.
    expect((await claim(topic.id, agentA, { runId: 'run-three-c3' })).json()).toMatchObject({ runs: 3 });

    // A fourth machine starts on it while the third is alive (one agent key runs every routine): turned away, and
    // the third carries on.
    const alive = await claim(topic.id, agentA, { runId: 'run-four-d4' });
    expect(alive.statusCode).toBe(409);
    expect(alive.json().error.message).toBe('Another run holds the claim on this question');
    expect(await prisma.question.findUniqueOrThrow({ where: { id: topic.id } })).toMatchObject({ status: 'PENDING', runs: 3 });
    expect((await claim(topic.id, agentA, { runId: 'run-three-c3' })).statusCode).toBe(200);

    // The third's machine goes away too: the next run's claim gives the topic up.
    await prisma.question.update({ where: { id: topic.id }, data: { progress: { running: [], failed: ['bundle'] } } });
    await lapse(topic.id);
    const fourth = await claim(topic.id, agentA, { runId: 'run-four-d4' });
    expect(fourth.statusCode).toBe(409);
    expect(fourth.json().error.message).toBe('Question was marked failed');
    expect(await prisma.question.findUniqueOrThrow({ where: { id: topic.id } })).toMatchObject({
      status: 'FAILED', runs: 3, progress: null,
      failedReason: 'given up after 3 runs, each of which ended before the lesson was posted.',
    });
  });

  it('gives up in the queue a topic whose last run went away, and hands on the next instead', async () => {
    const [topic] = (await queue(agentA, 1)).json().data;
    await prisma.question.update({ where: { id: topic.id }, data: { runs: 3 } });
    // Alive on its last run: left to it, while another run takes the rest.
    const others = ids(await queue(agentB, 6));
    expect(others).toHaveLength(5);
    expect(others).not.toContain(topic.id);
    expect((await prisma.question.findUniqueOrThrow({ where: { id: topic.id } })).status).toBe('PENDING');

    // Its machine goes away too: the next sweep gives it up, and hands on the next topic whose lease lapsed.
    await lapse(topic.id);
    await lapse(others[0]);
    expect((await queue(agentB, 1)).json().data).toEqual([expect.objectContaining({ id: others[0], runs: 0, maxRuns: 3 })]);
    expect(await prisma.question.findUniqueOrThrow({ where: { id: topic.id } })).toMatchObject({
      status: 'FAILED', failedReason: 'given up after 3 runs, each of which ended before the lesson was posted.',
    });
    expect((await claim(topic.id, agentA)).json().error.message).toBe('Question was marked failed');
  });

  it('takes the most runs from QUESTION_MAX_RUNS', async () => {
    process.env.QUESTION_MAX_RUNS = '1';
    const [topic] = (await queue(agentA, 1)).json().data;
    expect((await claim(topic.id, agentA, { runId: 'run-one-a1' })).json()).toMatchObject({ runs: 1, maxRuns: 1 });
    await lapse(topic.id);
    expect(ids(await queue(agentA, 6))).not.toContain(topic.id);
    expect((await prisma.question.findUniqueOrThrow({ where: { id: topic.id } })).failedReason)
      .toBe('given up after 1 run, which ended before the lesson was posted.');
  });
});
