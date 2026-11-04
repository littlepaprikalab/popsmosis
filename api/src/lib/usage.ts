/**
 * The limits on what learners may ask for: topics a day each, follow-ups an hour each, and topics a day across the
 * whole site. Admins have no topic limit of their own (isAdmin), but count toward the site's. A topic costs about $0.70 to $1 of model spend and a follow-up a run of the teacher, and anyone with a
 * Google account can sign up, so these are what bound the bill.
 *
 * Counted from usage_events, never from the requests themselves: taking a request back or deleting it (or a
 * follow-up) leaves its row, so it cannot hand back what it used.
 *
 * Each take is its own short transaction, apart from writing the request: a request may carry 8 MB of files, and an
 * interactive transaction holding a pooled connection (and the learner's lock) for that long would hit Prisma's
 * 5-second limit and starve the pool. A transaction-scoped advisory lock per learner makes count-then-insert one
 * step, so one learner's requests sent in parallel cannot all pass the same count. A topic also takes the site's lock,
 * after the learner's, so topics from different learners landing in the same instant
 * cannot all pass the site's count either: the ceiling is exact. It is held for a count and an insert, milliseconds,
 * and only by a topic that passed a look at the site's count without it, so once the day is used up nobody waits on
 * it. When the request then fails to be written, its take is given back. Rows older than two days are let go (pruneUsage); an
 * account's go with it.
 *
 * Files are limited the same way, in bytes: what a learner sends a day (the take's row keeps its bytes, so deleting a
 * request gives nothing back) and what popsmosis holds of theirs at a time (counted from the files themselves, since a
 * deleted request's files are gone). A message may carry 8 MB, and follow-ups 30 an hour, so without these one account
 * could add about 240 MB an hour to Postgres. Topics and follow-ups take the same per-learner lock, so one learner's
 * messages sent at once cannot both pass the byte count.
 */
import { AppError } from './errors.js';
import { knob } from './knobs.js';
import { attachmentRetentionDays, minimumFileBytes } from './attachments.js';
import prisma, { runTransaction, type TxClient } from './prisma.js';

const HOUR_MS = 60 * 60 * 1000;
const MB = 1024 * 1024;
// How long a take's files count as on their way when nothing says they arrived (a crash between the take and the
// write): a body is read and parsed before the take, so only the insert itself (seconds for 8 MB) falls in between.
const IN_FLIGHT_MS = 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const KEEP_MS = 2 * DAY_MS;
// How long a topic's take may wait for a pooled connection, and run, in a burst: the takes that may still get a place
// queue on the site's lock (takeTopic), each holding a connection while it waits, so Prisma's 2 seconds for a
// connection turned 1 in 4 of 1,000 learners sending at once into a server error. Measured on one instance with 5
// connections, 1,000 at once are all answered (taken, or told the site is full): in about 3 seconds at a cap of 30,
// and 5 when every one gets a place.
const TAKE_WAITS = { maxWait: 15_000, timeout: 15_000 };

