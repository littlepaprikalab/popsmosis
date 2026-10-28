/**
 * Where the studio is while it makes a droplet (lib/progress.ts), against a real Postgres (DATABASE_URL).
 * Skipped unless INTEGRATION=1.
 *
 * Covers: the lesson's report on the topic and the videos' on the answer, who may send them and when, what the
 * page reads back, and the lesson text held back until the videos are done.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = { course: 'AP Biology', topic: 'the light reactions', scenes: [], lyrics: [] };
const FACTCHECK = { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] };
const LESSON_BILL = {
  steps: [
    { step: 'request', seconds: 0.4, tries: 1, models: [], tools: [] },
    { step: 'bundle', seconds: 196, tries: 1, models: [{ model: 'claude-opus-5-5', calls: 1, costUsd: 0.53, credits: null }], tools: [] },
  ],
  seconds: 196.4,
};
const ART_BILL = { steps: [{ step: 'art', seconds: 76, tries: 1, models: [{ model: 'claude-haiku-5-5', calls: 3, costUsd: 0.008, credits: null }] }], seconds: 76 };

d('progress reports', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let keyA = '';
  let keyB = '';
  let cookie = '';
  let userId = '';
  let questionId = '';

  const asAgent = (key: string) => ({ authorization: `Bearer ${key}` });
  const asUser = () => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const lessonReport = (payload: unknown, key = keyA, id = questionId) =>
    app.inject({ method: 'PUT', url: `/api/questions/${id}/progress`, headers: asAgent(key), payload: payload as object });
  const videoReport = (answerId: string, payload: unknown, key = keyA) =>
    app.inject({ method: 'PUT', url: `/api/answers/${answerId}/video/progress`, headers: asAgent(key), payload: payload as object });
  const claim = (key = keyA) => app.inject({ method: 'POST', url: `/api/questions/${questionId}/claim`, headers: asAgent(key), payload: {} });
  const claimVideos = (answerId: string, payload: object = {}) =>
    app.inject({ method: 'POST', url: `/api/answers/${answerId}/video/claim`, headers: asAgent(keyA), payload });
  const post = (payload: object = { content: 'The lesson text.', bundle: BUNDLE, factcheck: FACTCHECK, breakdown: LESSON_BILL }) =>
    app.inject({ method: 'POST', url: `/api/questions/${questionId}/answer`, headers: asAgent(keyA), payload });
  const card = async () => {
    const res = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser() });
    expect(res.statusCode).toBe(200);
    return res.json();
  };

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
    delete process.env.LESSON_HOLD_MINUTES;
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    const { generateApiKey } = await import('../lib/apikey.js');
    const ka = generateApiKey();
    const kb = generateApiKey();
    keyA = ka.raw;
    keyB = kb.raw;
    await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: ka.hashed } });
    await prisma.agent.create({ data: { name: 'other', apiKeyHash: kb.hashed } });
    userId = (await prisma.user.create({ data: { email: 'ada@example.com', googleId: 'g-ada', name: 'Ada' } })).id;
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    cookie = `popsmosis_session=${app.jwt.sign({ sub: userId }, { expiresIn: SESSION_EXPIRES_IN })}`;
    questionId = (await prisma.question.create({ data: { userId, content: 'ATP synthesis' } })).id;
  });

  it('shows a topic nobody has picked up as the whole droplet still to make', async () => {
    const q = await card();
    expect(q.status).toBe('PENDING');
    expect(q.progress).toEqual({ running: [], failed: [], videos: true, breakdown: null });
  });

  it('shows the lesson being written, step by step, with its bill so far', async () => {
    expect((await claim()).statusCode).toBe(200);
    const sent = await lessonReport({ running: [{ step: 'factcheck', seconds: 30 }], breakdown: LESSON_BILL, videos: true });
    expect(sent.statusCode).toBe(204);

    const q = await card();
    expect(q.progress.running).toHaveLength(1);
    expect(q.progress.running[0].step).toBe('factcheck');
    const started = Date.parse(q.progress.running[0].startedAt);
    expect(Date.now() - started).toBeGreaterThanOrEqual(29_000);
    expect(Date.now() - started).toBeLessThan(40_000);
    expect(q.progress.running[0].seconds).toBeGreaterThanOrEqual(30); // what the page counts on from
    expect(q.progress.running[0].seconds).toBeLessThan(40);
    expect(q.progress.videos).toBe(true);
    expect(q.progress.breakdown.steps.map((s: { step: string }) => s.step)).toEqual(['request', 'bundle']);
    expect(q.progress.breakdown.costUsd).toBe(0.53);

    // A step that failed is said so until it runs again; a run whose lease lapsed has nothing running.
    expect((await lessonReport({ running: [], failed: ['factcheck'], breakdown: LESSON_BILL, videos: true })).statusCode).toBe(204);
    expect((await card()).progress).toMatchObject({ running: [], failed: ['factcheck'] });
    expect((await lessonReport({ running: [{ step: 'factcheck', seconds: 0 }], videos: true })).statusCode).toBe(204);
    await prisma.question.update({ where: { id: questionId }, data: { claimedAt: new Date(Date.now() - 2 * 60 * 60_000) } });
    expect((await card()).progress.running).toEqual([]);
  });

  it('takes the lesson\'s report only from the agent holding the claim, before the lesson is posted', async () => {
    expect((await lessonReport({ running: [] })).statusCode).toBe(409); // never claimed
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [] }, keyB)).statusCode).toBe(409);
    expect((await lessonReport({ running: [{ step: 'bundle' }] })).statusCode).toBe(400);
    expect((await lessonReport({ running: [] }, keyA, 'nope')).statusCode).toBe(404);
    expect((await app.inject({ method: 'PUT', url: `/api/questions/${questionId}/progress`, payload: { running: [] } })).statusCode).toBe(401);
    // A report is not a sign of life the agent row needs: the claim keeps that, and every run would queue on it.
    const seen = async () => (await prisma.agent.findFirstOrThrow({ where: { name: 'teacher' } })).lastSeenAt;
    const before = await seen();
    await new Promise((r) => setTimeout(r, 20));
    expect((await lessonReport({ running: [{ step: 'bundle', seconds: 0 }], breakdown: LESSON_BILL, videos: true })).statusCode).toBe(204);
    expect(await seen()).toEqual(before);
    expect((await post()).statusCode).toBe(201);
    // Posted: of the lesson's report only whether videos follow stays, not its bill, which every list would carry.
    expect((await prisma.question.findUniqueOrThrow({ where: { id: questionId } })).progress).toEqual({ videos: true });
    const late = await lessonReport({ running: [] });
    expect(late.statusCode).toBe(409);
    expect(late.json().error.message).toBe('Question already has an answer');
  });

  it('drops the bill so far when READY comes without one, and holds nothing for a lesson posted without a bundle', async () => {
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'factcheck', seconds: 0 }], videos: true })).statusCode).toBe(204);
    const answerId = (await post()).json().id;
    expect((await claimVideos(answerId)).statusCode).toBe(200);
    expect((await videoReport(answerId, { attempt: 1, running: [], breakdown: ART_BILL })).statusCode).toBe(204);
    const ready = await app.inject({ method: 'PATCH', url: `/api/answers/${answerId}/video`, headers: asAgent(keyA),
      payload: { status: 'READY', attempt: 1, songVideoUrl: 'https://cdn.example/song.mp4' } });
    expect(ready.statusCode).toBe(200);
    expect((await prisma.answer.findUniqueOrThrow({ where: { id: answerId } })).videoBreakdown).toBeNull();
    expect((await card()).answer.breakdown.steps.map((s: { step: string }) => s.step)).toEqual(['request', 'bundle']);

    questionId = (await prisma.question.create({ data: { userId, content: 'Osmosis' } })).id;
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'factcheck', seconds: 0 }], videos: true })).statusCode).toBe(204);
    expect((await post({ content: 'A lesson written by hand.' })).statusCode).toBe(201); // no bundle: no videos can come
    expect((await card()).answer.awaitingVideos).toBe(false);
  });

  it('drops the lesson\'s report when the topic is given up, and keeps nothing when none said whether videos follow', async () => {
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'bundle', seconds: 0 }], breakdown: LESSON_BILL })).statusCode).toBe(204);
    const failed = await app.inject({ method: 'POST', url: `/api/questions/${questionId}/fail`, headers: asAgent(keyA), payload: { reason: 'bundle failed' } });
    expect(failed.statusCode).toBe(200);
    expect((await prisma.question.findUniqueOrThrow({ where: { id: questionId } })).progress).toBeNull();
    expect((await card()).progress).toBeNull();

    questionId = (await prisma.question.create({ data: { userId, content: 'Osmosis' } })).id;
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'bundle', seconds: 0 }], breakdown: LESSON_BILL })).statusCode).toBe(204);
    expect((await post()).statusCode).toBe(201);
    expect((await prisma.question.findUniqueOrThrow({ where: { id: questionId } })).progress).toBeNull();
    expect((await card()).answer.awaitingVideos).toBe(false); // nobody said videos follow: a text-only droplet
  });

  it('holds the lesson text back while its videos are made, then shows it with them', async () => {
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'factcheck', seconds: 0 }], videos: true })).statusCode).toBe(204);
    const answerId = (await post()).json().id;

    // Posted, its videos not claimed yet: the studio said they are coming, so the text waits already.
    let q = await card();
    expect(q.answer.video).toBeNull();
    expect(q.answer.awaitingVideos).toBe(true);
    expect(q.progress).toMatchObject({ running: [], videos: true });

    expect((await claimVideos(answerId)).json().attempt).toBe(1);
    const sent = await videoReport(answerId, { attempt: 1, running: [{ step: 'art', seconds: 5 }, { step: 'audio', seconds: 5 }], breakdown: ART_BILL });
    expect(sent.statusCode).toBe(204);
    q = await card();
    expect(q.answer.awaitingVideos).toBe(true);
    expect(q.answer.content).toBe('The lesson text.'); // the page holds it back; the learner's own data is not withheld
    expect(q.progress.running.map((r: { step: string }) => r.step)).toEqual(['art', 'audio']);
    expect(q.progress.breakdown.steps.map((s: { step: string }) => s.step)).toEqual(['request', 'bundle', 'art']);
    expect(q.answer.breakdown.steps).toHaveLength(3); // the bill under the lesson fills in as the videos are made

    // READY with the last bill: the text shows with the videos, and nothing is in progress any more. (One sent
    // without a bill drops the bill so far, which would read as the total: tested below.)
    const ready = await app.inject({
      method: 'PATCH', url: `/api/answers/${answerId}/video`, headers: asAgent(keyA),
      payload: { status: 'READY', attempt: 1, songVideoUrl: 'https://cdn.example/song.mp4', breakdown: ART_BILL },
    });
    expect(ready.statusCode).toBe(200);
    q = await card();
    expect(q.answer.awaitingVideos).toBe(false);
    expect(q.progress).toBeNull();
    expect((await videoReport(answerId, { attempt: 1, running: [] })).statusCode).toBe(409);
  });

  it('shows the text without waiting for a text-only droplet, and after 20 minutes whatever the videos do', async () => {
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'factcheck', seconds: 0 }], videos: false })).statusCode).toBe(204);
    const answerId = (await post()).json().id;
    let q = await card();
    expect(q.answer.awaitingVideos).toBe(false);
    expect(q.progress).toBeNull();

    // Its videos claimed after all (by another machine, or by hand): a lesson already shown stays shown.
    expect((await claimVideos(answerId)).statusCode).toBe(200);
    expect((await card()).answer.awaitingVideos).toBe(false);

    // A droplet whose videos were coming: held while they render, then shown 20 minutes on whatever they do.
    questionId = (await prisma.question.create({ data: { userId, content: 'Osmosis' } })).id;
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'factcheck', seconds: 0 }], videos: true })).statusCode).toBe(204);
    const held = (await post()).json().id;
    expect((await claimVideos(held)).statusCode).toBe(200);
    expect((await card()).answer.awaitingVideos).toBe(true);
    await prisma.answer.update({ where: { id: held }, data: { createdAt: new Date(Date.now() - 21 * 60_000) } });
    q = await card();
    expect(q.answer.awaitingVideos).toBe(false);
    expect(q.answer.video.status).toBe('RENDERING');
    expect(q.progress).toMatchObject({ videos: true }); // still rendering: the bill under the text keeps filling in
  });

  it('counts the reports in a rate-limit bucket of their own, so a burst never starves the claims', async () => {
    expect((await claim()).statusCode).toBe(200);
    const codes: number[] = [];
    for (let i = 0; i < 301; i++) codes.push((await lessonReport({ running: [{ step: 'bundle', seconds: i }] })).statusCode);
    expect(codes.slice(0, 300).every((c) => c === 204)).toBe(true);
    expect(codes[300]).toBe(429);
    expect((await claim()).statusCode).toBe(200); // the key's own budget, for its claims, uploads and READY, is untouched
  });

  it('takes the videos\' report only while they render, from the attempt that holds them', async () => {
    expect((await claim()).statusCode).toBe(200);
    expect((await lessonReport({ running: [{ step: 'factcheck', seconds: 0 }], videos: true })).statusCode).toBe(204);
    const answerId = (await post()).json().id;
    expect((await videoReport(answerId, { running: [] })).statusCode).toBe(409); // not started
    expect((await claimVideos(answerId)).statusCode).toBe(200);
    expect((await videoReport(answerId, { attempt: 2, running: [] })).statusCode).toBe(409);
    expect((await videoReport(answerId, { attempt: 0, running: [] })).statusCode).toBe(400);
    expect((await videoReport(answerId, { attempt: 1, running: [], videos: true })).statusCode).toBe(400);
    expect((await videoReport(answerId, { attempt: 1, running: [] }, keyB)).statusCode).toBe(403);
    expect((await videoReport('nope', { attempt: 1, running: [] })).statusCode).toBe(404);
    expect((await videoReport(answerId, { attempt: 1, running: [{ step: 'render', seconds: 1 }] })).statusCode).toBe(204);

    // A report without a bill keeps the last one; a new attempt starts with nothing running and a second
    // attempt never holds the text back, so a lesson once shown stays shown.
    expect((await videoReport(answerId, { attempt: 1, running: [{ step: 'art', seconds: 1 }], breakdown: ART_BILL })).statusCode).toBe(204);
    expect((await videoReport(answerId, { attempt: 1, running: [{ step: 'locate', seconds: 0 }] })).statusCode).toBe(204);
    expect((await prisma.answer.findUniqueOrThrow({ where: { id: answerId } })).videoBreakdown).toEqual(ART_BILL);
    await prisma.answer.update({ where: { id: answerId }, data: { videoUpdatedAt: new Date(Date.now() - 2 * 60 * 60_000) } });
    let q = await card();
    expect(q.answer.video.status).toBe('FAILED'); // gone quiet
    expect(q.progress).toBeNull();
    expect((await claimVideos(answerId)).json().attempt).toBe(2);
    q = await card();
    expect(q.answer.awaitingVideos).toBe(false);
    expect(q.progress.running).toEqual([]);
    expect(q.progress.breakdown.steps.map((s: { step: string }) => s.step)).toEqual(['request', 'bundle', 'art']);
  });
});
