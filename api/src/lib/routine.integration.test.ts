/**
 * The trailing fire's "is there actually work?" query, against a real Postgres
 * (DATABASE_URL). Skipped unless INTEGRATION=1, like flow.integration.test.ts.
 *
 * This is the one piece of routine.ts the unit tests cannot cover: raw SQL and
 * a Prisma enum filter, where a mistake is invisible until production and costs
 * either a wasted cloud session per window or a question nobody is woken for.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('queuesHaveWork', () => {
  let prisma: (typeof import('./prisma.js'))['default'];
  let queuesHaveWork: (typeof import('./routine.js'))['queuesHaveWork'];

  beforeAll(async () => {
    prisma = (await import('./prisma.js')).default;
    queuesHaveWork = (await import('./routine.js')).queuesHaveWork;
  });
  beforeEach(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    await prisma.$disconnect();
  });

  const makeUser = () => prisma.user.create({
    data: { email: `u${Date.now()}${Math.random()}@example.com`, googleId: `g${Date.now()}${Math.random()}`, name: 'Ada' },
  });
  const makeAgent = () => prisma.agent.create({
    data: { name: `popsmosis${Date.now()}${Math.random()}`, apiKeyHash: `h${Date.now()}${Math.random()}` },
  });

  it('is false on an empty database', async () => {
    expect(await queuesHaveWork()).toBe(false);
  });

  it('is true while a question is PENDING, false once it is ANSWERED', async () => {
    const user = await makeUser();
    const agent = await makeAgent();
    const question = await prisma.question.create({ data: { userId: user.id, content: 'how do I export a mesh?' } });
    expect(await queuesHaveWork()).toBe(true);

    await prisma.answer.create({ data: { questionId: question.id, agentId: agent.id, content: 'like so' } });
    await prisma.question.update({ where: { id: question.id }, data: { status: 'ANSWERED' } });
    expect(await queuesHaveWork()).toBe(false);
  });

  it('follows the reply thread: true on a human reply, false once the agent answers it', async () => {
    const user = await makeUser();
    const agent = await makeAgent();
    const question = await prisma.question.create({ data: { userId: user.id, content: 'how do I export a mesh?' } });
    const answer = await prisma.answer.create({ data: { questionId: question.id, agentId: agent.id, content: 'like so' } });
    await prisma.question.update({ where: { id: question.id }, data: { status: 'ANSWERED' } });
    expect(await queuesHaveWork()).toBe(false);

    const human = await prisma.reply.create({ data: { answerId: answer.id, userId: user.id, content: 'that did not work' } });
    expect(await queuesHaveWork()).toBe(true);

    await prisma.reply.create({ data: { answerId: answer.id, agentId: agent.id, replyToId: human.id, content: 'try this' } });
    expect(await queuesHaveWork()).toBe(false);

    // Only the newest reply decides, so a second human reply re-opens the thread.
    await prisma.reply.create({ data: { answerId: answer.id, userId: user.id, content: 'still stuck' } });
    expect(await queuesHaveWork()).toBe(true);
  });
});
