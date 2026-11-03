/**
 * What is the learner's and what is everyone's (lib/privacy.ts), against a real Postgres (DATABASE_URL). Skipped
 * unless INTEGRATION=1.
 *
 * Covers: a request, its files and its follow-ups are the learner's alone (and the admins') -> taking back a request
 * that has a lesson removes the learner's side and leaves the lesson public, naming no one -> deleting one with
 * everything takes the lesson and all that hangs on it too (its files in R2: lib/storage-delete.integration.test.ts)
 * -> deleting an account does
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
  songVideoUrl: 'https://media.popsmosis.com/a/out/song.mp4',
  lessonVideoUrl: 'https://media.popsmosis.com/a/out/lesson.mp4',
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
    await app.inject({ method: 'POST', url: `/api/questions/${questionId}/claim`, headers: asAgent() });
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
    await prisma.$executeRawUnsafe('TRUNCATE replies, answers, questions, agents, users, storage_deletions RESTART IDENTITY CASCADE');
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

  it('deletes a request with everything: the lesson, its thread, likes, comments and play counts go, and it leaves the tab', async () => {
    const { questionId, answerId, questionFile, replyFile } = await lesson(grace);
    const bens = await lesson(ben, 'Glycolysis, please');
    // Others' likes and comments on Grace's droplet, a play counted, and Ben's like on his own.
    for (const who of [ben, admin]) {
      expect((await app.inject({ method: 'PUT', url: `/api/droplets/${answerId}/like`, headers: asUser(who.cookie) })).statusCode).toBe(200);
    }
    expect((await app.inject({
      method: 'POST', url: `/api/droplets/${answerId}/comments`, headers: asUser(ben.cookie), payload: { content: 'This helped a lot' },
    })).statusCode).toBe(201);
    expect((await app.inject({ method: 'POST', url: `/api/droplets/${answerId}/plays`, payload: { video: 'song', event: 'started' } })).statusCode).toBe(204);
    expect((await app.inject({ method: 'PUT', url: `/api/droplets/${bens.answerId}/like`, headers: asUser(ben.cookie) })).statusCode).toBe(200);
    // Grace's card says what deleting it with everything would take from others.
    const card = (await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(grace.cookie) })).json();
    expect([card.answer.likeCount, card.answer.commentCount]).toEqual([2, 1]);

    const del = await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asUser(grace.cookie) });
    expect(del.statusCode).toBe(204);

    // Nothing of it is left: the request, the lesson and everything under it.
    expect(await prisma.question.count({ where: { id: questionId } })).toBe(0);
    expect(await prisma.answer.count({ where: { id: answerId } })).toBe(0);
    expect(await prisma.reply.count({ where: { answerId } })).toBe(0);
    expect(await prisma.attachment.count({ where: { OR: [{ questionId }, { reply: { answerId } }] } })).toBe(0);
    expect(await prisma.dropletLike.count({ where: { answerId } })).toBe(0);
    expect(await prisma.dropletComment.count({ where: { answerId } })).toBe(0);
    expect(await prisma.dropletPlays.count({ where: { answerId } })).toBe(0);
    for (const url of [questionFile, replyFile]) {
      expect((await app.inject({ method: 'GET', url, headers: asUser(admin.cookie) })).statusCode).toBe(404);
    }
    // Off the tab and off /learn, for everyone.
    expect((await app.inject({ method: 'GET', url: `/api/droplets/${answerId}` })).statusCode).toBe(404);
    expect((await app.inject({ method: 'GET', url: '/api/droplets' })).json().data.map((x: { id: string }) => x.id)).toEqual([bens.answerId]);
    expect((await app.inject({ method: 'GET', url: `/api/questions/${questionId}`, headers: asUser(admin.cookie) })).statusCode).toBe(404);
    // With no file storage on this API there were no files, so nothing waits to be cleared from it.
    expect(await prisma.storageDeletion.count()).toBe(0);

    // Ben's droplet is untouched, its like counted as before; Ben's and the admin's accounts are theirs still.
    expect((await app.inject({ method: 'GET', url: `/api/droplets/${bens.answerId}` })).json().likeCount).toBe(1);
    expect(await prisma.reply.count({ where: { answerId: bens.answerId } })).toBe(2);
    expect(await prisma.user.count()).toBe(3);
    // The teacher has nothing left to answer under it, and a lesson for it is refused as for any deleted topic.
    expect((await app.inject({ method: 'GET', url: '/api/replies/queue', headers: asAgent() })).json().data).toEqual([]);
    expect((await app.inject({ method: 'POST', url: `/api/answers/${answerId}/video/claim`, headers: asAgent(), payload: {} })).statusCode).toBe(404);
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asUser(grace.cookie) })).statusCode).toBe(404);
  });

  it('deletes a request with no lesson the same with everything or without', async () => {
    const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(grace.cookie), payload: { content: 'Tides' } });
    const id = asked.json().id as string;
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${id}/everything`, headers: asUser(grace.cookie) })).statusCode).toBe(204);
    expect(await prisma.question.count({ where: { id } })).toBe(0);
  });

  it('deletes everything only of the learner\'s own request: not someone else\'s, not as an admin, not once taken back', async () => {
    const { questionId, answerId } = await lesson(grace);
    for (const who of [ben, admin]) {
      const res = await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asUser(who.cookie) });
      expect(res.statusCode).toBe(403);
    }
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asAgent() })).statusCode).toBe(401);
    // CSRF: a cookie write without the header is refused.
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: { cookie: grace.cookie } })).statusCode).toBe(403);
    expect(await prisma.answer.count({ where: { id: answerId } })).toBe(1);

    // Taken back, it is no one's: the lesson can no longer be deleted by the learner who asked for it.
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}`, headers: asUser(grace.cookie) })).statusCode).toBe(204);
    expect((await app.inject({ method: 'DELETE', url: `/api/questions/${questionId}/everything`, headers: asUser(grace.cookie) })).statusCode).toBe(403);
    expect((await app.inject({ method: 'GET', url: `/api/droplets/${answerId}` })).statusCode).toBe(200);
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
      await app.inject({ method: 'POST', url: `/api/questions/${id}/claim`, headers: asAgent() });
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
      // Each user may ask 10 a day, counted apart from the requests (lib/usage.ts); the race is the subject here, not
      // the quota.
      await prisma.usageEvent.updateMany({ where: { userId: ben.id }, data: { createdAt: new Date(Date.now() - 2 * 86_400_000) } });
    }
  });

  it('a lesson landing as the learner deletes everything ends with nothing left either way', async () => {
    for (let round = 0; round < 12; round++) {
      const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(ben.cookie), payload: { content: `Race ${round}: tides` } });
      const id = asked.json().id as string;
      await app.inject({ method: 'POST', url: `/api/questions/${id}/claim`, headers: asAgent() });
      const [answer, del] = await Promise.all([
        app.inject({ method: 'POST', url: `/api/questions/${id}/answer`, headers: asAgent(), payload: { content: 'The Moon pulls the oceans.' } }),
        app.inject({ method: 'DELETE', url: `/api/questions/${id}/everything`, headers: asUser(ben.cookie) }),
      ]);
      expect(del.statusCode).toBe(204);
      expect([201, 404]).toContain(answer.statusCode);
      expect(await prisma.question.count({ where: { id } })).toBe(0);
      expect(await prisma.answer.count({ where: { questionId: id } })).toBe(0);
      await prisma.usageEvent.updateMany({ where: { userId: ben.id }, data: { createdAt: new Date(Date.now() - 2 * 86_400_000) } });
    }
  });

  it('a lesson or a follow-up landing as the account is deleted ends whole either way, never a server error', async () => {
    for (let round = 0; round < 6; round++) {
      const learner = await user(`racer${round}`);
      const { answerId } = await lesson(learner, `Race ${round}: volcanoes`);
      const asked = await app.inject({ method: 'POST', url: '/api/questions', headers: asUser(learner.cookie), payload: { content: `Race ${round}: earthquakes` } });
      const pendingId = asked.json().id as string;
      await app.inject({ method: 'POST', url: `/api/questions/${pendingId}/claim`, headers: asAgent() });
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
  // Deleting with everything is the first way a droplet goes while people and the teacher still write to it. Every
  // route that writes to one is sent in the moment between its look at the droplet and the delete's commit: the
  // delete is held open, the request goes, then the delete commits. Each has to end in a 4xx, never a server error.
  it('every write to a droplet that lands as it is deleted with everything ends in a 4xx, never a server error', async () => {
    type Case = { name: string; state: 'READY' | 'RENDERING'; send: (ids: { answerId: string; followUpId: string; commentId: string }) => Promise<{ statusCode: number; body: string }> };
    const cases: Case[] = [
      { name: 'like', state: 'READY', send: ({ answerId }) => app.inject({ method: 'PUT', url: `/api/droplets/${answerId}/like`, headers: asUser(ben.cookie) }) },
      { name: 'unlike', state: 'READY', send: ({ answerId }) => app.inject({ method: 'DELETE', url: `/api/droplets/${answerId}/like`, headers: asUser(admin.cookie) }) },
      { name: 'comment', state: 'READY', send: ({ answerId }) => app.inject({ method: 'POST', url: `/api/droplets/${answerId}/comments`, headers: asUser(ben.cookie), payload: { content: 'Nice' } }) },
      { name: 'delete comment', state: 'READY', send: ({ answerId, commentId }) => app.inject({ method: 'DELETE', url: `/api/droplets/${answerId}/comments/${commentId}`, headers: asUser(admin.cookie) }) },
      { name: 'play', state: 'READY', send: ({ answerId }) => app.inject({ method: 'POST', url: `/api/droplets/${answerId}/plays`, payload: { video: 'song', event: 'started' } }) },
      { name: 'follow-up', state: 'READY', send: ({ answerId }) => app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(grace.cookie), payload: { content: 'One more?' } }) },
      { name: 'teacher reply', state: 'READY', send: ({ answerId, followUpId }) => app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asAgent(), payload: { content: 'Here is why.', replyToId: followUpId } }) },
      { name: 'admin hide', state: 'READY', send: ({ answerId }) => app.inject({ method: 'PATCH', url: `/api/answers/${answerId}`, headers: asUser(admin.cookie), payload: { hidden: true } }) },
      { name: 'video claim', state: 'RENDERING', send: ({ answerId }) => app.inject({ method: 'POST', url: `/api/answers/${answerId}/video/claim`, headers: asAgent(), payload: { attempt: 1 } }) },
      { name: 'video progress', state: 'RENDERING', send: ({ answerId }) => app.inject({ method: 'PUT', url: `/api/answers/${answerId}/video/progress`, headers: asAgent(), payload: { attempt: 1, running: [{ step: 'render', seconds: 3 }] } }) },
      { name: 'video report', state: 'RENDERING', send: ({ answerId }) => app.inject({ method: 'PATCH', url: `/api/answers/${answerId}/video`, headers: asAgent(), payload: { status: 'RENDERING', attempt: 1 } }) },
      { name: 'video fail', state: 'RENDERING', send: ({ answerId }) => app.inject({ method: 'POST', url: `/api/answers/${answerId}/video/fail`, headers: asAgent(), payload: { reason: 'render crashed', attempt: 1 } }) },
    ];
    for (const c of cases) {
      const { questionId, answerId } = await lesson(grace, `${c.name}: tides`);
      // A follow-up of Grace's waiting for the teacher, and a comment of Ben's, for the routes that name one.
      const followUp = await app.inject({ method: 'POST', url: `/api/answers/${answerId}/replies`, headers: asUser(grace.cookie), payload: { content: 'And the Sun?' } });
      const comment = await app.inject({ method: 'POST', url: `/api/droplets/${answerId}/comments`, headers: asUser(ben.cookie), payload: { content: 'Thanks' } });
      await app.inject({ method: 'PUT', url: `/api/droplets/${answerId}/like`, headers: asUser(admin.cookie) });
      if (c.state === 'RENDERING') {
        await prisma.answer.update({ where: { id: answerId }, data: { videoStatus: null, songVideoUrl: null, lessonVideoUrl: null } });
        expect((await app.inject({ method: 'POST', url: `/api/answers/${answerId}/video/claim`, headers: asAgent(), payload: {} })).statusCode).toBe(200);
      }
      const ids = { answerId, followUpId: followUp.json().id as string, commentId: comment.json().id as string };

      let commit!: () => void;
      let deleted!: () => void;
      const ready = new Promise<void>((resolve) => { deleted = resolve; });
      const holding = prisma.$transaction(async (tx) => {
        await tx.question.delete({ where: { id: questionId } });
        deleted();
        await new Promise<void>((resolve) => { commit = resolve; });
      }, { timeout: 20_000 });
      await ready;
      const sent = c.send(ids);
      await new Promise((resolve) => setTimeout(resolve, 300)); // it reads, then waits on the delete's locks
      commit();
      await holding;
      const res = await sent;
      // Gone, as the studio reads a 404 ("the topic was deleted"); deleting a comment and counting a play answer 204
      // whether or not there was anything left to do.
      expect(res.statusCode, `${c.name}: ${res.body}`).toBe(c.name === 'delete comment' || c.name === 'play' ? 204 : 404);
      expect(await prisma.answer.count({ where: { id: answerId } }), c.name).toBe(0);
      await prisma.usageEvent.deleteMany({});
    }
  });
});

