import 'dotenv/config';
import Fastify, { FastifyError, FastifyRequest, FastifyReply, LogController } from 'fastify';
import cors from '@fastify/cors';
import cookie from '@fastify/cookie';
import helmet from '@fastify/helmet';
import jwt from '@fastify/jwt';
import rateLimit from '@fastify/rate-limit';
import prisma from './lib/prisma.js';
import { hashApiKey, isAgentToken, isWellFormedAgentToken, bearerToken } from './lib/apikey.js';
import { sessionCookieToken, hasSessionCookie, cacheControlForCookieResponse } from './lib/session.js';
import { resolveAgentToken } from './middleware/auth.js';
import authRoutes from './routes/auth.js';
import questionRoutes from './routes/questions.js';
import replyRoutes from './routes/replies.js';
import agentRoutes from './routes/agents.js';
import userRoutes from './routes/users.js';
import attachmentRoutes from './routes/attachments.js';
import answerRoutes from './routes/answers.js';
import videoRoutes from './routes/videos.js';

export async function buildApp() {
  const app = Fastify({
    logger: process.env.NODE_ENV !== 'test',
    // We log responses ourselves below so the two polling endpoints do not
    // flood the logs at one line per user per 10 seconds.
    logController: new LogController({ disableRequestLogging: true }),
  });

  // ---- CORS: browser origins that may call the API with cookies ----
  const allowedOrigins = (process.env.ALLOWED_ORIGINS || 'http://localhost:3000')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  await app.register(cors, {
    origin: (origin, cb) => cb(null, !origin || allowedOrigins.includes(origin)),
    credentials: true,
  });

  await app.register(cookie);
  await app.register(helmet, { contentSecurityPolicy: false, crossOriginEmbedderPolicy: false });

  // ---- Proxy-token gate (production): humans must come through the Next.js proxy ----
  const PROXY_SECRET = process.env.PROXY_SECRET;
  if (PROXY_SECRET) {
    app.addHook('onRequest', async (request, reply) => {
      const token = bearerToken(request.headers.authorization);
      if (isWellFormedAgentToken(token)) return; // agents call the API directly
      if (request.url === '/api/health') return;
      if (request.headers['x-proxy-token'] !== PROXY_SECRET) {
        return reply.status(403).send({ error: { code: 'FORBIDDEN', message: 'Direct API access not allowed' } });
      }
    });
  } else if (process.env.NODE_ENV === 'production') {
    // Same rule as sparks: in production only the web app (which attaches the
    // secret) and agents (with their key) may reach the API. Refuse to start
    // half-configured rather than run open.
    throw new Error('PROXY_SECRET environment variable is required in production. Set the same value on popsmosis-api and popsmosis-web.');
  }

  if (!process.env.JWT_SECRET) {
    throw new Error('JWT_SECRET environment variable is required');
  }
  await app.register(jwt, { secret: process.env.JWT_SECRET });

  // ---- Rate limiting keyed by agent key hash, then session subject, then IP ----
  const subFromCookie = (req: FastifyRequest): string | null => {
    const token = sessionCookieToken(req.cookies);
    if (!token) return null;
    try {
      const payload = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString());
      return typeof payload.sub === 'string' ? payload.sub : null;
    } catch {
      return null;
    }
  };
  await app.register(rateLimit, {
    timeWindow: '1 minute',
    keyGenerator: (req: FastifyRequest) => {
      const token = bearerToken(req.headers.authorization);
      if (isAgentToken(token)) return `agent:${hashApiKey(token).slice(0, 16)}`;
      const sub = subFromCookie(req);
      if (sub) return `user:${sub}`;
      return req.ip;
    },
    max: (req: FastifyRequest) => {
      const token = bearerToken(req.headers.authorization);
      if (isAgentToken(token)) return 300;
      if (hasSessionCookie(req.cookies)) return 200;
      return 60;
    },
  });

  // ---- CSRF: cookie-authenticated writes must carry a same-origin signal ----
  app.addHook('onRequest', async (request, reply) => {
    const isWrite = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method);
    // Same rule as requireUser: the cookie is the credential unless the bearer is an agent key.
    const usesCookie = hasSessionCookie(request.cookies) && !isAgentToken(bearerToken(request.headers.authorization));
    if (!isWrite || !usesCookie) return;
    const hasXhrHeader = request.headers['x-requested-with'] === 'XMLHttpRequest';
    const site = request.headers['sec-fetch-site'];
    if (hasXhrHeader || site === 'same-origin' || site === 'same-site') return;
    return reply.status(403).send({ error: { code: 'FORBIDDEN', message: 'Missing CSRF header' } });
  });

  // ---- Resolve agent keys once, early; per-route guards short-circuit when set ----
  app.addHook('onRequest', async (request) => {
    const token = bearerToken(request.headers.authorization);
    if (!isAgentToken(token)) return;
    try {
      const result = await resolveAgentToken(token);
      if ('agent' in result) request.agent = result.agent;
    } catch {
      // requireAgent reports the error on gated routes
    }
  });

  // ---- Never let a Set-Cookie response be publicly cacheable ----
  app.addHook('onSend', async (_request, reply) => {
    if (!reply.getHeader('set-cookie')) return;
    const forced = cacheControlForCookieResponse(reply.getHeader('cache-control'));
    if (forced) reply.header('cache-control', forced);
  });

  // ---- Errors ----
  app.setErrorHandler((error: FastifyError, request: FastifyRequest, reply: FastifyReply) => {
    if (error.validation) {
      return reply.status(400).send({ error: { code: 'VALIDATION_ERROR', message: error.message } });
    }
    if (error.code === 'FST_ERR_CTP_INVALID_JSON_BODY' || error.code === 'FST_ERR_CTP_EMPTY_JSON_BODY') {
      return reply.status(400).send({ error: { code: 'INVALID_JSON', message: 'Request body is not valid JSON' } });
    }
    if (error.statusCode === 429) {
      return reply.status(429).send({ error: { code: 'RATE_LIMITED', message: 'Too many requests' } });
    }
    if (error.name === 'PrismaClientKnownRequestError') {
      const code = (error as unknown as { code: string }).code;
      if (code === 'P2025') return reply.status(404).send({ error: { code: 'NOT_FOUND', message: 'Record not found' } });
      if (code === 'P2002') return reply.status(409).send({ error: { code: 'CONFLICT', message: 'Duplicate record' } });
    }
    if (error.name === 'PrismaClientValidationError') {
      request.log.error({ err: error, url: request.url }, 'Prisma validation error');
      return reply.status(400).send({ error: { code: 'VALIDATION_ERROR', message: 'Invalid request data' } });
    }
    request.log.error({ err: error, url: request.url, method: request.method }, 'Unhandled route error');
    const statusCode = error.statusCode ?? 500;
    return reply.status(statusCode).send({
      error: { code: 'SERVER_ERROR', message: statusCode >= 500 ? 'Internal server error' : error.message },
    });
  });

  const QUIET_PATHS = new Set(['/api/health', '/api/questions/status']);
  app.addHook('onResponse', async (request, reply) => {
    const path = request.url.split('?')[0];
    const ms = Math.round(reply.elapsedTime);
    const status = reply.statusCode;
    if (status >= 500 || ms > 1000) {
      request.log.warn({ method: request.method, path, status, ms }, 'slow or failed request');
    } else if (status >= 400 || !QUIET_PATHS.has(path)) {
      request.log.info({ method: request.method, path, status, ms }, 'request');
    }
  });

  app.setNotFoundHandler((_request, reply) => {
    reply.status(404).send({ error: { code: 'NOT_FOUND', message: 'Route not found' } });
  });

  // ---- Routes ----
  await app.register(authRoutes);
  await app.register(questionRoutes);
  await app.register(replyRoutes);
  await app.register(agentRoutes);
  await app.register(userRoutes);
  await app.register(attachmentRoutes);
  await app.register(answerRoutes);
  await app.register(videoRoutes);

  app.get('/api/health', async (_request, reply) => {
    try {
      await prisma.$queryRaw`SELECT 1`;
      return { status: 'ok', timestamp: new Date().toISOString() };
    } catch {
      reply.status(503);
      return { status: 'error', message: 'Database connection failed', timestamp: new Date().toISOString() };
    }
  });

  return app;
}

// Only listen when run directly (tests import buildApp instead).
const isMain = process.argv[1] && /index\.(ts|js)$/.test(process.argv[1]);
if (isMain) {
  const app = await buildApp();
  const shutdown = async () => {
    app.log.info('Shutting down...');
    await app.close();
    await prisma.$disconnect();
    process.exit(0);
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  const port = parseInt(process.env.PORT || '3001', 10);
  try {
    await app.listen({ port, host: '0.0.0.0' });
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
}
