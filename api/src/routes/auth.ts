import { FastifyInstance } from 'fastify';
import { OAuth2Client } from 'google-auth-library';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireUser } from '../middleware/auth.js';
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
  app.post('/api/auth/google', { config: { rateLimit: { max: 10, timeWindow: '1 minute' } } }, async (request, reply) => {
    const { code } = (request.body ?? {}) as { code?: string };
    if (!code || typeof code !== 'string') {
      return sendError(reply, 'VALIDATION_ERROR', 'Missing authorization code');
    }
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
          update: { name: payload.name ?? null, avatarUrl: payload.picture ?? null, ...(role ? { role } : {}) },
          create: {
            email, googleId: payload.sub, name: payload.name ?? null, avatarUrl: payload.picture ?? null,
            ...(role ? { role } : {}),
          },
        });
      } catch (err: unknown) {
        // Same email already exists under a different googleId: link the accounts.
        if (err && typeof err === 'object' && 'code' in err && (err as { code: string }).code === 'P2002') {
          user = await prisma.user.update({
            where: { email },
            data: { googleId: payload.sub, name: payload.name ?? null, avatarUrl: payload.picture ?? null, ...(role ? { role } : {}) },
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

  // POST /api/auth/logout — clear the session cookie
  app.post('/api/auth/logout', async (_request, reply) => {
    clearSessionCookie(reply);
    return { ok: true };
  });
}
