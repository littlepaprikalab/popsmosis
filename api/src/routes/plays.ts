import { FastifyInstance, FastifyReply } from 'fastify';
import prisma from '../lib/prisma.js';
import { Prisma } from '../generated/prisma/client.js';
import { isForeignKeyViolation, sendError } from '../lib/errors.js';
import { parseLimit } from '../lib/validation.js';
import { dropletTitle } from '../lib/droplet.js';
import { KeyedWindowCounter, WindowCounter, addressGroup, clientAddress } from '../lib/public-limit.js';
import { NO_PLAYS, PLAY_VIDEOS, PlayCounts, PlayVideo, countPlaySql, isLaterStep, playEventOf, serializePlays } from '../lib/plays.js';
import { requireAdmin } from '../middleware/auth.js';

export const PLAY_COUNT_URL = '/api/droplets/:id/plays';

// The limits come in two lanes. A start (and a hook share or download) is what they refuse first: a play whose start
// is refused is not counted at all, since the web app then sends no more of it, so a crowd past a limit is counted
// short, not skewed. The steps after a counted start (25%, 50%, 75%, finished) have budgets of their own, four times
// the starts' since a play has at most four: a burst of new starts (a class swiping through the Clips feed behind
// one address, a surge across the site) never cuts off the plays already counted halfway, which would drag their
// finish rate down. A request that is not a count at all is held to the starts' budget.
//
// Per address, each minute. A page view sends at most five steps for each video it plays, and a share and a
// download, so a viewer stays far under both.
export const STARTS_PER_ADDRESS_PER_MINUTE = 120;
export const STEPS_PER_ADDRESS_PER_MINUTE = 480;
// What makes a count dear to inflate: one address adds at most this many to any one count of a droplet video in an
// hour, about a class sharing a school's address. A step is also never above the one before it (lib/plays.ts).
export const PLAYS_PER_ADDRESS_PER_COUNT_PER_HOUR = 40;
// Every address together, per API instance: apart from the public reads' ceiling, so counting never slows the tab.
export const STARTS_PER_INSTANCE_PER_MINUTE = 1500;
export const STEPS_PER_INSTANCE_PER_MINUTE = 6000;
// The hourly limit's keys, at most: the two ceilings let through 450,000 an hour, and a key takes about 90 bytes, so
// 27 MB when full (measured). Past it a new key is refused until the hour ends, which only a flood at both ceilings
// for most of an hour reaches.
const COUNT_KEYS_PER_HOUR = 300_000;

/**
 * Play counts (lib/plays.ts): one write anyone may send, and the owner's ranking of droplets by finish rate.
 *
 * The write needs no cookie and reads none for who sent it: the web app sends it without one, so a count is never
 * tied to an account. The address is used for the limits above and never written down. They keep it only as a keyed
 * hash, under a secret drawn afresh each window (lib/public-limit.ts), and the API's own limiter is off for this
 * route, so not even its memory holds the address as it came.
 */
