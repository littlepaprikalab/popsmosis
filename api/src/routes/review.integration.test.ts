/**
 * An admin's review of a droplet (lib/review.ts), against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: the code checks sent with the lesson and the art checks sent with READY, kept off the cards; the review
 * read back by an admin only; and a scene's picture read out of the private bucket for an admin.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { STORAGE_SETTINGS } from '../lib/storage.js';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = {
  course: 'Biology', topic: 'how antibiotics kill bacteria', core_idea: 'Antibiotics block jobs only bacteria have.',
  facts: [{ id: 'f1', text: 'Some antibiotics stop cell-wall pieces joining.', source_quote: '' }],
  misconception: 'Antibiotics do not cure colds.',
  scenes: [{ id: 'hook', image_prompt: 'A person with a cold.', structures: [] }],
  lyrics: [{ id: 'c1', section: 'chorus', text: 'Block the jobs, bacteria fall', names: [], fact_id: 'f1' }],
  narration: [], direction: [],
};
const FACTCHECK = {
  status: 'passed', reason: '', rounds: 2,
  lines: [
    { kind: 'lyric', id: 'c1', text: 'Block the jobs, bacteria fall', verdict: 'SUPPORTED', source: 'f1', round: 1, problem: '' },
    { kind: 'lesson', id: 'l1', text: 'Ask a follow-up.', verdict: 'FILLER', source: '', round: 1, problem: '' },
    { kind: 'tag', id: 't1', text: 'biology', verdict: 'UNSUPPORTED', source: '', round: 1, problem: 'not a tag' },
  ],
  corrections: [{ round: 1, kind: 'scene', ids: ['overview'], before: 'as a Campbell Biology figure', after: 'as a textbook figure', problem: 'Names a brand.' }],
};
const CHECKS = { problems: [
  { line_id: 'n4', rule: 'not-in-scene', detail: 'names bacterium, which scene "analogy" does not draw', repaired: false },
  { line_id: 'c2', rule: 'spotlight', detail: 'moved to the line that names it', repaired: true },
] };
const ART = { scenes: [
  {
    scene: 'strep-case', file: 'a1/art/strep-case.png', model: 'muse-image-1.0', kept: 2,
    check: { text: [], misplaced: [], unnamed: [], wrong: ['throat outside the head'], notes: [] },
    tries: [
      { try: 1, declined: 'Muse Image declined the picture' },
      { try: 2, text: [], misplaced: [], unnamed: [], wrong: ['throat outside the head'], notes: [] },
    ],
  },
  { scene: 'chart', file: 'a1/art/chart.png', model: null, kept: null, check: null, tries: [] },
] };
const R2 = {
  R2_ACCOUNT_ID: '0123456789abcdef0123456789abcdef',
  R2_ACCESS_KEY_ID: '9f2c4e1a7b3d5f6e8a0c2e4b6d8f0a1c',
  R2_SECRET_ACCESS_KEY: 'a'.repeat(64),
  R2_PUBLIC_URL: 'https://media.popsmosis.com',
};
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex');

d('an admin reviews a droplet', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let key = '';
  let learner = '';
  let admin = '';
  let questionId = '';

  const asAgent = () => ({ authorization: `Bearer ${key}` });
  const as = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const post = (url: string, payload: object = {}) => app.inject({ method: 'POST', url, headers: asAgent(), payload });
  const answer = async (extra: object = {}) => {
    await post(`/api/questions/${questionId}/claim`);
    return post(`/api/questions/${questionId}/answer`, { content: 'A lesson.', bundle: BUNDLE, factcheck: FACTCHECK, ...extra });
  };
  const ready = (answerId: string, extra: object = {}) => app.inject({
    method: 'PATCH', url: `/api/answers/${answerId}/video`, headers: asAgent(),
    payload: { status: 'READY', songVideoUrl: 'https://media.example.com/song.mp4', ...extra },
  });
  const review = (answerId: string, cookie = admin) =>
    app.inject({ method: 'GET', url: `/api/answers/${answerId}/review`, headers: as(cookie) });

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
    const k = generateApiKey();
    key = k.raw;
    await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: k.hashed } });
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    const cookieFor = (id: string) => `popsmosis_session=${app.jwt.sign({ sub: id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    const ada = await prisma.user.create({ data: { email: 'ada@example.com', googleId: 'g-ada', name: 'Ada' } });
    const grace = await prisma.user.create({ data: { email: 'grace@example.com', googleId: 'g-grace', name: 'Grace', role: 'ADMIN' } });
    learner = cookieFor(ada.id);
    admin = cookieFor(grace.id);
    questionId = (await prisma.question.create({ data: { userId: ada.id, content: 'How do antibiotics work?' } })).id;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    for (const name of STORAGE_SETTINGS) delete process.env[name];
  });

  it('keeps the code checks with the lesson and the art checks with READY, off the cards, for an admin to read', async () => {
    const posted = await answer({ checks: CHECKS });
    expect(posted.statusCode).toBe(201);
    const answerId = posted.json().id;

    // The art checks come only with READY; then they are kept beside the videos.
    const early = await app.inject({ method: 'PATCH', url: `/api/answers/${answerId}/video`, headers: asAgent(), payload: { status: 'RENDERING', artChecks: ART } });
    expect(early.json().error.message).toMatch(/only for status READY/);
    expect((await ready(answerId, { artChecks: ART })).statusCode).toBe(200);

    // Never on a card: the learner's own nor an admin's list.
    for (const cookie of [learner, admin]) {
      const card = (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: as(cookie) })).json();
      expect(card.answer).not.toHaveProperty('checks');
      expect(card.answer).not.toHaveProperty('artChecks');
    }

    const body = (await review(answerId)).json();
    expect(body.factcheck).toMatchObject({
      status: 'passed', rounds: 2,
      // A tag is kept or dropped, never said: not counted.
      counts: { supported: 1, filler: 1, unsupported: 0 },
      corrections: [{ round: 1, kind: 'scene', ids: ['overview'], problem: 'Names a brand.' }],
    });
    expect(body.factcheck.lines).toHaveLength(3);
    expect(body.checks.problems).toEqual([
      { lineId: 'n4', rule: 'not-in-scene', detail: 'names bacterium, which scene "analogy" does not draw', repaired: false },
      { lineId: 'c2', rule: 'spotlight', detail: 'moved to the line that names it', repaired: true },
    ]);
    // No storage on this API: nothing to show the pictures from, so no address for them.
    expect(body.art.scenes[0]).toMatchObject({
      scene: 'strep-case', imageUrl: null, model: 'muse-image-1.0', kept: 2,
      check: { wrong: ['throat outside the head'], text: [], notes: [] },
      tries: [{ try: 1, declined: 'Muse Image declined the picture' }, { try: 2, declined: null, wrong: ['throat outside the head'] }],
    });
    expect(body.art.scenes[1]).toMatchObject({ scene: 'chart', model: null, check: null, tries: [] });

    Object.assign(process.env, R2);
    expect((await review(answerId)).json().art.scenes[0].imageUrl).toBe(`/api/answers/${answerId}/review/art/strep-case`);
  });

  it('is for admins only', async () => {
    const answerId = (await answer({ checks: CHECKS })).json().id;
    expect((await review(answerId, learner)).statusCode).toBe(403);
    expect((await app.inject({ method: 'GET', url: `/api/answers/${answerId}/review`, headers: asAgent() })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: `/api/answers/${answerId}/review` })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: `/api/answers/${answerId}/review/art/hook`, headers: as(learner) })).statusCode).toBe(403);
    expect((await review('nope')).statusCode).toBe(404);
  });

  it('reads back as what it has: a lesson without checks, or with no videos yet', async () => {
    const answerId = (await answer()).json().id;
    expect((await review(answerId)).json()).toMatchObject({ id: answerId, checks: null, art: null, factcheck: { status: 'passed' } });
  });

  it('refuses checks and art checks that are not what the studio writes', async () => {
    for (const checks of [[], { problems: 'none' }, { problems: [{ rule: 'x' }] }, { problems: [{ rule: 'x', detail: 'y'.repeat(70_000) }] }]) {
      expect((await answer({ checks })).statusCode).toBe(400);
    }
    const noBundle = await post(`/api/questions/${questionId}/answer`, { content: 'A lesson.', checks: CHECKS });
    expect(noBundle.json().error.message).toMatch(/come with the bundle/);

    const answerId = (await answer()).json().id;
    for (const artChecks of [
      { scenes: 'hook' },
      { scenes: [{ file: 'a1/art/hook.png' }] }, // no scene
      { scenes: [{ scene: 'hook', file: '../other/art/hook.png' }] }, // out of its folder
      { scenes: [{ scene: 'hook', file: 'a1/out/song_h.mp4' }] }, // not a picture of the art
      { scenes: [{ scene: 'hook', tries: [1, 2] }] },
      { scenes: Array.from({ length: 41 }, (_, i) => ({ scene: `s${i}` })) },
    ]) {
      const res = await ready(answerId, { artChecks });
      expect(res.statusCode, JSON.stringify(artChecks)).toBe(400);
    }
    expect((await prisma.answer.findUniqueOrThrow({ where: { id: answerId } })).videoStatus).toBeNull();
  });

  it('serves a scene\'s picture out of the private bucket to an admin', async () => {
    const answerId = (await answer()).json().id;
    await ready(answerId, { artChecks: ART });
    const picture = (scene: string) => app.inject({ method: 'GET', url: `/api/answers/${answerId}/review/art/${scene}`, headers: as(admin) });

    expect((await picture('strep-case')).statusCode).toBe(503); // no storage on this API
    Object.assign(process.env, R2);

    const fetched: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      fetched.push(url);
      return url.includes('/chart.png') ? new Response('<Error><Code>NoSuchKey</Code></Error>', { status: 404 }) : new Response(PNG, { status: 200 });
    });
    const res = await picture('strep-case');
    expect(res.statusCode).toBe(200);
    expect(res.headers['content-type']).toBe('image/png');
    expect(res.headers['cache-control']).toBe('private, max-age=3600');
    expect(res.rawPayload.equals(PNG)).toBe(true);
    // Read with a signed GET from the private bucket, at the file READY named, inside the answer's own folder.
    const url = new URL(fetched[0]);
    expect(url.host).toBe(`${R2.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`);
    expect(url.pathname).toBe(`/studio/${answerId}/a1/art/strep-case.png`);
    expect(url.searchParams.get('X-Amz-Signature')).toMatch(/^[0-9a-f]{64}$/);

    expect((await picture('chart')).statusCode).toBe(404); // named, but not in storage
    expect((await picture('hook')).statusCode).toBe(404); // not a scene READY named
    expect(fetched).toHaveLength(2);
  });
});
