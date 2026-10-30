/**
 * The studio's half of the API, against a real Postgres (DATABASE_URL). Skipped
 * unless INTEGRATION=1.
 *
 * Covers: claiming one question (and renewing the claim), marking one failed,
 * posting a lesson with its bundle and fact-check, the follow-up context in the
 * reply queue, the video status, and signed upload URLs.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { STORAGE_SETTINGS } from '../lib/storage.js';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = {
  course: 'AP Biology', topic: 'the light reactions', core_idea: 'Light pumps protons; their flow back makes ATP.',
  facts: [{ id: 'f1', text: 'Only cytochrome b6f pumps protons.', source_quote: '' }],
  misconception: 'Not every carrier pumps protons.',
  scenes: [{ id: 'hook', image_prompt: 'A chloroplast.', structures: [] }],
  lyrics: [{ id: 'c1', section: 'chorus', text: 'Protons flow and ATP grows', names: [], fact_id: 'f1' }],
  narration: [], direction: [],
};
const FACTCHECK = { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] };
// The four R2 settings, as the API reads them; the key is a stand-in and never reaches R2.
const R2 = {
  R2_ACCOUNT_ID: '0123456789abcdef0123456789abcdef',
  R2_ACCESS_KEY_ID: '9f2c4e1a7b3d5f6e8a0c2e4b6d8f0a1c',
  R2_SECRET_ACCESS_KEY: 'a'.repeat(64),
  R2_PUBLIC_URL: 'https://media.popsmosis.com',
};

d('droplet endpoints', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let keyA = '';
  let keyB = '';
  let cookie = '';
  let userId = '';
  let questionId = '';

  const asAgent = (key: string) => ({ authorization: `Bearer ${key}` });
  const asUser = () => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const post = (key: string, url: string, payload?: object) => app.inject({ method: 'POST', url, headers: asAgent(key), payload: payload ?? {} });
  const video = (key: string, answerId: string, payload: object) =>
    app.inject({ method: 'PATCH', url: `/api/answers/${answerId}/video`, headers: asAgent(key), payload });
  // Claimed first, as the studio does: only the run holding the topic may answer it.
  const answer = async (key = keyA, payload: object = { content: 'A lesson.', bundle: BUNDLE, factcheck: FACTCHECK }) => {
    await post(key, `/api/questions/${questionId}/claim`);
    return post(key, `/api/questions/${questionId}/answer`, payload);
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
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    const { generateApiKey } = await import('../lib/apikey.js');
    const ka = generateApiKey();
    const kb = generateApiKey();
    keyA = ka.raw;
    keyB = kb.raw;
    await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: ka.hashed } });
    await prisma.agent.create({ data: { name: 'other', apiKeyHash: kb.hashed } });
    const user = await prisma.user.create({ data: { email: 'ada@example.com', googleId: 'g-ada', name: 'Ada' } });
    userId = user.id;
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    cookie = `popsmosis_session=${app.jwt.sign({ sub: userId }, { expiresIn: SESSION_EXPIRES_IN })}`;
    const q = await prisma.question.create({
      data: {
        userId, content: 'ATP synthesis for my AP Bio exam',
        attachments: { create: [{ name: 'notes.txt', mimeType: 'text/plain', size: 5, data: Buffer.from('notes') }] },
      },
    });
    questionId = q.id;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    for (const name of [...STORAGE_SETTINGS, 'SUPABASE_URL']) delete process.env[name];
  });

  it('claims one question for its agent, renews it, and refuses a second agent until the lease lapses', async () => {
    const claim = await post(keyA, `/api/questions/${questionId}/claim`);
    expect(claim.statusCode).toBe(200);
    expect(claim.json()).toMatchObject({ id: questionId, content: 'ATP synthesis for my AP Bio exam', status: 'PENDING', answer: null });
    expect(claim.json().attachments).toEqual([expect.objectContaining({ name: 'notes.txt', mimeType: 'text/plain', expired: false })]);

    // A claimed question is out of another agent's queue.
    expect((await app.inject({ method: 'GET', url: '/api/questions/queue', headers: asAgent(keyB) })).json().data).toEqual([]);
    expect((await post(keyB, `/api/questions/${questionId}/claim`)).statusCode).toBe(409);

    const first = (await prisma.question.findUniqueOrThrow({ where: { id: questionId } })).claimedAt!;
    await new Promise((r) => setTimeout(r, 5));
    expect((await post(keyA, `/api/questions/${questionId}/claim`)).statusCode).toBe(200);
    const renewed = (await prisma.question.findUniqueOrThrow({ where: { id: questionId } })).claimedAt!;
    expect(renewed.getTime()).toBeGreaterThan(first.getTime());

    await prisma.$executeRaw`UPDATE questions SET "claimedAt" = now() - interval '1 day' WHERE id = ${questionId}`;
    expect((await post(keyB, `/api/questions/${questionId}/claim`)).statusCode).toBe(200);
    expect((await prisma.question.findUniqueOrThrow({ where: { id: questionId } })).claimedById).not.toBeNull();

    expect((await post(keyA, '/api/questions/nope/claim')).statusCode).toBe(404);
    expect((await app.inject({ method: 'POST', url: `/api/questions/${questionId}/claim`, headers: asUser() })).statusCode).toBe(401);
  });

  it('marks a question failed, with the reason on the card, and takes it out of the queue for good', async () => {
    expect((await post(keyA, `/api/questions/${questionId}/fail`, { reason: 'x' })).statusCode).toBe(409); // never claimed
    await post(keyA, `/api/questions/${questionId}/claim`);
    expect((await post(keyA, `/api/questions/${questionId}/fail`, {})).statusCode).toBe(400);
    expect((await post(keyB, `/api/questions/${questionId}/fail`, { reason: 'x' })).statusCode).toBe(409); // not its claim

    const reason = 'factcheck failed: after 3 round(s), 1 line(s) still fail';
    const failed = await post(keyA, `/api/questions/${questionId}/fail`, { reason });
    expect(failed.statusCode).toBe(200);
    expect(failed.json()).toMatchObject({ status: 'FAILED', failedReason: reason });

    await prisma.$executeRaw`UPDATE questions SET "claimedAt" = NULL WHERE id = ${questionId}`;
    expect((await app.inject({ method: 'GET', url: '/api/questions/queue', headers: asAgent(keyA) })).json().data).toEqual([]);
    expect((await post(keyA, `/api/questions/${questionId}/claim`)).json().error.message).toMatch(/failed/);
    expect((await post(keyA, `/api/questions/${questionId}/fail`, { reason })).statusCode).toBe(409);
    expect((await answer()).statusCode).toBe(409);

    const card = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser() });
    expect(card.json()).toMatchObject({ status: 'FAILED', failedReason: reason, canDelete: true });
    const failedOnly = await app.inject({ method: 'GET', url: '/api/questions?status=FAILED', headers: asUser() });
    expect(failedOnly.json().data.map((q: { id: string }) => q.id)).toEqual([questionId]);
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}`, headers: asUser() })).statusCode).toBe(204);
  });

  it('posts a lesson with its bundle and fact-check, which the cards leave out and the reply queue reads back', async () => {
    expect((await answer(keyA, { content: 'A lesson.', bundle: BUNDLE })).statusCode).toBe(400);
    expect((await answer(keyA, { content: 'A lesson.', bundle: BUNDLE, factcheck: { ...FACTCHECK, status: 'failed' } })).json().error.message)
      .toMatch(/passed/);
    const posted = await answer();
    expect(posted.statusCode).toBe(201);
    const answerId = posted.json().id;
    const row = await prisma.answer.findUniqueOrThrow({ where: { id: answerId } });
    expect(row.bundle).toEqual(BUNDLE);
    expect(row.factcheck).toEqual(FACTCHECK);
    expect((await post(keyA, `/api/questions/${questionId}/claim`)).json().error.message).toMatch(/already has an answer/);

    // A retry whose 201 was lost learns its own lesson's id from the 409; another agent learns nothing.
    const again = await answer();
    expect(again.statusCode).toBe(409);
    expect(again.json().error).toMatchObject({ code: 'CONFLICT', answerId });
    expect((await answer(keyB)).json().error).not.toHaveProperty('answerId');

    const card = (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser() })).json();
    expect(card.answer.video).toBeNull();
    expect(card.answer).not.toHaveProperty('bundle');
    expect(card.failedReason).toBeNull();

    await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(), payload: { content: 'Why only b6f?' } });
    const threads = (await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(keyA) })).json().data;
    expect(threads).toHaveLength(1);
    expect(threads[0].answer.context).toMatchObject({
      course: 'AP Biology', topic: 'the light reactions', coreIdea: BUNDLE.core_idea,
      facts: [{ id: 'f1', text: 'Only cytochrome b6f pumps protons.' }],
      lyrics: [{ section: 'chorus', text: 'Protons flow and ATP grows' }],
    });
    expect(threads[0].answer.context).not.toHaveProperty('scenes');
  });

  it('keeps the video status: its own answers only, URLs kept between updates, a reason on failure', async () => {
    const answerId = (await answer()).json().id;
    expect((await video(keyB, answerId, { status: 'RENDERING' })).statusCode).toBe(403);
    expect((await video(keyA, 'nope', { status: 'RENDERING' })).statusCode).toBe(404);
    expect((await video(keyA, answerId, { status: 'DONE' })).statusCode).toBe(400);
    expect((await video(keyA, answerId, { status: 'READY' })).json().error.message).toMatch(/needs/);
    expect((await video(keyA, answerId, { status: 'FAILED' })).statusCode).toBe(400);
    expect((await video(keyA, answerId, { status: 'RENDERING', error: 'x' })).statusCode).toBe(400);
    expect((await video(keyA, answerId, { status: 'RENDERING', songVideoUrl: 'javascript:alert(1)' })).statusCode).toBe(400);

    const song = 'https://media.popsmosis.com/a/out/song_h.mp4';
    const lesson = 'https://media.popsmosis.com/a/out/lesson_h.mp4';
    expect((await video(keyA, answerId, { status: 'RENDERING', songVideoUrl: song })).json())
      .toMatchObject({ status: 'RENDERING', songVideoUrl: song, lessonVideoUrl: null, error: null });

    // The two videos render in parallel and report on their own: neither report may erase the other's URL.
    for (let i = 0; i < 10; i++) {
      const [s, l] = [`${song}?v=${i}`, `${lesson}?v=${i}`];
      await Promise.all([video(keyA, answerId, { status: 'RENDERING', songVideoUrl: s }), video(keyA, answerId, { status: 'RENDERING', lessonVideoUrl: l })]);
      expect(await prisma.answer.findUniqueOrThrow({ where: { id: answerId }, select: { songVideoUrl: true, lessonVideoUrl: true } }))
        .toEqual({ songVideoUrl: s, lessonVideoUrl: l });
    }
    expect((await video(keyA, answerId, { status: 'RENDERING', songVideoUrl: song })).statusCode).toBe(200);
    expect((await video(keyA, answerId, { status: 'FAILED', error: 'render: ffmpeg exited 1' })).json())
      .toMatchObject({ status: 'FAILED', songVideoUrl: song, error: 'render: ffmpeg exited 1' });
    const ready = await video(keyA, answerId, { status: 'READY', lessonVideoUrl: lesson });
    expect(ready.json()).toMatchObject({ status: 'READY', songVideoUrl: song, lessonVideoUrl: lesson, error: null });

    const card = (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser() })).json();
    expect(card.answer.video).toMatchObject({ status: 'READY', songVideoUrl: song, lessonVideoUrl: lesson });

    // READY is final: a listed droplet keeps the videos it was listed with, whoever holds the key.
    for (const change of [{ status: 'RENDERING' }, { status: 'READY', songVideoUrl: `${song}?swapped` }, { status: 'FAILED', error: 'x' }]) {
      const refused = await video(keyA, answerId, change);
      expect(refused.statusCode).toBe(409);
      expect(refused.json().error.message).toMatch(/READY/);
    }
    expect(await prisma.answer.findUniqueOrThrow({ where: { id: answerId }, select: { videoStatus: true, songVideoUrl: true } }))
      .toEqual({ videoStatus: 'READY', songVideoUrl: song });
  });

  it('keeps the breakdown: the lesson\'s steps with the lesson, the videos\' with READY, shown on the card', async () => {
    const lessonSteps = { steps: [{ step: 'bundle', seconds: 180, tries: 1, costUsd: 0.5,
      models: [{ model: 'claude-opus-5-5', calls: 1, costUsd: 0.5, credits: null }] }], seconds: 180 };
    const videoSteps = { steps: [{ step: 'audio', seconds: 95, tries: 1, costUsd: 0.08, models: [
      { model: 'lyria-3.5', calls: 1, costUsd: 0.08, credits: null },
      { model: 'eleven_multilingual_v2', calls: 2, costUsd: null, credits: 3300 }] },
    { step: 'timing', seconds: 110, tries: 1, costUsd: 0, models: [{ model: 'htdemucs', calls: 1, costUsd: 0, credits: null }],
      tools: [{ name: 'demucs', version: '4.1.0' }, { name: 'ffmpeg', version: null }] }], seconds: 300 };
    expect((await answer(keyA, { content: 'A lesson.', bundle: BUNDLE, factcheck: FACTCHECK, breakdown: [] })).json().error.message)
      .toMatch(/breakdown/);
    const answerId = (await answer(keyA, { content: 'A lesson.', bundle: BUNDLE, factcheck: FACTCHECK, breakdown: lessonSteps })).json().id;
    const card = async () => (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser() })).json();
    expect((await card()).answer.breakdown).toMatchObject({
      steps: [{ step: 'bundle', part: 'lesson', seconds: 180, costUsd: 0.5 }], seconds: 180, costUsd: 0.5, complete: true,
    });

    const song = 'https://media.popsmosis.com/a/out/song_h.mp4';
    expect((await video(keyA, answerId, { status: 'RENDERING', breakdown: videoSteps })).json().error.message).toMatch(/only for status READY/);
    expect((await video(keyA, answerId, { status: 'READY', songVideoUrl: song, breakdown: { steps: 'audio' } })).statusCode).toBe(400);
    expect((await video(keyA, answerId, { status: 'READY', songVideoUrl: song, breakdown: videoSteps })).statusCode).toBe(200);
    const b = (await card()).answer.breakdown;
    expect(b.steps.map((s: { part: string; step: string }) => `${s.part}:${s.step}`)).toEqual(['lesson:bundle', 'videos:audio', 'videos:timing']);
    expect(b).toMatchObject({ seconds: 480, costUsd: 0.58, credits: 3300, complete: false });
    // Who made each model and tool, and the tools' versions as the studio sent them.
    expect(b.steps[0]).toMatchObject({ models: [{ model: 'claude-opus-5-5', maker: 'Anthropic' }], tools: [] });
    expect(b.steps[2]).toMatchObject({ models: [{ model: 'htdemucs', maker: 'Meta' }],
      tools: [{ name: 'demucs', version: '4.1.0', maker: 'Meta' }, { name: 'ffmpeg', version: null, maker: 'FFmpeg' }] });

    // A droplet's bill stays on /learn: the public tab shows only the title, lesson, videos and lyrics.
    const listed = (await app.inject({ method: 'GET', url: `/api/droplets/${answerId}` })).json();
    expect(listed).not.toHaveProperty('breakdown');
  });

  it('signs upload URLs for its own answers once storage is configured', async () => {
    const answerId = (await answer()).json().id;
    const upload = (key: string, payload: object) => post(key, `/api/answers/${answerId}/upload-url`, payload);
    const off = await upload(keyA, { bucket: 'droplets', file: 'out/song_h.mp4' });
    expect(off.statusCode).toBe(503);
    expect(off.json().error.message).toMatch(/not configured.*R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_PUBLIC_URL/);

    // A setting that cannot be right is named, and is not worth a retry.
    Object.assign(process.env, R2, { R2_PUBLIC_URL: `https://${R2.R2_ACCOUNT_ID}.r2.cloudflarestorage.com` });
    const misconfigured = await upload(keyA, { bucket: 'droplets', file: 'out/song_h.mp4' });
    expect(misconfigured.statusCode).toBe(503);
    expect(misconfigured.json().error.message).toMatch(/misconfigured.*R2_PUBLIC_URL is the S3 API endpoint/);
    expect(misconfigured.json().error.retryable).toBeUndefined();

    Object.assign(process.env, R2);
    // Signing asks nothing of R2: no request leaves the API.
    vi.stubGlobal('fetch', async () => { throw new Error('the API called out while signing'); });
    expect((await upload(keyA, { bucket: 'public', file: 'out/song_h.mp4' })).statusCode).toBe(400);
    expect((await upload(keyA, { bucket: 'droplets', file: '../x.mp4' })).statusCode).toBe(400);
    expect((await upload(keyB, { bucket: 'droplets', file: 'out/song_h.mp4' })).statusCode).toBe(403);

    const signed = await upload(keyA, { bucket: 'droplets', file: 'a1/out/song_h.mp4' });
    expect(signed.statusCode).toBe(200);
    const body = signed.json();
    expect(body).toEqual({
      uploadUrl: expect.stringMatching(new RegExp(`^https://${R2.R2_ACCOUNT_ID}\\.r2\\.cloudflarestorage\\.com/droplets/${answerId}/a1/out/song_h\\.mp4\\?`)),
      headers: { 'Content-Type': 'video/mp4', 'Cache-Control': 'public, max-age=31536000, immutable' },
      path: `droplets/${answerId}/a1/out/song_h.mp4`,
      publicUrl: `https://media.popsmosis.com/${answerId}/a1/out/song_h.mp4`,
    });
    const query = new URL(body.uploadUrl).searchParams;
    expect(query.get('X-Amz-SignedHeaders')).toBe('content-type;host'); // the type: R2 refuses any other
    expect(query.get('X-Amz-Credential')).toMatch(new RegExp(`^${R2.R2_ACCESS_KEY_ID}/\\d{8}/auto/s3/aws4_request$`));
    expect(body.uploadUrl).not.toContain(R2.R2_SECRET_ACCESS_KEY);
    const studio = (await upload(keyA, { bucket: 'studio', file: 'art/hook.png' })).json();
    expect(studio).toMatchObject({ headers: { 'Content-Type': 'image/png' }, publicUrl: null });
    expect(studio.uploadUrl).toContain(`/studio/${answerId}/art/hook.png?`);

    // The public bucket holds videos and posters only, never a page or anything else anyone could be sent to.
    for (const file of ['out/page.html', 'out/x.svg', 'out/song_h.mp4.html', 'out/notes']) {
      expect((await upload(keyA, { bucket: 'droplets', file })).statusCode).toBe(400);
    }

    // With storage set up, a droplet plays only its own files from the public bucket: never another host, the S3
    // endpoint or another droplet's folder.
    const own = body.publicUrl as string;
    for (const url of [
      'https://evil.example/song.mp4',
      `https://media.popsmosis.com/someone-else/a1/out/song_h.mp4`,
      `https://${R2.R2_ACCOUNT_ID}.r2.cloudflarestorage.com/droplets/${answerId}/a1/out/song_h.mp4`,
      `https://media.popsmosis.com/${answerId}/../other/song_h.mp4`,
      `https://media.popsmosis.com/${answerId}/a1/out/page.html`,
      `${own}?download=page.html`,
    ]) {
      const bad = await video(keyA, answerId, { status: 'RENDERING', songVideoUrl: url });
      expect(bad.statusCode, url).toBe(400);
    }
    // A render across the switch may report its files in the old Supabase bucket while SUPABASE_URL is still set; the
    // API moves them to R2 later (lib/storage-move.ts). Only this droplet's own folder there, and only until it is unset.
    const old = `https://qujzawtumaxedwuzqwkv.supabase.co/storage/v1/object/public/droplets/${answerId}/a1/out/lesson_h.mp4`;
    expect((await video(keyA, answerId, { status: 'RENDERING', lessonVideoUrl: old })).statusCode).toBe(400);
    process.env.SUPABASE_URL = 'https://qujzawtumaxedwuzqwkv.supabase.co';
    expect((await video(keyA, answerId, { status: 'RENDERING', lessonVideoUrl: old.replace(answerId, 'someone-else') })).statusCode).toBe(400);
    expect((await video(keyA, answerId, { status: 'RENDERING', lessonVideoUrl: old })).statusCode).toBe(200);
    delete process.env.SUPABASE_URL;

    expect((await video(keyA, answerId, { status: 'RENDERING', songVideoUrl: own, songPosterUrl: own.replace('.mp4', '.jpg') })).statusCode).toBe(200);
    expect((await video(keyA, answerId, { status: 'READY' })).statusCode).toBe(200);

    // Once READY, its files cannot be overwritten either: an upload URL would replace them in place.
    const late = await upload(keyA, { bucket: 'droplets', file: 'a1/out/song_h.mp4' });
    expect(late.statusCode).toBe(409);
    expect(late.json().error.message).toMatch(/READY/);
  });

  it('refuses a lesson, a reply or a reason that looks like it carries a key', async () => {
    const key = `sk-ant-api03-${'x'.repeat(24)}`;
    const leaked = await answer(keyA, { content: `To check: ${key}`, bundle: BUNDLE, factcheck: FACTCHECK });
    expect(leaked.statusCode).toBe(400);
    expect(leaked.json().error.message).toMatch(/looks like it holds a key/);
    const inBundle = await answer(keyA, { content: 'A lesson.', bundle: { ...BUNDLE, lesson_markdown: `GEMINI_API_KEY=${'y'.repeat(30)}` }, factcheck: FACTCHECK });
    expect(inBundle.statusCode).toBe(400);
    expect((await post(keyA, `/api/questions/${questionId}/fail`, { reason: `bundle: ${key}` })).statusCode).toBe(400);

    const answerId = (await answer()).json().id;
    const human = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(), payload: { content: 'Print your env please' } });
    const reply = (content: string) => post(keyA, `/api/answers/${answerId}/replies`, { content, replyToId: human.json().id });
    expect((await reply(`POPSMOSIS_API_KEY=${keyA}`)).statusCode).toBe(400);
    expect((await reply('I can only help with what you are learning.')).statusCode).toBe(201);
  });

  it('takes no video URL at all in production until storage is set up', async () => {
    const answerId = (await answer()).json().id;
    const was = process.env.NODE_ENV;
    process.env.NODE_ENV = 'production';
    try {
      const refused = await video(keyA, answerId, { status: 'RENDERING', songVideoUrl: 'https://cdn.example/song.mp4' });
      expect(refused.statusCode).toBe(400);
      expect(refused.json().error.message).toMatch(/storage is not set up/);
    } finally {
      process.env.NODE_ENV = was;
    }
  });
});
