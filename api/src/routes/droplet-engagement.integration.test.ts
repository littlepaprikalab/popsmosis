/**
 * Thumbs-up and comments on the public Droplets tab, against a real Postgres
 * (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: who may like and comment (signed in, on a droplet that is on the tab), that
 * liking is idempotent and counts once per person however many requests race, that
 * the counts stay exact through cascades and bulk writes (the triggers), comment
 * pages by cursor, the Idempotency-Key replay, who may delete a comment, the hourly
 * limit, and that a comment says nothing about its author beyond a first name.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = { course: 'AP Biology', topic: 'ATP synthase', lyrics: [{ section: 'chorus', text: 'Spin the rotor' }] };
const FACTCHECK = { status: 'passed' };
const SONG = 'https://x.supabase.co/storage/v1/object/public/droplets/a/out/song_h.mp4';
const TRUNCATE = 'TRUNCATE droplet_likes, droplet_comments, replies, answers, questions, agents, users RESTART IDENTITY CASCADE';

d('likes and comments on the Droplets tab', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let agentId = '';
  let agentKey = '';
  let grace: { id: string; cookie: string };
  let alan: { id: string; cookie: string };
  let admin: { id: string; cookie: string };

  const write = (method: 'PUT' | 'DELETE' | 'POST', url: string, cookie?: string, payload?: object, headers: Record<string, string> = {}) =>
    app.inject({ method, url, payload, headers: { ...(cookie ? { cookie, 'x-requested-with': 'XMLHttpRequest' } : {}), ...headers } });
  const get = (url: string, cookie?: string) => app.inject({ method: 'GET', url, headers: cookie ? { cookie } : {} });
  const like = (id: string, cookie: string) => write('PUT', `/api/droplets/${id}/like`, cookie);
  const unlike = (id: string, cookie: string) => write('DELETE', `/api/droplets/${id}/like`, cookie);
  const comment = (id: string, cookie: string, content: string, key?: string) =>
    write('POST', `/api/droplets/${id}/comments`, cookie, { content }, key ? { 'idempotency-key': key } : {});
  const counts = async (id: string) => prisma.answer.findUniqueOrThrow({ where: { id }, select: { likeCount: true, commentCount: true } });

  async function user(name: string, role: 'USER' | 'ADMIN' = 'USER') {
    const slug = name.toLowerCase().replace(/\W+/g, '-');
    const row = await prisma.user.create({ data: { email: `${slug}@example.com`, googleId: `g-${slug}`, name, role, avatarUrl: `https://avatars.example/${slug}.png` } });
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    return { id: row.id, cookie: `popsmosis_session=${app.jwt.sign({ sub: row.id }, { expiresIn: SESSION_EXPIRES_IN })}` };
  }

  async function droplet(minutesAgo = 10, video: Record<string, unknown> = { videoStatus: 'READY', songVideoUrl: SONG }) {
    const at = new Date(Date.now() - minutesAgo * 60_000);
    const q = await prisma.question.create({ data: { userId: grace.id, status: 'ANSWERED', content: 'ATP synthase please', createdAt: at } });
    const a = await prisma.answer.create({ data: { questionId: q.id, agentId, content: 'ATP synthase spins.', createdAt: at, bundle: BUNDLE, factcheck: FACTCHECK, ...video } });
    return a.id;
  }

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prisma = (await import('../lib/prisma.js')).default;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe(TRUNCATE);
    await app.close();
    await prisma.$disconnect();
  });
  beforeEach(async () => {
    delete process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR;
    await prisma.$executeRawUnsafe(TRUNCATE);
    const { generateApiKey } = await import('../lib/apikey.js');
    const key = generateApiKey();
    agentKey = key.raw;
    agentId = (await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: key.hashed } })).id;
    grace = await user('Grace Brewster Hopper');
    alan = await user('Alan Turing');
    admin = await user('Ada Lovelace', 'ADMIN');
  });

  describe('a thumbs-up', () => {
    it('needs a signed-in person, never an agent, and the CSRF header', async () => {
      const id = await droplet();
      expect((await app.inject({ method: 'PUT', url: `/api/droplets/${id}/like` })).statusCode).toBe(401);
      expect((await app.inject({ method: 'PUT', url: `/api/droplets/${id}/like`, headers: { authorization: `Bearer ${agentKey}` } })).statusCode).toBe(401);
      expect((await app.inject({ method: 'PUT', url: `/api/droplets/${id}/like`, headers: { cookie: grace.cookie } })).statusCode).toBe(403);
      expect(await counts(id)).toEqual({ likeCount: 0, commentCount: 0 });
    });

    it('counts once per person, however many times they send it, and comes back off', async () => {
      const id = await droplet();
      expect((await like(id, grace.cookie)).json()).toEqual({ liked: true, likeCount: 1 });
      expect((await like(id, grace.cookie)).json()).toEqual({ liked: true, likeCount: 1 });
      expect((await like(id, alan.cookie)).json()).toEqual({ liked: true, likeCount: 2 });
      expect((await unlike(id, grace.cookie)).json()).toEqual({ liked: false, likeCount: 1 });
      expect((await unlike(id, grace.cookie)).json()).toEqual({ liked: false, likeCount: 1 });
      expect(await counts(id)).toEqual({ likeCount: 1, commentCount: 0 });
    });

    it('stays exact when many people, and one person many times, like at once', async () => {
      const id = await droplet();
      const people = await Promise.all(Array.from({ length: 12 }, (_, i) => user(`Person ${i}`)));
      const results = await Promise.all([
        ...people.map((p) => like(id, p.cookie)),
        ...Array.from({ length: 8 }, () => like(id, grace.cookie)),
      ]);
      expect(results.every((r) => r.statusCode === 200)).toBe(true);
      expect((await counts(id)).likeCount).toBe(13);
      expect(await prisma.dropletLike.count({ where: { answerId: id } })).toBe(13);
      // And all of them taking it back at once, some twice.
      await Promise.all([...people, ...people].map((p) => unlike(id, p.cookie)));
      expect((await counts(id)).likeCount).toBe(1);
    });

    it('only goes on a droplet that is on the tab, and can always be taken back', async () => {
      const rendering = await droplet(5, { videoStatus: 'RENDERING', songVideoUrl: SONG });
      const textOnly = await droplet(6, {});
      expect((await like(rendering, grace.cookie)).statusCode).toBe(404);
      expect((await like(textOnly, grace.cookie)).statusCode).toBe(404);
      expect((await like('nope', grace.cookie)).statusCode).toBe(404);
      expect(await prisma.dropletLike.count()).toBe(0);

      const id = await droplet();
      await like(id, grace.cookie);
      await prisma.answer.update({ where: { id }, data: { hiddenAt: new Date() } });
      expect((await like(id, alan.cookie)).statusCode).toBe(404);
      expect((await unlike(id, grace.cookie)).json()).toEqual({ liked: false, likeCount: 0 });
      expect((await unlike('nope', grace.cookie)).statusCode).toBe(404);
    });

    it('shows on the list and the droplet, with the viewer’s own, and never renews a cookie there', async () => {
      const id = await droplet(10);
      const other = await droplet(20);
      await like(id, grace.cookie);
      await like(id, alan.cookie);
      const card = (cookie?: string) => get('/api/droplets', cookie).then((r) => r.json().data.find((x: { id: string }) => x.id === id));

      expect(await card()).toMatchObject({ likeCount: 2, commentCount: 0, liked: false });
      expect(await card(grace.cookie)).toMatchObject({ likeCount: 2, liked: true });
      expect(await card(admin.cookie)).toMatchObject({ likeCount: 2, liked: false });
      expect((await get('/api/droplets', grace.cookie)).json().data.find((x: { id: string }) => x.id === other).liked).toBe(false);
      expect((await get(`/api/droplets/${id}`, grace.cookie)).json()).toMatchObject({ likeCount: 2, liked: true });
      expect((await get(`/api/droplets/${id}`)).json()).toMatchObject({ likeCount: 2, liked: false });

      // A made-up cookie is someone signed out, not an error.
      expect((await get(`/api/droplets/${id}`, 'popsmosis_session=x.y.z')).json()).toMatchObject({ liked: false });
      // A session near its end would be renewed by a signed-in route; a public read leaves it alone.
      const ending = `popsmosis_session=${app.jwt.sign({ sub: grace.id }, { expiresIn: '1h' })}`;
      for (const url of ['/api/droplets', `/api/droplets/${id}`, `/api/droplets/${id}/comments`]) {
        const res = await get(url, ending);
        expect(res.statusCode).toBe(200);
        expect(res.headers['set-cookie']).toBeUndefined();
        expect(res.headers['cache-control']).toBe('private');
        expect(String(res.headers.vary)).toMatch(/Cookie/);
      }
      expect((await get(`/api/droplets/${id}`, ending)).json().liked).toBe(true);
    });

    it('holds one person to a pace no real use reaches', async () => {
      const id = await droplet();
      const statuses: number[] = [];
      for (let i = 0; i < 61; i++) statuses.push((await (i % 2 ? unlike(id, grace.cookie) : like(id, grace.cookie))).statusCode);
      expect(statuses.filter((s) => s === 200)).toHaveLength(60);
      expect(statuses.at(-1)).toBe(429);
      expect((await like(id, alan.cookie)).statusCode).toBe(200); // someone else is unaffected
    });
  });

  describe('a comment', () => {
    it('needs a signed-in person and a sane body', async () => {
      const id = await droplet();
      expect((await app.inject({ method: 'POST', url: `/api/droplets/${id}/comments`, payload: { content: 'hi' } })).statusCode).toBe(401);
      expect((await app.inject({ method: 'POST', url: `/api/droplets/${id}/comments`, headers: { authorization: `Bearer ${agentKey}` }, payload: { content: 'hi' } })).statusCode).toBe(401);
      expect((await comment(id, grace.cookie, '   ')).statusCode).toBe(400);
      expect((await comment(id, grace.cookie, 'x'.repeat(1001))).statusCode).toBe(400);
      expect((await write('POST', `/api/droplets/${id}/comments`, grace.cookie, { content: 42 })).statusCode).toBe(400);
      expect((await comment(id, grace.cookie, 'mail me at grace@example.com')).json().error.message).toMatch(/public/);
      expect((await comment(id, grace.cookie, 'ok', 'bad key')).statusCode).toBe(400);
      expect((await comment('nope', grace.cookie, 'hi')).statusCode).toBe(404);
      expect(await counts(id)).toEqual({ likeCount: 0, commentCount: 0 });
    });

    it('is read by anyone, newest first, by cursor, with the count, and says only whose it is', async () => {
      const id = await droplet();
      const posted = await comment(id, grace.cookie, '  The chorus is stuck in my head.  ');
      expect(posted.statusCode).toBe(201);
      expect(posted.json()).toEqual({
        id: expect.any(String), content: 'The chorus is stuck in my head.', createdAt: expect.any(String),
        author: { name: 'Grace H.' }, mine: true,
      });
      await comment(id, alan.cookie, 'Same here');
      await comment(id, grace.cookie, 'Third');

      const all = (await get(`/api/droplets/${id}/comments`)).json();
      expect(all.data.map((c: { content: string }) => c.content)).toEqual(['Third', 'Same here', 'The chorus is stuck in my head.']);
      expect(all.commentCount).toBe(3);
      expect(all.nextCursor).toBeNull();
      expect(all.data.every((c: { mine: boolean }) => c.mine === false)).toBe(true);

      const mine = (await get(`/api/droplets/${id}/comments`, alan.cookie)).json().data.map((c: { mine: boolean }) => c.mine);
      expect(mine).toEqual([false, true, false]);

      const first = (await get(`/api/droplets/${id}/comments?limit=2`)).json();
      expect(first.data).toHaveLength(2);
      const rest = (await get(`/api/droplets/${id}/comments?limit=2&cursor=${first.nextCursor}`)).json();
      expect(rest.data.map((c: { content: string }) => c.content)).toEqual(['The chorus is stuck in my head.']);
      expect(rest.nextCursor).toBeNull();

      expect((await get('/api/droplets')).json().data[0]).toMatchObject({ id, commentCount: 3 });
      expect((await get(`/api/droplets/${id}`)).json()).toMatchObject({ commentCount: 3 });

      // Nothing about who wrote it beyond the first name, whoever reads it.
      for (const cookie of [undefined, grace.cookie, admin.cookie]) {
        const body = (await get(`/api/droplets/${id}/comments`, cookie)).body;
        for (const secret of ['Hopper', 'Brewster', 'Turing', '@example.com', 'avatars.example', 'g-', grace.id, alan.id]) {
          expect(body).not.toContain(secret);
        }
      }
    });

    it('comes with the droplet, first page and all, so the page needs one request', async () => {
      const id = await droplet();
      const at = Date.now() - 60_000;
      await prisma.dropletComment.createMany({ data: Array.from({ length: 25 }, (_, i) => ({ answerId: id, userId: i % 2 ? alan.id : grace.id, content: `c${i}`, createdAt: new Date(at + i * 1000) })) });

      const one = (await get(`/api/droplets/${id}`, alan.cookie)).json();
      expect(one.commentCount).toBe(25);
      expect(one.comments.data).toHaveLength(20);
      expect(one.comments.data[0]).toEqual({ id: expect.any(String), content: 'c24', createdAt: expect.any(String), author: { name: 'Grace H.' }, mine: false });
      expect(one.comments.data[1]).toMatchObject({ content: 'c23', author: { name: 'Alan T.' }, mine: true });
      // The rest follow the embedded page's cursor on the comments route.
      const rest = (await get(`/api/droplets/${id}/comments?cursor=${one.comments.nextCursor}`)).json();
      expect(rest.data.map((c: { content: string }) => c.content)).toEqual(['c4', 'c3', 'c2', 'c1', 'c0']);
      expect(rest.nextCursor).toBeNull();
      // Signed out, the same page says nothing is theirs.
      expect((await get(`/api/droplets/${id}`)).json().comments.data.every((c: { mine: boolean }) => !c.mine)).toBe(true);
    });

    it('needs something a reader can see', async () => {
      const id = await droplet();
      for (const blank of ['\u200B\u200B', '\u3164', '\u2800\u2800', ' \u00AD ', '\u2060\uFEFF', '\u200E\u200F']) {
        expect((await comment(id, grace.cookie, blank)).statusCode).toBe(400);
      }
      for (const ok of ['👍', 'a\u200Bb', '👩‍🔬 nice', 'ok']) expect((await comment(id, grace.cookie, ok)).statusCode).toBe(201);
      expect((await counts(id)).commentCount).toBe(4);
    });

    it('pages through comments made in the same instant without skipping or repeating one', async () => {
      const id = await droplet();
      const at = new Date('2026-10-30T12:00:00.000Z');
      await prisma.dropletComment.createMany({ data: Array.from({ length: 7 }, (_, i) => ({ answerId: id, userId: grace.id, content: `tie ${i}`, createdAt: at })) });
      await prisma.dropletComment.create({ data: { answerId: id, userId: alan.id, content: 'older', createdAt: new Date(at.getTime() - 1) } });
      const seen: string[] = [];
      let cursor: string | null = null;
      do {
        const page = (await get(`/api/droplets/${id}/comments?limit=2${cursor ? `&cursor=${cursor}` : ''}`)).json();
        seen.push(...page.data.map((c: { content: string }) => c.content));
        cursor = page.nextCursor;
      } while (cursor);
      expect(seen).toHaveLength(8);
      expect(new Set(seen).size).toBe(8);
      expect(seen.at(-1)).toBe('older');
    });

    it('is only on a droplet that is on the tab', async () => {
      const id = await droplet();
      await comment(id, grace.cookie, 'Before it was hidden');
      await prisma.answer.update({ where: { id }, data: { hiddenAt: new Date() } });
      expect((await get(`/api/droplets/${id}/comments`)).statusCode).toBe(404);
      expect((await comment(id, alan.cookie, 'After')).statusCode).toBe(404);
      await prisma.answer.update({ where: { id }, data: { hiddenAt: null } });
      expect((await get(`/api/droplets/${id}/comments`)).json().data.map((c: { content: string }) => c.content)).toEqual(['Before it was hidden']);
      const rendering = await droplet(5, { videoStatus: 'RENDERING' });
      expect((await get(`/api/droplets/${rendering}/comments`)).statusCode).toBe(404);
    });

    it('posts once per Idempotency-Key, however often or how concurrently it is sent', async () => {
      const id = await droplet();
      const key = '7c9e6679-7425-40de-944b-e07fc1f90ae7';
      const first = await comment(id, grace.cookie, 'Posted once', key);
      expect(first.statusCode).toBe(201);
      const again = await comment(id, grace.cookie, 'Posted once', key);
      expect(again.statusCode).toBe(200);
      expect(again.json().id).toBe(first.json().id);
      expect((await comment(id, grace.cookie, 'Something else', key)).statusCode).toBe(409);
      const other = await droplet(20);
      expect((await comment(other, grace.cookie, 'Posted once', key)).statusCode).toBe(409);
      // The key is the poster's own: someone else's same key is their own comment.
      expect((await comment(id, alan.cookie, 'Posted once', key)).statusCode).toBe(201);

      const raced = await Promise.all(Array.from({ length: 6 }, () => comment(id, grace.cookie, 'Raced', 'race-key-0001')));
      expect(raced.map((r) => r.statusCode).sort()).toEqual([200, 200, 200, 200, 200, 201]);
      expect(new Set(raced.map((r) => r.json().id)).size).toBe(1);
      expect((await counts(id)).commentCount).toBe(3);
    });

    it('is held to an hourly limit, which never refuses the replay of one that went through', async () => {
      process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR = '2';
      const id = await droplet();
      expect((await comment(id, grace.cookie, 'one', 'limit-key-0001')).statusCode).toBe(201);
      expect((await comment(id, grace.cookie, 'two')).statusCode).toBe(201);
      const over = await comment(id, grace.cookie, 'three');
      expect(over.statusCode).toBe(429);
      expect(over.json().error.retryable).toBe(false);
      expect((await comment(id, grace.cookie, 'one', 'limit-key-0001')).statusCode).toBe(200);
      expect((await comment(id, alan.cookie, 'mine')).statusCode).toBe(201);
    });

    it('is deleted by its author or an admin, and no one else', async () => {
      const id = await droplet();
      const c1 = (await comment(id, grace.cookie, 'mine')).json().id;
      const c2 = (await comment(id, grace.cookie, 'also mine')).json().id;
      const del = (commentId: string, cookie: string, dropletId = id) => write('DELETE', `/api/droplets/${dropletId}/comments/${commentId}`, cookie);

      expect((await del(c1, alan.cookie)).statusCode).toBe(403);
      expect((await app.inject({ method: 'DELETE', url: `/api/droplets/${id}/comments/${c1}` })).statusCode).toBe(401);
      expect((await del(c1, grace.cookie, 'another-droplet')).statusCode).toBe(404);
      expect((await del(c1, grace.cookie)).statusCode).toBe(204);
      expect((await del(c1, grace.cookie)).statusCode).toBe(404);
      expect((await del(c2, admin.cookie)).statusCode).toBe(204);
      expect(await counts(id)).toEqual({ likeCount: 0, commentCount: 0 });

      // Off the tab, the author can still take theirs down.
      const c3 = (await comment(id, grace.cookie, 'before hiding')).json().id;
      await prisma.answer.update({ where: { id }, data: { hiddenAt: new Date() } });
      expect((await del(c3, grace.cookie)).statusCode).toBe(204);
    });
  });

  describe('the edges', () => {
    it('refuses a deleted account whose session is still cached, without a server error or a stray row', async () => {
      const id = await droplet();
      expect((await like(id, alan.cookie)).statusCode).toBe(200); // the auth cache now holds Alan
      await prisma.user.delete({ where: { id: alan.id } });
      const liked = await like(id, alan.cookie);
      const commented = await comment(id, alan.cookie, 'from beyond');
      for (const res of [liked, commented]) expect([401, 404]).toContain(res.statusCode);
      expect(await prisma.dropletLike.count({ where: { answerId: id } })).toBe(0);
      expect(await prisma.dropletComment.count({ where: { answerId: id } })).toBe(0);
      expect(await counts(id)).toEqual({ likeCount: 0, commentCount: 0 });
    });

    it('answers every like racing the droplet’s deletion with a 200 or a 404, never a 500, and leaves nothing behind', async () => {
      const people = await Promise.all(Array.from({ length: 20 }, (_, i) => user(`Racer ${i}`)));
      for (let round = 0; round < 3; round++) {
        const id = await droplet(round);
        const [, ...results] = await Promise.all([
          new Promise((r) => setTimeout(r, 2)).then(() => prisma.answer.delete({ where: { id } })),
          ...people.map((p) => like(id, p.cookie)),
          ...people.slice(0, 5).map((p) => comment(id, p.cookie, 'racing')),
        ]);
        for (const res of results) expect([200, 201, 404]).toContain(res.statusCode);
        expect(await prisma.dropletLike.count({ where: { answerId: id } })).toBe(0);
        expect(await prisma.dropletComment.count({ where: { answerId: id } })).toBe(0);
      }
    });
  });

  describe('the counts', () => {
    it('follow a deleted person’s likes and comments away, and survive the droplet going', async () => {
      const a = await droplet(10);
      const b = await droplet(20);
      for (const id of [a, b]) {
        await like(id, alan.cookie);
        await like(id, grace.cookie);
        await comment(id, alan.cookie, 'from Alan');
        await comment(id, grace.cookie, 'from Grace');
      }
      expect(await counts(a)).toEqual({ likeCount: 2, commentCount: 2 });

      await prisma.user.delete({ where: { id: alan.id } }); // cascades to both tables
      expect(await counts(a)).toEqual({ likeCount: 1, commentCount: 1 });
      expect(await counts(b)).toEqual({ likeCount: 1, commentCount: 1 });

      // Rows written in bulk count once each, per droplet.
      const people = await Promise.all(['P1', 'P2', 'P3'].map((n) => user(n)));
      await prisma.dropletLike.createMany({ data: people.flatMap((p) => [{ answerId: a, userId: p.id }, { answerId: b, userId: p.id }]) });
      expect(await counts(a)).toEqual({ likeCount: 4, commentCount: 1 });
      expect(await counts(b)).toEqual({ likeCount: 4, commentCount: 1 });
      await prisma.dropletLike.deleteMany({ where: { userId: { in: people.map((p) => p.id) } } });
      expect(await counts(b)).toEqual({ likeCount: 1, commentCount: 1 });

      // A droplet deleted with its likes and comments takes them with it, without an error.
      await prisma.answer.delete({ where: { id: a } });
      expect(await prisma.dropletLike.count({ where: { answerId: a } })).toBe(0);
      expect(await prisma.dropletComment.count({ where: { answerId: a } })).toBe(0);
      expect(await counts(b)).toEqual({ likeCount: 1, commentCount: 1 });
    });
  });
});
