/**
 * The droplet the studio makes from a topic (docs/PLAN.md, section 10): the lesson
 * text, the bundle and fact-check it was written from, and two videos.
 *
 * The API stores the bundle and the fact-check as the studio wrote them and does not
 * re-validate their schema (the studio's pydantic models own that). It checks only
 * what it relies on: that they are objects of a sane size, that the fact-check
 * passed, and, for the reply queue, the few fields it reads back out. Each video's
 * hashtags are copied out of the bundle when it is stored (lib/tags.ts).
 */
import { serializeTags } from './tags.js';

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

// The video fields the page, the agent and the Droplets tab read.
export const VIDEO_FIELDS = ['songVideoUrl', 'lessonVideoUrl', 'songPosterUrl', 'lessonPosterUrl', 'hookVideoUrl',
  'hookPosterUrl'] as const;
export type VideoField = (typeof VIDEO_FIELDS)[number];

type VideoRow = {
  videoStatus: VideoStatus | null;
  songVideoUrl: string | null;
  lessonVideoUrl: string | null;
  songPosterUrl: string | null;
  lessonPosterUrl: string | null;
  hookVideoUrl: string | null;
  hookPosterUrl: string | null;
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
    songPosterUrl: a.songPosterUrl,
    lessonPosterUrl: a.lessonPosterUrl,
    hookVideoUrl: a.hookVideoUrl,
    hookPosterUrl: a.hookPosterUrl,
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
  return {
    course: str(bundle.course),
    topic: str(bundle.topic),
    audience: str(bundle.audience),
    coreIdea: str(bundle.core_idea),
    facts: list(bundle.facts).map((f) => ({ id: str(f.id), text: str(f.text) })),
    misconception: str(bundle.misconception),
    lyrics: list(bundle.lyrics).map((l) => ({ section: str(l.section), text: str(l.text) })),
  };
}

// --- The public Droplets tab (docs/PLAN.md, section 10) ---------------------------

/**
 * Only these fields leave the database for the public endpoints: never the question,
 * its asker, its files or the follow-ups. The bundle is read for its title and lyrics.
 */
export const publicDropletSelect = {
  id: true,
  content: true,
  createdAt: true,
  bundle: true,
  songVideoUrl: true,
  lessonVideoUrl: true,
  songPosterUrl: true,
  lessonPosterUrl: true,
  hookVideoUrl: true,
  hookPosterUrl: true,
  likeCount: true,
  commentCount: true,
  songTags: true,
  lessonTags: true,
} as const;

/** What the Droplets tab may list: the videos are READY and no admin hid it. */
export const publicDropletWhere = { videoStatus: 'READY' as const, hiddenAt: null };

type PublicDropletRow = {
  id: string;
  content: string;
  createdAt: Date;
  bundle: unknown;
  songVideoUrl: string | null;
  lessonVideoUrl: string | null;
  songPosterUrl: string | null;
  lessonPosterUrl: string | null;
  hookVideoUrl: string | null;
  hookPosterUrl: string | null;
  likeCount: number;
  commentCount: number;
  songTags: string[];
  lessonTags: string[];
};

const EXCERPT_LENGTH = 200;
// How much of the lesson the list reads to make an excerpt; markdown only shrinks it.
export const EXCERPT_SOURCE_LENGTH = EXCERPT_LENGTH * 8;

/** The lesson's first lines as plain text, for a card on the list. */
export function excerptOf(markdown: string, max = EXCERPT_LENGTH): string {
  const text = markdown
    .slice(0, max * 8) // EXCERPT_SOURCE_LENGTH for the default max
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1') // links and images
    .replace(/^[ \t|:-]*-{3,}[ \t|:-]*$/gm, ' ') // table and horizontal rules
    .replace(/[#>*_`|]+/g, ' ') // headings, quotes, emphasis, code, table pipes
    .replace(/\s+/g, ' ')
    .trim();
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}

/** "AP Biology: the light reactions", from the bundle; never the learner's own words. */
export function dropletTitle(bundle: unknown): { title: string; course: string; topic: string } {
  const course = isObject(bundle) && typeof bundle.course === 'string' ? bundle.course.trim() : '';
  const topic = isObject(bundle) && typeof bundle.topic === 'string' ? bundle.topic.trim() : '';
  const title = course && topic ? `${course}: ${topic}` : topic || course || 'A droplet';
  return { title, course, topic };
}

function publicVideo(a: PublicDropletRow) {
  return {
    songVideoUrl: a.songVideoUrl,
    lessonVideoUrl: a.lessonVideoUrl,
    songPosterUrl: a.songPosterUrl,
    lessonPosterUrl: a.lessonPosterUrl,
    hookVideoUrl: a.hookVideoUrl,
    hookPosterUrl: a.hookPosterUrl,
  };
}

/**
 * A card on the list: the title, a few lines of the lesson, the videos and their
 * hashtags, and how many people liked it and commented. `liked` is the viewer's own
 * thumbs-up: false for anyone not signed in.
 */
export function serializeDropletCard(a: PublicDropletRow, liked = false) {
  return {
    id: a.id,
    ...dropletTitle(a.bundle),
    excerpt: excerptOf(a.content),
    video: publicVideo(a),
    tags: serializeTags(a), // each video's hashtags; the tab searches by them (lib/tags.ts)
    likeCount: a.likeCount,
    commentCount: a.commentCount,
    liked,
    createdAt: a.createdAt.toISOString(),
  };
}

/** One droplet: the card, the whole lesson and the song's lyrics. */
export function serializeDroplet(a: PublicDropletRow, liked = false) {
  const lyrics = isObject(a.bundle) && Array.isArray(a.bundle.lyrics) ? a.bundle.lyrics.filter(isObject) : [];
  return {
    ...serializeDropletCard(a, liked),
    content: a.content,
    lyrics: lyrics
      .map((l) => ({ section: typeof l.section === 'string' ? l.section : '', text: typeof l.text === 'string' ? l.text : '' }))
      .filter((l) => l.text),
  };
}
