/**
 * The studio's settings on /admin/studio, against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: only admins change them, admins and the teacher read them, a change is checked and kept, and the
 * studio's claims carry it.
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
      updatedAt: null,
      defaults: {
        brain: { default: 'claude', effort: 'high' }, song: { provider: 'elevenlabs' }, art: { claude: 'gemini', meta: 'muse' },
      },
      choices: {
        brain: { default: ['claude', 'muse-wonder', 'muse-spark'], effort: ['low', 'medium', 'high', 'xhigh', 'max'] },
        song: { provider: ['elevenlabs', 'gemini'] },
        art: { claude: ['gemini', 'muse', 'svg'], meta: ['muse', 'gemini'] },
      },
    });
  });

  it('lets the teacher read them with its agent key, and only admins change them', async () => {
    const asTeacher = await app.inject({ method: 'GET', url: '/api/studio/settings', headers: asAgent });
    expect(asTeacher.statusCode).toBe(200);
    expect(asTeacher.json()).toEqual({
      brain: { default: 'claude', effort: 'high' }, song: { provider: 'elevenlabs' }, art: { claude: 'gemini', meta: 'muse' },
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
    expect((await claim()).json().studio).toEqual({ brain, song, art: { claude: 'gemini', meta: 'muse' } });
    await put(adminCookie, { art: { claude: 'svg' } });
    expect((await claim()).json().studio).toEqual({ brain, song, art: { claude: 'svg', meta: 'muse' } });

    const answered = await prisma.question.create({ data: { userId, content: 'Osmosis', status: 'ANSWERED' } });
    const answer = await prisma.answer.create({
      data: { questionId: answered.id, agentId, content: 'A lesson.', bundle: { topic: 'osmosis' }, factcheck: { status: 'passed' } },
    });
    const videos = await app.inject({ method: 'POST', url: `/api/answers/${answer.id}/video/claim`, headers: asAgent, payload: {} });
    expect(videos.json().studio).toEqual({ brain, song, art: { claude: 'svg', meta: 'muse' } });
  });

  it('never shows a learner the studio settings on their own topics', async () => {
    const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(learnerCookie), payload: { content: 'Diffusion' } });
    expect(asked.json().studio).toBeUndefined();
    const listed = await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(learnerCookie) });
    expect(listed.json().data[0].studio).toBeUndefined();
  });
});
