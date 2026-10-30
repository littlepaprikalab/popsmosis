/**
 * The public Droplets tab, against a real Postgres (DATABASE_URL). Skipped unless
 * INTEGRATION=1.
 *
 * Covers: what is listed (READY and not hidden, newest first, by cursor), that
 * nothing about the learner ever leaves through the public endpoints, the admin's
 * hide switch, a droplet waiting for an admin's review when /admin/studio asks for it,
 * the poster URLs on the video, and the per-visitor rate limit. Review is off for the
 * rest, which are about what a READY droplet does once listed.
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
const SONG = 'https://media.popsmosis.com/a/out/song_h.mp4';
const LESSON = 'https://media.popsmosis.com/a/out/lesson_h.mp4';
const POSTER = 'https://media.popsmosis.com/a/out/song_h.jpg';

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
    await prisma.$executeRawUnsafe('TRUNCATE settings, replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    await prisma.setting.create({ data: { key: 'review', value: { required: false } } });
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
      video: { songVideoUrl: SONG, lessonVideoUrl: LESSON, songPosterUrl: null, lessonPosterUrl: null, hookVideoUrl: null, hookPosterUrl: null },
      tags: { song: [], lesson: [] }, // a droplet made before there were tags
      madeWith: null, // and before the bills
      likeCount: 0, commentCount: 0, liked: false,
      createdAt: expect.any(String),
      comments: { data: [], nextCursor: null },
    });

    const first = (await get('/api/droplets?limit=1')).json();
    expect(first.data.map((x: { id: string }) => x.id)).toEqual([newer]);
    const second = (await get(`/api/droplets?limit=1&cursor=${first.nextCursor}`)).json();
    expect(second.data.map((x: { id: string }) => x.id)).toEqual([older]);
    expect((await get(`/api/droplets?limit=1&cursor=${second.nextCursor}`)).json()).toEqual({ data: [], nextCursor: null });
  });

  it('lists only droplets with a hook clip for the Clips feed, by cursor, ties broken by id', async () => {
    const HOOK = 'https://x.supabase.co/storage/v1/object/public/droplets/a/out/hook_v.mp4';
    const clip = { videoStatus: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON, hookVideoUrl: HOOK };
    const a = await droplet(10, clip);
    const b = await droplet(10, clip); // the same minute as a: the cursor must not skip or repeat one
    await prisma.answer.update({ where: { id: b }, data: { createdAt: (await prisma.answer.findUniqueOrThrow({ where: { id: a } })).createdAt } });
    const c = await droplet(20, clip);
    await droplet(5); // no clip
    await droplet(1, { ...clip, videoStatus: 'RENDERING' });
    const hidden = await droplet(2, clip);
    await prisma.answer.update({ where: { id: hidden }, data: { hiddenAt: new Date() } });

    const order = [a, b].sort().reverse().concat(c);
    expect((await get('/api/droplets?clips=1')).json().data.map((x: { id: string }) => x.id)).toEqual(order);
    const seen: string[] = [];
    let cursor = '';
    for (let page = 0; page < 4; page++) {
      const r = (await get(`/api/droplets?clips=1&limit=1${cursor ? `&cursor=${cursor}` : ''}`)).json();
      seen.push(...r.data.map((x: { id: string }) => x.id));
      if (!r.nextCursor) break;
      cursor = r.nextCursor;
    }
    expect(seen).toEqual(order);
    expect((await get('/api/droplets?clips=1')).json().data[0].video.hookVideoUrl).toBe(HOOK);
  });

  it("keeps each video's hashtags from the bundle, shows them, and finds a droplet by one", async () => {
    // The studio posts the lesson with its bundle, and the tags come in it, written however the brain wrote them.
    const q = await prisma.question.create({ data: { userId: learnerId, content: 'photosynthesis for my AP Bio exam on Friday' } });
    await app.inject({ method: 'POST', url: `/api/questions/${q.id}/claim`, headers: { authorization: `Bearer ${agentKey}` } });
    const tags = { song: ['#Photosynthesis', '#APBio', '#K-pop', '#photosynthesis'], lesson: ['#Photosynthesis', '#APBio', '#Explained'] };
    const posted = await app.inject({
      method: 'POST', url: `/api/questions/${q.id}/answer`, headers: { authorization: `Bearer ${agentKey}` },
      payload: { content: 'Light makes ATP.', bundle: { ...BUNDLE, topic: 'the light reactions', tags }, factcheck: FACTCHECK },
    });
    expect(posted.statusCode).toBe(201);
    const id = posted.json().id as string;
    const cleaned = { song: ['photosynthesis', 'apbio', 'kpop'], lesson: ['photosynthesis', 'apbio', 'explained'] };
    expect(await prisma.answer.findUnique({ where: { id }, select: { songTags: true, lessonTags: true } }))
      .toEqual({ songTags: cleaned.song, lessonTags: cleaned.lesson });
    // The learner sees them on /learn as soon as the lesson is up.
    const mine = await get(`/api/questions/${q.id}`, { cookie: learnerCookie });
    expect(mine.json().answer.tags).toEqual(cleaned);

    const ids = async (url: string) => (await get(url)).json().data.map((x: { id: string }) => x.id);
    // Not on the tab until its videos are READY, so no search finds it before then.
    expect(await ids('/api/droplets?tag=photosynthesis')).toEqual([]);
    await prisma.answer.update({ where: { id }, data: { videoStatus: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON } });
    const another = await droplet(20, { videoStatus: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON, lessonTags: ['apbio'] });
    const untagged = await droplet(30);

    expect((await get(`/api/droplets/${id}`)).json().tags).toEqual(cleaned);
    expect((await get('/api/droplets')).json().data[0].tags).toEqual(cleaned);
    expect(await ids('/api/droplets')).toEqual([id, another, untagged]);
    expect(await ids(`/api/droplets?tag=${encodeURIComponent('#APBio')}`)).toEqual([id, another]);
    expect(await ids('/api/droplets?tag=kpop')).toEqual([id]); // the song's own
    expect(await ids('/api/droplets?tag=explained')).toEqual([id]); // the lesson's own
    expect(await ids('/api/droplets?tag=photo')).toEqual([]); // the whole tag, as a hashtag search is
    expect(await ids(`/api/droplets?tag=${'x'.repeat(300)}`)).toEqual([]);
    expect(await ids('/api/droplets?tag=%23')).toEqual([id, another, untagged]); // "#" names no tag

    // A search pages like the list.
    const first = (await get('/api/droplets?tag=apbio&limit=1')).json();
    expect(first.data.map((x: { id: string }) => x.id)).toEqual([id]);
    expect(await ids(`/api/droplets?tag=apbio&limit=1&cursor=${first.nextCursor}`)).toEqual([another]);

    // A hidden droplet leaves the search with the tab.
    await prisma.answer.update({ where: { id }, data: { hiddenAt: new Date() } });
    expect(await ids('/api/droplets?tag=apbio')).toEqual([another]);
  });

  it('says which models made a droplet from its two bills, and nothing else from them', async () => {
    const agent = { authorization: `Bearer ${agentKey}` };
    const q = await prisma.question.create({ data: { userId: learnerId, content: 'ATP synthase for my AP Bio exam on Friday' } });
    await app.inject({ method: 'POST', url: `/api/questions/${q.id}/claim`, headers: agent });
    const lessonBill = { seconds: 200, steps: [
      { step: 'request', seconds: 0.3, tries: 1, models: [] },
      { step: 'bundle', seconds: 180, tries: 1, models: [{ model: 'claude-opus-5-5', calls: 1, costUsd: 0.5213, credits: null }] },
      { step: 'factcheck', seconds: 20, tries: 1, models: [
        { model: 'claude-opus-5-5', calls: 1, costUsd: 0.2, credits: null }, { model: 'claude-sonnet-5-5', calls: 2, costUsd: 0.01, credits: null }] },
    ] };
    const videoBill = { seconds: 400, steps: [
      { step: 'art', seconds: 60, tries: 1, models: [{ model: 'gemini-3.1-flash-image', calls: 3, costUsd: 0.12, credits: null }], tools: [{ name: 'skia-python', version: '144.1' }] },
      { step: 'audio', seconds: 90, tries: 1, models: [{ model: 'music_v2_5', calls: 1, costUsd: null, credits: 4100 }, { model: 'eleven_multilingual_v2', calls: 2, costUsd: null, credits: 900 }] },
      { step: 'timing', seconds: 100, tries: 1, models: [{ model: 'htdemucs', calls: 1, costUsd: 0, credits: null }, { model: 'mms-fa', calls: 1, costUsd: 0, credits: null }] },
      { step: 'render', seconds: 150, tries: 1, models: [], tools: [{ name: 'ffmpeg', version: '7.1.1' }] },
    ] };
    const posted = await app.inject({
      method: 'POST', url: `/api/questions/${q.id}/answer`, headers: agent,
      payload: { content: 'ATP synthase spins.', bundle: BUNDLE, factcheck: FACTCHECK, breakdown: lessonBill },
    });
    expect(posted.statusCode).toBe(201);
    const id = posted.json().id as string;
    const patchVideo = (payload: object) => app.inject({ method: 'PATCH', url: `/api/answers/${id}/video`, headers: agent, payload });
    expect((await patchVideo({ status: 'RENDERING' })).statusCode).toBe(200);
    expect((await patchVideo({ status: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON, breakdown: videoBill })).statusCode).toBe(200);

    const madeWith = [
      { maker: 'Anthropic', models: ['claude-opus-5-5', 'claude-sonnet-5-5'] },
      { maker: 'Google', models: ['gemini-3.1-flash-image'] },
      { maker: 'ElevenLabs', models: ['music_v2_5', 'eleven_multilingual_v2'] },
      { maker: 'Meta', models: ['htdemucs', 'mms-fa'] },
    ];
    // The list has Postgres read the names out of the bills; the droplet's page reads the bills: the two agree.
    const card = (await get('/api/droplets')).json().data[0];
    const page = (await get(`/api/droplets/${id}`)).json();
    expect(card.madeWith).toEqual(madeWith);
    expect(page.madeWith).toEqual(madeWith);
    for (const body of [card, page]) expect(JSON.stringify(body)).not.toMatch(/costUsd|credits|0\.5213|skia|ffmpeg|seconds/);

    // A videos' bill the READY left out: the lesson's alone would not say who drew and sang it, so neither says.
    await prisma.$executeRaw`UPDATE answers SET "videoBreakdown" = NULL WHERE id = ${id}`;
    expect((await get('/api/droplets')).json().data[0].madeWith).toBeNull();
    expect((await get(`/api/droplets/${id}`)).json().madeWith).toBeNull();
  });

  it('copies the tags when the videos go READY, for a lesson posted to an API older than them', async () => {
    const patchVideo = (id: string, payload: object) => app.inject({
      method: 'PATCH', url: `/api/answers/${id}/video`, headers: { authorization: `Bearer ${agentKey}` }, payload,
    });
    const ids = async (url: string) => (await get(url)).json().data.map((x: { id: string }) => x.id);
    // As an older API stored it: the tags in the bundle, none in the columns.
    const tags = { song: ['#Osmosis', '#PopPunk'], lesson: ['#Osmosis', '#Explained'] };
    const old = await droplet(10, { videoStatus: 'RENDERING', bundle: { ...BUNDLE, tags } });
    expect((await patchVideo(old, { status: 'RENDERING', songVideoUrl: SONG })).statusCode).toBe(200);
    expect(await prisma.answer.findUnique({ where: { id: old }, select: { songTags: true } })).toEqual({ songTags: [] });
    expect((await patchVideo(old, { status: 'READY', lessonVideoUrl: LESSON })).statusCode).toBe(200);
    expect(await prisma.answer.findUnique({ where: { id: old }, select: { songTags: true, lessonTags: true } }))
      .toEqual({ songTags: ['osmosis', 'poppunk'], lessonTags: ['osmosis', 'explained'] });
    expect(await ids('/api/droplets?tag=poppunk')).toEqual([old]);

    // Tags the lesson already has are never written over, and a bundle without any changes nothing.
    const kept = await droplet(20, { videoStatus: 'RENDERING', bundle: { ...BUNDLE, tags }, songTags: ['kept'] });
    expect((await patchVideo(kept, { status: 'READY', songVideoUrl: SONG })).statusCode).toBe(200);
    expect(await prisma.answer.findUnique({ where: { id: kept }, select: { songTags: true, lessonTags: true } }))
      .toEqual({ songTags: ['kept'], lessonTags: [] });
    const none = await droplet(30, { videoStatus: 'RENDERING' });
    expect((await patchVideo(none, { status: 'READY', songVideoUrl: SONG })).statusCode).toBe(200);
    expect(await prisma.answer.findUnique({ where: { id: none }, select: { songTags: true, lessonTags: true } }))
      .toEqual({ songTags: [], lessonTags: [] });

    // A query naming two tags searches the first, never everything.
    expect(await ids('/api/droplets?tag=poppunk&tag=kept')).toEqual([old]);
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

    expect((await hide(id, { hidden: false })).json()).toEqual({ id, hidden: false, hiddenAt: null, awaitingReview: false });
    expect((await get('/api/droplets')).json().data.map((x: { id: string }) => x.id)).toEqual([id]);
  });

  it('with review on, keeps a droplet whose videos go READY off the tab until an admin approves it', async () => {
    await prisma.setting.update({ where: { key: 'review' }, data: { value: { required: true } } });
    const id = await droplet(10, { videoStatus: 'RENDERING', songVideoUrl: SONG });
    const ready = await app.inject({ method: 'PATCH', url: `/api/answers/${id}/video`, headers: { authorization: `Bearer ${agentKey}` }, payload: { status: 'READY' } });
    expect(ready.json().status).toBe('READY');
    // Off the tab, its page, its likes and its comments, for anyone; the learner still has it on /learn.
    expect((await get('/api/droplets')).json().data).toEqual([]);
    expect((await get(`/api/droplets/${id}`)).statusCode).toBe(404);
    const like = await app.inject({ method: 'POST', url: `/api/droplets/${id}/like`, headers: { cookie: learnerCookie, 'x-requested-with': 'XMLHttpRequest' } });
    expect(like.statusCode).toBe(404);
    const card = (await get(`/api/questions?mine=true`, { cookie: learnerCookie })).json().data[0];
    expect(card.answer).toMatchObject({ id, awaitingReview: true, hidden: false, video: { status: 'READY' } });

    expect((await hide(id, { approved: true }, learnerCookie)).statusCode).toBe(403);
    expect((await hide(id, { approved: 'yes' })).statusCode).toBe(400);
    expect((await hide(id, {})).statusCode).toBe(400);
    expect((await hide(id, { approved: true })).json()).toEqual({ id, hidden: false, hiddenAt: null, awaitingReview: false });
    expect((await get('/api/droplets')).json().data.map((x: { id: string }) => x.id)).toEqual([id]);
    // Sent back to wait, and approved and hidden in one go.
    expect((await hide(id, { approved: false })).json().awaitingReview).toBe(true);
    expect((await get('/api/droplets')).json().data).toEqual([]);
    expect((await hide(id, { approved: true, hidden: true })).json()).toMatchObject({ hidden: true, awaitingReview: false });
    expect((await get('/api/droplets')).json().data).toEqual([]);

    // Turned off, the next READY lists at once; the setting never touches a droplet already made.
    await prisma.setting.update({ where: { key: 'review' }, data: { value: { required: false } } });
    const next = await droplet(5, { videoStatus: 'RENDERING', songVideoUrl: SONG });
    await app.inject({ method: 'PATCH', url: `/api/answers/${next}/video`, headers: { authorization: `Bearer ${agentKey}` }, payload: { status: 'READY' } });
    expect((await get('/api/droplets')).json().data.map((x: { id: string }) => x.id)).toEqual([next]);
  });

  it('keeps a poster for each video, sent with it or later', async () => {
    const id = await droplet(10, {});
    const patch = (payload: object) => app.inject({ method: 'PATCH', url: `/api/answers/${id}/video`, headers: { authorization: `Bearer ${agentKey}` }, payload });
    expect((await patch({ status: 'RENDERING', songPosterUrl: 'data:image/png;base64,AA' })).statusCode).toBe(400);
    await patch({ status: 'RENDERING', songVideoUrl: SONG, songPosterUrl: POSTER });
    const ready = await patch({ status: 'READY', lessonVideoUrl: LESSON });
    expect(ready.json()).toMatchObject({ status: 'READY', songVideoUrl: SONG, songPosterUrl: POSTER, lessonVideoUrl: LESSON, lessonPosterUrl: null });
    expect((await get(`/api/droplets/${id}`)).json().video).toEqual({ songVideoUrl: SONG, lessonVideoUrl: LESSON, songPosterUrl: POSTER, lessonPosterUrl: null, hookVideoUrl: null, hookPosterUrl: null });
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
