import { FastifyInstance } from 'fastify';
import { OAuth2Client } from 'google-auth-library';
import prisma, { runTransaction } from '../lib/prisma.js';
import { sendError, AppError, isAppError, isNotFound } from '../lib/errors.js';
import { requireUser, invalidateUserCache } from '../middleware/auth.js';
import { detachQuestions } from '../lib/privacy.js';
import { termsAccepted } from '../lib/terms.js';
import { setSessionCookie, clearSessionCookie, SESSION_EXPIRES_IN } from '../lib/session.js';
import { isAdminEmail } from '../lib/access.js';
import { publicUser } from '../lib/serialize.js';

export default async function authRoutes(app: FastifyInstance) {
  const googleClient = new OAuth2Client(
    process.env.GOOGLE_CLIENT_ID,
    process.env.GOOGLE_CLIENT_SECRET,
    process.env.GOOGLE_REDIRECT_URI,
  );

  // POST /api/auth/google — exchange the OAuth authorization code for a session cookie.
  // Any Google account can complete this and create a user, so the route gets
  // its own tight per-IP limit on top of the global one.
  //
  // `acceptTerms: true` is the person ticking "I agree" to the Terms of Use and Privacy Policy on the sign-in page.
  // Without it nobody is signed in, and the account records which version they agreed to and when (lib/terms.ts):
  // that record is what shows they agreed. It is checked before the code is spent, so a refusal costs nothing.
  app.post('/api/auth/google', { config: { rateLimit: { max: 10, timeWindow: '1 minute' } } }, async (request, reply) => {
    const { code, acceptTerms } = (request.body ?? {}) as { code?: string; acceptTerms?: unknown };
    if (!code || typeof code !== 'string') {
      return sendError(reply, 'VALIDATION_ERROR', 'Missing authorization code');
    }
    if (acceptTerms !== true) {
      return sendError(reply, 'VALIDATION_ERROR', 'Agree to the Terms of Use and Privacy Policy to sign in');
    }
    const agreed = termsAccepted();
    if (!process.env.GOOGLE_CLIENT_ID || !process.env.GOOGLE_CLIENT_SECRET) {
      return sendError(reply, 'SERVER_ERROR', 'Google sign-in is not configured on the server');
    }

    try {
      const { tokens } = await googleClient.getToken(code);
      if (!tokens.id_token) {
        return sendError(reply, 'UNAUTHORIZED', 'Google did not return an identity token');
      }
      const ticket = await googleClient.verifyIdToken({
        idToken: tokens.id_token,
        audience: process.env.GOOGLE_CLIENT_ID,
      });
      const payload = ticket.getPayload();
      if (!payload?.email || !payload.sub) {
        return sendError(reply, 'VALIDATION_ERROR', 'Invalid Google token payload');
      }

      const email = payload.email.toLowerCase();
      // ADMIN_EMAILS grants the role here and again on every session
      // (lib/access.ts), so an address added later works without a new sign-in.
      const role = isAdminEmail(email) ? 'ADMIN' : undefined;

      let user;
      try {
        user = await prisma.user.upsert({
          where: { googleId: payload.sub },
          update: { name: payload.name ?? null, avatarUrl: payload.picture ?? null, ...agreed, ...(role ? { role } : {}) },
          create: {
            email, googleId: payload.sub, name: payload.name ?? null, avatarUrl: payload.picture ?? null, ...agreed,
            ...(role ? { role } : {}),
          },
        });
      } catch (err: unknown) {
        // Same email already exists under a different googleId: link the accounts.
        if (err && typeof err === 'object' && 'code' in err && (err as { code: string }).code === 'P2002') {
          user = await prisma.user.update({
            where: { email },
            data: { googleId: payload.sub, name: payload.name ?? null, avatarUrl: payload.picture ?? null, ...agreed, ...(role ? { role } : {}) },
          });
        } else {
          throw err;
        }
      }

      const token = app.jwt.sign({ sub: user.id }, { expiresIn: SESSION_EXPIRES_IN });
      setSessionCookie(reply, token);
      return { user: publicUser(user) };
    } catch (err: unknown) {
      request.log.error({ err }, 'Google auth error');
      const e = err as { response?: { data?: { error_description?: string } }; message?: string };
      const msg = e?.response?.data?.error_description || e?.message || '';
      if (msg.includes('invalid_grant') || msg.includes('expired')) {
        return sendError(reply, 'UNAUTHORIZED', 'Sign-in expired — please try again');
      }
      if (msg.includes('redirect_uri_mismatch')) {
        return sendError(reply, 'UNAUTHORIZED', 'OAuth configuration error — please contact support');
      }
      return sendError(reply, 'UNAUTHORIZED', 'Unable to sign in with Google — please try again');
    }
  });

  // GET /api/auth/me — the signed-in user.
  app.get('/api/auth/me', { preHandler: [requireUser] }, async (request, reply) => {
    const user = await prisma.user.findUnique({ where: { id: request.viewer!.id } });
    if (!user) return sendError(reply, 'UNAUTHORIZED', 'User not found');
    return publicUser(user);
  });

  // POST /api/auth/terms — { accept: true }: the signed-in person agrees to the current Terms of Use and Privacy Policy,
  // when their account has none on record or an older version (the web app's TermsGate asks). Records it as sign-in does.
  app.post('/api/auth/terms', { preHandler: [requireUser] }, async (request, reply) => {
    const { accept } = (request.body ?? {}) as { accept?: unknown };
    if (accept !== true) return sendError(reply, 'VALIDATION_ERROR', 'accept must be true');
    try {
      const user = await prisma.user.update({ where: { id: request.viewer!.id }, data: termsAccepted() });
      return publicUser(user);
    } catch (err) {
      if (isNotFound(err)) return sendError(reply, 'UNAUTHORIZED', 'User not found'); // deleted since the cache let it in
      throw err;
    }
  });

  // POST /api/auth/logout — clear the session cookie
  app.post('/api/auth/logout', async (_request, reply) => {
    clearSessionCookie(reply);
    return { ok: true };
  });

  // DELETE /api/auth/me — delete the signed-in account (the /account page; the /privacy page promises it). The
  // account row goes, and with it every request that has no lesson yet, their files, and every follow-up the person
  // wrote. A request that has a lesson is taken back instead: the learner's side of it goes and the lesson stays
  // public, naming no one (lib/privacy.ts). Signing in again later makes a new, empty account.
  app.delete('/api/auth/me', { preHandler: [requireUser] }, async (request, reply) => {
    const viewer = request.viewer!;
    try {
      await runTransaction(async (tx) => {
        if (viewer.role === 'ADMIN' && (await tx.user.count({ where: { role: 'ADMIN' } })) <= 1) {
          throw new AppError('VALIDATION_ERROR', 'You are the only admin. Make someone else an admin before you delete your account.');
        }
        // Locked as the answer route locks them, so no request gains a lesson while the account goes: one with a
        // lesson is kept, one without is deleted, and the two must not cross.
        const rows = await tx.$queryRaw<{ id: string; status: string }[]>`
          SELECT id, status FROM questions WHERE "userId" = ${viewer.id} FOR UPDATE
        `;
        await detachQuestions(tx, rows.filter((r) => r.status === 'ANSWERED').map((r) => r.id));
        // The rest cascades from the user row: requests with no lesson and their files, and every follow-up the
        // person wrote, with its files and the teacher's reply to it.
        await tx.user.delete({ where: { id: viewer.id } });
      });
    } catch (err) {
      if (isAppError(err)) return sendError(reply, err.code, err.message);
      throw err;
    }
    request.log.info({ userId: viewer.id }, 'account deleted');
    invalidateUserCache(viewer.id); // other instances notice within their cache TTL (middleware/auth.ts)
    clearSessionCookie(reply);
    return reply.status(204).send();
  });
}
