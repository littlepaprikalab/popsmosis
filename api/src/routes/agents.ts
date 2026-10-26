import { FastifyInstance } from 'fastify';
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import prisma from '../lib/prisma.js';
import { sendError } from '../lib/errors.js';
import { requireAgent, requireAdmin, invalidateAgentCache } from '../middleware/auth.js';
import { generateApiKey } from '../lib/apikey.js';
import { validateString } from '../lib/validation.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

// docs/zoey-agent-skill.md, relative to both src/routes (tsx) and dist/routes (node).
const SKILL_PATH = resolve(__dirname, '../../../docs/zoey-agent-skill.md');
const SKILL_BASE_URL_PLACEHOLDER = 'https://api.zoey.example';

function loadSkillTemplate(): string {
  return readFileSync(SKILL_PATH, 'utf8');
}

function publicAgent(a: { id: string; name: string; enabled: boolean; apiKeyHash: string | null; createdAt: Date; lastSeenAt: Date | null }) {
  return {
    id: a.id,
    name: a.name,
    enabled: a.enabled,
    hasKey: a.apiKeyHash !== null,
    createdAt: a.createdAt.toISOString(),
    lastSeenAt: a.lastSeenAt?.toISOString() ?? null,
  };
}

export default async function agentRoutes(app: FastifyInstance) {
  // GET /api/agents/me — the agent checks its key works; bumps lastSeenAt
  app.get('/api/agents/me', { preHandler: [requireAgent] }, async (request) => {
    const agent = await prisma.agent.update({
      where: { id: request.agent!.id },
      data: { lastSeenAt: new Date() },
    });
    return publicAgent(agent);
  });

  // GET /api/agents — admin: list agents
  app.get('/api/agents', { preHandler: [requireAdmin] }, async () => {
    const agents = await prisma.agent.findMany({ orderBy: { createdAt: 'asc' } });
    return { data: agents.map(publicAgent) };
  });

  // POST /api/agents — admin: create an agent; the raw key is returned exactly once
  app.post('/api/agents', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { name } = (request.body ?? {}) as { name?: unknown };
    const err = validateString(name, 'name', { required: true, minLength: 2, maxLength: 60 });
    if (err) return sendError(reply, 'VALIDATION_ERROR', err);
    const { raw, hashed } = generateApiKey();
    try {
      const agent = await prisma.agent.create({ data: { name: (name as string).trim(), apiKeyHash: hashed } });
      reply.status(201);
      return { ...publicAgent(agent), apiKey: raw };
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'code' in e && (e as { code: string }).code === 'P2002') {
        return sendError(reply, 'CONFLICT', 'An agent with that name already exists');
      }
      throw e;
    }
  });

  // POST /api/agents/:id/rotate-key — admin: mint a new key (and kill the old one, if any).
  // Works for an agent with no key too, so "mint" and "rotate" are the same call.
  app.post('/api/agents/:id/rotate-key', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { raw, hashed } = generateApiKey();
    const agent = await prisma.agent.findUnique({ where: { id } });
    if (!agent) return sendError(reply, 'NOT_FOUND', 'Agent not found');
    const updated = await prisma.agent.update({ where: { id }, data: { apiKeyHash: hashed } });
    if (agent.apiKeyHash) invalidateAgentCache(agent.apiKeyHash);
    return { ...publicAgent(updated), apiKey: raw };
  });

  // POST /api/agents/:id/revoke-key — admin: the agent keeps its name and history but no key
  // works until an admin mints a new one. Distinct from disable (reversible, keeps the key).
  app.post('/api/agents/:id/revoke-key', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agent = await prisma.agent.findUnique({ where: { id } });
    if (!agent) return sendError(reply, 'NOT_FOUND', 'Agent not found');
    const updated = await prisma.agent.update({ where: { id }, data: { apiKeyHash: null } });
    if (agent.apiKeyHash) invalidateAgentCache(agent.apiKeyHash);
    return publicAgent(updated);
  });

  // PATCH /api/agents/:id — admin: enable/disable (the kill switch)
  app.patch('/api/agents/:id', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { enabled } = (request.body ?? {}) as { enabled?: unknown };
    if (typeof enabled !== 'boolean') return sendError(reply, 'VALIDATION_ERROR', 'enabled must be a boolean');
    const agent = await prisma.agent.findUnique({ where: { id } });
    if (!agent) return sendError(reply, 'NOT_FOUND', 'Agent not found');
    const updated = await prisma.agent.update({ where: { id }, data: { enabled } });
    if (agent.apiKeyHash) invalidateAgentCache(agent.apiKeyHash);
    return publicAgent(updated);
  });

  // GET /api/agents/:id/skill — admin: download the skill file with the real base URL
  app.get('/api/agents/:id/skill', { preHandler: [requireAdmin] }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const agent = await prisma.agent.findUnique({ where: { id } });
    if (!agent) return sendError(reply, 'NOT_FOUND', 'Agent not found');

    const baseUrl = (process.env.API_BASE_URL || 'http://localhost:3001').replace(/\/$/, '');
    const content = loadSkillTemplate().split(SKILL_BASE_URL_PLACEHOLDER).join(baseUrl);

    reply.header('Content-Type', 'text/markdown; charset=utf-8');
    reply.header('Content-Disposition', `attachment; filename="${agent.name.toLowerCase().replace(/\s+/g, '-')}-skill.md"`);
    return content;
  });
}
