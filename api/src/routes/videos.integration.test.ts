/**
 * The claim on a droplet's videos, against a real Postgres (DATABASE_URL). Skipped
 * unless INTEGRATION=1.
 *
 * Covers: starting and renewing an attempt, refusing a second run, the recovery queue
 * for a render whose machine died and for videos never claimed, the attempt cap, giving
 * up a render that went quiet on its last attempt, and giving the videos up.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = { course: 'AP Biology', topic: 'the light reactions', scenes: [], lyrics: [] };
const FACTCHECK = { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] };

d('video claim and queue', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let keyA = '';
  let keyB = '';
  let agentA = '';
  let userId = '';

  const asAgent = (key: string) => ({ authorization: `Bearer ${key}` });
  const claim = (answerId: string, payload: object = {}, key = keyA) =>
    app.inject({ method: 'POST', url: `/api/answers/${answerId}/video/claim`, headers: asAgent(key), payload });
  const fail = (answerId: string, payload: object, key = keyA) =>
    app.inject({ method: 'POST', url: `/api/answers/${answerId}/video/fail`, headers: asAgent(key), payload });
  const queue = (key = keyA, limit = 1) =>
    app.inject({ method: 'GET', url: `/api/videos/queue?limit=${limit}`, headers: asAgent(key) });
  const row = (id: string) => prisma.answer.findUniqueOrThrow({ where: { id } });
  const hoursAgo = (hours: number) => new Date(Date.now() - hours * 60 * 60_000);
  const goQuiet = (id: string) => prisma.answer.update({ where: { id }, data: { videoUpdatedAt: hoursAgo(2) } });

  async function lesson(opts: { bundle?: boolean; agentId?: string; brain?: string; createdAt?: Date } = {}) {
    const q = await prisma.question.create({ data: { userId, content: 'ATP synthesis', status: 'ANSWERED', brain: opts.brain } });
    const a = await prisma.answer.create({
      data: {
        questionId: q.id, agentId: opts.agentId ?? agentA, content: 'A lesson.', createdAt: opts.createdAt,
        ...(opts.bundle !== false && { bundle: BUNDLE, factcheck: FACTCHECK }),
      },
    });
    return a.id;
  }

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
    delete process.env.VIDEO_MAX_ATTEMPTS;
    delete process.env.STUDIO_VIDEOS;
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    const { generateApiKey } = await import('../lib/apikey.js');
    const ka = generateApiKey();
    const kb = generateApiKey();
    keyA = ka.raw;
    keyB = kb.raw;
    agentA = (await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: ka.hashed } })).id;
    await prisma.agent.create({ data: { name: 'other', apiKeyHash: kb.hashed } });
    userId = (await prisma.user.create({ data: { email: 'ada@example.com', googleId: 'g-ada', name: 'Ada' } })).id;
  });

  it('starts an attempt with what a fresh machine needs, then renews only that attempt', async () => {
    const id = await lesson();
    const first = await claim(id);
    expect(first.statusCode).toBe(200);
    expect(first.json()).toMatchObject({
      answerId: id, attempt: 1, maxAttempts: 2, status: 'RENDERING', bundle: BUNDLE, factcheck: FACTCHECK, brain: null,
    });
    expect((await row(id)).videoAttempts).toBe(1);

    const before = (await row(id)).videoUpdatedAt!;
    await new Promise((r) => setTimeout(r, 20));
    const renewed = await claim(id, { attempt: 1 });
    expect(renewed.statusCode).toBe(200);
    expect(renewed.json()).toMatchObject({ attempt: 1, maxAttempts: 2 });
    expect(renewed.json().bundle).toBeUndefined(); // the heartbeat does not resend the lesson's plan
    expect((await row(id)).videoUpdatedAt!.getTime()).toBeGreaterThan(before.getTime());

    // A second run, with no attempt or the wrong one, is turned away while the first is alive.
    for (const payload of [{}, { attempt: 2 }]) {
      const second = await claim(id, payload);
      expect(second.statusCode).toBe(409);
      expect(second.json().error.message).toMatch(/Another run/);
    }
    expect((await row(id)).videoAttempts).toBe(1);
  });

  it('carries the brain an admin picked for the topic, so a machine that recovers the render paints its art the same way', async () => {
    const id = await lesson({ brain: 'muse-spark' });
    await prisma.answer.update({ where: { id }, data: { model: 'muse-spark-1.3' } });
    expect((await claim(id)).json()).toMatchObject({ brain: 'muse-spark', model: 'muse-spark-1.3' });
    expect((await claim(id, { attempt: 1 })).json().brain).toBe('muse-spark');
    await goQuiet(id);
    expect((await queue()).json().data[0]).toMatchObject({ answerId: id, attempt: 2, brain: 'muse-spark', model: 'muse-spark-1.3' });
  });

  it('refuses another agent, a lesson with no bundle, finished videos and a bad attempt', async () => {
    const id = await lesson();
    expect((await claim(id, {}, keyB)).statusCode).toBe(403);
    expect((await claim(await lesson({ bundle: false }))).json().error.message).toMatch(/no bundle/);
    expect((await claim(id, { attempt: 0 })).statusCode).toBe(400);
    await prisma.answer.update({ where: { id }, data: { videoStatus: 'READY', songVideoUrl: 'https://x/s.mp4' } });
    expect((await claim(id)).json().error.message).toMatch(/already made/);
  });

  it('hands a render that went quiet to the next run as a new attempt, up to the cap', async () => {
    const id = await lesson();
    await claim(id);
    expect((await queue()).json().data).toEqual([]); // alive: not recoverable

    await goQuiet(id);
    const recovered = (await queue()).json().data;
    expect(recovered).toHaveLength(1);
    expect(recovered[0]).toMatchObject({ answerId: id, attempt: 2, maxAttempts: 2, status: 'RENDERING', bundle: BUNDLE, brain: null });
    expect((await queue()).json().data).toEqual([]); // just claimed: alive again

    // The run that went quiet comes back: its attempt is no longer the current one.
    expect((await claim(id, { attempt: 1 })).statusCode).toBe(409);
    expect((await fail(id, { reason: 'late', attempt: 1 })).statusCode).toBe(409);

    // An admin raises the cap before the next sweep: the third attempt is handed on.
    await goQuiet(id);
    process.env.VIDEO_MAX_ATTEMPTS = '3';
    expect((await queue()).json().data[0]).toMatchObject({ attempt: 3, maxAttempts: 3 });
  });

  it('gives up a render that went quiet on its last attempt, so the droplet says why and shows the lesson', async () => {
    const id = await lesson();
    await claim(id);
    await goQuiet(id);
    expect((await queue()).json().data[0].attempt).toBe(2);
    expect((await queue()).json().data).toEqual([]); // alive on its last attempt: left to it
    expect((await row(id)).videoStatus).toBe('RENDERING');

    // Another agent's, quiet on its last attempt too, is not this caller's to give up.
    const theirs = await lesson({ agentId: (await prisma.agent.findFirstOrThrow({ where: { name: 'other' } })).id });
    await prisma.answer.update({ where: { id: theirs }, data: { videoStatus: 'RENDERING', videoAttempts: 2, videoUpdatedAt: hoursAgo(2) } });

    await goQuiet(id);
    expect((await queue()).json().data).toEqual([]);
    expect(await row(id)).toMatchObject({
      videoStatus: 'FAILED', videoAttempts: 2,
      videoError: 'The machine making them went away before they were finished, on their last attempt (2 of 2).',
    });
    expect((await row(theirs)).videoStatus).toBe('RENDERING');

    // The run that went quiet comes back too late, and nobody may start a third attempt.
    expect((await claim(id, { attempt: 2 })).json().error.message).toMatch(/given up after 2 attempt/);
    // The learner's card: the lesson's text, and why there are no videos.
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    const cookie = `popsmosis_session=${app.jwt.sign({ sub: userId }, { expiresIn: SESSION_EXPIRES_IN })}`;
    const card = await app.inject({
      method: 'GET', url: `/api/questions/${(await row(id)).questionId}`, headers: { cookie, 'x-requested-with': 'XMLHttpRequest' },
    });
    expect(card.json().answer).toMatchObject({
      content: 'A lesson.', awaitingVideos: false,
      video: { status: 'FAILED', error: 'The machine making them went away before they were finished, on their last attempt (2 of 2).' },
    });
  });

  it('hands on videos never claimed a stale time after the post, for a day, oldest first with the stale renders', async () => {
    const other = (await prisma.agent.findFirstOrThrow({ where: { name: 'other' } })).id;
    const fresh = await lesson(); // just posted: its own claim may still come
    const old = await lesson({ createdAt: hoursAgo(26) }); // past the day: a text-only lesson from before the videos
    await lesson({ bundle: false, createdAt: hoursAgo(2) }); // nothing to make videos from
    await lesson({ agentId: other, createdAt: hoursAgo(2) });
    const unclaimed = await lesson({ createdAt: hoursAgo(2) });
    const quiet = await lesson({ createdAt: hoursAgo(3) });
    await claim(quiet);
    await goQuiet(quiet);

    // One at a time, the older first, whichever way it was stranded.
    expect((await queue()).json().data).toEqual([expect.objectContaining({ answerId: quiet, attempt: 2 })]);
    const handed = (await queue()).json().data;
    expect(handed).toHaveLength(1);
    expect(handed[0]).toMatchObject({
      answerId: unclaimed, attempt: 1, maxAttempts: 2, status: 'RENDERING', bundle: BUNDLE, factcheck: FACTCHECK,
    });
    expect((await queue(keyA, 5)).json().data).toEqual([]);
    expect((await row(fresh)).videoStatus).toBeNull();
    expect((await row(old)).videoStatus).toBeNull();

    // Claimed now, it is a render like any other: alive while it renews, recovered once it goes quiet.
    expect((await claim(unclaimed, { attempt: 1 })).statusCode).toBe(200);
    await goQuiet(unclaimed);
    expect((await queue()).json().data[0]).toMatchObject({ answerId: unclaimed, attempt: 2 });
  });

  it('leaves videos never claimed alone while the videos are turned off, but still recovers a render', async () => {
    const unclaimed = await lesson({ createdAt: hoursAgo(2) });
    const quiet = await lesson({ createdAt: hoursAgo(3) });
    await claim(quiet);
    await goQuiet(quiet);
    process.env.STUDIO_VIDEOS = 'off';
    expect((await queue(keyA, 5)).json().data.map((x: { answerId: string }) => x.answerId)).toEqual([quiet]);
    expect((await row(unclaimed)).videoStatus).toBeNull();
    process.env.STUDIO_VIDEOS = 'lesson'; // the lesson video alone is still videos
    expect((await queue(keyA, 5)).json().data.map((x: { answerId: string }) => x.answerId)).toEqual([unclaimed]);
  });

  it('never recovers videos that were given up, never started, finished, or another agent\'s', async () => {
    const failed = await lesson();
    await claim(failed);
    await fail(failed, { reason: 'the song was refused', attempt: 1 });
    await lesson({ createdAt: hoursAgo(48) }); // never started: a lesson from before the videos, or with them turned off
    const ready = await lesson();
    await prisma.answer.update({ where: { id: ready }, data: { videoStatus: 'READY', videoUpdatedAt: new Date(0) } });
    const theirs = await lesson({ agentId: (await prisma.agent.findFirstOrThrow({ where: { name: 'other' } })).id });
    await prisma.answer.update({ where: { id: theirs }, data: { videoStatus: 'RENDERING', videoUpdatedAt: new Date(0) } });
    await prisma.answer.updateMany({ data: { videoUpdatedAt: new Date(0) } });
    expect((await queue(keyA, 5)).json().data).toEqual([]);
  });

  it('gives two overlapping runs different droplets to recover', async () => {
    const ids = [await lesson(), await lesson()];
    for (const id of ids) {
      await claim(id);
      await goQuiet(id);
    }
    const [a, b] = await Promise.all([queue(), queue()]);
    const got = [...a.json().data, ...b.json().data].map((x: { answerId: string }) => x.answerId).sort();
    expect(got).toEqual([...ids].sort());

    // One call handing out several: every one comes with the plan a fresh machine needs.
    process.env.VIDEO_MAX_ATTEMPTS = '3'; // both have used the default two
    for (const id of ids) await goQuiet(id);
    const batch = (await queue(keyA, 5)).json().data;
    expect(batch).toHaveLength(2);
    expect(batch.every((x: { bundle: unknown }) => x.bundle !== undefined)).toBe(true);
  });

  it('gives the videos up for the current attempt only, and a given-up render can be started again by hand', async () => {
    const id = await lesson();
    await claim(id);
    expect((await fail(id, { reason: 'render failed: ffmpeg died', attempt: 2 })).statusCode).toBe(409);
    expect((await fail(id, { attempt: 1 })).statusCode).toBe(400);
    const given = await fail(id, { reason: 'render failed: ffmpeg died', attempt: 1 });
    expect(given.statusCode).toBe(200);
    expect(given.json()).toMatchObject({ status: 'FAILED', error: 'render failed: ffmpeg died' });
    expect((await fail(id, { reason: 'again', attempt: 1 })).statusCode).toBe(409);

    const again = await claim(id);
    expect(again.json()).toMatchObject({ attempt: 2, status: 'RENDERING' });
    expect((await row(id)).videoError).toBeNull();
    await fail(id, { reason: 'still failing', attempt: 2 });
    expect((await claim(id)).json().error.message).toMatch(/given up after 2 attempt/);
  });

  it('keeps what the failed tries took and cost with the give-up, from the current attempt only', async () => {
    const id = await lesson();
    await claim(id);
    const bill = { steps: [{ step: 'audio', seconds: 95, tries: 2, costUsd: 0.16,
      models: [{ model: 'lyria-3.5', calls: 2, costUsd: 0.16, credits: null }] }], seconds: 140 };
    expect((await fail(id, { reason: 'x', attempt: 1, breakdown: { steps: 'audio' } })).statusCode).toBe(400);
    expect((await fail(id, { reason: 'late', attempt: 2, breakdown: bill })).statusCode).toBe(409);
    expect((await row(id)).videoBreakdown).toBeNull(); // a run that does not hold the render writes nothing

    expect((await fail(id, { reason: 'timing failed: no vocals', attempt: 1, breakdown: bill })).statusCode).toBe(200);
    expect((await row(id)).videoBreakdown).toEqual(bill);

    // Started again by hand and given up without one (an older studio): the bill already there stays.
    await claim(id);
    expect((await fail(id, { reason: 'again', attempt: 2 })).statusCode).toBe(200);
    expect((await row(id)).videoBreakdown).toEqual(bill);
  });
});
