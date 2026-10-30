import { FastifyInstance } from 'fastify';
import { sendError } from '../lib/errors.js';
import { requireAdmin } from '../middleware/auth.js';
import { currentAnnouncement, parseAnnouncement, writeAnnouncement } from '../lib/announcement.js';

/**
 * The announcement under the nav (lib/announcement.ts): anyone reads it, admins set it on /admin/studio.
 *
 * The read is the same for everyone and comes from memory, so the API's own limiter is off for it: a class on one
 * address, each tab asking once a minute, then spends none of that address's requests on it. It is never stored on
 * the way (no-store): not by the browser, nor by a cache rule at the edge, so nothing holds it longer than the API's
 * own CACHE_MS.
 */
export default async function announcementRoutes(app: FastifyInstance) {
  // GET /api/announcement — anyone: { announcement: { message, tone, updatedAt } | null }
  app.get('/api/announcement', { config: { rateLimit: false } }, async (_request, reply) => {
    reply.header('cache-control', 'no-store');
    return { announcement: await currentAnnouncement() };
  });

  // PUT /api/announcement — admin: { message, tone? }, in place of the one before; { announcement }
  app.put('/api/announcement', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { announcement, error } = parseAnnouncement(request.body);
    if (error) return sendError(reply, 'VALIDATION_ERROR', error);
    return { announcement: await writeAnnouncement(announcement!) };
  });

  // DELETE /api/announcement — admin: take it down; { announcement: null }, whether or not one was up
  app.delete('/api/announcement', { preHandler: [requireAdmin] }, async () => ({ announcement: await writeAnnouncement(null) }));
}
