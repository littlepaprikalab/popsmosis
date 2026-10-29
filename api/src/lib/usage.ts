/**
 * The limits on what learners may ask for: topics a day each, follow-ups an hour each, and topics a day across the
 * whole site. A topic costs about $0.70 to $1 of model spend and a follow-up a run of the teacher, and anyone with a
 * Google account can sign up, so these are what bound the bill.
 *
 * Counted from usage_events, never from the requests themselves: taking a request back or deleting it (or a
 * follow-up) leaves its row, so it cannot hand back what it used.
 *
 * Each take is its own short transaction, apart from writing the request: a request may carry 8 MB of files, and an
 * interactive transaction holding a pooled connection (and the learner's lock) for that long would hit Prisma's
 * 5-second limit and starve the pool. A transaction-scoped advisory lock per learner makes count-then-insert one
 * step, so one learner's requests sent in parallel cannot all pass the same count. The site-wide ceiling takes no
 * lock of its own: requests from different learners landing in the same instant can pass it by one or two. When the
 * request then fails to be written, its take is given back. Rows older than two days are let go (pruneUsage); an
 * account's go with it.
 */
import { AppError } from './errors.js';
import prisma, { runTransaction, type TxClient } from './prisma.js';

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const KEEP_MS = 2 * DAY_MS;

function intEnv(raw: string | undefined, fallback: number): number {
  const n = parseInt(raw ?? '', 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

export function usageLimits(env: NodeJS.ProcessEnv = process.env) {
  return {
    topicsPerUserPerDay: intEnv(env.QUESTIONS_PER_USER_PER_DAY, 10),
    // Site-wide, everyone's topics together: the ceiling on a day's model spend. 0: no ceiling.
    topicsPerDay: intEnv(env.TOPICS_PER_DAY, 100),
    repliesPerUserPerHour: intEnv(env.REPLIES_PER_USER_PER_HOUR, 30),
  };
}

type Db = typeof prisma | TxClient;

/** Why the caller may not ask for a topic now, or null. */
async function topicRefusal(db: Db, userId: string, env: NodeJS.ProcessEnv): Promise<AppError | null> {
  const { topicsPerUserPerDay, topicsPerDay } = usageLimits(env);
  const since = new Date(Date.now() - DAY_MS);
  const mine = await db.usageEvent.count({ where: { userId, kind: 'TOPIC', createdAt: { gte: since } } });
  if (mine >= topicsPerUserPerDay) {
    return new AppError('RATE_LIMITED', `Max ${topicsPerUserPerDay} questions per 24 hours`, { retryable: false });
  }
  if (topicsPerDay > 0) {
    const all = await db.usageEvent.count({ where: { kind: 'TOPIC', createdAt: { gte: since } } });
    if (all >= topicsPerDay) {
      return new AppError('RATE_LIMITED', 'Popsmosis has made all the droplets it can for today. Please try again tomorrow.', { retryable: false });
    }
  }
  return null;
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
 * Take one topic from the caller's allowance and the site's: the id of the usage row it wrote, to give back if the
 * topic is not written after all. Throws RATE_LIMITED when either is used up.
 */
export async function takeTopic(userId: string, env: NodeJS.ProcessEnv = process.env): Promise<string> {
  return runTransaction(async (tx) => {
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${`usage-topics:${userId}`}))`;
    const refusal = await topicRefusal(tx, userId, env);
    if (refusal) throw refusal;
    return (await tx.usageEvent.create({ data: { userId, kind: 'TOPIC' }, select: { id: true } })).id;
  });
}

/** Take one follow-up from the caller's hourly allowance: the usage row's id, as takeTopic. Throws RATE_LIMITED. */
export async function takeReply(userId: string, env: NodeJS.ProcessEnv = process.env): Promise<string> {
  return runTransaction(async (tx) => {
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${`usage-replies:${userId}`}))`;
    const refusal = await replyRefusal(tx, userId, env);
    if (refusal) throw refusal;
    return (await tx.usageEvent.create({ data: { userId, kind: 'REPLY' }, select: { id: true } })).id;
  });
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
