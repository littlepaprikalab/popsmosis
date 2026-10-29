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

function intEnv(raw: string | undefined, fallback: number): number {
  const n = parseInt(raw ?? '', 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

export function usageLimits(env: NodeJS.ProcessEnv = process.env) {
  return {
    topicsPerUserPerDay: intEnv(knob('QUESTIONS_PER_USER_PER_DAY', env), 10),
    // Site-wide, everyone's topics together: the ceiling on a day's model spend. 0: no ceiling.
    topicsPerDay: intEnv(knob('TOPICS_PER_DAY', env), 100),
    repliesPerUserPerHour: intEnv(knob('REPLIES_PER_USER_PER_HOUR', env), 30),
    // Files, in MB. 0: no limit. A phone's photo of notes is 2 to 4 MB.
    attachmentMbPerUserPerDay: intEnv(knob('ATTACHMENT_MB_PER_USER_PER_DAY', env), 20),
    attachmentMbHeldPerUser: intEnv(knob('ATTACHMENT_MB_HELD_PER_USER', env), 60),
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
    const refusal = (await topicRefusal(tx, userId, env)) ?? (await bytesRefusal(tx, userId, bytes, env));
    if (refusal) throw refusal;
    return (await tx.usageEvent.create({ data: { userId, kind: 'TOPIC', bytes, filesPending: bytes > 0 }, select: { id: true } })).id;
  });
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
