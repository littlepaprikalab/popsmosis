/**
 * Who is making a droplet's videos, and how often to try (docs/PLAN.md, section 12).
 *
 * The routine's machine goes away with every run, so the videos of a posted lesson
 * are claimed like a topic is: a run claims them (POST /api/answers/:id/video/claim),
 * which marks them RENDERING and counts an attempt, and renews the claim before every
 * media step. A render that stops reporting for VIDEO_STALE_MINUTES has died with its
 * machine; the video queue (GET /api/videos/queue) hands it to a later run, which
 * starts the next attempt, until VIDEO_MAX_ATTEMPTS. The attempt number is the claim's
 * identity: one agent key runs every routine, so the agent alone cannot tell two of
 * its runs apart.
 */
import { knob } from './knobs.js';

const DEFAULT_VIDEO_MAX_ATTEMPTS = 2;

export function videoMaxAttempts(): number {
  const n = parseInt(knob('VIDEO_MAX_ATTEMPTS') ?? '', 10);
  return Number.isNaN(n) || n <= 0 ? DEFAULT_VIDEO_MAX_ATTEMPTS : n;
}
