/**
 * Commands against a real Postgres (DATABASE_URL). Skipped unless
 * INTEGRATION=1, like the other integration files.
 *
 * Covers: a question and a human reply naming their client -> the advisor
 * seeing it in both queues -> commands accepted in response to a client that
 * runs them, refused in response to the web page -> commands served back on
 * the thread, which is how Zed's Zoey panel reads them -> the context such a
 * client reports about itself reaching the advisor, and nobody else.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('commands', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./../lib/prisma.js'))['default'];
  let cookieA: string;
  let zedToken: string;
  let agentKey: string;
  const tag = `cmd-${Date.now()}`;

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const asBearer = (key: string) => ({ authorization: `Bearer ${key}` });
  const setScalar = { command: 'mic set Abyss/Materials/MI_Rock.zmic scalar Roughness 0.8', description: 'Make the rock rougher' };
  const save = { command: 'mic save Abyss/Materials/MI_Rock.zmic' };

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    process.env.JWT_SECRET ||= 'integration-test-secret';
    const { buildApp } = await import('./../index.js');
    const { generateApiKey } = await import('./../lib/apikey.js');
    prisma = (await import('./../lib/prisma.js')).default;
    app = await buildApp();
    await app.ready();

    const userA = await prisma.user.create({
      data: { email: `${tag}-a@example.com`, googleId: `${tag}-a`, name: 'Ada', status: 'APPROVED', reviewedAt: new Date() },
    });
    const { SESSION_EXPIRES_IN } = await import('./../lib/session.js');
    cookieA = `zoey_session=${app.jwt.sign({ sub: userA.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    // Zed signs in with a personal access token, not the cookie.
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

  const ask = async (headers: Record<string, string>, payload: Record<string, unknown>) => {
    const r = await app.inject({ method: 'POST', url: '/api/questions', headers, payload });
    expect(r.statusCode).toBe(201);
    return r.json();
  };

  it('records the client a question was asked from, and refuses one it does not know', async () => {
    const bad = await app.inject({ method: 'POST', url: '/api/questions', headers: asBearer(zedToken), payload: { content: `${tag} make the rock rougher`, client: 'vim' } });
    expect(bad.statusCode).toBe(400);
    expect(bad.json().error.message).toMatch(/client must be one of: zed/);

    const fromZed = await ask(asBearer(zedToken), { content: `${tag} make the rock rougher`, client: 'zed' });
    expect(fromZed.client).toBe('zed');
    const fromWeb = await ask(asUser(cookieA), { content: `${tag} what is a material instance?` });
    expect(fromWeb.client).toBeNull();

    // The advisor reads it off the queue it answers from.
    const queue = await app.inject({ method: 'GET', url: '/api/questions/queue?limit=20', headers: asBearer(agentKey) });
    const queued = queue.json().data.find((q: { id: string }) => q.id === fromZed.id);
    if (queued) expect(queued.client).toBe('zed'); // a shared dev database can hold 20 older pending questions
  });

  it('takes commands in an answer only to a question from a client that runs them', async () => {
    const fromWeb = await ask(asUser(cookieA), { content: `${tag} make the grass greener` });
    const refused = await app.inject({ method: 'POST', url: `/api/questions/${fromWeb.id}/answer`, headers: asBearer(agentKey), payload: { content: 'Here is how.', commands: [setScalar] } });
    expect(refused.statusCode).toBe(400);
    expect(refused.json().error.message).toMatch(/came from the web page/);
    // Refused whole: the question is still waiting, so the advisor can answer again without them.
    const plain = await app.inject({ method: 'POST', url: `/api/questions/${fromWeb.id}/answer`, headers: asBearer(agentKey), payload: { content: 'Here is how.', commands: [] } });
    expect(plain.statusCode).toBe(201);
    expect(plain.json().commands).toEqual([]);

    const fromZed = await ask(asBearer(zedToken), { content: `${tag} make the rock rougher please`, client: 'zed' });
    const malformed = await app.inject({ method: 'POST', url: `/api/questions/${fromZed.id}/answer`, headers: asBearer(agentKey), payload: { content: 'Done.', commands: [{ cmd: 'x' }] } });
    expect(malformed.statusCode).toBe(400);
    expect(malformed.json().error.message).toBe('commands[0].command is required');

    const answered = await app.inject({ method: 'POST', url: `/api/questions/${fromZed.id}/answer`, headers: asBearer(agentKey), payload: { content: 'Run these two.', commands: [setScalar, save] } });
    expect(answered.statusCode).toBe(201);
    expect(answered.json().commands).toEqual([setScalar, { ...save, description: null }]);

    // Served back on the card, which is what the Zed panel polls.
    const polled = await app.inject({ method: 'GET', url: `/api/questions?ids=${fromZed.id}`, headers: asBearer(zedToken) });
    expect(polled.json().data[0].answer.commands).toEqual([setScalar, { ...save, description: null }]);
    const web = await app.inject({ method: 'GET', url: `/api/questions/${fromWeb.id}`, headers: asUser(cookieA) });
    expect(web.json().answer.commands).toEqual([]);

    // A reply that it did not work hands the advisor the lines it sent, so it can see what to change.
    await app.inject({ method: 'POST', url: `/api/answers/${answered.json().id}/replies`, headers: asBearer(zedToken), payload: { content: 'Nothing changed.', client: 'zed' } });
    const queue = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asBearer(agentKey) });
    const thread = queue.json().data.find((t: { answer: { id: string } }) => t.answer.id === answered.json().id);
    expect(thread.answer.commands).toEqual([setScalar, { ...save, description: null }]);
  });

  it('takes commands in a reply only to a human reply from a client that runs them', async () => {
    // Asked on the web page, carried on from Zed: the reply's own client is what counts.
    const q = await ask(asUser(cookieA), { content: `${tag} which material does the rock use?` });
    const a = await app.inject({ method: 'POST', url: `/api/questions/${q.id}/answer`, headers: asBearer(agentKey), payload: { content: 'MI_Rock.' } });
    const answerId = a.json().id;

    const badClient = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(zedToken), payload: { content: 'x', client: 'emacs' } });
    expect(badClient.statusCode).toBe(400);

    const fromZed = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(zedToken), payload: { content: 'Make it rougher for me.', client: 'zed' } });
    expect(fromZed.statusCode).toBe(201);
    expect(fromZed.json().client).toBe('zed');

    const queue = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asBearer(agentKey) });
    const thread = queue.json().data.find((t: { answer: { id: string } }) => t.answer.id === answerId);
    expect(thread.question.client).toBeNull();
    expect(thread.replyTo.client).toBe('zed');

    const agentReply = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(agentKey),
      payload: { content: 'Run these.', replyToId: fromZed.json().id, commands: [setScalar, save] },
    });
    expect(agentReply.statusCode).toBe(201);
    expect(agentReply.json().commands).toEqual([setScalar, { ...save, description: null }]);

    // The next human reply comes from the web page, so the answer to it cannot carry any.
    const fromWeb = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieA), payload: { content: 'And the moss?' } });
    expect(fromWeb.json().client).toBeNull();
    const refused = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(agentKey),
      payload: { content: 'Run this.', replyToId: fromWeb.json().id, commands: [save] },
    });
    expect(refused.statusCode).toBe(400);
    expect(refused.json().error.message).toMatch(/came from the web page/);
    const plain = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(agentKey), payload: { content: 'Open MI_Moss.', replyToId: fromWeb.json().id } });
    expect(plain.statusCode).toBe(201);

    const card = await app.inject({ method: 'GET', url: `/api/questions/${q.id}`, headers: asBearer(zedToken) });
    const replies = card.json().answer.replies as { authorType: string; client: string | null; commands: unknown[] }[];
    expect(replies.map((r) => [r.authorType, r.client, r.commands.length])).toEqual([
      ['HUMAN', 'zed', 0], ['AGENT', null, 2], ['HUMAN', null, 0], ['AGENT', null, 0],
    ]);
  });

  it('hands the advisor the context a client reports about itself, taken only from a client that runs commands', async () => {
    const context = 'camera 1200 -340 150 yaw 90\nselected: actor BP_Rock_2\n\tRoughness\t0.5';
    const fromWeb = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: `${tag} make this rock rougher`, context } });
    expect(fromWeb.statusCode).toBe(400);
    expect(fromWeb.json().error.message).toMatch(/context can only come from a client that runs commands/);

    const q = await ask(asBearer(zedToken), { content: `${tag} make this rock rougher`, client: 'zed', context: `\n${context}  ` });
    expect(q.context).toBeUndefined(); // the pages show every user's questions, so context stays off them
    expect((await prisma.question.findUnique({ where: { id: q.id } }))!.context).toBe(context);
    const questionQueue = await app.inject({ method: 'GET', url: '/api/questions/queue?limit=20', headers: asBearer(agentKey) });
    const queued = questionQueue.json().data.find((x: { id: string }) => x.id === q.id);
    if (queued) expect(queued.context).toBe(context); // a shared dev database can hold 20 older pending questions

    const a = await app.inject({ method: 'POST', url: `/api/questions/${q.id}/answer`, headers: asBearer(agentKey), payload: { content: 'Run this.', commands: [setScalar] } });
    const answerId = a.json().id;
    const replyContext = 'camera 1250 -300 150 yaw 95\nselected: actor BP_Rock_3';
    const noClient = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieA), payload: { content: 'And this one?', context: replyContext } });
    expect(noClient.statusCode).toBe(400);
    expect(noClient.json().error.message).toMatch(/context can only come from a client that runs commands/);
    const fromZed = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(zedToken), payload: { content: 'And this one?', client: 'zed', context: replyContext } });
    expect(fromZed.statusCode).toBe(201);
    expect(fromZed.json().context).toBeUndefined();

    const queue = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asBearer(agentKey) });
    const thread = queue.json().data.find((t: { answer: { id: string } }) => t.answer.id === answerId);
    expect(thread.question.context).toBe(context);
    expect(thread.replyTo.context).toBe(replyContext);
    expect(thread.thread.map((r: { context: string | null }) => r.context)).toEqual([replyContext]);

    // The advisor reports nothing about itself: context on its reply is refused whole.
    const agentContext = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(agentKey),
      payload: { content: 'Run this too.', replyToId: fromZed.json().id, commands: [setScalar], context: 'camera 0 0 0' },
    });
    expect(agentContext.statusCode).toBe(400);
    expect(agentContext.json().error.message).toMatch(/context is for human replies only/);
    const agentReply = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asBearer(agentKey),
      payload: { content: 'Run this too.', replyToId: fromZed.json().id, commands: [setScalar] },
    });
    expect(agentReply.statusCode).toBe(201);

    const card = await app.inject({ method: 'GET', url: `/api/questions/${q.id}`, headers: asBearer(zedToken) });
    expect(card.json().answer.replies.map((r: { context?: string }) => r.context)).toEqual([undefined, undefined]);
  });
});
