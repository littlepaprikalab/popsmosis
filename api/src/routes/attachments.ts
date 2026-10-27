import { FastifyInstance } from 'fastify';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireUserOrAgent } from '../middleware/auth.js';
import { servedAs, contentDisposition, sniffMimeType } from '../lib/attachments.js';

export default async function attachmentRoutes(app: FastifyInstance) {
  // GET /api/attachments/:id — a file sent with a question or reply, to whoever may read the thread it is in: any
  // signed-in user (the pages show every user's questions) and the teacher. Served as lib/attachments.ts decides, in a
  // sandbox, so a file can only ever be looked at or saved.
  app.get('/api/attachments/:id', { preHandler: [requireUserOrAgent] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const attachment = await prisma.attachment.findUnique({ where: { id }, select: { name: true, data: true } });
    if (!attachment) return sendError(reply, 'NOT_FOUND', 'Attachment not found');
    if (!attachment.data) return sendError(reply, 'GONE', 'This file is past the time attachments are kept');
    const data = Buffer.from(attachment.data);
    const { contentType, disposition } = servedAs(sniffMimeType(data) ?? 'application/octet-stream'); // the bytes decide, whatever the row says
    reply.header('Content-Type', contentType);
    reply.header('Content-Disposition', contentDisposition(disposition, attachment.name));
    reply.header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; sandbox");
    reply.header('Cache-Control', 'private, max-age=86400'); // an id's bytes never change, but they may be let go
    return reply.send(data);
  });
}
