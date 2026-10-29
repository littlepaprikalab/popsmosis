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
import { clientAddress } from './lib/public-limit.js';
import { resolveAgentToken } from './middleware/auth.js';
import { storageConfig } from './lib/storage.js';
import { keepKnobsFresh } from './lib/knobs.js';
import { loadKnobs, reloadKnobs } from './lib/studio-settings.js';
import authRoutes from './routes/auth.js';
import questionRoutes from './routes/questions.js';
import replyRoutes from './routes/replies.js';
import agentRoutes from './routes/agents.js';
import userRoutes from './routes/users.js';
import attachmentRoutes from './routes/attachments.js';
import answerRoutes from './routes/answers.js';
import dropletRoutes from './routes/droplets.js';
import videoRoutes from './routes/videos.js';
import studioRoutes from './routes/studio.js';

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

  // ---- The knobs an admin set on /admin/studio (lib/knobs.ts): read again every REFRESH_MS ----
  // On a timer rather than on requests: another instance's change is in force here within REFRESH_MS whatever the
  // traffic, and no request waits on the read.
  const knobTimer = keepKnobsFresh(loadKnobs, app.log);
  app.addHook('onClose', async () => clearInterval(knobTimer));

  // ---- Resolve agent keys first: the proxy gate and the rate limit below both ask whether a key is real ----
  // A well-formed key is looked up (cached both ways for AGENT_TTL_MS, middleware/auth.ts); per-route guards then
  // short-circuit on request.agent. A key that is not real is treated as no key at all.
  app.addHook('onRequest', async (request) => {
    const token = bearerToken(request.headers.authorization);
    if (!isWellFormedAgentToken(token)) return;
    try {
      const result = await resolveAgentToken(token);
      if ('agent' in result) request.agent = result.agent;
      request.agentKeyKnown = 'agent' in result || result.errorCode === 'FORBIDDEN'; // FORBIDDEN: real, but disabled
    } catch (err) {
      // The database did not answer. The route's own guard looks again and answers a 5xx the studio retries; a
      // refusal here would read as a bad key, and stop the teacher's run over a blip.
      request.agentKeyLookupFailed = true;
      request.log.warn({ err }, 'agent key lookup failed');
    }
  });

  // ---- Proxy-token gate (production): humans must come through the Next.js proxy ----
  const PROXY_SECRET = process.env.PROXY_SECRET;
  if (PROXY_SECRET) {
    app.addHook('onRequest', async (request, reply) => {
      // Agents call the API directly, with a key that is really theirs (a disabled one still reaches its route, which
      // says so). Anything that only looks like a key comes through the proxy like everyone else.
      if (request.agentKeyKnown || request.agentKeyLookupFailed) return;
      if (request.url === '/api/health') return;
      // A key in the right shape that is no agent's (revoked, rotated, made up) gets the answer requireAgent gives it,
      // so a runner with an old key reads it as a bad key, not as a disabled agent or a blocked caller.
      if (isWellFormedAgentToken(bearerToken(request.headers.authorization))) {
        return reply.status(401).send({ error: { code: 'UNAUTHORIZED', message: 'Invalid API key' } });
      }
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

  // ---- Rate limiting keyed by a real agent key, then a verified session, then the client's address ----
  // Only a credential that checks out gets a bucket of its own: a made-up key or cookie would otherwise be a fresh
  // bucket on every request. Everyone else is keyed by the address the edge saw (lib/public-limit.ts), never by the
  // web server's, which every browser's request comes from through the proxy.
  const verifiedSub = new WeakMap<FastifyRequest, string | null>();
  const subOf = (req: FastifyRequest): string | null => {
    if (verifiedSub.has(req)) return verifiedSub.get(req)!;
    let sub: string | null = null;
    const token = sessionCookieToken(req.cookies);
    if (token) {
      try {
        const payload = app.jwt.verify<{ sub?: unknown }>(token);
        sub = typeof payload.sub === 'string' && payload.sub ? payload.sub : null;
      } catch {
        sub = null;
      }
    }
    verifiedSub.set(req, sub);
    return sub;
  };
  const isProgressReport = (req: FastifyRequest) => req.method === 'PUT' && /\/progress$/.test(req.url.split('?')[0]);
  await app.register(rateLimit, {
    timeWindow: '1 minute',
    keyGenerator: (req: FastifyRequest) => {
      // The studio's progress reports (lib/progress.ts) count in a bucket of their own: every run shares one key,
      // and a burst of reports for the page must never use up what its claims, uploads and READY need.
      if (req.agent) {
        const token = bearerToken(req.headers.authorization)!;
        return `${isProgressReport(req) ? 'agent-progress' : 'agent'}:${hashApiKey(token).slice(0, 16)}`;
      }
      const sub = subOf(req);
      if (sub) return `user:${sub}`;
      return `ip:${clientAddress(req)}`;
    },
    max: (req: FastifyRequest) => {
      if (req.agent) return 300;
      if (subOf(req)) return 200;
      // Per address, and a school's students share one: a class loading the site together stays well under it.
      return 120;
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
  await app.register(dropletRoutes);
  await app.register(videoRoutes);
  await app.register(studioRoutes);

  // A storage setting that cannot upload is said once at boot, where whoever set it looks;
  // the rest of the API works without storage, so it still starts. So is a key on borrowed time.
  const storage = storageConfig();
  if (storage && 'problem' in storage) app.log.error(`File storage is misconfigured: ${storage.problem}`);
  else if (storage?.legacy) {
    app.log.warn('File storage uses the legacy service_role key, which Supabase retires at the end of 2026; set SUPABASE_SERVICE_ROLE_KEY to the secret key (sb_secret_…)');
  }

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

  // The knobs set on /admin/studio in force before the first request. A database that does not answer within
  // KNOB_START_WAIT_MS never keeps the API from starting: the environment's are in force until the timer reads them.
  const KNOB_START_WAIT_MS = 5000;
  const late = new Promise<'late'>((resolve) => setTimeout(() => resolve('late'), KNOB_START_WAIT_MS).unref());
  await Promise.race([reloadKnobs(), late])
    .then((read) => { if (read === 'late') app.log.warn('the knobs were not read in time; the environment\'s are in force until they are'); })
    .catch((err) => app.log.warn({ err }, 'could not read the knobs; the environment\'s are in force'));
  const port = parseInt(process.env.PORT || '3001', 10);
  try {
    await app.listen({ port, host: '0.0.0.0' });
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
}
