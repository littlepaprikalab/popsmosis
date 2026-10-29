import { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import prisma from '../lib/prisma.js';
import { Prisma } from '../generated/prisma/client.js';
import { sendError } from '../lib/errors.js';
import { parseLimit } from '../lib/validation.js';
import { dropletTitle } from '../lib/droplet.js';
import { KeyedWindowCounter, WindowCounter, addressGroup, clientAddress } from '../lib/public-limit.js';
import { PLAY_VIDEOS, PlayCounts, PlayVideo, countPlaySql, playEventOf, serializePlays } from '../lib/plays.js';
import { requireAdmin } from '../middleware/auth.js';

export const PLAY_COUNT_URL = '/api/droplets/:id/plays';

// What one address may count. A page view sends at most five steps for each video it plays, and a share and a
// download, so a viewer stays far under the first. The second is what makes a count dear to inflate: one address
// adds at most this many to any one count of a droplet video in an hour, about a class sharing a school's address.
// A play whose start goes over it is not counted at all (the web app sends no more of it), so a class bigger than
// that is counted short, never skewed.
export const PLAYS_PER_ADDRESS_PER_MINUTE = 120;
export const PLAYS_PER_ADDRESS_PER_COUNT_PER_HOUR = 40;
// Every address together, per API instance: apart from the public reads' ceiling, so counting never slows the tab.
export const PLAYS_PER_INSTANCE_PER_MINUTE = 1500;

/**
 * Play counts (lib/plays.ts): one write anyone may send, and the owner's ranking of droplets by finish rate.
 *
 * The write needs no cookie and reads none for who sent it: the web app sends it without one, so a count is never
 * tied to an account. The address is used for the limits above and never written down. Both keep it only as a keyed
 * hash, under a secret drawn afresh each window (lib/public-limit.ts), and the API's own limiter is off for this
 * route, so not even its memory holds the address as it came.
 */
export default async function playRoutes(app: FastifyInstance) {
  const perAddress = new KeyedWindowCounter(PLAYS_PER_ADDRESS_PER_MINUTE, 60_000);
  const perCount = new KeyedWindowCounter(PLAYS_PER_ADDRESS_PER_COUNT_PER_HOUR, 3600_000);
  const ceiling = new WindowCounter(PLAYS_PER_INSTANCE_PER_MINUTE);
  const addressOf = (request: FastifyRequest) => addressGroup(clientAddress(request));
  const refuse = (reply: FastifyReply) => sendError(reply, 'RATE_LIMITED', 'Too many plays counted from here; this one was not');

  // POST /api/droplets/:id/plays — { video: song | lesson | hook, event: started | reached25 | reached50 | reached75 |
  // finished | shared | downloaded }; shared and downloaded are the hook's only. 204 once it is taken, whether or not a
  // count moved (a droplet without that video, or a step past the one before it, moves none), so the answer says
  // nothing about which droplets exist. 429 when a limit is reached: the play is not counted, and the web app sends
  // nothing more for it.
  app.post(PLAY_COUNT_URL, {
    bodyLimit: 1024,
    config: { rateLimit: false },
    // Before the body is read, as the API's own limiter would.
    onRequest: async (request, reply) => {
      if (!perAddress.take([addressOf(request)])) return refuse(reply);
    },
  }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const counted = playEventOf(request.body);
    if (typeof counted === 'string') return sendError(reply, 'VALIDATION_ERROR', counted);
    const { video, event } = counted;
    // Everyone's ceiling first: it bounds how many keys the hourly limit makes (90,000 an hour, under its 100,000), so
    // a flood of new addresses can never fill it and stop the counting for the rest of the hour.
    if (!ceiling.take() || !perCount.take([addressOf(request), id, video, event])) return refuse(reply);
    await prisma.$executeRaw(countPlaySql(id, video, event));
    return reply.status(204).send();
  });

  // GET /api/plays — admin: droplets ranked by how many of one video's plays finished, best first. ?video= song
  // (default), lesson or hook; ?min= the fewest starts a droplet needs to be ranked (default 1); ?limit= (default 50,
  // max 200). `fewer` is how many droplets have plays of that video but fewer starts than min. Hidden droplets are
  // in it, marked: what learners watched on /learn counts too.
  app.get('/api/plays', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { video = 'song', min, limit } = request.query as Record<string, string | undefined>;
    if (!PLAY_VIDEOS.includes(video as PlayVideo)) return sendError(reply, 'VALIDATION_ERROR', `video must be one of ${PLAY_VIDEOS.join(', ')}`);
    const fewest = parseLimit(min, 1, 1_000_000);
    const take = parseLimit(limit, 50, 200);
    const kind = Prisma.raw(`'${video.toUpperCase()}'::"DropletVideo"`); // from PLAY_VIDEOS
    const [rows, [{ fewer }]] = await Promise.all([
      prisma.$queryRaw<(PlayCounts & { id: string; createdAt: Date; hiddenAt: Date | null; bundle: unknown })[]>`
        SELECT a.id, a."createdAt", a."hiddenAt", jsonb_build_object('course', a.bundle->'course', 'topic', a.bundle->'topic') AS bundle,
               p.started, p."reached25", p."reached50", p."reached75", p.finished, p.shared, p.downloaded
        FROM droplet_plays p JOIN answers a ON a.id = p."answerId"
        WHERE p.video = ${kind} AND p.started >= ${fewest}
        ORDER BY p.finished::float8 / p.started DESC, p.started DESC, a.id
        LIMIT ${take}
      `,
      prisma.$queryRaw<{ fewer: number }[]>`
        SELECT count(*)::int AS fewer FROM droplet_plays WHERE video = ${kind} AND started < ${fewest}
      `,
    ]);
    return {
      video,
      min: fewest,
      data: rows.map((r) => ({
        id: r.id,
        ...dropletTitle(r.bundle),
        hidden: !!r.hiddenAt,
        createdAt: r.createdAt.toISOString(),
        plays: serializePlays(r),
      })),
      fewer,
    };
  });
}
