import { FastifyInstance } from 'fastify';
import prisma, { runTransaction } from '../lib/prisma.js';
import { sendError, AppError, isAppError } from '../lib/errors.js';
import { requireAdmin, invalidateUserCache } from '../middleware/auth.js';
import { isAdminEmail } from '../lib/access.js';
import { parseLimit } from '../lib/validation.js';
import { publicUser } from '../lib/serialize.js';
import { decodeCursor, decodeCreatedAtIdCursor, encodeCursor } from '../lib/cursor.js';
import { Prisma } from '../generated/prisma/client.js';
import { UserRole } from '../generated/prisma/enums.js';

// The roles in the order the Users page lists them. Postgres sorts an enum in the order it is declared (USER, then
// ADMIN), so `role: 'desc'` puts admins first, and this is that order.
const ROLES_LISTED: UserRole[] = Object.values(UserRole).reverse();

/**
 * The users after a page's last one, in the list's order (role, then createdAt and id, newest first), from its
 * cursor { role, createdAt, id }: the rest of that role from that point, then every role listed after it. Null when
 * the cursor is missing or malformed. The (createdAt, id) half is the shared one (lib/cursor.ts), so ties on
 * createdAt are neither skipped nor repeated, and a page of one role seeks the (role, createdAt, id) index.
 */
function afterCursor(cursor: string | undefined): Prisma.UserWhereInput | null {
  const sameRole = decodeCreatedAtIdCursor(cursor);
  const role = cursor ? decodeCursor(cursor)?.role : undefined;
  const at = ROLES_LISTED.indexOf(role as UserRole);
  if (!sameRole || at < 0) return null;
  const rest = { role: ROLES_LISTED[at], ...sameRole } as Prisma.UserWhereInput;
  const later = ROLES_LISTED.slice(at + 1);
  return later.length > 0 ? { OR: [rest, { role: { in: later } }] } : rest;
}

/**
 * Admin user management.
 *
 * Anyone with a Google account can sign in and use the site; every sign-in
 * creates a user. Roles: USER | ADMIN. The first admin comes from ADMIN_EMAILS
 * (or the user:promote script); after that, admins promote and demote here.
 * You cannot change your own role or demote the last admin.
 */
export default async function userRoutes(app: FastifyInstance) {
  // GET /api/users?q=&limit=&cursor= — admin: a page of users, admins first, then newest first, optional email/name
  // search. nextCursor brings the next page (null on the last one). adminCount is every admin, whatever the search,
  // so the page knows who is the last admin without loading them all.
  app.get('/api/users', { preHandler: [requireAdmin] }, async (request) => {
    const { q, limit, cursor } = request.query as Record<string, string | undefined>;
    const take = parseLimit(limit, 50, 200);
    const search = (q ?? '').trim();
    const where: Prisma.UserWhereInput[] = [];
    if (search) {
      where.push({ OR: [{ email: { contains: search, mode: 'insensitive' } }, { name: { contains: search, mode: 'insensitive' } }] });
    }
    const after = afterCursor(cursor); // a malformed cursor is ignored: the first page, as the other lists do
    if (after) where.push(after);
    const [rows, adminCount] = await Promise.all([
      prisma.user.findMany({
        where: { AND: where },
        orderBy: [{ role: 'desc' }, { createdAt: 'desc' }, { id: 'desc' }],
        take: take + 1, // one more than the page, to know whether there is a next one
      }),
      prisma.user.count({ where: { role: 'ADMIN' } }),
    ]);
    const page = rows.slice(0, take);
    const last = page[page.length - 1];
    const nextCursor = rows.length > take
      ? encodeCursor({ role: last.role, createdAt: last.createdAt.toISOString(), id: last.id })
      : null;
    return { data: page.map(publicUser), nextCursor, adminCount };
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
