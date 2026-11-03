/**
 * Clearing a deleted droplet's files from R2 (lib/storage-delete.ts), against a real database, with R2 stood in for by
 * a fake fetch: a learner deleting a topic with everything has its folders cleared in both buckets at once, a file a
 * render still running uploads afterwards goes at a later sweep, the record stays until no upload URL signed before
 * the delete can still be used, and R2 refusing leaves it for the next sweep. Skipped unless INTEGRATION=1.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';
import type { Bucket } from './storage.js';

const RUN = process.env.INTEGRATION === '1';

const ENV = {
  R2_ACCOUNT_ID: '0123456789abcdef0123456789abcdef',
  R2_ACCESS_KEY_ID: '9f2c4e1a7b3d5f6e8a0c2e4b6d8f0a1c',
  R2_SECRET_ACCESS_KEY: 'a'.repeat(64),
  R2_PUBLIC_URL: 'https://media.popsmosis.com',
};
const ENDPOINT = `https://${ENV.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`;

/** R2's S3 API behind one fetch: the keys of both buckets, and whether it refuses everything. */
function world() {
  const store: Record<Bucket, Set<string>> = { droplets: new Set(), studio: new Set() };
  let refusing = false;
  let calls = 0;
  const fetchImpl = (async (input: string | URL, init: RequestInit = {}) => {
    const url = new URL(String(input));
    if (url.origin !== ENDPOINT) throw new Error(`unexpected ${url}`);
    calls++;
    const method = init.method ?? 'GET';
    const [, bucket, ...rest] = url.pathname.split('/') as [string, Bucket, ...string[]];
    if (refusing) return new Response('<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>', { status: 403 });
    if (method === 'GET') {
      const prefix = url.searchParams.get('prefix') ?? '';
      const keys = [...store[bucket]].filter((k) => k.startsWith(prefix)).slice(0, Number(url.searchParams.get('max-keys')));
      return new Response(`<ListBucketResult>${keys.map((k) => `<Contents><Key>${k}</Key></Contents>`).join('')}</ListBucketResult>`);
    }
    if (method === 'DELETE') {
      store[bucket].delete(rest.map(decodeURIComponent).join('/'));
      return new Response(null, { status: 204 });
    }
    throw new Error(`unexpected ${method} ${url}`);
  }) as unknown as typeof fetch;
  const put = (answerId: string) => {
    store.droplets.add(`${answerId}/a1/out/song_h.mp4`).add(`${answerId}/a1/out/song_h.jpg`).add(`${answerId}/a2/out/lesson_h.mp4`);
    store.studio.add(`${answerId}/a1/art/scene-1.png`).add(`${answerId}/a1/audio/song.mp3`).add(`${answerId}/a1/timeline_song.json`);
  };
  const filesOf = (answerId: string) => [...store.droplets, ...store.studio].filter((k) => k.startsWith(`${answerId}/`));
  return { store, fetchImpl, put, filesOf, refuse: (on: boolean) => { refusing = on; }, calls: () => calls };
}

