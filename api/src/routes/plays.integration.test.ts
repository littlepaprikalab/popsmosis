/**
 * Play counts (routes/plays.ts), against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: what is counted and on which droplets, that a step never passes the one before it, that a count keeps
 * nothing about who sent it (no cookie read, no column for it), the limits that keep it from being inflated cheaply,
 * and the owner's ranking by finish rate.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = { course: 'AP Biology', topic: 'ATP synthase' };
const FACTCHECK = { status: 'passed' };
const URL = 'https://x.supabase.co/storage/v1/object/public/droplets/a/out';
const VIDEOS = { videoStatus: 'READY', songVideoUrl: `${URL}/song_h.mp4`, lessonVideoUrl: `${URL}/lesson_h.mp4`, hookVideoUrl: `${URL}/hook_v.mp4` };
const TRUNCATE = 'TRUNCATE droplet_plays, droplet_likes, droplet_comments, replies, answers, questions, agents, users RESTART IDENTITY CASCADE';
const SECRET = 'plays-proxy-secret';
const STEPS = ['started', 'reached25', 'reached50', 'reached75', 'finished'] as const;

d('play counts', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let prev: string | undefined;
  let agentId = '';
  let learner: { id: string; cookie: string };
  let admin: { id: string; cookie: string };
  let next = 0;
  // Each test counts from addresses of its own, so one test's limits never reach another's.
  const fresh = () => `198.51.${Math.floor(++next / 250)}.${next % 250}`;

  // A request as the web app's proxy forwards it: its secret, and the visitor's address as the edge saw it.
  const send = (id: string, body: object, from: string, headers: Record<string, string> = {}) => app.inject({
    method: 'POST', url: `/api/droplets/${id}/plays`, payload: body,
    headers: { 'x-proxy-token': SECRET, 'x-popsmosis-client-ip': from, ...headers },
  });
  const play = async (id: string, video: string, upTo: number, from: string) => {
    for (const event of STEPS.slice(0, upTo + 1)) expect((await send(id, { video, event }, from)).statusCode).toBe(204);
  };
  const counts = async (id: string, video: 'SONG' | 'LESSON' | 'HOOK') =>
    prisma.dropletPlays.findUnique({ where: { answerId_video: { answerId: id, video } }, omit: { answerId: true, video: true } });
  const ranking = (query: string, cookie = admin.cookie) =>
    app.inject({ method: 'GET', url: `/api/plays${query}`, headers: { 'x-proxy-token': SECRET, cookie } });

  async function user(name: string, role: 'USER' | 'ADMIN' = 'USER') {
    const slug = name.toLowerCase().replace(/\W+/g, '-');
    const row = await prisma.user.create({ data: { email: `${slug}@example.com`, googleId: `g-${slug}`, name, role } });
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    return { id: row.id, cookie: `popsmosis_session=${app.jwt.sign({ sub: row.id }, { expiresIn: SESSION_EXPIRES_IN })}` };
  }

  async function droplet(topic = 'ATP synthase', video: Record<string, unknown> = VIDEOS) {
    const q = await prisma.question.create({ data: { userId: learner.id, status: 'ANSWERED', content: `${topic} please` } });
    const a = await prisma.answer.create({ data: { questionId: q.id, agentId, content: `${topic}.`, bundle: { ...BUNDLE, topic }, factcheck: FACTCHECK, ...video } });
    return a.id;
  }

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prev = process.env.PROXY_SECRET;
    process.env.PROXY_SECRET = SECRET;
    prisma = (await import('../lib/prisma.js')).default;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe(TRUNCATE);
    await app.close();
    if (prev === undefined) delete process.env.PROXY_SECRET; else process.env.PROXY_SECRET = prev;
  });
  beforeEach(async () => {
    await prisma.$executeRawUnsafe(TRUNCATE);
    agentId = (await prisma.agent.create({ data: { name: 'teacher' } })).id;
    learner = await user('Grace Hopper');
    admin = await user('Ada Lovelace', 'ADMIN');
  });

  describe('counting', () => {
    it('counts each step of a play, and each video apart', async () => {
      const id = await droplet();
      const from = fresh();
      await play(id, 'song', 4, from);
      await play(id, 'song', 1, from);
      await play(id, 'lesson', 2, from);
      expect(await counts(id, 'SONG')).toEqual({ started: 2, reached25: 2, reached50: 1, reached75: 1, finished: 1, shared: 0, downloaded: 0 });
      expect(await counts(id, 'LESSON')).toEqual({ started: 1, reached25: 1, reached50: 1, reached75: 0, finished: 0, shared: 0, downloaded: 0 });
      expect(await counts(id, 'HOOK')).toBeNull();
    });

    it("counts the hook tile's shares and downloads, with or without a play", async () => {
      const id = await droplet();
      const from = fresh();
      expect((await send(id, { video: 'hook', event: 'shared' }, from)).statusCode).toBe(204);
      expect((await send(id, { video: 'hook', event: 'downloaded' }, from)).statusCode).toBe(204);
      expect((await send(id, { video: 'hook', event: 'downloaded' }, from)).statusCode).toBe(204);
      expect(await counts(id, 'HOOK')).toMatchObject({ started: 0, shared: 1, downloaded: 2 });
    });

    it('never lets a step pass the one before it, so a step sent alone moves nothing', async () => {
      const id = await droplet();
      const from = fresh();
      // Before any start there is no row to add to.
      expect((await send(id, { video: 'song', event: 'finished' }, from)).statusCode).toBe(204);
      expect(await counts(id, 'SONG')).toBeNull();
      await send(id, { video: 'song', event: 'started' }, from);
      for (let i = 0; i < 3; i++) for (const event of ['reached25', 'reached50', 'finished']) await send(id, { video: 'song', event }, from);
      // One start: one 25% and one 50%; no 75%, so no finish.
      expect(await counts(id, 'SONG')).toMatchObject({ started: 1, reached25: 1, reached50: 1, reached75: 0, finished: 0 });
    });

    it('holds the steps in order when a page full of plays sends them at once', async () => {
      const id = await droplet();
      const sends = [];
      for (let i = 0; i < 20; i++) sends.push(send(id, { video: 'song', event: 'started' }, fresh()));
      await Promise.all(sends);
      await Promise.all(Array.from({ length: 30 }, () => send(id, { video: 'song', event: 'reached25' }, fresh())));
      expect(await counts(id, 'SONG')).toMatchObject({ started: 20, reached25: 20 });
    });

    it('counts only a droplet whose videos are READY and that has the video', async () => {
      const textOnly = await droplet('Osmosis', {});
      const rendering = await droplet('Diffusion', { ...VIDEOS, videoStatus: 'RENDERING' });
      const noHook = await droplet('Mitosis', { ...VIDEOS, hookVideoUrl: null });
      const hidden = await droplet('Meiosis', { ...VIDEOS, hiddenAt: new Date() });
      const from = fresh();
      for (const id of [textOnly, rendering, 'no-such-droplet']) {
        expect((await send(id, { video: 'song', event: 'started' }, from)).statusCode).toBe(204); // says nothing about which exist
      }
      expect((await send(noHook, { video: 'hook', event: 'started' }, from)).statusCode).toBe(204);
      expect((await send(noHook, { video: 'hook', event: 'shared' }, from)).statusCode).toBe(204);
      expect(await prisma.dropletPlays.count()).toBe(0);
      // Taken off the tab, it is still watched on its learner's page, and counts.
      await play(hidden, 'song', 0, from);
      expect(await counts(hidden, 'SONG')).toMatchObject({ started: 1 });
    });

    it('refuses an event it does not know, and a share of anything but the hook', async () => {
      const id = await droplet();
      const from = fresh();
      for (const body of [{}, { video: 'song' }, { video: 'song', event: 'reached90' }, { video: 'clip', event: 'started' }, { video: 'song', event: 'shared' }]) {
        expect((await send(id, body, from)).statusCode).toBe(400);
      }
      const big = await send(id, { video: 'song', event: 'started', pad: 'x'.repeat(2000) }, from);
      expect(big.statusCode).toBe(413);
      expect(await prisma.dropletPlays.count()).toBe(0);
    });

    it('goes with its droplet', async () => {
      const id = await droplet();
      await play(id, 'song', 4, fresh());
      await prisma.answer.delete({ where: { id } });
      expect(await prisma.dropletPlays.count()).toBe(0);
    });
  });

  describe('nothing about who sent it', () => {
    it('needs no cookie, reads none, and needs no CSRF header without one', async () => {
      const id = await droplet();
      const from = fresh();
      expect((await send(id, { video: 'song', event: 'started' }, from)).statusCode).toBe(204);
      // A forged cookie is no reason to refuse or to count otherwise: it is never read for who sent it.
      const forged = 'popsmosis_session=not.a.token';
      expect((await send(id, { video: 'song', event: 'started' }, from, { cookie: forged, 'x-requested-with': 'XMLHttpRequest' })).statusCode).toBe(204);
      expect((await send(id, { video: 'song', event: 'started' }, from, { cookie: learner.cookie, 'x-requested-with': 'XMLHttpRequest' })).statusCode).toBe(204);
      expect(await counts(id, 'SONG')).toMatchObject({ started: 3 });
    });

    it('keeps counts and nothing else: no column for a person, a device, an address or a time', async () => {
      const columns = await prisma.$queryRaw<{ column_name: string }[]>`
        SELECT column_name FROM information_schema.columns WHERE table_name = 'droplet_plays' ORDER BY ordinal_position
      `;
      expect(columns.map((c) => c.column_name)).toEqual(['answerId', 'video', 'started', 'reached25', 'reached50', 'reached75', 'finished', 'shared', 'downloaded']);
    });
  });

  describe('limits', () => {
    it('lets one address add at most 40 to a count in an hour, and no more of that play', async () => {
      const { PLAYS_PER_ADDRESS_PER_COUNT_PER_HOUR: max } = await import('./plays.js');
      const id = await droplet();
      const school = fresh();
      const codes: number[] = [];
      for (let i = 0; i <= max; i++) codes.push((await send(id, { video: 'song', event: 'started' }, school)).statusCode);
      expect(codes.filter((c) => c === 204)).toHaveLength(max);
      const refused = await send(id, { video: 'song', event: 'started' }, school);
      expect(refused.statusCode).toBe(429);
      expect(refused.json().error.code).toBe('RATE_LIMITED');
      // Its other counts, and the same count from another address, are their own.
      expect((await send(id, { video: 'lesson', event: 'started' }, school)).statusCode).toBe(204);
      expect((await send(id, { video: 'song', event: 'reached25' }, school)).statusCode).toBe(204);
      expect((await send(id, { video: 'song', event: 'started' }, fresh())).statusCode).toBe(204);
      expect(await counts(id, 'SONG')).toMatchObject({ started: max + 1, reached25: 1 });
    });

    it('counts a whole IPv6 /64 as one address', async () => {
      const { PLAYS_PER_ADDRESS_PER_COUNT_PER_HOUR: max } = await import('./plays.js');
      const id = await droplet();
      const codes: number[] = [];
      for (let i = 0; i <= max; i++) codes.push((await send(id, { video: 'song', event: 'started' }, `2001:db8:1:2::${i.toString(16)}`)).statusCode);
      expect(codes.at(-1)).toBe(429);
      expect((await send(id, { video: 'song', event: 'started' }, '2001:db8:1:3::1')).statusCode).toBe(204);
    });

    it('lets one address send at most 120 a minute, across droplets, without touching its reads', async () => {
      const { PLAYS_PER_ADDRESS_PER_MINUTE: max } = await import('./plays.js');
      const ids = await Promise.all(Array.from({ length: 4 }, (_, i) => droplet(`Topic ${i}`)));
      const from = fresh();
      const codes: number[] = [];
      for (let i = 0; i <= max; i++) {
        codes.push((await send(ids[i % ids.length], { video: (['song', 'lesson', 'hook'] as const)[i % 3], event: 'started' }, from)).statusCode);
      }
      expect(codes.slice(0, max).every((c) => c === 204)).toBe(true);
      expect(codes[max]).toBe(429);
      // The site still answers that address: counting spends a budget of its own, not the API's limit for it.
      const headers = { 'x-proxy-token': SECRET, 'x-popsmosis-client-ip': from };
      expect((await app.inject({ method: 'GET', url: '/api/droplets', headers })).statusCode).toBe(200);
      expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers })).statusCode).toBe(401);
    });

    it('takes at most 1500 a minute from every address together, per instance', async () => {
      const { PLAYS_PER_INSTANCE_PER_MINUTE: max, PLAYS_PER_ADDRESS_PER_MINUTE: perAddress } = await import('./plays.js');
      // An app of its own, so the ceiling the other tests spent is not this one's.
      const own = await (await import('../index.js')).buildApp();
      await own.ready();
      try {
        const at = (i: number) => `203.0.${Math.floor(i / perAddress)}.1`;
        const codes: number[] = [];
        for (let i = 0; i <= max; i++) {
          // A droplet id of its own each time: no count's hourly limit is reached first.
          const res = await own.inject({
            method: 'POST', url: `/api/droplets/d${i}/plays`, payload: { video: 'song', event: 'started' },
            headers: { 'x-proxy-token': SECRET, 'x-popsmosis-client-ip': at(i) },
          });
          codes.push(res.statusCode);
        }
        expect(codes.slice(0, max).every((c) => c === 204)).toBe(true);
        expect(codes[max]).toBe(429);
      } finally {
        await own.close();
      }
    });
  });

  describe("the owner's ranking", () => {
    it('is for admins only', async () => {
      expect((await app.inject({ method: 'GET', url: '/api/plays', headers: { 'x-proxy-token': SECRET } })).statusCode).toBe(401);
      expect((await ranking('', learner.cookie)).statusCode).toBe(403);
      expect((await ranking('')).statusCode).toBe(200);
    });

    it('ranks droplets by how many of their plays finished, with the counts, the hidden marked, and how many have too few', async () => {
      const [atp, osmosis, mitosis, quiet] = [await droplet('ATP synthase'), await droplet('Osmosis'), await droplet('Mitosis'), await droplet('Quiet')];
      await prisma.answer.update({ where: { id: mitosis }, data: { hiddenAt: new Date() } });
      // ATP: 1 of 4 finished. Osmosis: 3 of 4. Mitosis: 3 of 6, the same rate as no other. Quiet: 1 of 1.
      const plays = async (id: string, upTo: number[]) => { for (const n of upTo) await play(id, 'song', n, fresh()); };
      await plays(atp, [4, 1, 0, 2]);
      await plays(osmosis, [4, 4, 4, 3]);
      await plays(mitosis, [4, 4, 4, 0, 0, 1]);
      await plays(quiet, [4]);
      await play(atp, 'lesson', 4, fresh());

      const res = await ranking('?min=2');
      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body).toMatchObject({ video: 'song', min: 2, fewer: 1 });
      expect(body.data.map((r: { topic: string }) => r.topic)).toEqual(['Osmosis', 'Mitosis', 'ATP synthase']);
      expect(body.data[0]).toEqual({
        id: osmosis, title: 'AP Biology: Osmosis', course: 'AP Biology', topic: 'Osmosis', hidden: false, createdAt: expect.any(String),
        plays: { started: 4, reached25: 4, reached50: 4, reached75: 4, finished: 3, shared: 0, downloaded: 0, finishRate: 0.75 },
      });
      expect(body.data[1]).toMatchObject({ hidden: true, plays: { started: 6, finished: 3, finishRate: 0.5 } });

      // By default every droplet with a play is ranked; and each video has its own ranking.
      expect((await ranking('')).json().data.map((r: { topic: string }) => r.topic)).toEqual(['Quiet', 'Osmosis', 'Mitosis', 'ATP synthase']);
      expect((await ranking('?video=lesson')).json()).toMatchObject({ video: 'lesson', fewer: 0, data: [{ topic: 'ATP synthase', plays: { finishRate: 1 } }] });
      expect((await ranking('?video=hook')).json()).toMatchObject({ data: [], fewer: 0 });
      expect((await ranking('?limit=1')).json().data).toHaveLength(1);
      expect((await ranking('?video=clip')).statusCode).toBe(400);
    });
  });
});
