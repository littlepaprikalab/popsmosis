/**
 * Where the studio is while it makes a droplet, and whether the page holds the lesson text back for its videos.
 *
 * The studio reports at the start and the end of each step: the steps running now with how long each has run,
 * the steps whose last try failed, and the bill so far (lib/breakdown.ts). While it writes the lesson the report
 * goes on the question (PUT /api/questions/:id/progress), with whether the videos' steps come after; while it
 * makes the videos it goes on the answer (PUT /api/answers/:id/video/progress), and its bill is the videos' bill
 * so far. The page shows it as the bill of materials filling in, one line per step: done, running or waiting.
 *
 * The lesson text shows as soon as the lesson is posted, which the studio does the moment its fact-check passes:
 * the videos follow above it when they are done, 20 to 30 minutes later, with the bill filling in under the text
 * meanwhile. LESSON_HOLD_MINUTES (0 by default) holds the text back instead, so a learner meets the droplet
 * through its videos rather than through a text the videos then repeat: until the videos are READY or FAILED, a
 * second attempt starts (the first went quiet or was given up), or that many minutes have passed since the post,
 * whichever comes first. Each of these only ever becomes true, so a lesson once shown is never hidden again.
 */
import { breakdownError, serializeBreakdown, type Breakdown } from './breakdown.js';
import { videoStaleMinutes } from './droplet.js';

// The studio's step names are short (bundle, factcheck, timeline...); a bill caps them the same.
const STEP_MAX_LENGTH = 40;
// Two at a time in practice (the art and the audio run side by side); the cap keeps a report small.
const STEPS_MAX = 12;

// The text shows at once: a learner reads the lesson while its videos are made, rather than waiting for them.
const DEFAULT_LESSON_HOLD_MINUTES = 0;

/** How long after the lesson is posted the page waits for its videos before it shows the text anyway; 0, never. */
export function lessonHoldMinutes(): number {
  const n = parseInt(process.env.LESSON_HOLD_MINUTES ?? '', 10);
  return Number.isNaN(n) || n < 0 ? DEFAULT_LESSON_HOLD_MINUTES : n;
}

/**
 * A step running now: when it started by this server's clock, and how long it has run by the same clock when the
 * response was made, which is what a browser counts on from (its own clock may be minutes off this one).
 */
export interface RunningStep {
  step: string;
  startedAt: string;
  seconds: number;
}

