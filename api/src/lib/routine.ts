/**
 * Wakes the teacher by firing its Claude Code routine's API trigger, so a new
 * question or a human reply is served within seconds instead of waiting for the
 * next scheduled firing.
 *
 * Fire-and-forget: the write that triggered it never waits on the POST and
 * never fails because of it. A trigger endpoint that is down, slow, or
 * misconfigured costs the teacher its latency, not the user their question.
 *
 * A new topic fires a run of its own, at once, every time (POPSMOSIS_ROUTINE_EVERY_TOPIC,
 * on by default): a run makes one topic at a time, 20 minutes or more each, so a burst
 * of topics debounced into one or two runs waited hours for their turn. Each fire is a
 * cloud session of its own and spends one of the routine's metered runs; the API's
 * limits on topics (TOPICS_PER_DAY, QUESTIONS_PER_USER_PER_DAY) bound how many.
 *
 * Replies, and topics while that knob is off, are debounced per process. The first
 * event fires immediately and opens a cooldown; an event arriving inside that
 * cooldown schedules one trailing fire for when it ends. Both halves earn their keep: the leading fire serves the
 * queue now, and the trailing one covers a write that landed after the run it
 * woke had already drained the queue. Debounce state is per instance, so two
 * API instances can each fire once per window.
 *
 * The trailing fire asks the database whether either queue still holds work
 * before spending a run. A fire is not two GETs — it is a whole cloud session
 * that clones a repo and burns a metered daily routine run, and the run woken
 * at the top of the window has had the entire window to drain both queues. The
 * leading fire never asks: the write that triggered it just put the work there,
 * and that path is the latency this module exists to remove.
 */

import { knob } from './knobs.js';

// The /fire endpoint ships behind a dated beta header. Overridable (on
// /admin/studio, or in the environment) so a header version can be rolled
// forward without a code change.
const DEFAULT_FIRE_BETA_HEADER = 'experimental-cc-routine-2026-04-01';
const FIRE_TIMEOUT_MS = 10_000;
// At least as long as a run takes end to end, or the trailing fire is guaranteed
// to wake a second session while the first is still working — which then mostly
// collects 409s from the one-answer-per-question guard, and leaves the
// queues-already-empty check with nothing it can ever skip. The one measured run
// took 4m29s.
const DEFAULT_DEBOUNCE_SECONDS = 300;
// setTimeout coerces a delay past 2^31-1 ms to 1, so an over-large debounce
// would fire on every write — the exact opposite of what it was set to do.
const MAX_DEBOUNCE_SECONDS = 3600;

export const FIRE_REASON_QUESTION = 'A new question is waiting in the popsmosis question queue.';
export const FIRE_REASON_REPLY = 'A human reply is waiting in the popsmosis reply queue.';

// The shape of the Fastify request logger, which is what every caller passes.
export interface FireLogger {
  info(obj: object, msg: string): void;
  warn(obj: object, msg: string): void;
}

export function parseDebounceMs(raw: string | undefined): number {
  const seconds = parseInt(raw ?? '', 10);
  if (Number.isNaN(seconds) || seconds < 0) return DEFAULT_DEBOUNCE_SECONDS * 1000;
  return Math.min(seconds, MAX_DEBOUNCE_SECONDS) * 1000;
}

export function fireBetaHeader(): string {
  return knob('POPSMOSIS_ROUTINE_BETA') || DEFAULT_FIRE_BETA_HEADER;
}

/** The cooldown in force: read at every write, so a change on /admin/studio applies from the next one. */
export function debounceMs(): number {
  return parseDebounceMs(knob('POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS'));
}

export class RoutineTrigger {
  private lastFiredAt = 0;
  private trailing: NodeJS.Timeout | null = null;
  private inFlight: Promise<void> | null = null;

  constructor(
    private readonly url: string,
    private readonly token: string,
    // A number, or the cooldown in force read at each write (debounceMs()).
    private readonly debounce: number | (() => number),
    private readonly hasWork: () => Promise<boolean>,
  ) {}

  // The POST in flight, or the last one to have run. Tests await it; nothing in
  // the request path does.
  get settled(): Promise<void> {
    return this.inFlight ?? Promise.resolve();
  }

  /** Fire now, whatever the cooldown: a new topic's own run (notifyRoutine). The fire opens a cooldown as any
   * does, so a reply right after it is served by a trailing fire, or by this run's own look at the queues. */
  fireNow(reason: string, log: FireLogger): void {
    this.start(reason, log);
  }

  notify(reason: string, log: FireLogger): void {
    // lastFiredAt is set before fire() reaches its first await, so a burst of
    // writes in one tick sees the updated cooldown and collapses to one fire.
    const waitMs = this.dueInMs();
    if (waitMs <= 0) {
      this.start(reason, log);
      return;
    }
    // A trailing fire is already queued and will drain both queues; it covers
    // this event too.
    if (this.trailing !== null) return;
    // Identity, not remaining time: setTimeout is allowed to fire a hair early,
    // and a "has the window really elapsed?" test would then silently drop a
    // legitimate trailing fire. Only a fire that actually happened in between
    // moves lastFiredAt.
    const firedAtWhenQueued = this.lastFiredAt;
    this.trailing = setTimeout(() => {
      this.trailing = null;
      // A write landing in the same tick ran ahead of this timer and fired.
      // That fire is later than the write which queued this one, so it already
      // serves it; firing again here would just double up.
      if (this.lastFiredAt !== firedAtWhenQueued) return;
      this.startIfWorkPending(reason, log);
    }, waitMs);
    // A wake that has not come due must never hold the process open on SIGTERM.
    this.trailing.unref();
  }

