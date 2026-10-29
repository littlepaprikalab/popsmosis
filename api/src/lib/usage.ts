/**
 * The limits on what learners may ask for: topics a day each, follow-ups an hour each, and topics a day across the
 * whole site. A topic costs about $0.70 to $1 of model spend and a follow-up a run of the teacher, and anyone with a
 * Google account can sign up, so these are what bound the bill.
 *
 * Counted from usage_events, never from the requests themselves: taking a request back or deleting it (or a
 * follow-up) leaves its row, so it cannot hand back what it used. A transaction-scoped advisory lock per learner makes
 * each count-then-insert one step, so one learner's requests sent in parallel cannot all pass the same count. The
 * site-wide ceiling takes no lock, so as not to queue every learner behind one another's upload: requests from
 * different learners landing in the same instant can pass it by one or two. Rows older than two days are let go
 * (pruneUsage); an account's go with it.
 */
import { AppError } from './errors.js';
import type { TxClient } from './prisma.js';
import type prismaClient from './prisma.js';

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

/** Take one topic from the caller's allowance and the site's, or throw RATE_LIMITED. Inside the topic's transaction. */
export async function takeTopic(tx: TxClient, userId: string, env: NodeJS.ProcessEnv = process.env): Promise<void> {
  const { topicsPerUserPerDay, topicsPerDay } = usageLimits(env);
  await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${`usage-topics:${userId}`}))`;
  const since = new Date(Date.now() - DAY_MS);
  const mine = await tx.usageEvent.count({ where: { userId, kind: 'TOPIC', createdAt: { gte: since } } });
  if (mine >= topicsPerUserPerDay) {
    throw new AppError('RATE_LIMITED', `Max ${topicsPerUserPerDay} questions per 24 hours`, { retryable: false });
  }
  if (topicsPerDay > 0) {
    const all = await tx.usageEvent.count({ where: { kind: 'TOPIC', createdAt: { gte: since } } });
    if (all >= topicsPerDay) {
      throw new AppError('RATE_LIMITED', 'Popsmosis has made all the droplets it can for today. Please try again tomorrow.', { retryable: false });
    }
  }
  await tx.usageEvent.create({ data: { userId, kind: 'TOPIC' } });
}

/** Let go of the rows no limit reads any more. After a topic is written, outside its transaction. */
export async function pruneUsage(db: typeof prismaClient): Promise<number> {
  const { count } = await db.usageEvent.deleteMany({ where: { createdAt: { lt: new Date(Date.now() - KEEP_MS) } } });
  return count;
}

/** Take one follow-up from the caller's hourly allowance, or throw RATE_LIMITED. Inside the follow-up's transaction. */
export async function takeReply(tx: TxClient, userId: string, env: NodeJS.ProcessEnv = process.env): Promise<void> {
  const { repliesPerUserPerHour } = usageLimits(env);
  await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${`usage-replies:${userId}`}))`;
  const since = new Date(Date.now() - HOUR_MS);
  const mine = await tx.usageEvent.count({ where: { userId, kind: 'REPLY', createdAt: { gte: since } } });
  if (mine >= repliesPerUserPerHour) {
    throw new AppError('RATE_LIMITED', `Max ${repliesPerUserPerHour} replies per hour`, { retryable: false });
  }
  await tx.usageEvent.create({ data: { userId, kind: 'REPLY' } });
}