/** What the page shows while a droplet is made: every step so far, and the ones running now. */
export interface Progress {
  running: RunningStep[];
  failed: string[]; // steps whose last try failed and that are not running again yet
  videos: boolean; // whether the videos' steps are part of this droplet
  breakdown: Breakdown | null; // the bill so far: the lesson's, then the videos'
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const isStep = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.length <= STEP_MAX_LENGTH;

/**
 * Null when a progress report can be stored, else why not: { running: [{ step, seconds }], failed?: [step],
 * breakdown?, videos? }. `videos` is the lesson's report only: the videos' own report is sent by them.
 */
export function progressError(value: unknown, part: 'lesson' | 'videos'): string | null {
  if (!isObject(value)) return 'the progress must be an object';
  const { running, failed, videos, breakdown } = value;
  if (!Array.isArray(running) || running.length > STEPS_MAX) return `running must be a list of at most ${STEPS_MAX} steps`;
  for (const r of running) {
    if (!isObject(r) || !isStep(r.step)) return `each running step must have a step name of 1 to ${STEP_MAX_LENGTH} characters`;
    if (typeof r.seconds !== 'number' || !Number.isFinite(r.seconds) || r.seconds < 0) {
      return 'each running step must say how many seconds it has run';
    }
  }
  if (failed !== undefined && (!Array.isArray(failed) || failed.length > STEPS_MAX || !failed.every(isStep))) {
    return `failed must be a list of at most ${STEPS_MAX} step names`;
  }
  if (videos !== undefined && (part !== 'lesson' || typeof videos !== 'boolean')) {
    return part === 'lesson' ? 'videos must be true or false' : 'videos is only for the lesson\'s progress';
  }
  return breakdownError(breakdown);
}

/** A report as it is stored: each running step with when it started, so the page can count on from there. */
export function storedProgress(value: Record<string, unknown>, now = new Date()) {
  const running = (value.running as { step: string; seconds: number }[]).map((r) => ({
    step: r.step,
    startedAt: new Date(now.getTime() - r.seconds * 1000).toISOString(),
  }));
  return {
    running,
    failed: Array.isArray(value.failed) ? (value.failed as string[]) : [],
    ...(typeof value.videos === 'boolean' && { videos: value.videos }),
  };
}

function readRunning(raw: unknown, now: Date): RunningStep[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((r): r is { step: string; startedAt: string } => isObject(r) && isStep(r.step) && typeof r.startedAt === 'string'
      && !Number.isNaN(Date.parse(r.startedAt)))
    .map((r) => {
      const started = Date.parse(r.startedAt);
      return { step: r.step, startedAt: new Date(started).toISOString(), seconds: Math.max(0, Math.round((now.getTime() - started) / 100) / 10) };
    });
}

/** A stored report read back, keeping only the fields it knows. */
export function readProgress(raw: unknown, now = new Date()) {
  const p = isObject(raw) ? raw : {};
  return {
    running: readRunning(p.running, now),
    failed: Array.isArray(p.failed) ? p.failed.filter(isStep) : [],
    videos: typeof p.videos === 'boolean' ? p.videos : null, // null: the studio did not say
    breakdown: p.breakdown, // the lesson's bill so far, on the question's report
  };
}

/**
 * Whether the page holds the lesson text back for its videos, `now`. `video` is the answer's videos as the page
 * reads them (a render gone quiet already reads as FAILED); `videosPlanned`, whether the studio said, before it
 * posted the lesson, that the videos come after it.
 *
 * Only a lesson whose studio said so is held, from its post: one posted without that word (a studio older than
 * the reports, a lesson by hand, a report that never got through) is shown at once, as it always was, and stays
 * shown when its videos are claimed a moment later. Every other condition only ever turns false, so a lesson
 * once shown is never hidden again.
 */
export function awaitingVideos(
  answer: { createdAt: Date; videoAttempts: number },
  video: { status: string } | null,
  videosPlanned: boolean,
  now = new Date(),
): boolean {
  if (!videosPlanned) return false;
  // Never past the stale limit either: a render quiet that long reads FAILED and shows the text, and a late
  // heartbeat that makes it RENDERING again must not hide it.
  const hold = Math.min(lessonHoldMinutes(), videoStaleMinutes());
  if (hold <= 0) return false; // even when the database's clock is a moment ahead of this server's
  if (now.getTime() - answer.createdAt.getTime() >= hold * 60_000) return false;
  return !video || (video.status === 'RENDERING' && answer.videoAttempts <= 1);
}

/** The page's view of a topic still being written: its report, if the lease that sent it is still held. */
export function lessonProgress(raw: unknown, leaseLive: boolean, now = new Date()): Progress {
  const p = readProgress(raw, now);
  return {
    running: leaseLive ? p.running : [], // a run that let its lease lapse has died: nothing of it is running
    failed: p.failed,
    videos: p.videos ?? true, // nothing said yet: the whole droplet, as the studio makes it by default
    breakdown: serializeBreakdown(p.breakdown, undefined),
  };
}

/** The page's view of a droplet whose lesson is posted and whose videos are still to come or being made. */
export function videoProgress(
  raw: unknown, rendering: boolean, lessonBreakdown: unknown, videoBreakdown: unknown, now = new Date(),
): Progress {
  const p = readProgress(raw, now);
  return {
    running: rendering ? p.running : [],
    failed: rendering ? p.failed : [],
    videos: true,
    breakdown: serializeBreakdown(lessonBreakdown, videoBreakdown),
  };
}
