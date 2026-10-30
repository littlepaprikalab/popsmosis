/**
 * Moving the videos made before R2 from Supabase Storage (lib/storage-move.ts), against a real database, with
 * Supabase and R2 stood in for by a fake fetch: what moves, what stays, and that a droplet's URLs switch only once
 * its files answer at the new address.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { StorageConfig } from './storage.js';

const RUN = process.env.INTEGRATION === '1';

const SUPABASE = 'https://qujzawtumaxedwuzqwkv.supabase.co/storage/v1/object/public/droplets';
const ACCOUNT = '0123456789abcdef0123456789abcdef';
const CONFIG: StorageConfig = {
  endpoint: `https://${ACCOUNT}.r2.cloudflarestorage.com`,
  accessKeyId: '9f2c4e1a7b3d5f6e8a0c2e4b6d8f0a1c',
  secretAccessKey: 'a'.repeat(64),
  publicUrl: 'https://media.popsmosis.com',
};

/** Supabase's public bucket and R2 behind one fetch: what each was asked, and what R2 holds. */
function world() {
  const asked: string[] = [];
  const r2 = new Map<string, { body: Buffer; headers: Record<string, string> }>();
  const missing = new Set<string>(); // Supabase URLs that answer 404
  let publicDown = false; // the custom domain is not Active yet
  let onPut: (key: string) => Promise<void> = async () => {};
  const fetchImpl = (async (input: string, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method ?? 'GET';
    asked.push(`${method} ${url.split('?')[0]}`);
    if (url.startsWith(SUPABASE)) {
      return missing.has(url) ? new Response('{"error":"not_found"}', { status: 404 }) : new Response(`bytes of ${url.slice(SUPABASE.length)}`);
    }
    if (url.startsWith(`${CONFIG.endpoint}/droplets/`) && method === 'PUT') {
      const key = url.slice(`${CONFIG.endpoint}/droplets/`.length).split('?')[0];
      r2.set(key, { body: Buffer.from(init.body as Uint8Array), headers: init.headers as Record<string, string> });
      await onPut(key);
      return new Response(null, { status: 200 });
    }
    if (url.startsWith(`${CONFIG.publicUrl}/`) && method === 'HEAD') {
      const stored = r2.get(url.slice(CONFIG.publicUrl.length + 1));
      if (publicDown || !stored) return new Response(null, { status: 404 });
      return new Response(null, { status: 200, headers: { 'content-length': String(stored.body.length) } });
    }
    throw new Error(`unexpected ${method} ${url}`);
  }) as unknown as typeof fetch;
  return {
    asked, r2, missing, fetchImpl,
    setPublicDown: (down: boolean) => { publicDown = down; },
    setOnPut: (fn: (key: string) => Promise<void>) => { onPut = fn; },
  };
}

