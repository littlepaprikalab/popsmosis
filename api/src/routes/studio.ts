import { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import { sendError } from '../lib/errors.js';
import { requireAdmin, requireUserOrAgent } from '../middleware/auth.js';
import { BRAINS } from '../lib/brains.js';
import {
  ART_CHOICES, DEFAULT_ART, DEFAULT_BRAIN, EFFORTS, parseArt, parseBrainSettings, readStudioSettings, studioForAgent,
  writeStudioSettings,
} from '../lib/studio-settings.js';

const DEFAULTS = { brain: DEFAULT_BRAIN, art: DEFAULT_ART };
const CHOICES = { brain: { default: BRAINS, effort: EFFORTS }, art: ART_CHOICES };

/** An admin, or the teacher with its agent key. */
async function requireAdminOrAgent(request: FastifyRequest, reply: FastifyReply) {
  await requireUserOrAgent(request, reply);
  if (reply.sent || request.agent) return;
  if (request.viewer?.role !== 'ADMIN') return sendError(reply, 'FORBIDDEN', 'Admin access required');
}

/**
 * The studio's settings (lib/studio-settings.ts): admins read and change them on /admin/studio; the studio reads
 * them with its agent key before it claims anything, and from its claims. A change reaches its next run.
 */
export default async function studioRoutes(app: FastifyInstance) {
  // GET /api/studio/settings — admin: { brain, art, updatedAt, defaults, choices }; agent: { brain, art }
  app.get('/api/studio/settings', { preHandler: [requireAdminOrAgent] }, async (request) => {
    if (request.agent) return studioForAgent();
    return { ...(await readStudioSettings()), defaults: DEFAULTS, choices: CHOICES };
  });

  // PUT /api/studio/settings — admin: { brain?: { default?, effort? }, art?: { claude?, meta? } }; what is left out
  // stays as it is.
  app.put('/api/studio/settings', { preHandler: [requireAdmin] }, async (request, reply) => {
    const body = (request.body ?? {}) as { brain?: unknown; art?: unknown };
    if (body.brain === undefined && body.art === undefined) return sendError(reply, 'VALIDATION_ERROR', 'brain or art is required');
    const brain = body.brain === undefined ? {} : parseBrainSettings(body.brain);
    if (brain.error) return sendError(reply, 'VALIDATION_ERROR', brain.error);
    const art = body.art === undefined ? {} : parseArt(body.art);
    if (art.error) return sendError(reply, 'VALIDATION_ERROR', art.error);
    return { ...(await writeStudioSettings({ brain: brain.brain, art: art.art })), defaults: DEFAULTS, choices: CHOICES };
  });
}
