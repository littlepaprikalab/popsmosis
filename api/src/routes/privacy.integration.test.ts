/**
 * What is the learner's and what is everyone's (lib/privacy.ts), against a real Postgres (DATABASE_URL). Skipped
 * unless INTEGRATION=1.
 *
 * Covers: a request, its files and its follow-ups are the learner's alone (and the admins') -> taking back a request
 * that has a lesson removes the learner's side and leaves the lesson public, naming no one -> deleting an account does
 * the same for every request with a lesson, deletes the rest, and ends the session -> the only admin cannot delete
 * their account.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import type { FastifyInstance } from 'fastify';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

const BUNDLE = {
  course: 'AP Biology', topic: 'ATP synthase', core_idea: 'A proton-driven rotary motor makes ATP.',
  facts: [{ id: 'f1', text: 'ATP synthase is driven by a proton gradient.', source_quote: '' }],
  lyrics: [{ id: 'c1', section: 'chorus', text: 'Spin the rotor, make the ATP', names: [], fact_id: 'f1' }],
};
const FACTCHECK = { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] };
const READY = {
  videoStatus: 'READY' as const,
  songVideoUrl: 'https://x.supabase.co/storage/v1/object/public/droplets/a/out/song.mp4',
  lessonVideoUrl: 'https://x.supabase.co/storage/v1/object/public/droplets/a/out/lesson.mp4',
};
const WORDS = 'ATP synthase, for my AP Bio exam on Friday at Lincoln High';
const LESSON = 'Protons flowing back through **ATP synthase** spin its rotor.';

d('learner privacy', () => {
  let app: FastifyInstance;
  let prisma: (typeof import('../lib/prisma.js'))['default'];
  let agentKey = '';
  let grace = { id: '', cookie: '' };
  let ben = { id: '', cookie: '' };
  let admin = { id: '', cookie: '' };

  const asUser = (cookie: string) => ({ cookie, 'x-requested-with': 'XMLHttpRequest' });
  const asAgent = () => ({ authorization: `Bearer ${agentKey}` });
  const b64 = (s: string) => Buffer.from(s).toString('base64');

  async function user(name: string, role: 'USER' | 'ADMIN' = 'USER') {
    const { SESSION_EXPIRES_IN } = await import('../lib/session.js');
    const row = await prisma.user.create({ data: { email: `${name}@example.com`, googleId: `g-${name}`, name, role } });
    return { id: row.id, cookie: `popsmosis_session=${app.jwt.sign({ sub: row.id }, { expiresIn: SESSION_EXPIRES_IN })}` };
  }

  /** A request asked through the API with a file, answered by the teacher, its videos READY, and a follow-up thread. */
  async function lesson(learner: { cookie: string }, words = WORDS) {
    const asked = await app.inject({
      method: 'POST', url: '/api/questions', headers: asUser(learner.cookie),
      payload: { content: words, attachments: [{ name: 'class-notes.txt', data: b64('my notes') }] },
    });
    expect(asked.statusCode).toBe(201);
    const questionId = asked.json().id as string;
    const answered = await app.inject({
      method: 'POST', url: `/api/questions/${questionId}/answer`, headers: asAgent(),
      payload: { content: LESSON, bundle: BUNDLE, factcheck: FACTCHECK },
    });
    expect(answered.statusCode).toBe(201);
    const answerId = answered.json().id as string;
    await prisma.answer.update({ where: { id: answerId }, data: READY });
    const followUp = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(learner.cookie),
      payload: { content: 'Why does the rotor only turn one way?', attachments: [{ name: 'sketch.txt', data: b64('a sketch') }] },
    });
    expect(followUp.statusCode).toBe(201);
    const agentReply = await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(),
      payload: { content: 'Because the gradient only runs one way.', replyToId: followUp.json().id },
    });
    expect(agentReply.statusCode).toBe(201);
    return {
      questionId, answerId,
      questionFile: asked.json().attachments[0].url as string,
      replyFile: followUp.json().attachments[0].url as string,
    };
  }

  beforeAll(async () => {
    process.env.NODE_ENV = 'test';
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
    // The admin-count rule needs a known set of admins; the other integration files reset the same way.
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users RESTART IDENTITY CASCADE');
    const { generateApiKey } = await import('../lib/apikey.js');
    const key = generateApiKey();
    agentKey = key.raw;
    await prisma.agent.create({ data: { name: 'teacher', apiKeyHash: key.hashed } });
    grace = await user('grace');
    ben = await user('ben');
    admin = await user('root', 'ADMIN');
  });

  it('keeps a request, its files and its follow-ups to the learner, the admins and the teacher', async () => {
    const { questionId, answerId, questionFile, replyFile } = await lesson(grace);

    // Ben: none of it, by any route.
    expect((await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(ben.cookie) })).json().data).toEqual([]);
    expect((await app.inject({ method: 'GET', url: `/api/questions?ids=${questionId}`, headers: asUser(ben.cookie) })).json().data).toEqual([]);
    expect((await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(ben.cookie) })).statusCode).toBe(404);
    for (const url of [questionFile, replyFile]) {
      expect((await app.inject({ method: 'GET', url, headers: asUser(ben.cookie) })).statusCode).toBe(404);
    }
    expect((await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(ben.cookie), payload: { content: 'Me too' },
    })).statusCode).toBe(403);

    // Grace, the admin and the teacher: all of it.
    for (const cookie of [grace.cookie, admin.cookie]) {
      const card = (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(cookie) })).json();
      expect(card.content).toBe(WORDS);
      expect(card.answer.replies).toHaveLength(2);
      for (const url of [questionFile, replyFile]) {
        expect((await app.inject({ method: 'GET', url, headers: asUser(cookie) })).statusCode).toBe(200);
      }
    }
    expect((await app.inject({ method: 'GET', url: questionFile, headers: asAgent() })).statusCode).toBe(200);

    // And the lesson is everyone's.
    const tab = await app.inject({ method: 'GET', url: `/api/droplets/${answerId}` });
    expect(tab.statusCode).toBe(200);
    expect(tab.body).not.toContain('Lincoln High');
  });

  it('takes back a request with a lesson: the learner\'s side goes, the lesson stays public and names no one', async () => {
    const { questionId, answerId, questionFile, replyFile } = await lesson(grace);
    const card = (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(grace.cookie) })).json();
    expect(card.canDelete).toBe(true);

    const del = await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}`, headers: asUser(grace.cookie) });
    expect(del.statusCode).toBe(204);

    // Gone from Grace's page; her words, files and the whole thread are gone from the database.
    expect((await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(grace.cookie) })).json().data).toEqual([]);
    expect((await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(grace.cookie) })).statusCode).toBe(404);
    const row = await prisma.question.findUniqueOrThrow({ where: { id: questionId } });
    expect(row.userId).toBeNull();
    expect(row.content).toBe('');
    expect(await prisma.attachment.count()).toBe(0); // the request's file and the follow-up's
    expect(await prisma.reply.count({ where: { answerId } })).toBe(0); // hers and the teacher's
    for (const url of [questionFile, replyFile]) {
      expect((await app.inject({ method: 'GET', url, headers: asUser(admin.cookie) })).statusCode).toBe(404);
    }

    // The lesson is untouched and still on the tab.
    const tab = await app.inject({ method: 'GET', url: `/api/droplets/${answerId}` });
    expect(tab.statusCode).toBe(200);
    expect(tab.json().content).toBe(LESSON);
    expect((await app.inject({ method: 'GET', url: '/api/droplets' })).json().data.map((x: { id: string }) => x.id)).toEqual([answerId]);

    // An admin still sees the lesson on /learn, with nothing of the learner's.
    const adminCard = (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(admin.cookie) })).json();
    expect(adminCard.user).toBeNull();
    expect(adminCard.content).toBe('');
    expect(adminCard.attachments).toEqual([]);
    expect(adminCard.answer.content).toBe(LESSON);
    expect(adminCard.answer.replies).toEqual([]);
    expect(JSON.stringify(adminCard)).not.toMatch(/grace|Lincoln/i);

    // It is no one's now: no follow-ups, no second delete, nothing on the teacher's queues.
    expect((await app.inject({
      method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(grace.cookie), payload: { content: 'One more thing' },
    })).statusCode).toBe(403);
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}`, headers: asUser(grace.cookie) })).statusCode).toBe(403);
    expect((await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent() })).json().data).toEqual([]);
  });

  it('deletes an account: requests with no lesson go, ones with a lesson are taken back, and the session ends', async () => {
    const kept = await lesson(grace);
    const waiting = await app.inject({
      method: 'POST', url: '/api/questions', headers: asUser(grace.cookie),
      payload: { content: 'Krebs cycle, for Friday', attachments: [{ name: 'more-notes.txt', data: b64('more') }] },
    });
    expect(waiting.statusCode).toBe(201);
    // A follow-up Grace wrote under someone else's lesson, from before threads were their owner's alone.
    const bens = await lesson(ben, 'Glycolysis, please');
    await prisma.reply.create({ data: { answerId: bens.answerId, userId: grace.id, content: 'Grace was here' } });
    // Ben's own thread has his follow-up and the teacher's reply.
    expect(await prisma.reply.count({ where: { answerId: bens.answerId } })).toBe(3);

    // CSRF: a cookie write without the header is refused before anything happens.
    expect((await app.inject({ method: 'DELETE', url: '/api/auth/me', headers: { cookie: grace.cookie } })).statusCode).toBe(403);
    expect(await prisma.user.count({ where: { id: grace.id } })).toBe(1);

    const res = await app.inject({ method: 'DELETE', url: '/api/auth/me', headers: asUser(grace.cookie) });
    expect(res.statusCode).toBe(204);
    const setCookie = ([] as string[]).concat((res.headers['set-cookie'] as string | string[] | undefined) ?? []);
    expect(setCookie.some((c) => /^popsmosis_session=;/.test(c))).toBe(true);

    // The account, the request without a lesson and its file, and her follow-up on Ben's thread are gone.
    expect(await prisma.user.count({ where: { id: grace.id } })).toBe(0);
    expect(await prisma.question.count({ where: { id: waiting.json().id } })).toBe(0);
    expect(await prisma.reply.count({ where: { content: 'Grace was here' } })).toBe(0);
    expect(await prisma.reply.count({ where: { answerId: bens.answerId } })).toBe(2); // Ben's thread is his
    // The request with a lesson is taken back, and the lesson is still on the tab.
    const row = await prisma.question.findUniqueOrThrow({ where: { id: kept.questionId } });
    expect([row.userId, row.content]).toEqual([null, '']);
    expect(await prisma.reply.count({ where: { answerId: kept.answerId } })).toBe(0);
    expect(await prisma.attachment.count({ where: { OR: [{ questionId: kept.questionId }, { questionId: waiting.json().id }] } })).toBe(0);
    expect((await app.inject({ method: 'GET', url: `/api/droplets/${kept.answerId}` })).statusCode).toBe(200);
    // Nothing anywhere still names her.
    const everything = (await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(admin.cookie) })).body;
    expect(everything).not.toMatch(/grace|Lincoln|Krebs/i);

    // The old cookie no longer works, and signing in again would make a new, empty account.
    const me = await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(grace.cookie) });
    expect(me.statusCode).toBe(401);
    expect(me.json().error.message).toBe('User not found');
  });

  it('refuses to delete the only admin, and lets an admin go once there is another', async () => {
    const refused = await app.inject({ method: 'DELETE', url: '/api/auth/me', headers: asUser(admin.cookie) });
    expect(refused.statusCode).toBe(400);
    expect(refused.json().error.message).toMatch(/only admin/);
    expect(await prisma.user.count({ where: { id: admin.id } })).toBe(1);

    await user('second', 'ADMIN');
    expect((await app.inject({ method: 'DELETE', url: '/api/auth/me', headers: asUser(admin.cookie) })).statusCode).toBe(204);
    expect(await prisma.user.count({ where: { id: admin.id } })).toBe(0);
  });

  it('needs a session: no cookie, or an agent key, deletes nothing', async () => {
    expect((await app.inject({ method: 'DELETE', url: '/api/auth/me' })).statusCode).toBe(401);
    expect((await app.inject({ method: 'DELETE', url: '/api/auth/me', headers: asAgent() })).statusCode).toBe(401);
    expect(await prisma.user.count()).toBe(3);
  });

  it('answers 401, not a 500, to a session this instance still remembers after the account was deleted', async () => {
    const { answerId } = await lesson(ben, 'Glaciers, please');
    // This instance has Ben cached (middleware/auth.ts); the account goes behind its back, as on another instance.
    expect((await app.inject({ method: 'GET', url: '/api/auth/me', headers: asUser(ben.cookie) })).statusCode).toBe(200);
    await prisma.user.delete({ where: { id: ben.id } });

    for (const [url, content] of [['/api/questions', 'Still here?'], [`/api/answers/${answerId}/replies`, 'And here?']]) {
      const res = await app.inject({ method: 'POST', url, headers: asUser(ben.cookie), payload: { content } });
      expect(res.statusCode).toBe(401);
      expect(res.json().error.message).toBe('User not found'); // the web app signs out on exactly this
    }
    // The stale entry went with the first refusal: reads are refused at once too.
    expect((await app.inject({ method: 'GET', url: '/api/questions', headers: asUser(ben.cookie) })).statusCode).toBe(401);
  });

  it('refuses a lesson for a request deleted before it landed, and leaves nothing behind', async () => {
    const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(ben.cookie), payload: { content: 'Tides, then never mind' } });
    const id = asked.json().id as string;
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${id}`, headers: asUser(ben.cookie) })).statusCode).toBe(204);
    const late = await app.inject({ method: 'POST', url: `/api/questions/${id}/answer`, headers: asAgent(), payload: { content: 'The Moon pulls the oceans.' } });
    expect(late.statusCode).toBe(404); // the studio reads a 404 as "the topic was deleted" and stops
    expect(await prisma.answer.count()).toBe(0);
  });

  // The teacher works on its own clock, so a lesson can land in the same instant the learner deletes. The row lock
  // both routes take (SELECT ... FOR UPDATE) has to make every such race end in one of two whole states. Which one
  // wins varies run to run; the two orders on their own are pinned by the tests above.
  it('a lesson landing as the learner takes the request back ends whole either way, never half', async () => {
    for (let round = 0; round < 12; round++) {
      const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(ben.cookie), payload: { content: `Race ${round}: tides` } });
      const id = asked.json().id as string;
      const [answer, del] = await Promise.all([
        app.inject({ method: 'POST', url: `/api/questions/${id}/answer`, headers: asAgent(), payload: { content: 'The Moon pulls the oceans.' } }),
        app.inject({ method: 'DELETE', url: `/api/questions/${id}`, headers: asUser(ben.cookie) }),
      ]);
      expect(del.statusCode).toBe(204);
      const row = await prisma.question.findUnique({ where: { id }, include: { answers: true } });
      if (answer.statusCode === 201) {
        // The lesson came first: the request was taken back around it.
        expect(row).toMatchObject({ userId: null, content: '', status: 'ANSWERED' });
        expect(row!.answers).toHaveLength(1);
      } else {
        // The delete came first: nothing is left for the lesson to hang on.
        expect(answer.statusCode).toBe(404);
        expect(row).toBeNull();
      }
      // Each user may ask 10 a day; the race is the subject here, not the quota.
      await prisma.question.updateMany({ where: { id }, data: { createdAt: new Date(Date.now() - 2 * 86_400_000) } });
    }
  });

  it('a lesson or a follow-up landing as the account is deleted ends whole either way, never a server error', async () => {
    for (let round = 0; round < 6; round++) {
      const learner = await user(`racer${round}`);
      const { answerId } = await lesson(learner, `Race ${round}: volcanoes`);
      const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(learner.cookie), payload: { content: `Race ${round}: earthquakes` } });
      const pendingId = asked.json().id as string;
      const [answer, followUp, del] = await Promise.all([
        app.inject({ method: 'POST', url: `/api/questions/${pendingId}/answer`, headers: asAgent(), payload: { content: 'Plates slip.' } }),
        app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(learner.cookie), payload: { content: 'One more question' } }),
        app.inject({ method: 'DELETE', url: '/api/auth/me', headers: asUser(learner.cookie) }),
      ]);
      expect(del.statusCode).toBe(204);
      expect(await prisma.user.count({ where: { id: learner.id } })).toBe(0);
      // The lesson that was already there stays, taken back.
      expect((await app.inject({ method: 'GET', url: `/api/droplets/${answerId}` })).statusCode).toBe(200);
      // The racing lesson: kept and taken back, or never made.
      const pending = await prisma.question.findUnique({ where: { id: pendingId } });
      if (answer.statusCode === 201) expect(pending).toMatchObject({ userId: null, content: '' });
      else { expect(answer.statusCode).toBe(404); expect(pending).toBeNull(); }
      // The racing follow-up: posted and then deleted with the account, or refused. Never a 500.
      expect([201, 401, 403]).toContain(followUp.statusCode);
      expect(await prisma.reply.count({ where: { answerId, userId: { not: null } } })).toBe(0);
    }
  });
});