export default async function playRoutes(app: FastifyInstance) {
  const lanes = {
    start: { perAddress: new KeyedWindowCounter(STARTS_PER_ADDRESS_PER_MINUTE, 60_000), ceiling: new WindowCounter(STARTS_PER_INSTANCE_PER_MINUTE) },
    step: { perAddress: new KeyedWindowCounter(STEPS_PER_ADDRESS_PER_MINUTE, 60_000), ceiling: new WindowCounter(STEPS_PER_INSTANCE_PER_MINUTE) },
  };
  const perCount = new KeyedWindowCounter(PLAYS_PER_ADDRESS_PER_COUNT_PER_HOUR, 3600_000, COUNT_KEYS_PER_HOUR);
  const refuse = (reply: FastifyReply) => sendError(reply, 'RATE_LIMITED', 'Too many plays counted from here; this one was not');

  // POST /api/droplets/:id/plays — { video: song | lesson | hook, event: started | reached25 | reached50 | reached75 |
  // finished | shared | downloaded }; shared and downloaded are the hook's only. 204 once it is taken, whether or not a
  // count moved (a droplet without that video, or a step past the one before it, moves none), so the answer says
  // nothing about which droplets exist. 429 when a limit is reached: the count was not made, and the web app sends
  // nothing more for that play.
  app.post(PLAY_COUNT_URL, { bodyLimit: 1024, config: { rateLimit: false } }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const address = addressGroup(clientAddress(request));
    const counted = playEventOf(request.body);
    if (typeof counted === 'string') {
      return lanes.start.perAddress.take([address]) ? sendError(reply, 'VALIDATION_ERROR', counted) : refuse(reply);
    }
    const { video, event } = counted;
    const lane = isLaterStep(event) ? lanes.step : lanes.start;
    // The address's own limit, then everyone's ceiling, then the hourly one: the ceilings bound how many keys the
    // hourly limit makes, so a flood of new addresses cannot fill it before most of an hour at both ceilings.
    if (!lane.perAddress.take([address]) || !lane.ceiling.take() || !perCount.take([address, id, video, event])) return refuse(reply);
    try {
      await prisma.$executeRaw(countPlaySql(id, video, event));
    } catch (err) {
      // The droplet was deleted while this was counted: nothing to count, as for one that never was.
      if (!isForeignKeyViolation(err)) throw err;
    }
    return reply.status(204).send();
  });

  // GET /api/plays — admin: droplets ranked by how many of one video's plays finished, best first. ?video= song
  // (default), lesson or hook; ?min= the fewest starts a droplet needs to be ranked (default 1); ?limit= (default 50,
  // max 200). `fewer` is how many droplets have plays of that video but fewer starts than min. Hidden droplets are
  // in it, marked: what learners watched on /learn counts too. The page is chosen from the counts alone and the
  // droplets joined after, so only the bundles shown are read: a bundle can be hundreds of KB. With ?totals=1,
  // `totals` too: each video's counts over every droplet, whatever min and limit, for the PLAYS page's drop-off curve.
  // Only when asked, since it sums every row (about 90 ms at 300,000 droplets) and the page needs it once, not on
  // every switch of video or minimum. Extra to the ranking: when it fails, the ranking goes out without it.
  app.get('/api/plays', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { video = 'song', min, limit, totals: withTotals } = request.query as Record<string, string | undefined>;
    if (!PLAY_VIDEOS.includes(video as PlayVideo)) return sendError(reply, 'VALIDATION_ERROR', `video must be one of ${PLAY_VIDEOS.join(', ')}`);
    const fewest = parseLimit(min, 1, 1_000_000);
    const take = parseLimit(limit, 50, 200);
    const kind = Prisma.raw(`'${video.toUpperCase()}'::"DropletVideo"`); // from PLAY_VIDEOS
    const [rows, [{ fewer }], sums] = await Promise.all([
      prisma.$queryRaw<(PlayCounts & { id: string; createdAt: Date; hiddenAt: Date | null; bundle: unknown })[]>`
        SELECT a.id, a."createdAt", a."hiddenAt", jsonb_build_object('course', a.bundle->'course', 'topic', a.bundle->'topic') AS bundle,
               p.started, p."reached25", p."reached50", p."reached75", p.finished, p.shared, p.downloaded
        FROM (
          SELECT * FROM droplet_plays WHERE video = ${kind} AND started >= ${fewest}
          ORDER BY finished::float8 / started DESC, started DESC, "answerId"
          LIMIT ${take}
        ) p JOIN answers a ON a.id = p."answerId"
        ORDER BY p.finished::float8 / p.started DESC, p.started DESC, a.id
      `,
      prisma.$queryRaw<{ fewer: number }[]>`
        SELECT count(*)::int AS fewer FROM droplet_plays WHERE video = ${kind} AND started < ${fewest}
      `,
      // float8: a sum of ints is a bigint, which comes back as a BigInt; a float8 holds any count exactly to 2^53.
      withTotals === '1'
        ? prisma.$queryRaw<(PlayCounts & { video: string })[]>`
            SELECT video::text, sum(started)::float8 AS started, sum("reached25")::float8 AS "reached25",
                   sum("reached50")::float8 AS "reached50", sum("reached75")::float8 AS "reached75",
                   sum(finished)::float8 AS finished, sum(shared)::float8 AS shared, sum(downloaded)::float8 AS downloaded
            FROM droplet_plays GROUP BY video
          `.catch((err: unknown) => {
            request.log.warn({ err }, 'the plays ranking went out without its totals');
            return null;
          })
        : null,
    ]);
    const totalOf = (v: PlayVideo) => serializePlays(sums?.find((s) => s.video === v.toUpperCase()) ?? NO_PLAYS);
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
      ...(sums && { totals: Object.fromEntries(PLAY_VIDEOS.map((v) => [v, totalOf(v)])) as Record<PlayVideo, ReturnType<typeof serializePlays>> }),
    };
  });
}
