import { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import { sendError } from '../lib/errors.js';
import { requireAdmin, requireUserOrAgent } from '../middleware/auth.js';
import { BRAINS } from '../lib/brains.js';
import {
  ART_CHOICES, DEFAULT_ART, DEFAULT_BRAIN, DEFAULT_SONG, EFFORTS, SONG_PROVIDERS, parseArt, parseBrainSettings,
  parseSongSettings, readStudioSettings, reloadKnobs, studioForAgent, writeStudioSettings,
} from '../lib/studio-settings.js';
import { KNOBS, KNOB_NAMES, parseKnobs, type ApiKnob, type KnobValue, type Knobs } from '../lib/knobs.js';
import { claimMinutes } from '../lib/claim.js';
import { videoStaleMinutes } from '../lib/droplet.js';
import { commentsPerUserPerHour } from '../lib/engagement.js';
import { lessonHoldMinutes } from '../lib/progress.js';
import { debounceMs, fireBetaHeader } from '../lib/routine.js';
import { usageLimits } from '../lib/usage.js';
import { videoMaxAttempts } from '../lib/video-claim.js';

const DEFAULTS = { brain: DEFAULT_BRAIN, song: DEFAULT_SONG, art: DEFAULT_ART };
const CHOICES = { brain: { default: BRAINS, effort: EFFORTS }, song: { provider: SONG_PROVIDERS }, art: ART_CHOICES };

// Each of the API's knobs as the code that reads it has it now: the value set here, else the environment's, else
// the default, after that code's own reading of a value it cannot use.
const IN_FORCE: Record<ApiKnob, () => KnobValue> = {
  QUESTIONS_PER_USER_PER_DAY: () => usageLimits().topicsPerUserPerDay,
  TOPICS_PER_DAY: () => usageLimits().topicsPerDay,
  REPLIES_PER_USER_PER_HOUR: () => usageLimits().repliesPerUserPerHour,
  DROPLET_COMMENTS_PER_USER_PER_HOUR: commentsPerUserPerHour,
  QUESTION_CLAIM_MINUTES: claimMinutes,
  VIDEO_STALE_MINUTES: videoStaleMinutes,
  LESSON_HOLD_MINUTES: lessonHoldMinutes,
  VIDEO_MAX_ATTEMPTS: videoMaxAttempts,
  POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS: () => debounceMs() / 1000,
  POPSMOSIS_ROUTINE_BETA: fireBetaHeader,
};

/**
 * Every knob for the admin page: what it takes, its default, and the value set here (null: none). The API's own
 * also say what its environment has and what is in force; the studio's environment is on the teacher's machine.
 */
function knobsForAdmin(set: Knobs) {
  return Object.fromEntries(KNOB_NAMES.map((name) => {
    // A text knob's pattern is the API's to check (its rule says it in words), and JSON has no regular expressions.
    const spec = Object.fromEntries(Object.entries(KNOBS[name]).filter(([key]) => key !== 'pattern'));
    const value = set[name] ?? null;
    if (KNOBS[name].where !== 'api') return [name, { ...spec, value }];
    return [name, { ...spec, value, env: process.env[name] ?? null, inForce: IN_FORCE[name as ApiKnob]() }];
  }));
}

function forAdmin(saved: Awaited<ReturnType<typeof readStudioSettings>>) {
  return { ...saved, knobs: knobsForAdmin(saved.knobs), defaults: DEFAULTS, choices: CHOICES };
}

/** An admin, or the teacher with its agent key. */
async function requireAdminOrAgent(request: FastifyRequest, reply: FastifyReply) {
  await requireUserOrAgent(request, reply);
  if (reply.sent || request.agent) return;
  if (request.viewer?.role !== 'ADMIN') return sendError(reply, 'FORBIDDEN', 'Admin access required');
}

/**
 * The studio's settings (lib/studio-settings.ts) and the knobs (lib/knobs.ts): admins read and change them on
 * /admin/studio; the studio reads its own with its agent key before it claims anything, and from its claims. A
 * change to the studio's reaches its next run; a change to the API's applies at once.
 */
export default async function studioRoutes(app: FastifyInstance) {
  // GET /api/studio/settings — admin: { brain, song, art, knobs, updatedAt, defaults, choices };
  // agent: { brain, song, art, knobs }, where knobs are the studio's set here, by name
  app.get('/api/studio/settings', { preHandler: [requireAdminOrAgent] }, async (request) => {
    if (request.agent) return studioForAgent();
    await reloadKnobs(); // what is in force on this instance, as of what is stored now
    return forAdmin(await readStudioSettings());
  });

  // PUT /api/studio/settings — admin: { brain?: { default?, effort? }, song?: { provider }, art?: { claude?, meta? },
  // knobs?: { NAME: value | null } }; what is left out stays as it is, and a knob set to null is the environment's again.
  app.put('/api/studio/settings', { preHandler: [requireAdmin] }, async (request, reply) => {
    const body = (request.body ?? {}) as { brain?: unknown; song?: unknown; art?: unknown; knobs?: unknown };
    if (body.brain === undefined && body.song === undefined && body.art === undefined && body.knobs === undefined) {
      return sendError(reply, 'VALIDATION_ERROR', 'brain, song, art or knobs is required');
    }
    const brain = body.brain === undefined ? {} : parseBrainSettings(body.brain);
    if (brain.error) return sendError(reply, 'VALIDATION_ERROR', brain.error);
    const song = body.song === undefined ? {} : parseSongSettings(body.song);
    if (song.error) return sendError(reply, 'VALIDATION_ERROR', song.error);
    const art = body.art === undefined ? {} : parseArt(body.art);
    if (art.error) return sendError(reply, 'VALIDATION_ERROR', art.error);
    const knobs = body.knobs === undefined ? {} : parseKnobs(body.knobs);
    if (knobs.error) return sendError(reply, 'VALIDATION_ERROR', knobs.error);
    return forAdmin(await writeStudioSettings({ brain: brain.brain, song: song.song, art: art.art, knobs: knobs.knobs }));
  });
}
