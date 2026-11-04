/**
 * Play counts: how far the plays of each droplet video got, and how often a hook clip was shared and downloaded,
 * so the owner can see which droplets work for students (routes/plays.ts, /admin/plays). The starts of the song and
 * the lesson are also everyone's to see, as each video's views (viewsOf), the way YouTube and TikTok show them.
 *
 * Totals only. The web app sends one event at a time, with no cookie and nothing that names a person, a browser or
 * a device; the API keeps no address, user id or time with the counts. A page view counts each step of each video
 * once (web/src/lib/plays.ts), and the steps go in order: started, then 25%, 50%, 75% and finished. In the table a
 * step is never above the one before it, so a step sent without its play moves nothing, and the finish rate
 * (finished / started) stays a share of real starts.
 */
import { Prisma } from '../generated/prisma/client.js';
import type { DropletVideo } from '../generated/prisma/enums.js';

export const PLAY_VIDEOS = ['song', 'lesson', 'hook'] as const;
export type PlayVideo = (typeof PLAY_VIDEOS)[number];

// How far a play got, in order. `finished` is 95% played, so a clip that loops or a fade at the end still counts.
export const PLAY_STEPS = ['started', 'reached25', 'reached50', 'reached75', 'finished'] as const;
// The hook tile's two buttons.
export const CLIP_ACTIONS = ['shared', 'downloaded'] as const;
export type PlayEvent = (typeof PLAY_STEPS)[number] | (typeof CLIP_ACTIONS)[number];
export const PLAY_EVENTS: readonly PlayEvent[] = [...PLAY_STEPS, ...CLIP_ACTIONS];

// Each video's enum value in the table, and the column on answers that says the droplet has it.
const VIDEO = {
  song: { value: 'SONG', url: 'songVideoUrl' },
  lesson: { value: 'LESSON', url: 'lessonVideoUrl' },
  hook: { value: 'HOOK', url: 'hookVideoUrl' },
} as const;

/** 25%, 50%, 75% or finished: a step that only follows a start. */
export function isLaterStep(event: PlayEvent): boolean {
  return (PLAY_STEPS as readonly string[]).indexOf(event) > 0;
}

/** The event in a request body, or why it is not one. */
export function playEventOf(body: unknown): { video: PlayVideo; event: PlayEvent } | string {
  const { video, event } = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>;
  if (!PLAY_VIDEOS.includes(video as PlayVideo)) return `video must be one of ${PLAY_VIDEOS.join(', ')}`;
  if (!PLAY_EVENTS.includes(event as PlayEvent)) return `event must be one of ${PLAY_EVENTS.join(', ')}`;
  if (CLIP_ACTIONS.includes(event as (typeof CLIP_ACTIONS)[number]) && video !== 'hook') return `${event} is counted for the hook clip only`;
  return { video: video as PlayVideo, event: event as PlayEvent };
}

/**
 * The one statement that counts an event: the number of rows it changed (0 or 1).
 *
 * A start, a share or a download makes the droplet's row for that video if it has none, and only when the droplet's
 * videos are READY and it has that video. Every later step adds to an existing row, and only while it stays at or
 * below the step before it.
 */
export function countPlaySql(answerId: string, video: PlayVideo, event: PlayEvent): Prisma.Sql {
  const { value, url } = VIDEO[video];
  const column = Prisma.raw(`"${event}"`); // from PLAY_EVENTS, never from the request as sent
  const step = (PLAY_STEPS as readonly string[]).indexOf(event);
  if (step > 0) {
    const before = Prisma.raw(`"${PLAY_STEPS[step - 1]}"`);
    return Prisma.sql`
      UPDATE droplet_plays SET ${column} = ${column} + 1
      WHERE "answerId" = ${answerId} AND video = ${value}::"DropletVideo" AND ${column} < ${before}
    `;
  }
  return Prisma.sql`
    INSERT INTO droplet_plays ("answerId", video, ${column})
    SELECT id, ${value}::"DropletVideo", 1 FROM answers
    WHERE id = ${answerId} AND "videoStatus" = 'READY' AND ${Prisma.raw(`"${url}"`)} IS NOT NULL
    ON CONFLICT ("answerId", video) DO UPDATE SET ${column} = droplet_plays.${column} + 1
  `;
}

export type PlayCounts = Record<PlayEvent, number>;

export const NO_PLAYS: PlayCounts = Object.fromEntries(PLAY_EVENTS.map((e) => [e, 0])) as PlayCounts;

/**
 * A droplet's views, shown beside its videos: how many plays of the song and of the lesson started, counted as above
 * (once a page view, an admin's never). Only the starts go out: how far plays got stays the owner's (/admin/plays).
 * The hook clip's are left out; it is a cut of the song, played in the Clips feed.
 */
export type DropletViews = { song: number; lesson: number };

// The videos whose starts are views.
export const VIEWED: DropletVideo[] = ['SONG', 'LESSON'];

// The views' rows in the Droplets tab's list, as a column of its one statement: a jsonb array of { video, started },
// one probe of the (answerId, video) key per card, for a query FROM answers, which it names. In the statement, not a
// read after it, as the list is the busiest public read: no second round trip for every page of cards.
export const viewsSql = Prisma.sql`(
  SELECT coalesce(jsonb_agg(jsonb_build_object('video', p.video, 'started', p.started)), '[]'::jsonb)
  FROM droplet_plays p WHERE p."answerId" = answers.id AND p.video IN ('SONG', 'LESSON')
)`;

/** The views from a droplet's rows of play counts: 0 for a video with none. */
export function viewsOf(rows: unknown): DropletViews {
  const views: DropletViews = { song: 0, lesson: 0 };
  if (!Array.isArray(rows)) return views;
  for (const row of rows as { video?: unknown; started?: unknown }[]) {
    const started = Number(row?.started);
    if (!Number.isFinite(started) || started <= 0) continue;
    if (row.video === 'SONG') views.song = started;
    else if (row.video === 'LESSON') views.lesson = started;
  }
  return views;
}

/** A row's counts, and the share of its starts that finished: null before any start. */
export function serializePlays(row: PlayCounts) {
  const counts = Object.fromEntries(PLAY_EVENTS.map((e) => [e, row[e]])) as PlayCounts;
  return { ...counts, finishRate: row.started > 0 ? row.finished / row.started : null };
}