function intEnv(raw: string | undefined, fallback: number): number {
  const n = parseInt(raw ?? '', 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

export function usageLimits(env: NodeJS.ProcessEnv = process.env) {
  return {
    topicsPerUserPerDay: intEnv(knob('QUESTIONS_PER_USER_PER_DAY', env), 10),
    // Site-wide, everyone's topics together: the ceiling on a day's model spend, and on the videos made in a day, as
    // each topic makes one droplet's. 0 pauses new topics for everyone, admins too. Here and in lib/knobs.ts.
    topicsPerDay: intEnv(knob('TOPICS_PER_DAY', env), 30),
    repliesPerUserPerHour: intEnv(knob('REPLIES_PER_USER_PER_HOUR', env), 30),
    // Files, in MB. 0: no limit. A phone's photo of notes is 2 to 4 MB. Generous for the hackathon; lower them
    // afterwards (20 a day and 60 held), here and in lib/knobs.ts, since the files live in the database.
    attachmentMbPerUserPerDay: intEnv(knob('ATTACHMENT_MB_PER_USER_PER_DAY', env), 100),
    attachmentMbHeldPerUser: intEnv(knob('ATTACHMENT_MB_HELD_PER_USER', env), 300),
  };
}

const mb = (bytes: number) => `${(bytes / MB).toFixed(1).replace(/\.0$/, '')} MB`;

/** Why the caller may not send `bytes` of files now, or null: their day's bytes, then what is held of theirs. */
async function bytesRefusal(db: Db, userId: string, bytes: number, env: NodeJS.ProcessEnv): Promise<AppError | null> {
  if (bytes <= 0) return null;
  const { attachmentMbPerUserPerDay: perDay, attachmentMbHeldPerUser: held } = usageLimits(env);
  if (perDay > 0) {
    const since = new Date(Date.now() - DAY_MS);
    const sent = (await db.usageEvent.aggregate({ where: { userId, createdAt: { gte: since } }, _sum: { bytes: true } }))._sum.bytes ?? 0;
    if (sent + bytes > perDay * MB) {
      return new AppError('RATE_LIMITED',
        `You can send ${perDay} MB of files a day and have sent ${mb(sent)} in the last 24 hours. Send smaller or fewer files, or send this without them.`,
        { retryable: false });
    }
  }
  if (held > 0) {
    // The files of this learner's requests and follow-ups that popsmosis still holds, each found by its index; and the
    // bytes of each take whose message is not written yet (filesPending, cleared by filesWritten()): the write follows
    // the take, outside the lock, so two messages sent at once would otherwise each miss the other's files. A take
    // left pending by a crash stops counting after IN_FLIGHT_MS.
    const days = attachmentRetentionDays(env);
    const fresh = days > 0 ? new Date(Date.now() - days * DAY_MS) : new Date(0);
    const inFlight = new Date(Date.now() - IN_FLIGHT_MS);
    const [row] = await db.$queryRaw<{ total: bigint | number | null; pending: bigint | number | null }[]>`
      SELECT
        COALESCE((SELECT SUM(a.size) FROM attachments a JOIN questions q ON q.id = a."questionId"
                  WHERE q."userId" = ${userId} AND a."purgedAt" IS NULL AND a."createdAt" >= ${fresh}), 0)
      + COALESCE((SELECT SUM(a.size) FROM attachments a JOIN replies r ON r.id = a."replyId"
                  WHERE r."userId" = ${userId} AND a."purgedAt" IS NULL AND a."createdAt" >= ${fresh}), 0)
      AS total,
      COALESCE((SELECT SUM(bytes) FROM usage_events
                WHERE "userId" = ${userId} AND "filesPending" AND "createdAt" >= ${inFlight}), 0) AS pending
    `;
    const kept = Number(row?.total ?? 0);
    const pending = Number(row?.pending ?? 0);
    if (kept + pending + bytes > held * MB) {
      return new AppError('RATE_LIMITED',
        `Popsmosis keeps up to ${held} MB of your files at a time and is keeping ${mb(kept + pending)}. `
        + (days > 0 ? `Each file goes ${days} days after you send it; ` : '')
        + 'deleting an old request lets its files go now. Or send this without files.',
        { retryable: false });
    }
  }
  return null;
}

type Db = typeof prisma | TxClient;

/**
 * Why a learner may not send a topic once everyone's together reach TOPICS_PER_DAY (`limit`), with the number. No time:
 * the window rolls, and the page, which reads the allowance again on a 429, says when in the learner's own time zone.
 */
export function siteFullMessage(limit: number): string {
  return `Popsmosis has reached its daily limit of ${limit} new topic${limit === 1 ? '' : 's'}. `
    + 'The limit is shared by everyone, not just you. Please try again later.';
}

/** Why a learner may not send a topic while TOPICS_PER_DAY is 0: an admin paused them, until they say otherwise. */
export const SITE_PAUSED = 'Popsmosis has paused new topics for now. This is for everyone, not just you. Please try again later.';

/**
 * Admins test the site, so they have no topic limit of their own (the owner, Nov 4). Their topics are still taken,
 * so they count toward the site's TOPICS_PER_DAY, and that ceiling holds them as it holds everyone. Read from the
 * account, so a role given or taken away counts from the next request.
 */
async function isAdmin(db: Db, userId: string): Promise<boolean> {
  return (await db.user.findUnique({ where: { id: userId }, select: { role: true } }))?.role === 'ADMIN';
}

const paused = () => new AppError('RATE_LIMITED', SITE_PAUSED, { retryable: false });

/**
 * Why the site takes no topic now, or null: paused (TOPICS_PER_DAY 0), or everyone's topics together have reached it.
 * There is no value for no ceiling (the owner, Nov 4: it guards the bill, so a slip must not lift it): one that never
 * binds is a high one, up to the knob's 100,000.
 */
async function siteRefusal(db: Db, env: NodeJS.ProcessEnv): Promise<AppError | null> {
  const { topicsPerDay } = usageLimits(env);
  if (topicsPerDay === 0) return paused();
  const all = await db.usageEvent.count({ where: { kind: 'TOPIC', createdAt: { gte: new Date(Date.now() - DAY_MS) } } });
  return all >= topicsPerDay ? new AppError('RATE_LIMITED', siteFullMessage(topicsPerDay), { retryable: false }) : null;
}

/** Why the caller may not ask for a topic now, or null: a pause first, before anything is counted, then theirs, then the site's. */
async function topicRefusal(db: Db, userId: string, env: NodeJS.ProcessEnv): Promise<AppError | null> {
  const { topicsPerUserPerDay, topicsPerDay } = usageLimits(env);
  if (topicsPerDay === 0) return paused();
  const since = new Date(Date.now() - DAY_MS);
  const mine = await db.usageEvent.count({ where: { userId, kind: 'TOPIC', createdAt: { gte: since } } });
  if (mine >= topicsPerUserPerDay && !(await isAdmin(db, userId))) {
    return new AppError('RATE_LIMITED', `Max ${topicsPerUserPerDay} topic${topicsPerUserPerDay === 1 ? '' : 's'} per 24 hours`, { retryable: false });
  }
  return siteRefusal(db, env);
}

/**
 * What the caller has left of their topics, for the page to show before they ask (GET /api/questions/allowance).
 * The window rolls: each topic's place comes back 24 hours after it was asked for, the same instant wherever the
 * learner is. So the times are seconds from now, which the page turns into a time on its own clock and in the
 * learner's own time zone.
 *
 * - `perDay`: the learners' limit, an admin's too, though it does not hold them.
 * - `left`: how many the learner may ask for now, by their own limit. Null for an admin, who has none of their own.
 * - `nextInSeconds`: with some left, until one more comes back (the oldest take in the window turns 24 hours old);
 *   with none left, until they may ask again, which is more than one coming back when an admin lowered the limit.
 *   Null when nothing is coming back: none used, a limit of 0, or an admin.
 * - `siteFull`: every learner's topics together have reached TOPICS_PER_DAY, whatever this learner has left, and
 *   `siteOpensInSeconds` is until the site takes one again. Paused (TOPICS_PER_DAY 0), it is full with no time: an
 *   admin decides when it opens.
 * - `sitePerDay`: that limit, TOPICS_PER_DAY, for the page to say; 0: new topics are paused.
 *
 * Lock-free, like topicAllowed: a look, never what holds.
 */
export interface TopicAllowance {
  perDay: number;
  left: number | null;
  nextInSeconds: number | null;
  siteFull: boolean;
  siteOpensInSeconds: number | null;
  sitePerDay: number;
}

export async function topicAllowance(userId: string, env: NodeJS.ProcessEnv = process.env): Promise<TopicAllowance> {
  const { topicsPerUserPerDay: perDay, topicsPerDay } = usageLimits(env);
  const now = Date.now();
  const since = new Date(now - DAY_MS);
  // Until the take at `index` in the window, oldest first, turns 24 hours old; null when there is no such take.
  const placeBackIn = async (who: { userId?: string }, index: number): Promise<number | null> => {
    if (index < 0) return null;
    const take = await prisma.usageEvent.findFirst({
      where: { ...who, kind: 'TOPIC', createdAt: { gte: since } },
      orderBy: { createdAt: 'asc' }, skip: index, select: { createdAt: true },
    });
    return take ? Math.max(0, Math.ceil((take.createdAt.getTime() + DAY_MS - now) / 1000)) : null;
  };

  let left: number | null = null;
  let nextInSeconds: number | null = null;
  if (!(await isAdmin(prisma, userId))) {
    const used = await prisma.usageEvent.count({ where: { userId, kind: 'TOPIC', createdAt: { gte: since } } });
    left = Math.max(0, perDay - used);
    nextInSeconds = await placeBackIn({ userId }, left > 0 ? 0 : used - perDay);
  }

  let siteFull = false;
  let siteOpensInSeconds: number | null = null;
  if (topicsPerDay === 0) siteFull = true; // paused
  else {
    const all = await prisma.usageEvent.count({ where: { kind: 'TOPIC', createdAt: { gte: since } } });
    siteFull = all >= topicsPerDay;
    if (siteFull) siteOpensInSeconds = await placeBackIn({}, all - topicsPerDay);
  }
  return { perDay, left, nextInSeconds, siteFull, siteOpensInSeconds, sitePerDay: topicsPerDay };
}

/** Why the caller may not post a follow-up now, or null. */
async function replyRefusal(db: Db, userId: string, env: NodeJS.ProcessEnv): Promise<AppError | null> {
  const { repliesPerUserPerHour } = usageLimits(env);
  const since = new Date(Date.now() - HOUR_MS);
  const mine = await db.usageEvent.count({ where: { userId, kind: 'REPLY', createdAt: { gte: since } } });
  return mine >= repliesPerUserPerHour
    ? new AppError('RATE_LIMITED', `Max ${repliesPerUserPerHour} replies per hour`, { retryable: false })
    : null;
}

/**
 * A look before the request's body is read (a message may be 11 MB of base64): refuse a caller already at a limit
 * without receiving and parsing what would be refused anyway. Lock-free, so only a first filter: the take below is
 * what holds.
 */
export async function topicAllowed(userId: string, env: NodeJS.ProcessEnv = process.env): Promise<AppError | null> {
  return topicRefusal(prisma, userId, env);
}
export async function replyAllowed(userId: string, env: NodeJS.ProcessEnv = process.env): Promise<AppError | null> {
  return replyRefusal(prisma, userId, env);
}

/**
 * The same look for files, from the body's announced length (Content-Length): a body too long to be text alone carries
 * at least minimumFileBytes() of files, and a learner with less room than that is refused before 11 MB is read.
 */
export async function filesAllowed(userId: string, bodyLength: number, env: NodeJS.ProcessEnv = process.env): Promise<AppError | null> {
  return bytesRefusal(prisma, userId, minimumFileBytes(bodyLength), env);
}

/**
 * Take one topic, with `bytes` of files, from the caller's allowances and the site's: the id of the usage row it wrote,
 * to give back if the topic is not written after all. Throws RATE_LIMITED when one is used up.
 */
export async function takeTopic(userId: string, bytes = 0, env: NodeJS.ProcessEnv = process.env): Promise<string> {
  return runTransaction(async (tx) => {
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${`usage:${userId}`}))`;
    // The learner's own limits, under their lock, and the site's count without its lock: a full site is turned away
    // here, so in a burst only the takes that may still get a place queue on the site's lock.
    const refusal = (await topicRefusal(tx, userId, env)) ?? (await bytesRefusal(tx, userId, bytes, env));
    if (refusal) throw refusal;
    // Then the site's count again under its lock, which is what holds: a count and an insert. Always after the
    // learner's lock, never before, so two takes never each hold the lock the other waits on.
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext('usage:site'))`;
    const full = await siteRefusal(tx, env);
    if (full) throw full;
    return (await tx.usageEvent.create({ data: { userId, kind: 'TOPIC', bytes, filesPending: bytes > 0 }, select: { id: true } })).id;
  }, TAKE_WAITS);
}