  // Milliseconds left on the cooldown.
  private dueInMs(): number {
    const windowMs = typeof this.debounce === 'function' ? this.debounce() : this.debounce;
    const elapsed = Date.now() - this.lastFiredAt;
    // A clock stepping backwards must not push a trailing fire past one window.
    if (elapsed < 0) return windowMs;
    return windowMs - elapsed;
  }

  private start(reason: string, log: FireLogger): void {
    // Nothing awaits this promise, so a rejection escaping it is an unhandled
    // rejection, which Node answers by killing the API process. Waking the
    // teacher must never be able to do that — not even if the logger throws.
    this.inFlight = this.fire(reason, log).catch(() => {});
  }

  private startIfWorkPending(reason: string, log: FireLogger): void {
    this.inFlight = this.fireIfWorkPending(reason, log).catch(() => {});
  }

  private async fireIfWorkPending(reason: string, log: FireLogger): Promise<void> {
    try {
      if (!(await this.hasWork())) {
        // The run woken at the top of the window already drained both queues.
        log.info({ reason }, 'routine fire skipped, queues already empty');
        return;
      }
    } catch (err) {
      // Fail open. A wasted heartbeat costs one run; a question nobody is woken
      // for waits until the next scheduled sweep.
      log.warn({ err }, 'routine work check failed, firing anyway');
    }
    await this.fire(reason, log);
  }

  private async fire(reason: string, log: FireLogger): Promise<void> {
    this.lastFiredAt = Date.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FIRE_TIMEOUT_MS);
    try {
      const response = await fetch(this.url, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${this.token}`,
          'anthropic-beta': fireBetaHeader(),
          'anthropic-version': '2023-06-01',
          'content-type': 'application/json',
        },
        // Freeform text the routine receives as untrusted data. Never put a
        // question or a reply in it: the teacher reads those from the queues,
        // which keeps both personal data and anything a user typed out of the
        // fire payload entirely.
        body: JSON.stringify({ text: reason }),
        signal: controller.signal,
      });
      if (!response.ok) {
        log.warn({ status: response.status }, 'routine fire rejected');
        return;
      }
      log.info({ reason }, 'routine fired');
    } catch (err) {
      log.warn({ err }, 'routine fire failed');
    } finally {
      clearTimeout(timeout);
    }
  }
}

// Built once, lazily: reading the env at import time would bind before
// dotenv/config has run under some entry points.
let cached: RoutineTrigger | null | undefined;

// Does either queue hold anything the teacher owes a response to? Mirrors what
// GET /api/questions/queue and GET /api/replies/queue select, as existence
// checks: the first rides @@index([status, createdAt]), the second the same
// LATERAL walk the reply queue uses, over @@index([answerId, createdAt]).
// The agent filter is left off on purpose — a thread under another agent's
// answer is still work, and over-firing is the safe direction.
//
// prisma is imported here rather than at the top of the file: prisma.ts throws
// without DATABASE_URL, and this module is unit-tested without one.
export async function queuesHaveWork(): Promise<boolean> {
  const { default: prisma } = await import('./prisma.js');
  const { claimMinutes } = await import('./claim.js');
  const [row] = await prisma.$queryRaw<{ hasWork: boolean }[]>`
    SELECT (
      EXISTS (
        SELECT 1 FROM questions
        WHERE status = 'PENDING'
          AND ("claimedAt" IS NULL OR "claimedAt" < now() - make_interval(mins => ${claimMinutes()}))
      )
      OR EXISTS (
        SELECT 1
        FROM answers a
        JOIN LATERAL (
          SELECT r."userId"
          FROM replies r
          WHERE r."answerId" = a.id
          ORDER BY r."createdAt" DESC, r.id DESC
          LIMIT 1
        ) last ON true
        WHERE last."userId" IS NOT NULL
      )
    ) AS "hasWork"
  `;
  return row?.hasWork === true;
}

export function routineTrigger(): RoutineTrigger | null {
  if (cached === undefined) {
    const url = (process.env.POPSMOSIS_ROUTINE_FIRE_URL ?? '').trim();
    const token = (process.env.POPSMOSIS_ROUTINE_FIRE_TOKEN ?? '').trim();
    // Both unset is the normal deployment without an API-triggered routine:
    // whatever schedule the runner has is then the only thing that wakes it.
    cached = url !== '' && token !== ''
      ? new RoutineTrigger(url, token, debounceMs, queuesHaveWork)
      : null;
  }
  return cached;
}

/** Whether every new topic fires a run of its own (POPSMOSIS_ROUTINE_EVERY_TOPIC), rather than being debounced. */
export function everyTopicFires(): boolean {
  return (knob('POPSMOSIS_ROUTINE_EVERY_TOPIC') || 'on') !== 'off';
}

export function notifyRoutine(reason: string, log: FireLogger): void {
  const trigger = routineTrigger();
  if (!trigger) return;
  if (reason === FIRE_REASON_QUESTION && everyTopicFires()) trigger.fireNow(reason, log);
  else trigger.notify(reason, log);
}
