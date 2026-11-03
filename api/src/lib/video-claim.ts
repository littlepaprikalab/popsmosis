/**
 * Who is making a droplet's videos, and how often to try (docs/PLAN.md, section 12).
 *
 * The routine's machine goes away with every run, so the videos of a posted lesson
 * are claimed like a topic is: a run claims them (POST /api/answers/:id/video/claim),
 * which marks them RENDERING and counts an attempt, and renews the claim before every
 * media step. A render that stops reporting for VIDEO_STALE_MINUTES has died with its
 * machine; the video queue (GET /api/videos/queue) hands it to a later run, which
 * starts the next attempt, until VIDEO_MAX_ATTEMPTS. One that goes quiet on its last
 * attempt is marked FAILED there, so the droplet stops waiting on it. The attempt
 * number is the claim's identity: one agent key runs every routine, so the agent
 * alone cannot tell two of its runs apart.
 *
 * The queue also hands on a lesson whose videos were never claimed at all: the studio
 * claims them the moment it posts the lesson, and the operator again when it starts
 * them, so videos still unclaimed VIDEO_STALE_MINUTES after the post had both claims
 * fail (an API blip), or the machine died in between.
 */
import { knob } from './knobs.js';

const DEFAULT_VIDEO_MAX_ATTEMPTS = 2;

// How long after the stale time a lesson whose videos were never claimed is still handed on: a day. A lesson
// posted while the videos were turned off has a bundle and no video status too, and turning them on again must
// not start the videos of every such lesson on record, at the cost of a song and its art each.
export const UNCLAIMED_VIDEOS_HOURS = 24;

export function videoMaxAttempts(): number {
  const n = parseInt(knob('VIDEO_MAX_ATTEMPTS') ?? '', 10);
  return Number.isNaN(n) || n <= 0 ? DEFAULT_VIDEO_MAX_ATTEMPTS : n;
}

/**
 * Whether the studio's videos are turned off (STUDIO_VIDEOS on /admin/studio, else the API's environment), read as
 * the studio reads it (studio/videos.py): then a lesson whose videos were never claimed is text-only, not stranded.
 */
export function videosTurnedOff(): boolean {
  return ['off', '0', 'false', 'no'].includes((knob('STUDIO_VIDEOS') ?? '').trim().toLowerCase());
}