/** Take one follow-up, with `bytes` of files, from the caller's allowances: the usage row's id, as takeTopic. */
export async function takeReply(userId: string, bytes = 0, env: NodeJS.ProcessEnv = process.env): Promise<string> {
  return runTransaction(async (tx) => {
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${`usage:${userId}`}))`;
    const refusal = (await replyRefusal(tx, userId, env)) ?? (await bytesRefusal(tx, userId, bytes, env));
    if (refusal) throw refusal;
    return (await tx.usageEvent.create({ data: { userId, kind: 'REPLY', bytes, filesPending: bytes > 0 }, select: { id: true } })).id;
  });
}

/** The take's message is written, its files with it: they count as held from the files now. Best-effort: a take left
 * pending stops counting after IN_FLIGHT_MS. */
export async function filesWritten(usageId: string): Promise<void> {
  await prisma.usageEvent.updateMany({ where: { id: usageId, filesPending: true }, data: { filesPending: false } });
}

/** Give a take back: the request it was for was never written. Best-effort; a row left behind only costs one place. */
export async function giveBack(usageId: string): Promise<void> {
  await prisma.usageEvent.deleteMany({ where: { id: usageId } });
}

/** Let go of the rows no limit reads any more. After a topic is written, outside any transaction. */
export async function pruneUsage(db: typeof prisma = prisma): Promise<number> {
  const { count } = await db.usageEvent.deleteMany({ where: { createdAt: { lt: new Date(Date.now() - KEEP_MS) } } });
  return count;
}
