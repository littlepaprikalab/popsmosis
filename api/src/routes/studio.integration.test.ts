/**
 * The studio's settings on /admin/studio, against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: only admins change them, admins and the teacher read them, a change is checked and kept, and the
 * studio's claims carry it. The knobs (lib/knobs.ts): the API's own are in force on the next request, the studio's
 * are sent to the teacher, and a cleared one is the environment's again.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('studio settings', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let adminCookie = '';
  let learnerCookie = '';
  let agentKey = '';
  let agentId = '';
  let userId = '';

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const asAgent = { authorization: '' };
  const get = (cookie: string) => app.inject({ method: 'GET', url: '/api/studio/settings', headers: asUser(cookie) });
  const put = (cookie: string, payload: object) =>
    app.inject({ method: 'PUT', url: '/api/studio/settings', headers: asUser(cookie), payload });

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prisma = (await import('../lib/prisma.js')).default;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE settings, replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    await app.close();
    await prisma.$disconnect();
  });

  beforeEach(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE settings, replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    (await import('../lib/knobs.js')).putKnobs({}); // what this instance has in force, as the table now is
    delete process.env.VIDEO_MAX_ATTEMPTS;
    const { generateApiKey } = await import('../lib/apikey.js');
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    const key = generateApiKey();
    agentKey = key.raw;
    asAgent.authorization = `Bearer ${agentKey}`;
    agentId = (await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: key.hashed } })).id;
    const admin = await prisma.user.create({ data: { email: 'ada@example.com', googleId: 'g-ada', name: 'Ada', role: 'ADMIN' } });
    const learner = await prisma.user.create({ data: { email: 'ben@example.com', googleId: 'g-ben', name: 'Ben' } });
    userId = learner.id;
    const cookie = (id: string) => `popsmosis_session=${app.jwt.sign({ sub: id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    adminCookie = cookie(admin.id);
    learnerCookie = cookie(learner.id);
  });

  it('shows an admin the defaults and the choices', async () => {
    const res = await get(adminCookie);
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({
      brain: { default: 'claude', effort: 'high' },
      song: { provider: 'elevenlabs' },
      art: { claude: 'gemini', meta: 'muse' },
      knobs: expect.any(Object),
      spend: { topicUsd: 20 },
      review: { required: false },
      updatedAt: null,
      defaults: {
        brain: { default: 'claude', effort: 'high' }, song: { provider: 'elevenlabs' }, art: { claude: 'gemini', meta: 'muse' },
        spend: { topicUsd: 20 }, review: { required: false },
      },
      choices: {
        brain: { default: ['claude', 'muse-wonder', 'muse-spark'], effort: ['low', 'medium', 'high', 'xhigh', 'max'] },
        song: { provider: ['elevenlabs', 'gemini'] },
        art: { claude: ['gemini', 'muse', 'svg'], meta: ['muse', 'gemini'] },
        spend: { topicUsd: { min: 0, max: 100 } },
      },
    });
  });

  it('lets the teacher read them with its agent key, and only admins change them', async () => {
    const asTeacher = await app.inject({ method: 'GET', url: '/api/studio/settings', headers: asAgent });
    expect(asTeacher.statusCode).toBe(200);
    expect(asTeacher.json()).toEqual({
      brain: { default: 'claude', effort: 'high' }, song: { provider: 'elevenlabs' }, art: { claude: 'gemini', meta: 'muse' },
      knobs: {},
      spend: { topicUsd: 20 },
    });
    expect((await app.inject({ method: 'PUT', url: '/api/studio/settings', headers: asAgent, payload: { art: { claude: 'muse' } } })).statusCode).toBe(401);
    expect((await get(learnerCookie)).statusCode).toBe(403);
    expect((await put(learnerCookie, { art: { claude: 'muse' } })).statusCode).toBe(403);
    expect((await app.inject({ method: 'GET', url: '/api/studio/settings' })).statusCode).toBe(401);
  });

  it('keeps the default brain and its effort, and refuses what is not one', async () => {
    const changed = await put(adminCookie, { brain: { default: 'muse-spark' } });
    expect(changed.statusCode).toBe(200);
    expect(changed.json().brain).toEqual({ default: 'muse-spark', effort: 'high' });
    expect((await put(adminCookie, { brain: { effort: 'medium' }, art: { meta: 'gemini' } })).json()).toMatchObject({
      brain: { default: 'muse-spark', effort: 'medium' }, art: { claude: 'gemini', meta: 'gemini' },
    });
    expect((await put(adminCookie, { brain: { default: 'claude-haiku-5-5' } })).statusCode).toBe(400);
    expect((await put(adminCookie, {})).statusCode).toBe(400);
    // A refused change leaves everything as it was, the art in the same request included.
    expect((await put(adminCookie, { brain: { effort: 'max' }, art: { meta: 'svg' } })).statusCode).toBe(400);
    expect((await get(adminCookie)).json().brain).toEqual({ default: 'muse-spark', effort: 'medium' });
  });

  it('keeps a change, leaves the other brain as it was, and refuses a painter that brain may not use', async () => {
    const changed = await put(adminCookie, { art: { claude: 'muse' } });
    expect(changed.statusCode).toBe(200);
    expect(changed.json().art).toEqual({ claude: 'muse', meta: 'muse' });
    expect(changed.json().updatedAt).not.toBeNull();
    expect((await put(adminCookie, { art: { meta: 'gemini' } })).json().art).toEqual({ claude: 'muse', meta: 'gemini' });

    const refused = await put(adminCookie, { art: { meta: 'svg' } });
    expect(refused.statusCode).toBe(400);
    expect(refused.json().error.message).toBe('art.meta must be one of muse, gemini');
    expect((await get(adminCookie)).json().art).toEqual({ claude: 'muse', meta: 'gemini' });
  });

  it('keeps who sings the song, and refuses a provider it does not have', async () => {
    expect((await put(adminCookie, { song: { provider: 'gemini' } })).json().song).toEqual({ provider: 'gemini' });
    expect((await put(adminCookie, { song: { provider: 'suno' } })).statusCode).toBe(400);
    const teacher = await app.inject({ method: 'GET', url: '/api/studio/settings', headers: asAgent });
    expect(teacher.json().song).toEqual({ provider: 'gemini' });
  });

  it('keeps the per-topic spend ceiling, and refuses one out of range', async () => {
    expect((await put(adminCookie, { spend: { topicUsd: 2.5 } })).json().spend).toEqual({ topicUsd: 2.5 });
    expect((await put(adminCookie, { spend: { topicUsd: 500 } })).statusCode).toBe(400);
    expect((await put(adminCookie, { spend: { topicUsd: 0 }, art: { meta: 'svg' } })).statusCode).toBe(400);
    const teacher = await app.inject({ method: 'GET', url: '/api/studio/settings', headers: asAgent });
    expect(teacher.json().spend).toEqual({ topicUsd: 2.5 });
    expect((await put(adminCookie, { spend: { topicUsd: 0 } })).json().spend).toEqual({ topicUsd: 0 });
  });

  it('keeps whether droplets wait for review, and never tells the studio', async () => {
    expect((await put(adminCookie, { review: { required: false } })).json().review).toEqual({ required: false });
    expect((await put(adminCookie, { review: { required: 'no' } })).statusCode).toBe(400);
    expect((await put(adminCookie, { review: { required: true, by: 'ada' } })).statusCode).toBe(400);
    expect((await get(adminCookie)).json().review).toEqual({ required: false });
    const teacher = await app.inject({ method: 'GET', url: '/api/studio/settings', headers: asAgent });
    expect(teacher.json().review).toBeUndefined();
  });

  it('keeps both of two changes saved at the same moment', async () => {
    const saves = await Promise.all([
      put(adminCookie, { art: { claude: 'muse' } }),
      put(adminCookie, { art: { meta: 'gemini' } }),
      put(adminCookie, { brain: { default: 'muse-wonder' } }),
      put(adminCookie, { brain: { effort: 'low' } }),
    ]);
    expect(saves.map((r) => r.statusCode)).toEqual([200, 200, 200, 200]);
    expect((await get(adminCookie)).json()).toMatchObject({
      art: { claude: 'muse', meta: 'gemini' }, brain: { default: 'muse-wonder', effort: 'low' },
    });
  });

  it('starts over from a stored value that is not an object, rather than keep a broken row', async () => {
    await prisma.setting.create({ data: { key: 'art', value: 'gemini' } });
    expect((await get(adminCookie)).json().art).toEqual({ claude: 'gemini', meta: 'muse' });
    expect((await put(adminCookie, { art: { meta: 'gemini' } })).json().art).toEqual({ claude: 'gemini', meta: 'gemini' });
  });

  it('reaches the studio on the claim of a topic and of its videos', async () => {
    const pending = await prisma.question.create({ data: { userId, content: 'ATP synthesis' } });
    const claim = () => app.inject({ method: 'POST', url: `/api/questions/${pending.id}/claim`, headers: asAgent });
    const brain = { default: 'claude', effort: 'high' };
    const song = { provider: 'elevenlabs' };
    expect((await claim()).json().studio).toEqual({ brain, song, art: { claude: 'gemini', meta: 'muse' }, knobs: {}, spend: { topicUsd: 20 } });
    await put(adminCookie, { art: { claude: 'svg' }, knobs: { ELEVENLABS_VOICE_ID: 'Voice42' }, spend: { topicUsd: 3 } });
    const knobs = { ELEVENLABS_VOICE_ID: 'Voice42' };
    const spend = { topicUsd: 3 };
    expect((await claim()).json().studio).toEqual({ brain, song, art: { claude: 'svg', meta: 'muse' }, knobs, spend });

    const answered = await prisma.question.create({ data: { userId, content: 'Osmosis', status: 'ANSWERED' } });
    const answer = await prisma.answer.create({
      data: { questionId: answered.id, agentId, content: 'A lesson.', bundle: { topic: 'osmosis' }, factcheck: { status: 'passed' } },
    });
    const videos = await app.inject({ method: 'POST', url: `/api/answers/${answer.id}/video/claim`, headers: asAgent, payload: {} });
    expect(videos.json().studio).toEqual({ brain, song, art: { claude: 'svg', meta: 'muse' }, knobs, spend });
  });

  it('shows an admin every knob: what it takes, its default, and for the API\'s own its environment and what is in force', async () => {
    process.env.VIDEO_MAX_ATTEMPTS = '3';
    const knobs = (await get(adminCookie)).json().knobs;
    expect(knobs.VIDEO_MAX_ATTEMPTS).toEqual({ where: 'api', kind: 'int', default: 2, min: 1, max: 10, value: null, env: '3', inForce: 3 });
    expect(knobs.QUESTION_CLAIM_MINUTES).toMatchObject({ value: null, env: null, inForce: 20 });
    expect(knobs.POPSMOSIS_ROUTINE_BETA).toEqual({
      where: 'api', kind: 'text', default: 'experimental-cc-routine-2026-04-01', rule: 'lowercase letters, digits and hyphens',
      value: null, env: null, inForce: 'experimental-cc-routine-2026-04-01',
    });
    // The studio's environment is on the teacher's machine: nothing about it here.
    expect(knobs.STUDIO_VIDEOS).toEqual({ where: 'studio', kind: 'choice', default: 'on', choices: ['on', 'off'], value: null });
    expect(knobs.ELEVENLABS_USD_PER_1K_CREDITS).toEqual({ where: 'studio', kind: 'number', default: null, min: 0, max: 100, value: null });
    // Never a secret, nor a knob that is not one.
    expect(Object.keys(knobs)).not.toContain('JWT_SECRET');
    expect(Object.keys(knobs)).not.toContain('ATTACHMENT_RETENTION_DAYS');

    const changed = await put(adminCookie, { knobs: { VIDEO_MAX_ATTEMPTS: 5 } });
    expect(changed.statusCode).toBe(200);
    expect(changed.json().knobs.VIDEO_MAX_ATTEMPTS).toMatchObject({ value: 5, env: '3', inForce: 5 });
    expect(changed.json().updatedAt).not.toBeNull();
    // Cleared, the environment's value is in force again.
    const cleared = await put(adminCookie, { knobs: { VIDEO_MAX_ATTEMPTS: null } });
    expect(cleared.json().knobs.VIDEO_MAX_ATTEMPTS).toMatchObject({ value: null, env: '3', inForce: 3 });
    expect((await prisma.setting.findUnique({ where: { key: 'knobs' } }))?.value).toEqual({});
  });

  it('puts an API knob in force on the next request, with no restart', async () => {
    const ask = (content: string) =>
      app.inject({ method: 'POST', url: '/api/questions', headers: asUser(learnerCookie), payload: { content } });
    expect((await put(adminCookie, { knobs: { QUESTIONS_PER_USER_PER_DAY: 1 } })).statusCode).toBe(200);
    expect((await ask('Diffusion')).statusCode).toBe(201);
    const refused = await ask('Osmosis');
    expect(refused.statusCode).toBe(429);
    expect(refused.json().error.message).toBe('Max 1 questions per 24 hours');
    await put(adminCookie, { knobs: { QUESTIONS_PER_USER_PER_DAY: null } });
    expect((await ask('Osmosis')).statusCode).toBe(201);
  });

  it('sends the teacher only the studio\'s knobs, and keeps both of two set at the same moment', async () => {
    const saves = await Promise.all([
      put(adminCookie, { knobs: { STUDIO_VIDEOS: 'off' } }),
      put(adminCookie, { knobs: { GEMINI_SONG_MODEL: 'lyria-3-clip-preview', ELEVENLABS_USD_PER_1K_CREDITS: 0.3 } }),
      put(adminCookie, { knobs: { LESSON_HOLD_MINUTES: 0 } }),
    ]);
    expect(saves.map((r) => r.statusCode)).toEqual([200, 200, 200]);
    const teacher = await app.inject({ method: 'GET', url: '/api/studio/settings', headers: asAgent });
    expect(teacher.json().knobs).toEqual({
      STUDIO_VIDEOS: 'off', GEMINI_SONG_MODEL: 'lyria-3-clip-preview', ELEVENLABS_USD_PER_1K_CREDITS: 0.3,
    });
    expect((await get(adminCookie)).json().knobs.LESSON_HOLD_MINUTES).toMatchObject({ value: 0, inForce: 0 });
  });

  it('refuses a knob that is not one or a value it does not take, and changes nothing else in the request', async () => {
    const refused = await put(adminCookie, { art: { claude: 'svg' }, knobs: { QUESTION_CLAIM_MINUTES: 0 } });
    expect(refused.statusCode).toBe(400);
    expect(refused.json().error.message).toBe('knobs.QUESTION_CLAIM_MINUTES must be a whole number from 1 to 1440');
    expect((await put(adminCookie, { knobs: { DATABASE_URL: 'postgres://elsewhere' } })).json().error.message).toBe('knobs has no DATABASE_URL');
    expect((await put(adminCookie, { knobs: {} })).statusCode).toBe(400);
    expect((await put(learnerCookie, { knobs: { TOPICS_PER_DAY: 0 } })).statusCode).toBe(403);
    expect((await app.inject({ method: 'PUT', url: '/api/studio/settings', headers: asAgent, payload: { knobs: { STUDIO_VIDEOS: 'off' } } })).statusCode).toBe(401);
    const now = (await get(adminCookie)).json();
    expect(now.art).toEqual({ claude: 'gemini', meta: 'muse' });
    expect(now.knobs.QUESTION_CLAIM_MINUTES.value).toBeNull();
  });

  it('reads a change another instance stored, and drops a stored value no longer allowed', async () => {
    await prisma.setting.create({ data: { key: 'knobs', value: { VIDEO_STALE_MINUTES: 30, VIDEO_MAX_ATTEMPTS: 99 } } });
    const knobs = (await get(adminCookie)).json().knobs;
    expect(knobs.VIDEO_STALE_MINUTES).toMatchObject({ value: 30, inForce: 30 });
    expect(knobs.VIDEO_MAX_ATTEMPTS).toMatchObject({ value: null, inForce: 2 });
    const { videoStaleMinutes } = await import('../lib/droplet.js');
    expect(videoStaleMinutes()).toBe(30);
  });

  it('never shows a learner the studio settings on their own topics', async () => {
    const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(learnerCookie), payload: { content: 'Diffusion' } });
    expect(asked.json().studio).toBeUndefined();
    const listed = await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(learnerCookie) });
    expect(listed.json().data[0].studio).toBeUndefined();
  });
});
