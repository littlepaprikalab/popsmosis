/**
 * Reading droplets' views (lib/plays.ts, viewsOf) for the routes that show them beside a droplet's videos: one
 * droplet's page, and a learner's own topics on /learn. The Droplets tab's list reads them in its own statement
 * (viewsSql).
 *
 * Extra to what those routes read: when the counts cannot be read, the droplet goes out without `views`, and the
 * page leaves them out, as it does for an API older than them. Never read for the teacher's queue and claims, which
 * have no use for them.
 */
import type { FastifyBaseLogger } from 'fastify';
import prisma from './prisma.js';
import { VIEWED, viewsOf, type DropletViews } from './plays.js';

/** Each droplet's views, by its answer id (0 for a video never played); null when they could not be read. */
export async function viewsFor(ids: string[], log: FastifyBaseLogger): Promise<Map<string, DropletViews> | null> {
  if (ids.length === 0) return new Map();
  try {
    // The (answerId, video) key, one probe a droplet and video.
    const rows = await prisma.dropletPlays.findMany({
      where: { answerId: { in: ids }, video: { in: VIEWED } },
      select: { answerId: true, video: true, started: true },
    });
    const byDroplet = new Map<string, typeof rows>();
    for (const row of rows) byDroplet.set(row.answerId, [...(byDroplet.get(row.answerId) ?? []), row]);
    return new Map(ids.map((id) => [id, viewsOf(byDroplet.get(id) ?? [])]));
  } catch (err) {
    log.warn({ err }, 'the droplets went out without their views');
    return null;
  }
}
