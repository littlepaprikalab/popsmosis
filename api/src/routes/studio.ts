import { FastifyInstance } from 'fastify';
import { sendError } from '../lib/errors.js';
import { requireAdmin } from '../middleware/auth.js';
import { ART_CHOICES, DEFAULT_ART, parseArt, readStudioSettings, writeArt } from '../lib/studio-settings.js';

/**
 * The studio's settings, for admins on /admin/studio (lib/studio-settings.ts). The studio itself reads them from
 * its claims, so a change here reaches its next run.
 */
export default async function studioRoutes(app: FastifyInstance) {
  // GET /api/studio/settings — admin: { art, defaults, choices, updatedAt }
  app.get('/api/studio/settings', { preHandler: [requireAdmin] }, async () => ({
    ...(await readStudioSettings()),
    defaults: { art: DEFAULT_ART },
    choices: { art: ART_CHOICES },
  }));

  // PUT /api/studio/settings — admin: { art: { claude?, meta? } }; what is left out stays as it is.
  app.put('/api/studio/settings', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { art: rawArt } = (request.body ?? {}) as { art?: unknown };
    const { art, error } = parseArt(rawArt);
    if (error || !art) return sendError(reply, 'VALIDATION_ERROR', error ?? 'art is required');
    return { ...(await writeArt(art)), defaults: { art: DEFAULT_ART }, choices: { art: ART_CHOICES } };
  });
}
