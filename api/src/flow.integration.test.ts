/**
 * End-to-end flow against a real Postgres (DATABASE_URL). Skipped unless
 * INTEGRATION=1 so `npm test` stays database-free in CI by default.
 *
 * Covers: ask -> agent auth -> answer -> one-answer rule -> human reply ->
 * reply queue -> agent reply -> one-agent-reply-per-human-reply -> delete rules.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('question / answer / reply flow', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('./lib/prisma.js'))['default'];
  let agentKey: string;
  let disabledKey: string;
  let cookieA: string;
  let cookieB: string;
  let adminCookie: string;
  let userA: { id: string };
  let userB: { id: string };
  const tag = `it-${Date.now()}`;

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const asAgent = (key: string) => ({ authorization: `Bearer ${key}` });

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
    process.env.JWT_SECRET ||= 'integration-test-secret';
    const { buildApp } = await import('./index.js');
    const { generateApiKey } = await import('./lib/apikey.js');
    prisma = (await import('./lib/prisma.js')).default;
    app = await buildApp();
    await app.ready();

    // Access defaults to PENDING at the schema level; these three are approved, like real auto-approved sign-ins.
    const approved = { status: 'APPROVED' as const, reviewedAt: new Date() };
    userA = await prisma.user.create({ data: { email: `${tag}-a@example.com`, googleId: `${tag}-a`, name: 'Ada', ...approved } });
    userB = await prisma.user.create({ data: { email: `${tag}-b@example.com`, googleId: `${tag}-b`, name: 'Ben', ...approved } });
    const admin = await prisma.user.create({ data: { email: `${tag}-admin@example.com`, googleId: `${tag}-admin`, name: 'Root', role: 'ADMIN', ...approved } });
    const { SESSION_EXPIRES_IN } = await import('./lib/session.js');
    // Full-lifetime tokens, like a real sign-in issues, so ordinary requests are not renewed mid-test.
    const mk = (id: string) => `zoey_session=${app.jwt.sign({ sub: id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    cookieA = mk(userA.id);
    cookieB = mk(userB.id);
    adminCookie = mk(admin.id);

    const k1 = generateApiKey();
    agentKey = k1.raw;
    await prisma.agent.create({ data: { name: `${tag}-agent`, apiKeyHash: k1.hashed } });
    const k2 = generateApiKey();
    disabledKey = k2.raw;
    await prisma.agent.create({ data: { name: `${tag}-disabled`, apiKeyHash: k2.hashed, enabled: false } });
  });

  afterAll(async () => {
    await prisma.user.deleteMany({ where: { email: { startsWith: tag } } });
    await prisma.agent.deleteMany({ where: { name: { startsWith: tag } } });
    await app.close();
    await prisma.$disconnect();
  });

  let questionId: string;
  let answerId: string;
  let humanReplyId: string;

  it('rejects anonymous and cross-credential access', async () => {
    expect((await app.inject({ method: 'GET', url: '/api/questions' })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/questions/queue' })).statusCode).toBe(401);
    // Agent key on a human endpoint
    const r = await app.inject({ method: 'GET', url: '/api/questions', headers: asAgent(agentKey) });
    expect(r.statusCode).toBe(401);
    // Cookie on an agent endpoint
    expect((await app.inject({ method: 'GET', url: '/api/questions/queue', headers: asUser(cookieA) })).statusCode).toBe(401);
  });

  it('rejects bad, malformed and disabled agent keys', async () => {
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_zoey_' + 'f'.repeat(64)) })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_zoey_nope') })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(disabledKey) })).statusCode).toBe(403);
    const ok = await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(agentKey) });
    expect(ok.statusCode).toBe(200);
    expect(ok.json().name).toBe(`${tag}-agent`);
    expect(ok.json().lastSeenAt).toBeTruthy();
  });

  it('enforces CSRF header on cookie writes', async () => {
    const r = await app.inject({ method: 'POST', url: '/api/questions', headers: { cookie: cookieA }, payload: { content: 'How do I reset my password?' } });
    expect(r.statusCode).toBe(403);
  });

  it('validates questions', async () => {
    const short = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: 'hi' } });
    expect(short.statusCode).toBe(400);
    const pii = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: 'email me at ada@example.com please' } });
    expect(pii.statusCode).toBe(400);
  });

  it('lets a user ask and everyone signed in see it', async () => {
    const r = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: `${tag} How do I reset my password?` } });
    expect(r.statusCode).toBe(201);
    const q = r.json();
    questionId = q.id;
    expect(q.status).toBe('PENDING');
    expect(q.canDelete).toBe(true);
    expect(q.answer).toBeNull();

    const listB = await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(cookieB) });
    expect(listB.statusCode).toBe(200);
    const found = listB.json().data.find((x: { id: string }) => x.id === questionId);
    expect(found).toBeTruthy();
    expect(found.isOwner).toBe(false);
    expect(found.canDelete).toBe(false);

    const mine = await app.inject({ method: 'GET', url: '/api/questions?mine=true', headers: asUser(cookieB) });
    expect(mine.json().data.find((x: { id: string }) => x.id === questionId)).toBeUndefined();

    const status = await app.inject({ method: 'GET', url: '/api/questions/status', headers: asUser(cookieB) });
    expect(status.json().latestAt >= q.createdAt).toBe(true);

    // ?mine=true scopes latestAt like the list: B's own tab must not report A's
    // question as new activity, or the refresh banner promises nothing.
    const mineStatus = await app.inject({ method: 'GET', url: '/api/questions/status?mine=true', headers: asUser(cookieB) });
    expect(mineStatus.statusCode).toBe(200);
    expect(mineStatus.json().latestAt === null || mineStatus.json().latestAt < q.createdAt).toBe(true);
    // The advisor heartbeat is never scoped — a stalled runner is everyone's problem.
    expect(mineStatus.json().agentLastSeenAt).toBe(status.json().agentLastSeenAt);

    // B's own question does move B's scoped watermark.
    const own = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieB), payload: { content: `${tag} Where do I find my invoices?` } });
    expect(own.statusCode).toBe(201);
    const afterOwn = await app.inject({ method: 'GET', url: '/api/questions/status?mine=true', headers: asUser(cookieB) });
    expect(afterOwn.json().latestAt >= own.json().createdAt).toBe(true);

    // ?since is the poller's watermark, and the bound is strict: the row that
    // set the watermark is not news, anything after it is.
    const at = own.json().createdAt;
    const atStatus = await app.inject({ method: 'GET', url: `/api/questions/status?mine=true&since=${encodeURIComponent(at)}`, headers: asUser(cookieB) });
    expect(atStatus.json().latestAt).toBeNull();
    // agentLastSeenAt is never bounded — a stalled runner must stay visible.
    expect(atStatus.json().agentLastSeenAt).toBe(status.json().agentLastSeenAt);

    const justBefore = new Date(new Date(at).getTime() - 1).toISOString();
    const beforeStatus = await app.inject({ method: 'GET', url: `/api/questions/status?mine=true&since=${encodeURIComponent(justBefore)}`, headers: asUser(cookieB) });
    expect(beforeStatus.json().latestAt).toBe(at);

    // The bound applies unscoped too, and A's question stays invisible to B's scoped probe.
    const globalSince = await app.inject({ method: 'GET', url: `/api/questions/status?since=${encodeURIComponent(justBefore)}`, headers: asUser(cookieB) });
    expect(globalSince.json().latestAt >= at).toBe(true);
    const scopedPastA = await app.inject({ method: 'GET', url: `/api/questions/status?mine=true&since=${encodeURIComponent(q.createdAt)}`, headers: asUser(cookieB) });
    expect(scopedPastA.json().latestAt).toBe(at);

    // A watermark the server cannot parse is a client bug, not a silent full scan.
    for (const bad of ['notadate', '', 'yesterday']) {
      const r = await app.inject({ method: 'GET', url: `/api/questions/status?since=${encodeURIComponent(bad)}`, headers: asUser(cookieB) });
      expect(r.statusCode).toBe(400);
      expect(r.json().error.code).toBe('VALIDATION_ERROR');
    }
  });

  it('shows the question in the agent queue and accepts exactly one answer', async () => {
    const queue = await app.inject({ method: 'GET', url: '/api/questions/queue?limit=20', headers: asAgent(agentKey) });
    expect(queue.statusCode).toBe(200);
    const queued = queue.json().data as { id: string; status: string; createdAt: string }[];
    // Only unanswered questions, oldest first.
    expect(queued.every((x) => x.status === 'PENDING')).toBe(true);
    expect([...queued].map((x) => x.createdAt)).toEqual([...queued].map((x) => x.createdAt).sort());
    // The shared dev database may hold more than 20 older pending questions; the new
    // one must be present unless the page is full of older ones.
    const present = queued.some((x) => x.id === questionId);
    if (queued.length < 20) expect(present).toBe(true);
    if (!present) expect(queued.every((x) => x.createdAt <= queued[queued.length - 1].createdAt)).toBe(true);

    const bad = await app.inject({ method: 'POST', url: `/api/questions/${questionId}/answer`, headers: asAgent(agentKey), payload: {} });
    expect(bad.statusCode).toBe(400);

    const ans = await app.inject({ method: 'POST', url: `/api/questions/${questionId}/answer`, headers: asAgent(agentKey), payload: { content: 'Use **Forgot password** on the sign-in page.', model: 'test-model' } });
    expect(ans.statusCode).toBe(201);
    answerId = ans.json().id;

    const again = await app.inject({ method: 'POST', url: `/api/questions/${questionId}/answer`, headers: asAgent(agentKey), payload: { content: 'second take' } });
    expect(again.statusCode).toBe(409);

    const queue2 = await app.inject({ method: 'GET', url: '/api/questions/queue?limit=20', headers: asAgent(agentKey) });
    expect(queue2.json().data.some((x: { id: string }) => x.id === questionId)).toBe(false);

    const q = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(q.json().status).toBe('ANSWERED');
    expect(q.json().answer.content).toContain('Forgot password');
    expect(q.json().answer.awaitingAgent).toBe(false);
    expect(q.json().canDelete).toBe(false);
  });

  it('refuses to delete an answered question, allows deleting a pending one', async () => {
    const r = await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(r.statusCode).toBe(400);
    const other = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieB), payload: { content: `${tag} temporary question to delete` } });
    const notOwner = await app.inject({ method: 'DELETE', url: `/api/questions/${other.json().id}`, headers: asUser(cookieA) });
    expect(notOwner.statusCode).toBe(403);
    const owner = await app.inject({ method: 'DELETE', url: `/api/questions/${other.json().id}`, headers: asUser(cookieB) });
    expect(owner.statusCode).toBe(204);
  });

  it('threads: human replies, agent owes a response, agent replies once', async () => {
    const empty = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) });
    expect(empty.json().data).toEqual([]);

    const hr = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieB), payload: { content: 'That link is greyed out for me.' } });
    expect(hr.statusCode).toBe(201);
    humanReplyId = hr.json().id;
    expect(hr.json().authorType).toBe('HUMAN');
    expect(hr.json().canDelete).toBe(true);

    const q = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(q.json().answer.awaitingAgent).toBe(true);
    expect(q.json().answer.replies).toHaveLength(1);

    const queue = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) });
    expect(queue.json().data).toHaveLength(1);
    const thread = queue.json().data[0];
    expect(thread.answer.id).toBe(answerId);
    expect(thread.replyTo.id).toBe(humanReplyId);
    expect(thread.question.content).toContain('reset my password');

    const missingReplyTo = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(agentKey), payload: { content: 'x' } });
    expect(missingReplyTo.statusCode).toBe(400);

    const ar = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(agentKey), payload: { content: 'Try an incognito window; the button is disabled while a session is active.', replyToId: humanReplyId } });
    expect(ar.statusCode).toBe(201);
    expect(ar.json().authorType).toBe('AGENT');

    const dup = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(agentKey), payload: { content: 'again', replyToId: humanReplyId } });
    expect(dup.statusCode).toBe(409);

    const queue2 = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) });
    expect(queue2.json().data).toEqual([]);

    const q2 = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(q2.json().answer.awaitingAgent).toBe(false);
    expect(q2.json().answer.replies.map((r: { authorType: string }) => r.authorType)).toEqual(['HUMAN', 'AGENT']);
  });

  it('threads keep going: human -> agent -> human -> agent, and the agent always sees the whole thread', async () => {
    // Round 2: the human replies again under the same answer.
    const h2 = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieB), payload: { content: 'Incognito worked. Is there a permanent fix?' } });
    expect(h2.statusCode).toBe(201);

    const queue = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) });
    expect(queue.json().data).toHaveLength(1);
    const thread = queue.json().data[0];
    // The agent gets the original question, its own answer, every reply in order, and the one to respond to.
    expect(thread.question.content).toContain('reset my password');
    expect(thread.question.user.name).toBe('Ada');
    expect(thread.answer.content).toContain('Forgot password');
    expect(thread.thread.map((r: { authorType: string }) => r.authorType)).toEqual(['HUMAN', 'AGENT', 'HUMAN']);
    expect(thread.thread[2].content).toContain('permanent fix');
    expect(thread.replyTo.id).toBe(h2.json().id);
    // Earlier human reply is not offered again: it already has an agent reply.
    expect(thread.replyTo.id).not.toBe(humanReplyId);

    const a2 = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(agentKey), payload: { content: 'Yes: clear the site cookies once; the stale session cookie is what greys the button out.', replyToId: h2.json().id } });
    expect(a2.statusCode).toBe(201);
    expect((await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) })).json().data).toEqual([]);

    // Round 3 from a different human on the same answer also lands in the queue.
    const h3 = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieA), payload: { content: 'Same problem here, that fixed it too.' } });
    expect(h3.statusCode).toBe(201);
    const q3 = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) });
    expect(q3.json().data[0].replyTo.id).toBe(h3.json().id);
    expect(q3.json().data[0].thread).toHaveLength(5);
    const a3 = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(agentKey), payload: { content: 'Glad it helped.', replyToId: h3.json().id } });
    expect(a3.statusCode).toBe(201);

    const q = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(q.json().answer.replies.map((r: { authorType: string }) => r.authorType)).toEqual(['HUMAN', 'AGENT', 'HUMAN', 'AGENT', 'HUMAN', 'AGENT']);
    expect(q.json().answer.awaitingAgent).toBe(false);
  });

  it('only the author can delete a reply, and the agent reply cascades', async () => {
    const notAuthor = await app.inject({ method: 'DELETE', url: `/api/replies/${humanReplyId}`, headers: asUser(cookieA) });
    expect(notAuthor.statusCode).toBe(403);
    const author = await app.inject({ method: 'DELETE', url: `/api/replies/${humanReplyId}`, headers: asUser(cookieB) });
    expect(author.statusCode).toBe(204);
    const q = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    // The deleted human reply and the agent's response to it are gone; later rounds remain.
    expect(q.json().answer.replies).toHaveLength(4);
    expect(q.json().answer.replies.some((r: { id: string }) => r.id === humanReplyId)).toBe(false);
  });

  it('keeps sessions alive by sliding renewal, and logout ends them', async () => {
    const { SESSION_TTL_S, RENEW_THRESHOLD_S } = await import('./lib/session.js');
    const cookieOf = (res: { headers: Record<string, unknown> }) => {
      const raw = res.headers['set-cookie'];
      const list = Array.isArray(raw) ? raw : raw ? [String(raw)] : [];
      return list.find((c) => c.startsWith('zoey_session=')) ?? null;
    };

    // A fresh token is left alone: no Set-Cookie on an ordinary request.
    const fresh = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieA) });
    expect(fresh.statusCode).toBe(200);
    expect(cookieOf(fresh)).toBeNull();

    // A token past the halfway point is re-issued for the full lifetime.
    // fast-jwt reads numeric expiresIn as milliseconds; use an explicit seconds string.
    const aging = app.jwt.sign({ sub: userA.id }, { expiresIn: `${Math.floor(RENEW_THRESHOLD_S / 2)}s` });
    const renewed = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(`zoey_session=${aging}`) });
    expect(renewed.statusCode).toBe(200);
    const setCookie = cookieOf(renewed)!;
    expect(setCookie).toBeTruthy();
    expect(setCookie).toContain(`Max-Age=${SESSION_TTL_S}`);
    expect(setCookie).toContain('HttpOnly');
    expect(setCookie).toContain('Path=/api');
    const newToken = setCookie.split(';')[0].replace('zoey_session=', '');
    expect(newToken).not.toBe(aging);
    const decoded = app.jwt.decode<{ exp: number }>(newToken)!;
    expect(decoded.exp - Math.floor(Date.now() / 1000)).toBeGreaterThan(SESSION_TTL_S - 60);
    // The renewed token works and, being fresh, is not renewed again.
    const again = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(`zoey_session=${newToken}`) });
    expect(again.statusCode).toBe(200);
    expect(cookieOf(again)).toBeNull();
    // A renewed response is never publicly cacheable.
    expect(String(renewed.headers['cache-control'] ?? '')).not.toMatch(/public/);

    // An expired token is rejected (and cannot be renewed).
    const { createHmac } = await import('crypto');
    const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url');
    const hdr = b64({ alg: 'HS256', typ: 'JWT' });
    const body = b64({ sub: userA.id, iat: Math.floor(Date.now() / 1000) - 100, exp: Math.floor(Date.now() / 1000) - 10 });
    const expired = `${hdr}.${body}.${createHmac('sha256', process.env.JWT_SECRET!).update(`${hdr}.${body}`).digest('base64url')}`;
    const dead = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(`zoey_session=${expired}`) });
    expect(dead.statusCode).toBe(401);
    expect(dead.json().error.message).toBe('Invalid token');

    // Logout clears the cookie on the same path it was set on.
    const out = await app.inject({ method: 'POST', url: '/api/auth/logout', headers: asUser(cookieA) });
    expect(out.statusCode).toBe(200);
    const cleared = cookieOf(out)!;
    expect(cleared).toMatch(/^zoey_session=;/);
    expect(cleared).toContain('Path=/api');
    expect(cleared).toMatch(/Expires=Thu, 01 Jan 1970|Max-Age=0/);
    // Without the cookie the next request is anonymous.
    expect((await app.inject({ method: 'GET', url: '/api/auth/me' })).statusCode).toBe(401);
  });

  it('pages with a keyset cursor: no gaps, no duplicates, stable under identical timestamps', async () => {
    // Ada asks several questions; two share a createdAt to exercise the id tiebreaker.
    const same = new Date('2026-01-01T00:00:00.000Z');
    const made: string[] = [];
    for (let i = 0; i < 5; i++) {
      const q = await prisma.question.create({ data: { userId: userA.id, content: `${tag} page test ${i} ${'x'.repeat(10)}`, createdAt: i < 2 ? same : new Date(same.getTime() + i * 1000) } });
      made.push(q.id);
    }
    const seen: string[] = [];
    let cursor: string | null = null;
    let pages = 0;
    do {
      const url = `/api/questions?mine=true&limit=2${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;
      const res = await app.inject({ method: 'GET', url, headers: asUser(cookieA) });
      expect(res.statusCode).toBe(200);
      const body = res.json() as { data: { id: string; createdAt: string }[]; nextCursor: string | null };
      expect(body.data.length).toBeLessThanOrEqual(2);
      seen.push(...body.data.map((q) => q.id));
      cursor = body.nextCursor;
      pages++;
    } while (cursor && pages < 20);
    // Every one of Ada's questions appears exactly once, newest first.
    const mine = seen.filter((id) => made.includes(id));
    expect(new Set(seen).size).toBe(seen.length);
    expect(mine.length).toBe(made.length);
    // A garbage cursor is ignored rather than a 500.
    expect((await app.inject({ method: 'GET', url: '/api/questions?cursor=garbage', headers: asUser(cookieA) })).statusCode).toBe(200);
    await prisma.question.deleteMany({ where: { id: { in: made } } });
  });

  it('admins can promote and demote users, never themselves or the last admin', async () => {
    // Non-admin cannot list or change roles.
    expect((await app.inject({ method: 'GET', url: '/api/users', headers: asUser(cookieB) })).statusCode).toBe(403);
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${userA.id}`, headers: asUser(cookieB), payload: { role: 'ADMIN' } })).statusCode).toBe(403);

    // Admin lists users; search narrows.
    const list = await app.inject({ method: 'GET', url: `/api/users?q=${tag}`, headers: asUser(adminCookie) });
    expect(list.statusCode).toBe(200);
    expect(list.json().data.map((u: { email: string }) => u.email).sort()).toEqual([`${tag}-a@example.com`, `${tag}-admin@example.com`, `${tag}-b@example.com`]);

    // Promote Ada; she can use admin endpoints right away (cache invalidated).
    const promote = await app.inject({ method: 'PATCH', url: `/api/users/${userA.id}`, headers: asUser(adminCookie), payload: { role: 'ADMIN' } });
    expect(promote.statusCode).toBe(200);
    expect(promote.json().role).toBe('ADMIN');
    expect((await app.inject({ method: 'GET', url: '/api/agents', headers: asUser(cookieA) })).statusCode).toBe(200);

    // Ada cannot demote herself, but the other admin can demote her.
    const self = await app.inject({ method: 'PATCH', url: `/api/users/${userA.id}`, headers: asUser(cookieA), payload: { role: 'USER' } });
    expect(self.statusCode).toBe(400);
    const demote = await app.inject({ method: 'PATCH', url: `/api/users/${userA.id}`, headers: asUser(adminCookie), payload: { role: 'USER' } });
    expect(demote.json().role).toBe('USER');
    expect((await app.inject({ method: 'GET', url: '/api/agents', headers: asUser(cookieA) })).statusCode).toBe(403);

    // Validation and not-found.
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${userA.id}`, headers: asUser(adminCookie), payload: { role: 'ROOT' } })).statusCode).toBe(400);
    expect((await app.inject({ method: 'PATCH', url: '/api/users/nope', headers: asUser(adminCookie), payload: { role: 'ADMIN' } })).statusCode).toBe(404);
  });

  it('pending users can only see that they are pending until an admin approves them', async () => {
    // Cal signed in with a Google account outside the auto-approve list: the row exists, status PENDING.
    const cal = await prisma.user.create({ data: { email: `${tag}-cal@example.com`, googleId: `${tag}-cal`, name: 'Cal' } });
    const cookieCal = `zoey_session=${app.jwt.sign({ sub: cal.id }, { expiresIn: '30d' })}`;
    expect(cal.status).toBe('PENDING');

    // /me works (that is how the waiting page polls); everything else is 403 PENDING_APPROVAL.
    const me = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieCal) });
    expect(me.statusCode).toBe(200);
    expect(me.json().status).toBe('PENDING');
    for (const url of ['/api/questions', '/api/questions/status', '/api/users', '/api/agents']) {
      const r = await app.inject({ method: 'GET', url, headers: asUser(cookieCal) });
      expect(r.statusCode).toBe(403);
      expect(r.json().error.code).toBe('PENDING_APPROVAL');
    }
    const ask = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieCal), payload: { content: 'Can I post while pending? I should not be able to.' } });
    expect(ask.statusCode).toBe(403);
    expect(ask.json().error.code).toBe('PENDING_APPROVAL');

    // The admin sees the queue (oldest first) and the count.
    const queue = await app.inject({ method: 'GET', url: `/api/users?status=PENDING&q=${tag}`, headers: asUser(adminCookie) });
    expect(queue.json().data.map((u: { email: string }) => u.email)).toEqual([`${tag}-cal@example.com`]);
    expect((await app.inject({ method: 'GET', url: '/api/users/pending-count', headers: asUser(adminCookie) })).json().count).toBeGreaterThanOrEqual(1);
    expect((await app.inject({ method: 'GET', url: '/api/users?status=nope', headers: asUser(adminCookie) })).statusCode).toBe(400);

    // Deny: still no access, with a different code so the UI can say so.
    const deny = await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: { status: 'DENIED' } });
    expect(deny.statusCode).toBe(200);
    expect(deny.json().status).toBe('DENIED');
    expect(deny.json().reviewedAt).not.toBeNull();
    const denied = await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(cookieCal) });
    expect(denied.statusCode).toBe(403);
    expect(denied.json().error.code).toBe('ACCESS_DENIED');

    // Approve: works on the very next request (cache invalidated), and records who did it.
    const approve = await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: { status: 'APPROVED' } });
    expect(approve.json().status).toBe('APPROVED');
    expect((await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(cookieCal) })).statusCode).toBe(200);
    const adminId = app.jwt.decode<{ sub: string }>(adminCookie.replace('zoey_session=', ''))!.sub;
    expect((await prisma.user.findUnique({ where: { id: cal.id } }))!.reviewedById).toBe(adminId);

    // Validation: bad status, no fields, own access, denying an admin.
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: { status: 'PENDING' } })).statusCode).toBe(400);
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: {} })).statusCode).toBe(400);
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${adminId}`, headers: asUser(adminCookie), payload: { status: 'DENIED' } })).statusCode).toBe(400);
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: { role: 'ADMIN' } })).statusCode).toBe(200);
    const denyAdmin = await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: { status: 'DENIED' } });
    expect(denyAdmin.statusCode).toBe(400);
    expect(denyAdmin.json().error.message).toMatch(/admin role/);
    // Demote and deny in one call is allowed; a denied user's session is dead for everything but /me.
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: { role: 'USER', status: 'DENIED' } })).json()).toMatchObject({ role: 'USER', status: 'DENIED' });
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieCal) })).json().status).toBe('DENIED');

    // Promoting a pending user approves them too.
    await prisma.user.update({ where: { id: cal.id }, data: { status: 'PENDING', role: 'USER' } });
    const promote = await app.inject({ method: 'PATCH', url: `/api/users/${cal.id}`, headers: asUser(adminCookie), payload: { role: 'ADMIN' } });
    expect(promote.json()).toMatchObject({ role: 'ADMIN', status: 'APPROVED' });
    await prisma.user.delete({ where: { id: cal.id } });
  });

  it('an admin left pending gets in: the row is reconciled on the next request, not only at sign-in', async () => {
    // `UPDATE users SET role = 'ADMIN'` in a SQL console grants the role and
    // leaves status PENDING. Nothing inside the product can repair that from
    // there: every route except /me answers 403 PENDING_APPROVAL, /admin/users
    // included, so the new admin is stuck on the waiting page they are supposed
    // to be able to clear. The session resolver has to notice.
    const { invalidateUserCache } = await import('./middleware/auth.js');
    const dee = await prisma.user.create({ data: { email: `${tag}-dee@example.com`, googleId: `${tag}-dee`, name: 'Dee', role: 'ADMIN' } });
    expect(dee.status).toBe('PENDING');
    const cookieDee = `zoey_session=${app.jwt.sign({ sub: dee.id }, { expiresIn: '30d' })}`;

    // The waiting page's own poll is what unsticks it.
    const me = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieDee) });
    expect(me.statusCode).toBe(200);
    expect(me.json()).toMatchObject({ role: 'ADMIN', status: 'APPROVED' });
    // Written back, not just answered: the admin list and the next instance agree.
    const stored = (await prisma.user.findUnique({ where: { id: dee.id } }))!;
    expect(stored.status).toBe('APPROVED');
    expect(stored.reviewedAt).not.toBeNull();
    expect(stored.reviewedById).toBeNull(); // automatic, so no admin is credited with it

    // Same again on a row an admin had denied: the approval must not read as
    // that admin's doing, with a reviewedAt saying it happened just now.
    const adminId = app.jwt.decode<{ sub: string }>(adminCookie.replace('zoey_session=', ''))!.sub;
    await prisma.user.update({ where: { id: dee.id }, data: { status: 'DENIED', reviewedAt: new Date(), reviewedById: adminId } });
    invalidateUserCache(dee.id);
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieDee) })).json().status).toBe('APPROVED');
    expect((await prisma.user.findUnique({ where: { id: dee.id } }))!.reviewedById).toBeNull();
    expect((await app.inject({ method: 'GET', url: '/api/users/pending-count', headers: asUser(cookieDee) })).statusCode).toBe(200);
    await prisma.user.delete({ where: { id: dee.id } });
  });

  it('ADMIN_EMAILS reaches a session that is already open, without signing in again', async () => {
    const { invalidateUserCache } = await import('./middleware/auth.js');
    const eve = await prisma.user.create({ data: { email: `${tag}-eve@example.com`, googleId: `${tag}-eve`, name: 'Eve' } });
    const cookieEve = `zoey_session=${app.jwt.sign({ sub: eve.id }, { expiresIn: '30d' })}`;
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieEve) })).json().status).toBe('PENDING');

    // The documented way out of "nobody can approve anyone": add the address and
    // redeploy. A redeploy starts on an empty auth cache, which is what the
    // invalidate stands in for here.
    const before = process.env.ADMIN_EMAILS;
    process.env.ADMIN_EMAILS = `${tag}-eve@example.com`;
    invalidateUserCache(eve.id);
    try {
      const me = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieEve) });
      expect(me.json()).toMatchObject({ role: 'ADMIN', status: 'APPROVED' });
      expect((await app.inject({ method: 'GET', url: '/api/users/pending-count', headers: asUser(cookieEve) })).statusCode).toBe(200);
    } finally {
      if (before === undefined) delete process.env.ADMIN_EMAILS; else process.env.ADMIN_EMAILS = before;
      invalidateUserCache(eve.id);
    }
    await prisma.user.delete({ where: { id: eve.id } });
  });

  it('refuses a demotion or denial that ADMIN_EMAILS would undo, instead of reporting a success that reverts', async () => {
    const { invalidateUserCache } = await import('./middleware/auth.js');
    const gus = await prisma.user.create({
      data: { email: `${tag}-gus@example.com`, googleId: `${tag}-gus`, name: 'Gus', role: 'ADMIN', status: 'APPROVED', reviewedAt: new Date() },
    });
    const before = process.env.ADMIN_EMAILS;
    process.env.ADMIN_EMAILS = `${tag}-gus@example.com`;
    try {
      for (const payload of [{ role: 'USER' }, { status: 'DENIED' }, { role: 'USER', status: 'DENIED' }]) {
        const r = await app.inject({ method: 'PATCH', url: `/api/users/${gus.id}`, headers: asUser(adminCookie), payload });
        expect(r.statusCode).toBe(400);
        expect(r.json().error.message).toMatch(/ADMIN_EMAILS/);
      }
      // Still an admin, so the refusal was the whole story.
      expect((await prisma.user.findUnique({ where: { id: gus.id } }))!.role).toBe('ADMIN');
      // Approving is not a reversal — the config wants that too, so it is allowed.
      await prisma.user.update({ where: { id: gus.id }, data: { status: 'PENDING', role: 'USER' } });
      expect((await app.inject({ method: 'PATCH', url: `/api/users/${gus.id}`, headers: asUser(adminCookie), payload: { status: 'APPROVED' } })).statusCode).toBe(200);
    } finally {
      if (before === undefined) delete process.env.ADMIN_EMAILS; else process.env.ADMIN_EMAILS = before;
      invalidateUserCache(gus.id);
    }
    await prisma.user.delete({ where: { id: gus.id } });
  });

  it('leaves a denied user denied: the lists let people skip the queue, they do not reverse a decision', async () => {
    const { invalidateUserCache } = await import('./middleware/auth.js');
    const fay = await prisma.user.create({
      data: { email: `${tag}-fay@example.com`, googleId: `${tag}-fay`, name: 'Fay', status: 'DENIED', reviewedAt: new Date() },
    });
    const cookieFay = `zoey_session=${app.jwt.sign({ sub: fay.id }, { expiresIn: '30d' })}`;
    const before = process.env.ALLOWED_EMAIL_DOMAINS;
    process.env.ALLOWED_EMAIL_DOMAINS = 'example.com';
    invalidateUserCache(fay.id);
    try {
      expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieFay) })).json().status).toBe('DENIED');
      const blocked = await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(cookieFay) });
      expect(blocked.json().error.code).toBe('ACCESS_DENIED');
    } finally {
      if (before === undefined) delete process.env.ALLOWED_EMAIL_DOMAINS; else process.env.ALLOWED_EMAIL_DOMAINS = before;
      invalidateUserCache(fay.id);
    }
    await prisma.user.delete({ where: { id: fay.id } });
  });

  it('refuses to demote the last admin', async () => {
    // Make the seeded admin the only admin in the test's world by trying to demote via a second admin:
    // promote Ben, then have Ben demote the original admin (allowed: two admins), then Ben demote himself (refused: last admin).
    const adminId = app.jwt.decode<{ sub: string }>(adminCookie.replace('zoey_session=', ''))!.sub;
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${userB.id}`, headers: asUser(adminCookie), payload: { role: 'ADMIN' } })).statusCode).toBe(200);
    // Other admins may exist in the shared DB (seed data); count them to know what to expect.
    const admins = await prisma.user.count({ where: { role: 'ADMIN' } });
    const r = await app.inject({ method: 'PATCH', url: `/api/users/${adminId}`, headers: asUser(cookieB), payload: { role: 'USER' } });
    expect(r.statusCode).toBe(admins >= 2 ? 200 : 400);
    if (r.statusCode === 200) {
      // restore for the remaining tests
      expect((await app.inject({ method: 'PATCH', url: `/api/users/${adminId}`, headers: asUser(cookieB), payload: { role: 'ADMIN' } })).statusCode).toBe(200);
    }
    // Self-demotion is always refused.
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${userB.id}`, headers: asUser(cookieB), payload: { role: 'USER' } })).statusCode).toBe(400);
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${userB.id}`, headers: asUser(adminCookie), payload: { role: 'USER' } })).statusCode).toBe(200);
  });

  it('with PROXY_SECRET set, only the web proxy and agents can reach the API', async () => {
    const { buildApp } = await import('./index.js');
    const prev = process.env.PROXY_SECRET;
    process.env.PROXY_SECRET = 'e2e-proxy-secret';
    const gated = await buildApp();
    await gated.ready();
    try {
      // Browser-style request without the proxy header: refused before auth.
      const direct = await gated.inject({ method: 'GET', url: '/api/questions', headers: asUser(cookieA) });
      expect(direct.statusCode).toBe(403);
      expect(direct.json().error.message).toBe('Direct API access not allowed');
      // Same request through the proxy (header attached by Next.js middleware): normal auth.
      const viaProxy = await gated.inject({ method: 'GET', url: '/api/questions', headers: { ...asUser(cookieA), 'x-proxy-token': 'e2e-proxy-secret' } });
      expect(viaProxy.statusCode).toBe(200);
      // Wrong secret is the same as none.
      expect((await gated.inject({ method: 'GET', url: '/api/questions', headers: { ...asUser(cookieA), 'x-proxy-token': 'nope' } })).statusCode).toBe(403);
      // Agents bypass the gate with a well-formed key and are then authenticated normally.
      expect((await gated.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(agentKey) })).statusCode).toBe(200);
      expect((await gated.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_zoey_' + 'f'.repeat(64)) })).statusCode).toBe(401);
      // A malformed key does not count as an agent and hits the gate.
      expect((await gated.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_zoey_short') })).statusCode).toBe(403);
      // Health stays open for Render's health check.
      expect((await gated.inject({ method: 'GET', url: '/api/health' })).statusCode).toBe(200);
    } finally {
      await gated.close();
      if (prev === undefined) delete process.env.PROXY_SECRET; else process.env.PROXY_SECRET = prev;
    }
  });

  it('admin agent management and skill download', async () => {
    const denied = await app.inject({ method: 'GET', url: '/api/agents', headers: asUser(cookieA) });
    expect(denied.statusCode).toBe(403);
    const created = await app.inject({ method: 'POST', url: '/api/agents', headers: asUser(adminCookie), payload: { name: `${tag}-admin-made` } });
    expect(created.statusCode).toBe(201);
    expect(created.json().apiKey).toMatch(/^sk_zoey_[0-9a-f]{64}$/);
    const me = await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(created.json().apiKey) });
    expect(me.statusCode).toBe(200);

    const off = await app.inject({ method: 'PATCH', url: `/api/agents/${created.json().id}`, headers: asUser(adminCookie), payload: { enabled: false } });
    expect(off.json().enabled).toBe(false);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(created.json().apiKey) })).statusCode).toBe(403);

    // Revoke: no key works; the agent still exists; a fresh mint works again.
    expect((await app.inject({ method: 'PATCH', url: `/api/agents/${created.json().id}`, headers: asUser(adminCookie), payload: { enabled: true } })).statusCode).toBe(200);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(created.json().apiKey) })).statusCode).toBe(200);
    const revoked = await app.inject({ method: 'POST', url: `/api/agents/${created.json().id}/revoke-key`, headers: asUser(adminCookie) });
    expect(revoked.statusCode).toBe(200);
    expect(revoked.json().hasKey).toBe(false);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(created.json().apiKey) })).statusCode).toBe(401);
    const listed = await app.inject({ method: 'GET', url: '/api/agents', headers: asUser(adminCookie) });
    expect(listed.json().data.some((x: { id: string; hasKey: boolean }) => x.id === created.json().id && x.hasKey === false)).toBe(true);
    const minted = await app.inject({ method: 'POST', url: `/api/agents/${created.json().id}/rotate-key`, headers: asUser(adminCookie) });
    expect(minted.statusCode).toBe(200);
    expect(minted.json().hasKey).toBe(true);
    expect(minted.json().apiKey).toMatch(/^sk_zoey_[0-9a-f]{64}$/);
    expect(minted.json().apiKey).not.toBe(created.json().apiKey);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(minted.json().apiKey) })).statusCode).toBe(200);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(created.json().apiKey) })).statusCode).toBe(401);

    const skill = await app.inject({ method: 'GET', url: `/api/agents/${created.json().id}/skill`, headers: asUser(adminCookie) });
    expect(skill.statusCode).toBe(200);
    expect(skill.headers['content-type']).toContain('text/markdown');
    expect(skill.body).toContain('http://localhost:3001/api/agents/me');
    expect(skill.body).not.toContain('api.zoey.example');
  });

  it('keeps credentials issued before the rename working', async () => {
    const { hashApiKey, LEGACY_API_KEY_PREFIX } = await import('./lib/apikey.js');
    const { SESSION_EXPIRES_IN, SESSION_COOKIE, LEGACY_SESSION_COOKIE } = await import('./lib/session.js');
    const setCookies = (res: { headers: Record<string, unknown> }) => {
      const raw = res.headers['set-cookie'];
      return (Array.isArray(raw) ? raw : raw ? [raw] : []).map(String);
    };
    const named = (res: { headers: Record<string, unknown> }, name: string) =>
      setCookies(res).filter((c) => c.startsWith(`${name}=`));

    // --- A key minted under the old prefix still authenticates. ---
    // Only the raw key's sha256 is stored and that hash covers the prefix, so
    // the key in the database is untouched by the rename; the format check was
    // the only thing that had to learn the old name.
    const legacyRaw = `${LEGACY_API_KEY_PREFIX}${'abcdef01'.repeat(8)}`;
    await prisma.agent.create({ data: { name: `${tag}-legacy-agent`, apiKeyHash: hashApiKey(legacyRaw) } });
    const legacyAgent = await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(legacyRaw) });
    expect(legacyAgent.statusCode).toBe(200);
    expect(legacyAgent.json().name).toBe(`${tag}-legacy-agent`);
    // Accepting the prefix is not accepting the key: an unissued one still fails.
    const unissued = `${LEGACY_API_KEY_PREFIX}${'f'.repeat(64)}`;
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(unissued) })).statusCode).toBe(401);

    // --- A session cookie set under the old name still signs you in, once. ---
    const legacyCookie = `${LEGACY_SESSION_COOKIE}=${app.jwt.sign({ sub: userB.id }, { expiresIn: SESSION_EXPIRES_IN })}`;
    const viaLegacy = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(legacyCookie) });
    expect(viaLegacy.statusCode).toBe(200);
    expect(viaLegacy.json().id).toBe(userB.id);
    // That response moves the session onto the current name and drops the old
    // one, so it is the last request that ever carries it.
    const [moved] = named(viaLegacy, SESSION_COOKIE);
    expect(moved).toBeTruthy();
    expect(moved).toContain('HttpOnly');
    expect(moved).toContain('Path=/api');
    expect(named(viaLegacy, LEGACY_SESSION_COOKIE)[0]).toMatch(new RegExp(`^${LEGACY_SESSION_COOKIE}=;`));
    // The token it hands back is an ordinary session under the current name.
    const movedToken = moved.split(';')[0].replace(`${SESSION_COOKIE}=`, '');
    const after = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(`${SESSION_COOKIE}=${movedToken}`) });
    expect(after.statusCode).toBe(200);
    expect(after.json().id).toBe(userB.id);
    expect(named(after, SESSION_COOKIE)).toHaveLength(0); // fresh, so not renewed again

    // --- The old name is still a session cookie, so CSRF still covers it. ---
    const noXhr = await app.inject({ method: 'POST', url: '/api/questions', headers: { cookie: legacyCookie }, payload: { content: `${tag} csrf probe, long enough to pass validation` } });
    expect(noXhr.statusCode).toBe(403);
    expect(noXhr.json().error.message).toBe('Missing CSRF header');

    // --- Logout ends a session started under either name, unambiguously: the
    // migration cookie must not be left sitting beside the clear. ---
    const out = await app.inject({ method: 'POST', url: '/api/auth/logout', headers: asUser(legacyCookie) });
    expect(out.statusCode).toBe(200);
    for (const name of [SESSION_COOKIE, LEGACY_SESSION_COOKIE]) {
      const cleared = named(out, name);
      expect(cleared).toHaveLength(1);
      expect(cleared[0]).toMatch(new RegExp(`^${name}=;`));
      expect(cleared[0]).toContain('Path=/api');
    }
  });
});
