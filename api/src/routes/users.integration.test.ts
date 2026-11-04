/**
 * The admin Users list, a page at a time, against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: admins first, then newest first; pages by cursor that neither skip nor repeat a user, across the step from
 * admins to users and across users who joined at the same instant; a search pages the same way; the last page says
 * so; adminCount counts every admin whatever the search; a malformed cursor is the first page, not a 500.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

type Row = { id: string; email: string; role: string };
type Page = { data: Row[]; nextCursor: string | null; adminCount: number };

d('admin users list', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let adminCookie = '';
  let learnerCookie = '';
  // Every user, in the order the list should show them.
  let expected: Row[] = [];

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const list = async (query: string, cookie = adminCookie) => {
    const res = await app.inject({ method: 'GET', url: `/api/users?${query}`, headers: asUser(cookie) });
    return { status: res.statusCode, body: res.json() as Page };
  };
  // Every page of a search, following nextCursor until it runs out.
  const walk = async (params: Record<string, string>) => {
    const pages: Page[] = [];
    let cursor: string | null = null;
    do {
      const { status, body } = await list(new URLSearchParams({ ...params, ...(cursor ? { cursor } : {}) }).toString());
      expect(status).toBe(200);
      pages.push(body);
      cursor = body.nextCursor;
    } while (cursor && pages.length < 50);
    return pages;
  };
  const emails = (rows: Row[]) => rows.map((u) => u.email);

  beforeAll(async () => {
    process.env.JWT_SECRET ||= 'test-secret';
    prisma = (await import('../lib/prisma.js')).default;
    app = await (await import('../index.js')).buildApp();
    await app.ready();
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE users RESTART IDENTITY CASCADE');
    await app.close();
    await prisma.$disconnect();
  });

  beforeEach(async () => {
    await prisma.$executeRawUnsafe('TRUNCATE users RESTART IDENTITY CASCADE');
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    // Three admins and seven users. Several joined at the same instant, so the order among them is by id, and a
    // cursor that kept only createdAt would skip or repeat them.
    const same = new Date('2026-10-01T12:00:00Z');
    const people: { name: string; role: 'ADMIN' | 'USER'; createdAt: Date }[] = [
      { name: 'ada', role: 'ADMIN', createdAt: new Date('2026-09-01T00:00:00Z') },
      { name: 'abe', role: 'ADMIN', createdAt: same },
      { name: 'amy', role: 'ADMIN', createdAt: same },
      { name: 'ben', role: 'USER', createdAt: new Date('2026-10-05T00:00:00Z') },
      { name: 'cal', role: 'USER', createdAt: same },
      { name: 'dot', role: 'USER', createdAt: same },
      { name: 'eve', role: 'USER', createdAt: same },
      { name: 'fay', role: 'USER', createdAt: new Date('2026-08-01T00:00:00Z') },
      { name: 'gus', role: 'USER', createdAt: new Date('2026-11-01T00:00:00Z') },
      { name: 'hal', role: 'USER', createdAt: same },
    ];
    const made = [];
    for (const p of people) {
      made.push(await prisma.user.create({
        data: { email: `${p.name}@pop.test`, googleId: `g-${p.name}`, name: p.name, role: p.role, createdAt: p.createdAt },
      }));
    }
    expected = [...made]
      .sort((a, b) =>
        (a.role === b.role ? 0 : a.role === 'ADMIN' ? -1 : 1)
        || b.createdAt.getTime() - a.createdAt.getTime()
        || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0))
      .map((u) => ({ id: u.id, email: u.email, role: u.role }));
    const cookie = (id: string) => `popsmosis_session=${app.jwt.sign({ sub: id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    adminCookie = cookie(made[0].id);
    learnerCookie = cookie(made[3].id);
  });

  it('lists admins first, then newest first, and pages through everyone once', async () => {
    const first = await list('');
    expect(first.status).toBe(200);
    expect(emails(first.body.data)).toEqual(emails(expected));
    expect(first.body.data.slice(0, 3).every((u) => u.role === 'ADMIN')).toBe(true);
    expect(first.body.nextCursor).toBeNull();
    expect(first.body.adminCount).toBe(3);

    // Pages of 2: the third page steps from the last admin to the newest user; the ties sit across page breaks.
    const pages = await walk({ limit: '2' });
    expect(pages.map((p) => p.data.length)).toEqual([2, 2, 2, 2, 2]);
    expect(emails(pages.flatMap((p) => p.data))).toEqual(emails(expected));
    expect(pages.at(-1)!.nextCursor).toBeNull(); // a full last page still says it is the last

    // Pages of 3 break in other places.
    expect(emails((await walk({ limit: '3' })).flatMap((p) => p.data))).toEqual(emails(expected));
  });

  it('pages a search the same way, and counts every admin whatever the search', async () => {
    // "a" is in ada, abe, amy, cal, fay and hal, in their names and their emails: three admins, then three users.
    const matches = expected.filter((u) => u.email.split('@')[0].includes('a'));
    const pages = await walk({ q: 'A', limit: '2' });
    expect(emails(pages.flatMap((p) => p.data))).toEqual(emails(matches));
    expect(pages.every((p) => p.adminCount === 3)).toBe(true);

    // One admin matches, but there are three: the page must not take her for the last admin.
    const one = await list('q=amy');
    expect(emails(one.body.data)).toEqual(['amy@pop.test']);
    expect(one.body.adminCount).toBe(3);
    expect(one.body.nextCursor).toBeNull();

    expect((await list('q=nobody')).body).toEqual({ data: [], nextCursor: null, adminCount: 3 });
  });

  it('reads a malformed cursor as the first page, and is for admins only', async () => {
    const top = emails(expected.slice(0, 2));
    for (const cursor of ['garbage', Buffer.from('{"role":"ROOT","createdAt":"2026-10-01T12:00:00Z","id":"x"}').toString('base64url'),
      Buffer.from('{"role":"USER","createdAt":"not a date","id":"x"}').toString('base64url')]) {
      const res = await list(`limit=2&cursor=${cursor}`);
      expect(res.status).toBe(200);
      expect(emails(res.body.data)).toEqual(top);
    }
    expect((await list('', learnerCookie)).status).toBe(403);
  });
});
