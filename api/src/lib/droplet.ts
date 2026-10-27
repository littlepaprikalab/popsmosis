/**
 * The droplet the studio makes from a topic (docs/PLAN.md, section 10): the lesson
 * text, the bundle and fact-check it was written from, and two videos.
 *
 * The API stores the bundle and the fact-check as the studio wrote them and does not
 * re-validate their schema (the studio's pydantic models own that). It checks only
 * what it relies on: that they are objects of a sane size, that the fact-check
 * passed, and, for the reply queue, the few fields it reads back out.
 */

// Tens of KB in practice; the cap keeps one answer from filling a list response or a row.
export const DROPLET_JSON_MAX_BYTES = 256 * 1024;
export const VIDEO_URL_MAX_LENGTH = 2000;
export const VIDEO_ERROR_MAX_LENGTH = 500;
export const FAILED_REASON_MAX_LENGTH = 500;
export const VIDEO_STATUSES = ['RENDERING', 'READY', 'FAILED'] as const;
export type VideoStatus = (typeof VIDEO_STATUSES)[number];

// A render that has not reported for this long has died with its machine, so the page
// says it failed instead of showing "rendering" for ever.
const DEFAULT_VIDEO_STALE_MINUTES = 60;
export const STALE_VIDEO_ERROR = 'The videos stopped rendering before they were finished.';

export function videoStaleMinutes(): number {
  const n = parseInt(process.env.VIDEO_STALE_MINUTES ?? '', 10);
  return Number.isNaN(n) || n <= 0 ? DEFAULT_VIDEO_STALE_MINUTES : n;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Null when the bundle and fact-check sent with a lesson can be stored, else why not. */
export function dropletJsonError(bundle: unknown, factcheck: unknown): string | null {
  if (bundle === undefined && factcheck === undefined) return null; // a text-only lesson
  if (!isObject(bundle) || !isObject(factcheck)) return 'bundle and factcheck must be sent together, as JSON objects';
  for (const [name, value] of [['bundle', bundle], ['factcheck', factcheck]] as const) {
    if (Buffer.byteLength(JSON.stringify(value)) > DROPLET_JSON_MAX_BYTES) {
      return `${name} must be ${DROPLET_JSON_MAX_BYTES / 1024} KB or less`;
    }
  }
  // The fact-check is the gate: nothing is posted that has not passed it (docs/PLAN.md, step 2).
  if (factcheck.status !== 'passed') return 'factcheck.status must be "passed"; a lesson that failed its fact-check is not posted';
  return null;
}

/** An absolute http(s) URL, or why it is not one. */
export function videoUrlError(value: unknown, field: string): string | null {
  if (typeof value !== 'string' || value.length === 0) return `${field} must be a URL`;
  if (value.length > VIDEO_URL_MAX_LENGTH) return `${field} must be ${VIDEO_URL_MAX_LENGTH} characters or less`;
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' || url.protocol === 'http:') return null;
  } catch {
    // falls through
  }
  return `${field} must be an http(s) URL`;
}

type VideoRow = {
  videoStatus: VideoStatus | null;
  songVideoUrl: string | null;
  lessonVideoUrl: string | null;
  videoError: string | null;
  videoUpdatedAt: Date | null;
};

/** The droplet's videos as the page and the agent read them; null for a text-only droplet. */
export function serializeVideo(a: VideoRow, now: Date = new Date()) {
  if (!a.videoStatus) return null;
  const stale = a.videoStatus === 'RENDERING' && !!a.videoUpdatedAt
    && now.getTime() - a.videoUpdatedAt.getTime() > videoStaleMinutes() * 60_000;
  return {
    status: stale ? ('FAILED' as const) : a.videoStatus,
    songVideoUrl: a.songVideoUrl,
    lessonVideoUrl: a.lessonVideoUrl,
    error: stale ? STALE_VIDEO_ERROR : a.videoError,
    updatedAt: a.videoUpdatedAt?.toISOString() ?? null,
  };
}

/**
 * What a follow-up is answered from: the bundle without its scenes, direction and
 * narration, so a thread stays a couple of thousand tokens. Whichever model answers
 * reads the same context. Null when the lesson was posted without a bundle.
 */
export function followUpContext(bundle: unknown) {
  if (!isObject(bundle)) return null;
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  const list = (v: unknown) => (Array.isArray(v) ? v.filter(isObject) : []);
  const frq = isObject(bundle.frq) ? bundle.frq : {};
  return {
    course: str(bundle.course),
    topic: str(bundle.topic),
    coreIdea: str(bundle.core_idea),
    facts: list(bundle.facts).map((f) => ({ id: str(f.id), text: str(f.text) })),
    misconception: str(bundle.misconception),
    frq: {
      prompt: str(frq.prompt),
      modelAnswerSteps: Array.isArray(frq.model_answer_steps) ? frq.model_answer_steps.filter((s) => typeof s === 'string') : [],
      twist: str(frq.twist),
    },
    lyrics: list(bundle.lyrics).map((l) => ({ section: str(l.section), text: str(l.text) })),
  };
}
