import { createHmac } from 'crypto';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { BrowserContext, APIRequestContext } from '@playwright/test';

export interface Seed {
  users: { ada: string; ben: string; cal: string }; // cal is seeded PENDING
  agent: { id: string; name: string };
  ZOEY_API_KEY: string;
}

export function seed(): Seed {
  return JSON.parse(readFileSync(resolve(__dirname, '.seed.json'), 'utf8'));
}

const API_URL = process.env.E2E_API_URL || 'http://localhost:3001';
export const apiUrl = (path: string) => `${API_URL}${path}`;

/** Mint a session token exactly as the API does (HS256 over { sub }). */
export function sessionToken(userId: string, ttlSeconds = 3600): string {
  const secret = process.env.JWT_SECRET!;
  const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString('base64url');
  const now = Math.floor(Date.now() / 1000);
  const h = b64({ alg: 'HS256', typ: 'JWT' });
  const p = b64({ sub: userId, iat: now, exp: now + ttlSeconds });
  return `${h}.${p}.${createHmac('sha256', secret).update(`${h}.${p}`).digest('base64url')}`;
}

export async function signIn(context: BrowserContext, userId: string) {
  const base = new URL(process.env.E2E_BASE_URL || 'http://localhost:3000');
  await context.addCookies([{
    name: 'zoey_session',
    value: sessionToken(userId),
    domain: base.hostname,
    path: '/api',
    httpOnly: true,
  }]);
}

/** Drive the advisor through the agent API, the way a runner would. */
export function agent(request: APIRequestContext, key: string) {
  const headers = { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' };
  return {
    me: () => request.get(apiUrl('/api/agents/me'), { headers }),
    queue: () => request.get(apiUrl('/api/questions/queue?limit=20'), { headers }),
    answer: (id: string, content: string) => request.post(apiUrl(`/api/questions/${id}/answer`), { headers, data: { content, model: 'e2e' } }),
    replyQueue: () => request.get(apiUrl('/api/replies/queue?limit=20'), { headers }),
    reply: (answerId: string, replyToId: string, content: string) =>
      request.post(apiUrl(`/api/answers/${answerId}/replies`), { headers, data: { content, replyToId } }),
  };
}
