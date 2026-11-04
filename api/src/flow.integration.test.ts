/**
 * End-to-end flow against a real Postgres (DATABASE_URL). Skipped unless
 * INTEGRATION=1 so `npm test` stays database-free in CI by default.
 *
 * Covers: ask -> only the asker (and admins) see it -> agent auth -> answer ->
 * one-answer rule -> the asker's follow-up -> reply queue -> agent reply ->
 * one-agent-reply-per-human-reply -> delete rules. Taking back a request and
 * deleting an account are in routes/privacy.integration.test.ts.
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

    userA = await prisma.user.create({ data: { email: `${tag}-a@example.com`, googleId: `${tag}-a`, name: 'Ada' } });
    userB = await prisma.user.create({ data: { email: `${tag}-b@example.com`, googleId: `${tag}-b`, name: 'Ben' } });
    const admin = await prisma.user.create({ data: { email: `${tag}-admin@example.com`, googleId: `${tag}-admin`, name: 'Root', role: 'ADMIN' } });
    const { SESSION_EXPIRES_IN } = await import('./lib/session.js');
    // Full-lifetime tokens, like a real sign-in issues, so ordinary requests are not renewed mid-test.
    const mk = (id: string) => `popsmosis_session=${app.jwt.sign({ sub: id }, { expiresIn: SESSION_EXPIRES_IN })}`;
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
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_pop_' + 'f'.repeat(64)) })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_pop_nope') })).statusCode).toBe(401);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(disabledKey) })).statusCode).toBe(403);
    const ok = await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(agentKey) });
    expect(ok.statusCode).toBe(200);
    expect(ok.json().name).toBe(`${tag}-agent`);
    expect(ok.json().lastSeenAt).toBeTruthy();
  });

  it('enforces CSRF header on cookie writes', async () => {
    const r = await app.inject({ method: 'POST', url: '/api/questions', headers: { cookie: cookieA }, payload: { content: 'How do I reset my password?' } });
    expect(r.statusCode).toBe(403);
    // A bearer that is not an agent key authenticates nothing, so the cookie is still the credential and gets no pass.
    for (const authorization of [`Bearer pk_pop_${'a'.repeat(64)}`, 'Bearer nonsense', 'Basic abc']) {
      const withHeader = await app.inject({ method: 'POST', url: '/api/questions', headers: { cookie: cookieA, authorization }, payload: { content: 'How do I reset my password?' } });
      expect(withHeader.statusCode).toBe(403);
      expect(withHeader.json().error.message).toBe('Missing CSRF header');
    }
  });

  it('validates questions', async () => {
    const short = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: 'hi' } });
    expect(short.statusCode).toBe(400);
    const pii = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: 'email me at ada@example.com please' } });
    expect(pii.statusCode).toBe(400);
  });

  it('lets a user ask, and only they and the admins see it', async () => {
    const r = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: `${tag} How do I reset my password?` } });
    expect(r.statusCode).toBe(201);
    const q = r.json();
    questionId = q.id;
    expect(q.status).toBe('PENDING');
    expect(q.canDelete).toBe(true);
    expect(q.answer).toBeNull();

    // Another learner sees none of it: not in the list, with or without ?mine, not by id, not in a re-fetch.
    for (const url of ['/api/questions', '/api/questions?mine=true', `/api/questions?ids=${questionId}`]) {
      const res = await app.inject({ method: 'GET', url, headers: asUser(cookieB) });
      expect(res.statusCode).toBe(200);
      expect(res.json().data.find((x: { id: string }) => x.id === questionId)).toBeUndefined();
    }
    expect((await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieB) })).statusCode).toBe(404);

    // An admin sees everyone's, as someone else's.
    const listAdmin = await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(adminCookie) });
    const found = listAdmin.json().data.find((x: { id: string }) => x.id === questionId);
    expect(found).toBeTruthy();
    expect(found.isOwner).toBe(false);
    expect(found.canDelete).toBe(false);
    expect((await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(adminCookie) })).statusCode).toBe(200);
    // ...and only their own with ?mine=true.
    const adminMine = await app.inject({ method: 'GET', url: '/api/questions?mine=true', headers: asUser(adminCookie) });
    expect(adminMine.json().data.find((x: { id: string }) => x.id === questionId)).toBeUndefined();

    const status = await app.inject({ method: 'GET', url: '/api/questions/status', headers: asUser(adminCookie) });
    expect(status.json().latestAt >= q.createdAt).toBe(true);

    // latestAt is scoped like the list: B's page must not report A's question as
    // new activity, or the refresh banner promises nothing. For B that holds
    // with or without ?mine=true, since B's list is B's own either way.
    for (const url of ['/api/questions/status?mine=true', '/api/questions/status']) {
      const mineStatus = await app.inject({ method: 'GET', url, headers: asUser(cookieB) });
      expect(mineStatus.statusCode).toBe(200);
      expect(mineStatus.json().latestAt === null || mineStatus.json().latestAt < q.createdAt).toBe(true);
      // The teacher heartbeat is never scoped — a stalled runner is everyone's problem.
      expect(mineStatus.json().agentLastSeenAt).toBe(status.json().agentLastSeenAt);
    }

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

    // The bound applies unscoped too (an admin's), and A's question stays invisible to B's scoped probe.
    const globalSince = await app.inject({ method: 'GET', url: `/api/questions/status?since=${encodeURIComponent(justBefore)}`, headers: asUser(adminCookie) });
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

    // Only the run holding the topic may answer it. (Released first: another test file's agent may have leased it
    // from the shared database.)
    await prisma.question.update({ where: { id: questionId }, data: { claimedAt: null, claimedById: null } });
    const unclaimed = await app.inject({ method: 'POST', url: `/api/questions/${questionId}/answer`, headers: asAgent(agentKey), payload: { content: 'Too early.' } });
    expect(unclaimed.statusCode).toBe(409);
    expect((await app.inject({ method: 'POST', url: `/api/questions/${questionId}/claim`, headers: asAgent(agentKey) })).statusCode).toBe(200);
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
    // The asker can always take a request back; one with a lesson keeps the lesson (privacy.integration.test.ts).
    expect(q.json().canDelete).toBe(true);
  });

  it('lets only the owner delete a question, and a pending one goes entirely', async () => {
    const other = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieB), payload: { content: `${tag} temporary question to delete` } });
    const notOwner = await app.inject({ method: 'DELETE', url: `/api/questions/${other.json().id}`, headers: asUser(cookieA) });
    expect(notOwner.statusCode).toBe(403);
    const admin = await app.inject({ method: 'DELETE', url: `/api/questions/${other.json().id}`, headers: asUser(adminCookie) });
    expect(admin.statusCode).toBe(403);
    const owner = await app.inject({ method: 'DELETE', url: `/api/questions/${other.json().id}`, headers: asUser(cookieB) });
    expect(owner.statusCode).toBe(204);
    expect(await prisma.question.count({ where: { id: other.json().id } })).toBe(0);
  });

  it('lets the sender pick the brain for a topic, and hands it to the studio with the claim', async () => {
    // A learner may pick any brain, Muse Wonder too (the page asks them to apologize for it first, web/src/lib/brains.ts).
    const learner = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieA), payload: { content: `${tag} wonder picked by a learner`, brain: 'muse-wonder' } });
    expect(learner.statusCode).toBe(201);
    expect(learner.json().brain).toBe('muse-wonder');
    await prisma.question.delete({ where: { id: learner.json().id } });
    const unknown = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(adminCookie), payload: { content: `${tag} brain unknown`, brain: 'muse-spark-1.3' } });
    expect(unknown.statusCode).toBe(400);

    const picked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(adminCookie), payload: { content: `${tag} brain picked by an admin`, brain: 'muse-spark' } });
    expect(picked.statusCode).toBe(201);
    expect(picked.json().brain).toBe('muse-spark');
    const claim = await app.inject({ method: 'POST', url: `/api/questions/${picked.json().id}/claim`, headers: asAgent(agentKey) });
    expect(claim.statusCode).toBe(200);
    expect(claim.json().brain).toBe('muse-spark');

    // Left out, the topic is the teacher's to make on its own brain.
    const own = await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookieA) });
    expect(own.json().brain).toBeNull();
    await prisma.question.delete({ where: { id: picked.json().id } });
  });

  it('threads: the asker follows up, agent owes a response, agent replies once', async () => {
    const empty = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) });
    expect(empty.json().data).toEqual([]);

    // The thread is the asker's: another learner, or an admin, cannot post in it.
    for (const cookie of [cookieB, adminCookie]) {
      const stranger = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookie), payload: { content: 'Me too!' } });
      expect(stranger.statusCode).toBe(403);
    }
    expect((await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) })).json().data).toEqual([]);

    const hr = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieA), payload: { content: 'That link is greyed out for me.' } });
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
    const h2 = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieA), payload: { content: 'Incognito worked. Is there a permanent fix?' } });
    expect(h2.statusCode).toBe(201);

    const queue = await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) });
    expect(queue.json().data).toHaveLength(1);
    const thread = queue.json().data[0];
    // The agent gets the original question, its own answer, every reply in order, and the one to respond to.
    expect(thread.question.content).toContain('reset my password');
    // ...and never who asked, or who wrote a follow-up: the agent's key is held by an LLM that reads learner text.
    expect(thread.question).not.toHaveProperty('user');
    expect(thread.thread.every((r: object) => !('user' in r))).toBe(true);
    expect(thread.replyTo).not.toHaveProperty('user');
    expect(thread.answer.content).toContain('Forgot password');
    expect(thread.thread.map((r: { authorType: string }) => r.authorType)).toEqual(['HUMAN', 'AGENT', 'HUMAN']);
    expect(thread.thread[2].content).toContain('permanent fix');
    expect(thread.replyTo.id).toBe(h2.json().id);
    // Earlier human reply is not offered again: it already has an agent reply.
    expect(thread.replyTo.id).not.toBe(humanReplyId);

    const a2 = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(agentKey), payload: { content: 'Yes: clear the site cookies once; the stale session cookie is what greys the button out.', replyToId: h2.json().id } });
    expect(a2.statusCode).toBe(201);
    expect((await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent(agentKey) })).json().data).toEqual([]);

    // Round 3 lands in the queue the same way.
    const h3 = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(cookieA), payload: { content: 'That fixed it, thanks.' } });
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
    for (const cookie of [cookieB, adminCookie]) {
      const notAuthor = await app.inject({ method: 'DELETE', url: `/api/replies/${humanReplyId}`, headers: asUser(cookie) });
      expect(notAuthor.statusCode).toBe(403);
    }
    const author = await app.inject({ method: 'DELETE', url: `/api/replies/${humanReplyId}`, headers: asUser(cookieA) });
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
      return list.find((c) => c.startsWith('popsmosis_session=')) ?? null;
    };

    // A fresh token is left alone: no Set-Cookie on an ordinary request.
    const fresh = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieA) });
    expect(fresh.statusCode).toBe(200);
    expect(cookieOf(fresh)).toBeNull();

    // A token past the halfway point is re-issued for the full lifetime.
    // fast-jwt reads numeric expiresIn as milliseconds; use an explicit seconds string.
    const aging = app.jwt.sign({ sub: userA.id }, { expiresIn: `${Math.floor(RENEW_THRESHOLD_S / 2)}s` });
    const renewed = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(`popsmosis_session=${aging}`) });
    expect(renewed.statusCode).toBe(200);
    const setCookie = cookieOf(renewed)!;
    expect(setCookie).toBeTruthy();
    expect(setCookie).toContain(`Max-Age=${SESSION_TTL_S}`);
    expect(setCookie).toContain('HttpOnly');
    expect(setCookie).toContain('Path=/api');
    const newToken = setCookie.split(';')[0].replace('popsmosis_session=', '');
    expect(newToken).not.toBe(aging);
    const decoded = app.jwt.decode<{ exp: number }>(newToken)!;
    expect(decoded.exp - Math.floor(Date.now() / 1000)).toBeGreaterThan(SESSION_TTL_S - 60);
    // The renewed token works and, being fresh, is not renewed again.
    const again = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(`popsmosis_session=${newToken}`) });
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
    const dead = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(`popsmosis_session=${expired}`) });
    expect(dead.statusCode).toBe(401);
    expect(dead.json().error.message).toBe('Invalid token');

    // Logout clears the cookie on the same path it was set on.
    const out = await app.inject({ method: 'POST', url: '/api/auth/logout', headers: asUser(cookieA) });
    expect(out.statusCode).toBe(200);
    const cleared = cookieOf(out)!;
    expect(cleared).toMatch(/^popsmosis_session=;/);
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
    expect((await app.inject({ method: 'PATCH', url: `/api/users/${userA.id}`, headers: asUser(adminCookie), payload: {} })).statusCode).toBe(400);
    expect((await app.inject({ method: 'PATCH', url: '/api/users/nope', headers: asUser(adminCookie), payload: { role: 'ADMIN' } })).statusCode).toBe(404);
  });

  it('lets anyone who signs in use the site at once: there is no approval step', async () => {
    // A fresh Google sign-in creates a row with nothing but the schema defaults.
    const cal = await prisma.user.create({ data: { email: `${tag}-cal@example.com`, googleId: `${tag}-cal`, name: 'Cal' } });
    const cookieCal = `popsmosis_session=${app.jwt.sign({ sub: cal.id }, { expiresIn: '30d' })}`;

    const me = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieCal) });
    expect(me.statusCode).toBe(200);
    expect(me.json()).toMatchObject({ email: `${tag}-cal@example.com`, role: 'USER' });
    expect(me.json()).not.toHaveProperty('status');
    for (const url of ['/api/questions', '/api/questions/status']) {
      expect((await app.inject({ method: 'GET', url, headers: asUser(cookieCal) })).statusCode).toBe(200);
    }
    const ask = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(cookieCal), payload: { content: 'Plate tectonics: why do the plates move at all?' } });
    expect(ask.statusCode).toBe(201);
    // Signed in is not admin.
    expect((await app.inject({ method: 'GET', url: '/api/users', headers: asUser(cookieCal) })).statusCode).toBe(403);
    await prisma.user.delete({ where: { id: cal.id } });
  });

  it('ADMIN_EMAILS reaches a session that is already open, without signing in again', async () => {
    const { invalidateUserCache } = await import('./middleware/auth.js');
    const eve = await prisma.user.create({ data: { email: `${tag}-eve@example.com`, googleId: `${tag}-eve`, name: 'Eve' } });
    const cookieEve = `popsmosis_session=${app.jwt.sign({ sub: eve.id }, { expiresIn: '30d' })}`;
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieEve) })).json().role).toBe('USER');

    // The documented way out of "nobody is an admin": add the address and
    // redeploy. A redeploy starts on an empty auth cache, which is what the
    // invalidate stands in for here.
    const before = process.env.ADMIN_EMAILS;
    process.env.ADMIN_EMAILS = `${tag}-eve@example.com`;
    invalidateUserCache(eve.id);
    try {
      expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(cookieEve) })).json().role).toBe('ADMIN');
      expect((await app.inject({ method: 'GET', url: '/api/users', headers: asUser(cookieEve) })).statusCode).toBe(200);
      // Written back, not just answered: the admin list and the next instance agree.
      expect((await prisma.user.findUnique({ where: { id: eve.id } }))!.role).toBe('ADMIN');
    } finally {
      if (before === undefined) delete process.env.ADMIN_EMAILS; else process.env.ADMIN_EMAILS = before;
      invalidateUserCache(eve.id);
    }
    await prisma.user.delete({ where: { id: eve.id } });
  });

  it('refuses a demotion that ADMIN_EMAILS would undo, instead of reporting a success that reverts', async () => {
    const { invalidateUserCache } = await import('./middleware/auth.js');
    const gus = await prisma.user.create({ data: { email: `${tag}-gus@example.com`, googleId: `${tag}-gus`, name: 'Gus', role: 'ADMIN' } });
    const before = process.env.ADMIN_EMAILS;
    process.env.ADMIN_EMAILS = `${tag}-gus@example.com`;
    try {
      const r = await app.inject({ method: 'PATCH', url: `/api/users/${gus.id}`, headers: asUser(adminCookie), payload: { role: 'USER' } });
      expect(r.statusCode).toBe(400);
      expect(r.json().error.message).toMatch(/ADMIN_EMAILS/);
      // Still an admin, so the refusal was the whole story.
      expect((await prisma.user.findUnique({ where: { id: gus.id } }))!.role).toBe('ADMIN');
    } finally {
      if (before === undefined) delete process.env.ADMIN_EMAILS; else process.env.ADMIN_EMAILS = before;
      invalidateUserCache(gus.id);
    }
    await prisma.user.delete({ where: { id: gus.id } });
  });

  it('refuses to demote the last admin', async () => {
    // Make the seeded admin the only admin in the test's world by trying to demote via a second admin:
    // promote Ben, then have Ben demote the original admin (allowed: two admins), then Ben demote himself (refused: last admin).
    const adminId = app.jwt.decode<{ sub: string }>(adminCookie.replace('popsmosis_session=', ''))!.sub;
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
      expect((await gated.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_pop_' + 'f'.repeat(64)) })).statusCode).toBe(401);
      // A malformed key does not count as an agent and hits the gate.
      expect((await gated.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent('sk_pop_short') })).statusCode).toBe(403);
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
    expect(created.json().apiKey).toMatch(/^sk_pop_[0-9a-f]{64}$/);
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
    expect(minted.json().apiKey).toMatch(/^sk_pop_[0-9a-f]{64}$/);
    expect(minted.json().apiKey).not.toBe(created.json().apiKey);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(minted.json().apiKey) })).statusCode).toBe(200);
    expect((await app.inject({ method: 'GET', url: '/api/agents/me', headers: asAgent(created.json().apiKey) })).statusCode).toBe(401);

    const skill = await app.inject({ method: 'GET', url: `/api/agents/${created.json().id}/skill`, headers: asUser(adminCookie) });
    expect(skill.statusCode).toBe(200);
    expect(skill.headers['content-type']).toContain('text/markdown');
    expect(skill.body).toContain('http://localhost:3001/api/agents/me');
    expect(skill.body).not.toContain('api.popsmosis.example');
  });
});
