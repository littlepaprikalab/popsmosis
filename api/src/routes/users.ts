import { FastifyInstance } from 'fastify';
import prisma, { runTransaction } from '../lib/prisma.js';
import { sendError, AppError, isAppError } from '../lib/errors.js';
import { requireAdmin, invalidateUserCache } from '../middleware/auth.js';
import { isAdminEmail } from '../lib/access.js';
import { parseLimit } from '../lib/validation.js';
import { publicUser } from '../lib/serialize.js';

const STATUSES = ['PENDING', 'APPROVED', 'DENIED'] as const;
type Status = (typeof STATUSES)[number];
const isStatus = (v: unknown): v is Status => STATUSES.includes(v as Status);

/**
 * Admin user management.
 *
 * Access: every sign-in creates a user; status PENDING | APPROVED | DENIED
 * decides whether they can use the site. Auto-approval at sign-in comes from
 * ALLOWED_EMAIL_DOMAINS / ALLOWED_EMAILS / ADMIN_EMAILS; the rest are approved
 * or denied here. Roles: USER | ADMIN. The first admin comes from ADMIN_EMAILS
 * (or the user:promote script); after that, admins promote and demote here.
 * You cannot change your own role or status, demote the last admin, or deny an
 * admin (demote first).
 */
export default async function userRoutes(app: FastifyInstance) {
  // GET /api/users?status=&q=&limit= — admin: list users, newest first, optional email/name search
  app.get('/api/users', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { q, limit, status } = request.query as Record<string, string | undefined>;
    if (status !== undefined && !isStatus(status)) {
      return sendError(reply, 'VALIDATION_ERROR', 'status must be PENDING, APPROVED or DENIED');
    }
    const take = parseLimit(limit, 50, 200);
    const search = (q ?? '').trim();
    const users = await prisma.user.findMany({
      where: {
        ...(status ? { status } : {}),
        ...(search
          ? { OR: [{ email: { contains: search, mode: 'insensitive' } }, { name: { contains: search, mode: 'insensitive' } }] }
          : {}),
      },
      // The pending queue reads oldest first (fair); everyone else ADMIN before USER, newest first.
      orderBy: status === 'PENDING' ? [{ createdAt: 'asc' }] : [{ role: 'asc' }, { createdAt: 'desc' }],
      take,
    });
    return { data: users.map(publicUser) };
  });

  // GET /api/users/pending-count — admin: how many are waiting (nav badge). One indexed COUNT.
  app.get('/api/users/pending-count', { preHandler: [requireAdmin] }, async () => {
    const count = await prisma.user.count({ where: { status: 'PENDING' } });
    return { count };
  });

  // PATCH /api/users/:id — admin: { role: 'ADMIN' | 'USER' } and/or { status: 'APPROVED' | 'DENIED' }
  app.patch('/api/users/:id', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { role, status } = (request.body ?? {}) as { role?: unknown; status?: unknown };
    if (role === undefined && status === undefined) {
      return sendError(reply, 'VALIDATION_ERROR', 'Provide role and/or status');
    }
    if (role !== undefined && role !== 'ADMIN' && role !== 'USER') {
      return sendError(reply, 'VALIDATION_ERROR', 'role must be ADMIN or USER');
    }
    if (status !== undefined && status !== 'APPROVED' && status !== 'DENIED') {
      return sendError(reply, 'VALIDATION_ERROR', 'status must be APPROVED or DENIED');
    }
    if (id === request.viewer!.id) {
      if (role === 'USER') return sendError(reply, 'VALIDATION_ERROR', 'You cannot remove your own admin role');
      if (status !== undefined) return sendError(reply, 'VALIDATION_ERROR', 'You cannot change your own access');
    }

    try {
      const updated = await runTransaction(async (tx) => {
        const user = await tx.user.findUnique({ where: { id } });
        if (!user) throw new AppError('NOT_FOUND', 'User not found');
        // ADMIN_EMAILS is reapplied on every request (lib/access.ts), so a demotion
        // or denial it will undo seconds later is refused here rather than
        // answered 200 and quietly reverted. Approving or promoting is still
        // allowed: the config agrees with those.
        if ((role === 'USER' || status === 'DENIED') && isAdminEmail(user.email)) {
          throw new AppError('VALIDATION_ERROR', `${user.email} is listed in ADMIN_EMAILS, which the API reapplies on their next request. Remove the address there and redeploy first.`);
        }
        const nextRole = role === 'ADMIN' || role === 'USER' ? role : user.role;
        // Promoting implies approving: a pending admin makes no sense. Denying an
        // admin is refused rather than silently demoting them.
        const nextStatus = status === 'APPROVED' || status === 'DENIED' ? status : nextRole === 'ADMIN' ? 'APPROVED' : user.status;
        if (nextStatus === 'DENIED' && nextRole === 'ADMIN') {
          throw new AppError('VALIDATION_ERROR', 'Remove the admin role before denying access');
        }
        if (nextRole === user.role && nextStatus === user.status) return user;
        if (nextRole === 'USER' && user.role === 'ADMIN') {
          const admins = await tx.user.count({ where: { role: 'ADMIN' } });
          if (admins <= 1) throw new AppError('VALIDATION_ERROR', 'Cannot demote the last admin');
        }
        return tx.user.update({
          where: { id },
          data: {
            role: nextRole,
            status: nextStatus,
            ...(nextStatus !== user.status ? { reviewedAt: new Date(), reviewedById: request.viewer!.id } : {}),
          },
        });
      });
      invalidateUserCache(id); // takes effect on this instance immediately
      return publicUser(updated);
    } catch (err) {
      if (isAppError(err)) return sendError(reply, err.code, err.message);
      throw err;
    }
  });
}