describe.runIf(RUN)('clearing a deleted droplet\'s files from R2', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./prisma.js'))['default'];
  let lib: typeof import('./storage-delete.js');
  let config: import('./storage.js').StorageConfig;
  let r2: ReturnType<typeof world>;
  let agentId = '';
  let learner = { id: '', cookie: '' };
  const saved: Record<string, string | undefined> = {};

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });

  /** A topic of the learner's with its lesson, its videos READY: the question's id and the answer's. */
  async function droplet(topic: string) {
    const question = await prisma.question.create({ data: { userId: learner.id, content: topic, status: 'ANSWERED' } });
    const answer = await prisma.answer.create({
      data: {
        questionId: question.id, agentId, content: `All about ${topic}.`, videoStatus: 'READY',
      },
    });
    r2.put(answer.id);
    return { questionId: question.id, answerId: answer.id };
  }

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    process.env.JWT_SECRET ||= 'test-secret';
    for (const [name, value] of Object.entries(ENV)) {
      saved[name] = process.env[name];
      process.env[name] = value;
    }
    prisma = (await import('./prisma.js')).default;
    lib = await import('./storage-delete.js');
    const storage = (await import('./storage.js')).storageConfig();
    if (!storage || 'problem' in storage) throw new Error('the test storage settings do not make a config');
    config = storage;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });
  afterAll(async () => {
    vi.unstubAllGlobals();
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users, storage_deletions RESTART IDENTITY CASCADE');
    await app.close();
    await prisma.$disconnect();
  });

  beforeEach(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users, storage_deletions RESTART IDENTITY CASCADE');
    r2 = world();
    // The route clears in the background with the global fetch, as in production.
    vi.stubGlobal('fetch', r2.fetchImpl);
    agentId = (await prisma.agent.create({ data: { name: 'teacher' } })).id;
    const row = await prisma.user.create({ data: { email: 'grace@example.com', googleId: 'g-grace', name: 'Grace' } });
    const { SESSION_EXPIRES_IN } = await import('./session.js');
    learner = { id: row.id, cookie: `popsmosis_session=${app.jwt.sign({ sub: row.id }, { expiresIn: SESSION_EXPIRES_IN })}` };
  });

  it('clears the droplet\'s folder in both buckets at once, and no other droplet\'s', async () => {
    const gone = await droplet('ATP synthase');
    const kept = await droplet('Glycolysis');
    expect(r2.filesOf(gone.answerId)).toHaveLength(6);

    const del = await app.inject({ method: 'DELETE', url: `/api/questions/${gone.questionId}/everything`, headers: asUser(learner.cookie) });
    expect(del.statusCode).toBe(204);
    await vi.waitFor(() => expect(r2.filesOf(gone.answerId)).toEqual([]));
    expect(r2.filesOf(kept.answerId)).toHaveLength(6);
    // Recorded until no URL signed before the delete can still upload; the other droplet is not.
    expect((await prisma.storageDeletion.findMany()).map((r) => r.answerId)).toEqual([gone.answerId]);
  });

  it('keeps the record until a pass after the last upload URL ran out, which takes what a running render uploaded', async () => {
    const { questionId, answerId } = await droplet('ATP synthase');
    await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asUser(learner.cookie) });
    await vi.waitFor(() => expect(r2.filesOf(answerId)).toEqual([]));
    const { createdAt } = await prisma.storageDeletion.findUniqueOrThrow({ where: { answerId } });

    expect((await prisma.storageDeletion.findUniqueOrThrow({ where: { answerId } })).clearedAt).not.toBeNull();

    // The render, still going, uploads its lesson video with a URL it was signed before the delete.
    r2.store.droplets.add(`${answerId}/a1/out/lesson_h.mp4`);
    const at = (ms: number) => () => new Date(createdAt.getTime() + ms);
    // The hourly sweeps in between leave it be: its first pass cleared it, and only the one after the window is due.
    const calls = r2.calls();
    expect(await lib.clearDeletedFiles(config, prisma, { fetchImpl: r2.fetchImpl, now: at(60 * 60 * 1000) })).toEqual({ cleared: [], failed: [] });
    expect(r2.calls()).toBe(calls);

    const settled = await lib.clearDeletedFiles(config, prisma, { fetchImpl: r2.fetchImpl, now: at(lib.SETTLE_MS) });
    expect(settled).toEqual({ cleared: [{ answerId, files: 1, done: true }], failed: [] });
    expect(r2.filesOf(answerId)).toEqual([]);
    expect(await prisma.storageDeletion.count()).toBe(0);
  });

  it('keeps the reason when R2 refuses, and the next sweep finishes', async () => {
    const { questionId, answerId } = await droplet('ATP synthase');
    r2.refuse(true);
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asUser(learner.cookie) })).statusCode).toBe(204);
    // The droplet is gone from the database whatever R2 says.
    expect(await prisma.answer.count({ where: { id: answerId } })).toBe(0);
    await vi.waitFor(async () => expect((await prisma.storageDeletion.findUniqueOrThrow({ where: { answerId } })).tries).toBe(1));
    expect((await prisma.storageDeletion.findUniqueOrThrow({ where: { answerId } })).lastError).toMatch(/403 AccessDenied/);
    expect(r2.filesOf(answerId)).toHaveLength(6);

    r2.refuse(false);
    const later = () => new Date(Date.now() + lib.SETTLE_MS);
    const report = await lib.clearDeletedFiles(config, prisma, { fetchImpl: r2.fetchImpl, now: later });
    expect(report).toEqual({ cleared: [{ answerId, files: 6, done: true }], failed: [] });
    expect(r2.filesOf(answerId)).toEqual([]);
    expect(await prisma.storageDeletion.count()).toBe(0);
  });

  it('clears at the next sweep a droplet whose first pass never ran (the API restarted), and takes the rows that failed least first', async () => {
    // Deleted, recorded, and the instance went down before its first pass: nothing was cleared.
    const fresh = await droplet('ATP synthase');
    const stuck = await droplet('Glycolysis');
    await prisma.answer.deleteMany({ where: { id: { in: [fresh.answerId, stuck.answerId] } } });
    await prisma.storageDeletion.createMany({ data: [
      { answerId: stuck.answerId, createdAt: new Date(Date.now() - 60_000), tries: 5, lastError: 'R2 did not answer' },
      { answerId: fresh.answerId },
    ] });
    const report = await lib.clearDeletedFiles(config, prisma, { fetchImpl: r2.fetchImpl });
    // The one that never failed first, though it is the newer; both are done, neither for good yet.
    expect(report.cleared.map((c) => c.answerId)).toEqual([fresh.answerId, stuck.answerId]);
    expect(r2.filesOf(fresh.answerId)).toEqual([]);
    expect(r2.filesOf(stuck.answerId)).toEqual([]);
    const rows = await prisma.storageDeletion.findMany({ orderBy: { tries: 'asc' } });
    expect(rows.map((r) => [r.clearedAt !== null, r.lastError])).toEqual([[true, null], [true, null]]);
  });

  it('records nothing and touches no file when the request is only taken back: the lesson stays', async () => {
    const { questionId, answerId } = await droplet('ATP synthase');
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}`, headers: asUser(learner.cookie) })).statusCode).toBe(204);
    expect(await prisma.storageDeletion.count()).toBe(0);
    expect(r2.calls()).toBe(0);
    expect(r2.filesOf(answerId)).toHaveLength(6);
  });

  it('runs one sweep at a time in a process', async () => {
    const { questionId } = await droplet('ATP synthase');
    r2.refuse(true); // so the record stays for the sweeps below
    await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asUser(learner.cookie) });
    await vi.waitFor(async () => expect((await prisma.storageDeletion.findFirstOrThrow()).tries).toBe(1));
    const [first, second] = await Promise.all([lib.sweepDeletedFiles(config, prisma, r2.fetchImpl), lib.sweepDeletedFiles(config, prisma, r2.fetchImpl)]);
    expect(first?.failed).toHaveLength(1);
    expect(second).toBeNull();
  });
});
