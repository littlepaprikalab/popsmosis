/**
 * The public Droplets tab, against a real Postgres (DATABASE_URL). Skipped unless
 * INTEGRATION=1.
 *
 * Covers: what is listed (READY and not hidden, newest first, by cursor), that
 * nothing about the learner ever leaves through the public endpoints, the admin's
 * hide switch, the poster URLs on the video, and the per-visitor rate limit.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = {
  course: 'AP Biology', topic: 'ATP synthase', core_idea: 'A proton-driven rotary motor makes ATP.',
  facts: [{ id: 'f1', text: 'ATP synthase is driven by a proton gradient.', source_quote: '' }],
  misconception: 'secret-misconception', frq: { prompt: 'secret-frq' },
  scenes: [{ id: 'hook', image_prompt: 'secret-scene', structures: [] }],
  lyrics: [{ id: 'c1', section: 'chorus', text: 'Spin the rotor, make the ATP', names: [], fact_id: 'f1' }],
  narration: [], direction: [],
};
const FACTCHECK = { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] };
// Everything that is the learner's own. None of it may appear in a public response.
const PRIVATE = ['Grace Hopper', 'grace@example.com', 'https://avatars.example/grace.png', 'my AP Bio exam on Friday', 'class-notes.txt', 'a follow-up only Grace should see'];
const SONG = 'https://x.supabase.co/storage/v1/object/public/droplets/a/out/song_h.mp4';
const LESSON = 'https://x.supabase.co/storage/v1/object/public/droplets/a/out/lesson_h.mp4';
const POSTER = 'https://x.supabase.co/storage/v1/object/public/droplets/a/out/song_h.jpg';

d('the Droplets tab', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let agentId = '';
  let learnerId = '';
  let adminCookie = '';
  let learnerCookie = '';
  let agentKey = '';

  const get = (url: string, headers: Record<string, string> = {}) => app.inject({ method: 'GET', url, headers });
  const hide = (id: string, payload: object, cookie = adminCookie) =>
    app.inject({ method: 'PATCH', url: `/api/answers/${id}`, headers: { cookie, 'x-requested-with': 'XMLHttpRequest' }, payload });

  /** A topic Grace asked for, with its lesson, a follow-up and a file; minutesAgo orders them. */
  async function droplet(minutesAgo: number, video: Record<string, unknown> = { videoStatus: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON }) {
    const at = new Date(Date.now() - minutesAgo * 60_000);
    const q = await prisma.question.create({
      data: {
        userId: learnerId, status: 'ANSWERED', createdAt: at, content: 'ATP synthase for my AP Bio exam on Friday',
        attachments: { create: [{ name: 'class-notes.txt', mimeType: 'text/plain', size: 5, data: Buffer.from('notes') }] },
      },
    });
    const a = await prisma.answer.create({
      data: { questionId: q.id, agentId, content: `Lesson ${minutesAgo}: **ATP synthase** spins.`, createdAt: at, bundle: BUNDLE, factcheck: FACTCHECK, ...video },
    });
    await prisma.reply.create({ data: { answerId: a.id, userId: learnerId, content: 'a follow-up only Grace should see' } });
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
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    const { generateApiKey } = await import('../lib/apikey.js');
    const key = generateApiKey();
    agentKey = key.raw;
    agentId = (await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: key.hashed } })).id;
    const learner = await prisma.user.create({ data: { email: 'grace@example.com', googleId: 'g-grace', name: 'Grace Hopper', avatarUrl: 'https://avatars.example/grace.png' } });
    learnerId = learner.id;
    const admin = await prisma.user.create({ data: { email: 'ada@example.com', googleId: 'g-ada', name: 'Ada', role: 'ADMIN' } });
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    adminCookie = `popsmosis_session=${app.jwt.sign({ sub: admin.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    learnerCookie = `popsmosis_session=${app.jwt.sign({ sub: learner.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
  });

  it('lists READY droplets that are not hidden, newest first, to anyone, by cursor', async () => {
    const older = await droplet(30);
    const newer = await droplet(10);
    await droplet(20, { videoStatus: 'RENDERING', songVideoUrl: SONG });
    await droplet(25, { videoStatus: 'FAILED', videoError: 'render: ffmpeg exited 1' });
    await droplet(5, {}); // text-only
    const hidden = await droplet(1);
    await prisma.answer.update({ where: { id: hidden }, data: { hiddenAt: new Date() } });

    const list = await get('/api/droplets');
    expect(list.statusCode).toBe(200);
    expect(list.json().data.map((x: { id: string }) => x.id)).toEqual([newer, older]);
    expect(list.json().data[0]).toEqual({
      id: newer, title: 'AP Biology: ATP synthase', course: 'AP Biology', topic: 'ATP synthase',
      excerpt: 'Lesson 10: ATP synthase spins.',
      video: { songVideoUrl: SONG, lessonVideoUrl: LESSON, songPosterUrl: null, lessonPosterUrl: null },
      likeCount: 0, commentCount: 0, liked: false,
      createdAt: expect.any(String),
    });

    const first = (await get('/api/droplets?limit=1')).json();
    expect(first.data.map((x: { id: string }) => x.id)).toEqual([newer]);
    const second = (await get(`/api/droplets?limit=1&cursor=${first.nextCursor}`)).json();
    expect(second.data.map((x: { id: string }) => x.id)).toEqual([older]);
    expect((await get(`/api/droplets?limit=1&cursor=${second.nextCursor}`)).json()).toEqual({ data: [], nextCursor: null });
  });

  it('shows one droplet with its lesson and lyrics, and 404s anything not on the tab', async () => {
    const id = await droplet(10);
    const one = await get(`/api/droplets/${id}`);
    expect(one.statusCode).toBe(200);
    expect(one.json()).toMatchObject({
      id, title: 'AP Biology: ATP synthase', content: 'Lesson 10: **ATP synthase** spins.',
      lyrics: [{ section: 'chorus', text: 'Spin the rotor, make the ATP' }],
    });

    const rendering = await droplet(5, { videoStatus: 'RENDERING', songVideoUrl: SONG });
    expect((await get(`/api/droplets/${rendering}`)).statusCode).toBe(404);
    await prisma.answer.update({ where: { id }, data: { hiddenAt: new Date() } });
    expect((await get(`/api/droplets/${id}`)).statusCode).toBe(404);
    expect((await get('/api/droplets/nope')).statusCode).toBe(404);
  });

  it('never sends the learner, their words, their files or the follow-ups, whoever asks', async () => {
    const id = await droplet(10);
    for (const headers of [{}, { cookie: learnerCookie }, { cookie: adminCookie }, { authorization: `Bearer ${agentKey}` }]) {
      for (const url of ['/api/droplets', `/api/droplets/${id}`]) {
        const res = await get(url, headers);
        expect(res.statusCode).toBe(200);
        for (const secret of [...PRIVATE, learnerId, 'secret-']) expect(res.body).not.toContain(secret);
        expect(res.headers['set-cookie']).toBeUndefined();
      }
    }
    // The learner's own page still has all of it: the tab is a view, not a move.
    const card = (await get(`/api/questions?mine=true`, { cookie: learnerCookie })).json().data[0];
    expect(card.content).toBe('ATP synthase for my AP Bio exam on Friday');
  });

  it('lets an admin take a droplet off the tab and put it back', async () => {
    const id = await droplet(10);
    expect((await hide(id, { hidden: true }, learnerCookie)).statusCode).toBe(403);
    expect((await app.inject({ method: 'PATCH', url: `/api/answers/${id}`, payload: { hidden: true } })).statusCode).toBe(401);
    expect((await app.inject({ method: 'PATCH', url: `/api/answers/${id}`, headers: { authorization: `Bearer ${agentKey}` }, payload: { hidden: true } })).statusCode).toBe(401);
    expect((await hide(id, { hidden: 'yes' })).statusCode).toBe(400);
    expect((await hide('nope', { hidden: true })).statusCode).toBe(404);

    const off = await hide(id, { hidden: true });
    expect(off.json()).toMatchObject({ id, hidden: true, hiddenAt: expect.any(String) });
    expect((await get('/api/droplets')).json().data).toEqual([]);
    expect((await hide(id, { hidden: true })).json().hiddenAt).toBe(off.json().hiddenAt); // the first time stays
    // /learn still shows the lesson, and says it is hidden.
    const card = (await get(`/api/questions?mine=true`, { cookie: learnerCookie })).json().data[0];
    expect(card.answer).toMatchObject({ id, hidden: true, video: { status: 'READY' } });

    expect((await hide(id, { hidden: false })).json()).toEqual({ id, hidden: false, hiddenAt: null });
    expect((await get('/api/droplets')).json().data.map((x: { id: string }) => x.id)).toEqual([id]);
  });

  it('keeps a poster for each video, sent with it or later', async () => {
    const id = await droplet(10, {});
    const patch = (payload: object) => app.inject({ method: 'PATCH', url: `/api/answers/${id}/video`, headers: { authorization: `Bearer ${agentKey}` }, payload });
    expect((await patch({ status: 'RENDERING', songPosterUrl: 'data:image/png;base64,AA' })).statusCode).toBe(400);
    await patch({ status: 'RENDERING', songVideoUrl: SONG, songPosterUrl: POSTER });
    const ready = await patch({ status: 'READY', lessonVideoUrl: LESSON });
    expect(ready.json()).toMatchObject({ status: 'READY', songVideoUrl: SONG, songPosterUrl: POSTER, lessonVideoUrl: LESSON, lessonPosterUrl: null });
    expect((await get(`/api/droplets/${id}`)).json().video).toEqual({ songVideoUrl: SONG, lessonVideoUrl: LESSON, songPosterUrl: POSTER, lessonPosterUrl: null });
  });

  it('marks READY only for the attempt that holds the render, so a run that was taken over cannot', async () => {
    const id = await droplet(10, { videoStatus: 'RENDERING', videoAttempts: 2, videoUpdatedAt: new Date() });
    const patch = (payload: object) => app.inject({ method: 'PATCH', url: `/api/answers/${id}/video`, headers: { authorization: `Bearer ${agentKey}` }, payload });
    const ready = (attempt: unknown) => patch({ status: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON, attempt });

    expect((await ready(0)).statusCode).toBe(400);
    const stale = await ready(1); // the first run's machine went quiet and attempt 2 took over
    expect(stale.statusCode).toBe(409);
    expect((await prisma.answer.findUniqueOrThrow({ where: { id } })).videoStatus).toBe('RENDERING');
    expect((await get('/api/droplets')).json().data).toEqual([]);

    expect((await ready(2)).json()).toMatchObject({ status: 'READY', songVideoUrl: SONG });
    expect((await ready(2)).statusCode).toBe(409); // no longer rendering
    expect((await get('/api/droplets')).json().data.map((x: { id: string }) => x.id)).toEqual([id]);
  });

  it('rate limits each visitor, whatever cookie they make up', async () => {
    const { PUBLIC_PER_CLIENT_PER_MINUTE } = await import('../lib/public-limit.js');
    const statuses: number[] = [];
    for (let i = 0; i <= PUBLIC_PER_CLIENT_PER_MINUTE; i++) {
      const forged = `popsmosis_session=x.${Buffer.from(JSON.stringify({ sub: `u${i}` })).toString('base64url')}.y`;
      statuses.push((await app.inject({ method: 'GET', url: '/api/droplets', remoteAddress: '198.51.100.9', headers: { cookie: forged } })).statusCode);
    }
    expect(statuses.filter((s) => s === 200)).toHaveLength(PUBLIC_PER_CLIENT_PER_MINUTE);
    expect(statuses.at(-1)).toBe(429);
    // Another visitor has their own allowance.
    expect((await app.inject({ method: 'GET', url: '/api/droplets', remoteAddress: '198.51.100.10' })).statusCode).toBe(200);
  });
});
