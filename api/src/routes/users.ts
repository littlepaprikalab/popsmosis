import { FastifyInstance } from 'fastify';
import prisma, { runTransaction } from '../lib/prisma.js';
import { sendError, AppError, isAppError } from '../lib/errors.js';
import { requireAdmin, invalidateUserCache } from '../middleware/auth.js';
import { isAdminEmail } from '../lib/access.js';
import { parseLimit } from '../lib/validation.js';
import { publicUser } from '../lib/serialize.js';

/**
 * Admin user management.
 *
 * Anyone with a Google account can sign in and use the site; every sign-in
 * creates a user. Roles: USER | ADMIN. The first admin comes from ADMIN_EMAILS
 * (or the user:promote script); after that, admins promote and demote here.
 * You cannot change your own role or demote the last admin.
 */
export default async function userRoutes(app: FastifyInstance) {
  // GET /api/users?q=&limit= — admin: list users, ADMIN before USER, newest first, optional email/name search
  app.get('/api/users', { preHandler: [requireAdmin] }, async (request) => {
    const { q, limit } = request.query as Record<string, string | undefined>;
    const take = parseLimit(limit, 50, 200);
    const search = (q ?? '').trim();
    const users = await prisma.user.findMany({
      where: search
        ? { OR: [{ email: { contains: search, mode: 'insensitive' } }, { name: { contains: search, mode: 'insensitive' } }] }
        : {},
      orderBy: [{ role: 'asc' }, { createdAt: 'desc' }],
      take,
    });
    return { data: users.map(publicUser) };
  });

  // PATCH /api/users/:id — admin: { role: 'ADMIN' | 'USER' }
  app.patch('/api/users/:id', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { role } = (request.body ?? {}) as { role?: unknown };
    if (role !== 'ADMIN' && role !== 'USER') {
      return sendError(reply, 'VALIDATION_ERROR', 'role must be ADMIN or USER');
    }
    if (id === request.viewer!.id && role === 'USER') {
      return sendError(reply, 'VALIDATION_ERROR', 'You cannot remove your own admin role');
    }

    try {
      const updated = await runTransaction(async (tx) => {
        const user = await tx.user.findUnique({ where: { id } });
        if (!user) throw new AppError('NOT_FOUND', 'User not found');
        if (role === user.role) return user;
        // ADMIN_EMAILS is reapplied on every request (lib/access.ts), so a demotion
        // it will undo seconds later is refused here rather than answered 200 and
        // quietly reverted.
        if (role === 'USER' && isAdminEmail(user.email)) {
          throw new AppError('VALIDATION_ERROR', `${user.email} is listed in ADMIN_EMAILS, which the API reapplies on their next request. Remove the address there and redeploy first.`);
        }
        if (role === 'USER') {
          const admins = await tx.user.count({ where: { role: 'ADMIN' } });
          if (admins <= 1) throw new AppError('VALIDATION_ERROR', 'Cannot demote the last admin');
        }
        return tx.user.update({ where: { id }, data: { role } });
      });
      invalidateUserCache(id); // takes effect on this instance immediately
      return publicUser(updated);
    } catch (err) {
      if (isAppError(err)) return sendError(reply, err.code, err.message);
      throw err;
    }
  });
}