describe.runIf(RUN)('moving the videos from Supabase Storage to R2', () => {
  let prisma: (typeof import('./prisma.js'))['default'];
  let move: (typeof import('./storage-move.js'))['moveSupabaseVideos'];
  let sweep: (typeof import('./storage-move.js'))['sweepSupabaseVideos'];
  let agentId = '';
  let userId = '';

  /** A lesson whose videos are at these URLs; with own: true, under its own folder of the old bucket. */
  async function droplet(urls: (id: string) => Record<string, string | null>, extra: Record<string, unknown> = { videoStatus: 'READY' }) {
    const q = await prisma.question.create({ data: { userId, status: 'ANSWERED', content: 'ATP synthase' } });
    const a = await prisma.answer.create({ data: { questionId: q.id, agentId, content: 'A lesson.', ...extra } });
    return (await prisma.answer.update({ where: { id: a.id }, data: urls(a.id) })).id;
  }
  const onSupabase = (id: string) => ({
    songVideoUrl: `${SUPABASE}/${id}/a1/out/song_h.mp4`,
    lessonVideoUrl: `${SUPABASE}/${id}/a1/out/lesson_h.mp4`,
    songPosterUrl: `${SUPABASE}/${id}/a1/out/song_h.jpg`,
    lessonPosterUrl: `${SUPABASE}/${id}/a1/out/lesson_h.jpg`,
    hookVideoUrl: `${SUPABASE}/${id}/a1/out/hook_v.mp4`,
    hookPosterUrl: `${SUPABASE}/${id}/a1/out/hook_v.jpg`,
  });
  const urlsOf = (id: string) => prisma.answer.findUniqueOrThrow({
    where: { id },
    select: { songVideoUrl: true, lessonVideoUrl: true, songPosterUrl: true, lessonPosterUrl: true, hookVideoUrl: true, hookPosterUrl: true },
  });

  beforeAll(async () => {
    prisma = (await import('./prisma.js')).default;
    ({ moveSupabaseVideos: move, sweepSupabaseVideos: sweep } = await import('./storage-move.js'));
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    await prisma.$disconnect();
  });
  beforeEach(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    agentId = (await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: 'h' } })).id;
    userId = (await prisma.user.create({ data: { email: 'grace@example.com', googleId: 'g-grace', name: 'Grace' } })).id;
  });

  it('copies each file to the same place on R2, checks it at the public address, then switches the URLs', async () => {
    const id = await droplet(onSupabase);
    const hidden = await droplet((x) => ({ songVideoUrl: `${SUPABASE}/${x}/a2/out/song_h.mp4`, lessonVideoUrl: `${SUPABASE}/${x}/a2/out/lesson_h.mp4` }),
      { videoStatus: 'READY', hiddenAt: new Date() });
    const w = world();

    expect(await move(CONFIG, prisma, w.fetchImpl)).toEqual({ moved: [id, hidden], failed: [] });
    expect(await urlsOf(id)).toEqual({
      songVideoUrl: `https://media.popsmosis.com/${id}/a1/out/song_h.mp4`,
      lessonVideoUrl: `https://media.popsmosis.com/${id}/a1/out/lesson_h.mp4`,
      songPosterUrl: `https://media.popsmosis.com/${id}/a1/out/song_h.jpg`,
      lessonPosterUrl: `https://media.popsmosis.com/${id}/a1/out/lesson_h.jpg`,
      hookVideoUrl: `https://media.popsmosis.com/${id}/a1/out/hook_v.mp4`, // the hook cut moves with them
      hookPosterUrl: `https://media.popsmosis.com/${id}/a1/out/hook_v.jpg`,
    });
    expect(await urlsOf(hidden)).toMatchObject({ songVideoUrl: `https://media.popsmosis.com/${hidden}/a2/out/song_h.mp4`, songPosterUrl: null });

    // The same bytes, typed and cached as a new upload would be.
    expect(w.r2.get(`${id}/a1/out/song_h.mp4`)?.body.toString()).toBe(`bytes of /${id}/a1/out/song_h.mp4`);
    expect(w.r2.get(`${id}/a1/out/song_h.mp4`)?.headers).toEqual({ 'Content-Type': 'video/mp4', 'Cache-Control': 'public, max-age=31536000, immutable' });
    expect(w.r2.get(`${id}/a1/out/lesson_h.jpg`)?.headers['Content-Type']).toBe('image/jpeg');
    // Each file: read from Supabase, put on R2, read back where viewers will read it.
    expect(w.asked.slice(0, 3)).toEqual([
      `GET ${SUPABASE}/${id}/a1/out/song_h.mp4`,
      `PUT ${CONFIG.endpoint}/droplets/${id}/a1/out/song_h.mp4`,
      `HEAD https://media.popsmosis.com/${id}/a1/out/song_h.mp4`,
    ]);

    // Once moved, a boot has nothing left to do.
    const again = world();
    expect(await move(CONFIG, prisma, again.fetchImpl)).toEqual({ moved: [], failed: [] });
    expect(again.asked).toEqual([]);
  });

  it('leaves alone what is not in the old bucket: R2 files, seeded clips, text-only lessons', async () => {
    const onR2 = await droplet((x) => ({ songVideoUrl: `https://media.popsmosis.com/${x}/a1/out/song_h.mp4` }));
    const seeded = await droplet(() => ({ songVideoUrl: 'http://localhost:3000/samples/song.mp4' }));
    const textOnly = await droplet(() => ({}), {});
    const w = world();
    expect(await move(CONFIG, prisma, w.fetchImpl)).toEqual({ moved: [], failed: [] });
    expect(w.asked).toEqual([]);
    expect((await urlsOf(onR2)).songVideoUrl).toBe(`https://media.popsmosis.com/${onR2}/a1/out/song_h.mp4`);
    expect((await urlsOf(seeded)).songVideoUrl).toBe('http://localhost:3000/samples/song.mp4');
    expect(await urlsOf(textOnly)).toEqual({ songVideoUrl: null, lessonVideoUrl: null, songPosterUrl: null, lessonPosterUrl: null, hookVideoUrl: null, hookPosterUrl: null });
  });

  it('keeps a droplet on Supabase when one of its files cannot be copied, and moves the rest', async () => {
    const broken = await droplet(onSupabase);
    const fine = await droplet(onSupabase);
    const w = world();
    w.missing.add(`${SUPABASE}/${broken}/a1/out/lesson_h.jpg`);
    const report = await move(CONFIG, prisma, w.fetchImpl);
    expect(report.moved).toEqual([fine]);
    expect(report.failed).toEqual([{ answerId: broken, reason: `Supabase answered 404 for ${SUPABASE}/${broken}/a1/out/lesson_h.jpg` }]);
    expect(await urlsOf(broken)).toEqual(onSupabase(broken)); // still playing from where it was

    // The next boot tries it again.
    expect(await move(CONFIG, prisma, world().fetchImpl)).toEqual({ moved: [broken], failed: [] });
  });

  it('switches nothing while the public domain does not serve the copies', async () => {
    const id = await droplet(onSupabase);
    const w = world();
    w.setPublicDown(true);
    const { failed } = await move(CONFIG, prisma, w.fetchImpl);
    expect(failed).toEqual([{ answerId: id, reason: expect.stringMatching(/answered 404; is https:\/\/media\.popsmosis\.com Active on the droplets bucket\?$/) }]);
    expect(await urlsOf(id)).toEqual(onSupabase(id));

    // A host that does not answer at all is named, not just "fetch failed".
    const offline = (async (input: string) => { if (String(input).startsWith(SUPABASE)) throw new TypeError('fetch failed'); return w.fetchImpl(input); }) as unknown as typeof fetch;
    expect((await move(CONFIG, prisma, offline)).failed).toEqual([{ answerId: id, reason: `${SUPABASE}/${id}/a1/out/song_h.mp4 did not answer: fetch failed` }]);
  });

  it('says what it is to the hosts Cloudflare guards, and sends R2 only the signed headers', async () => {
    const id = await droplet(onSupabase);
    const seen: { url: string; headers: Record<string, string> }[] = [];
    const w = world();
    const spy = (async (input: string, init: RequestInit = {}) => {
      seen.push({ url: String(input).split('?')[0], headers: { ...(init.headers as Record<string, string>) } });
      return w.fetchImpl(input, init);
    }) as unknown as typeof fetch;
    await move(CONFIG, prisma, spy);
    const [get, put, head] = seen;
    expect(get.headers['User-Agent']).toMatch(/^popsmosis-api\//);
    expect(head).toEqual({ url: `https://media.popsmosis.com/${id}/a1/out/song_h.mp4`, headers: { 'User-Agent': expect.stringMatching(/^popsmosis-api\//) } });
    expect(put.headers).toEqual({ 'Content-Type': 'video/mp4', 'Cache-Control': 'public, max-age=31536000, immutable' });
  });

  it('copies nothing from outside the droplet\'s own folder', async () => {
    const id = await droplet((x) => ({ songVideoUrl: `${SUPABASE}/someone-else/a1/out/song_h.mp4`, lessonVideoUrl: `${SUPABASE}/${x}/a1/out/lesson_h.mp4` }));
    const w = world();
    const { failed } = await move(CONFIG, prisma, w.fetchImpl);
    expect(failed).toEqual([{ answerId: id, reason: expect.stringMatching(/is not under this droplet's own folder$/) }]);
    expect(w.asked).toEqual([]);
  });

  it('does not overwrite URLs that changed while the files were copied', async () => {
    const id = await droplet(onSupabase);
    const w = world();
    const changed = `${SUPABASE}/${id}/a3/out/song_h.mp4`;
    w.setOnPut(async (key) => {
      if (key.endsWith('lesson_h.jpg')) await prisma.answer.update({ where: { id }, data: { songVideoUrl: changed } });
    });
    const { failed } = await move(CONFIG, prisma, w.fetchImpl);
    expect(failed).toEqual([{ answerId: id, reason: expect.stringMatching(/changed while the files were copied/) }]);
    expect((await urlsOf(id)).songVideoUrl).toBe(changed);
  });

  it('is not an error when another API instance moved the same droplet first', async () => {
    const id = await droplet(onSupabase);
    const w = world();
    // The other instance switches the URLs to the same R2 files while this one is still copying.
    w.setOnPut(async (key) => {
      if (key.endsWith('hook_v.jpg')) {
        await prisma.answer.update({ where: { id }, data: Object.fromEntries(Object.entries(onSupabase(id)).map(([f, u]) => [f, u.replace(SUPABASE, CONFIG.publicUrl)])) });
      }
    });
    expect(await move(CONFIG, prisma, w.fetchImpl)).toEqual({ moved: [], failed: [] });
    expect((await urlsOf(id)).hookVideoUrl).toBe(`https://media.popsmosis.com/${id}/a1/out/hook_v.mp4`);
  });

  it('runs one sweep at a time in a process: one started while another runs does nothing', async () => {
    await droplet(onSupabase);
    const w = world();
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    w.setOnPut(async () => held);
    const first = sweep(CONFIG, prisma, w.fetchImpl);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(await sweep(CONFIG, prisma, w.fetchImpl)).toBeNull();
    release();
    expect((await first)?.moved).toHaveLength(1);
    // And the next one runs again once the first is done.
    expect(await sweep(CONFIG, prisma, world().fetchImpl)).toEqual({ moved: [], failed: [] });
  });
});
