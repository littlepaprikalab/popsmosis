import { createHmac } from 'crypto';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { BrowserContext, APIRequestContext } from '@playwright/test';

export interface Seed {
  users: { ada: string; ben: string; cal: string }; // Cal is there to be deleted (account.spec.ts)
  agent: { id: string; name: string };
  droplet: string; // the seeded READY droplet's answer id
  calDroplet: string; // Cal's READY droplet, which outlives Cal's account
  POPSMOSIS_API_KEY: string;
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
    name: 'popsmosis_session',
    value: sessionToken(userId),
    domain: base.hostname,
    path: '/api',
    httpOnly: true,
  }]);
}

/** Drive the teacher through the agent API, the way a runner would. */
export function agent(request: APIRequestContext, key: string) {
  const headers = { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' };
  return {
    me: () => request.get(apiUrl('/api/agents/me'), { headers }),
    queue: () => request.get(apiUrl('/api/questions/queue?limit=20'), { headers }),
    answer: (id: string, content: string, extra: object = {}) =>
      request.post(apiUrl(`/api/questions/${id}/answer`), { headers, data: { content, model: 'e2e', ...extra } }),
    claim: (id: string) => request.post(apiUrl(`/api/questions/${id}/claim`), { headers, data: {} }),
    fail: (id: string, reason: string) => request.post(apiUrl(`/api/questions/${id}/fail`), { headers, data: { reason } }),
    // Where the studio is, as it reports at each step's start and end (api/src/lib/progress.ts).
    progress: (id: string, data: object) => request.put(apiUrl(`/api/questions/${id}/progress`), { headers, data }),
    videoClaim: (answerId: string) => request.post(apiUrl(`/api/answers/${answerId}/video/claim`), { headers, data: {} }),
    videoProgress: (answerId: string, data: object) => request.put(apiUrl(`/api/answers/${answerId}/video/progress`), { headers, data }),
    video: (answerId: string, data: object) => request.patch(apiUrl(`/api/answers/${answerId}/video`), { headers, data }),
    replyQueue: () => request.get(apiUrl('/api/replies/queue?limit=20'), { headers }),
    reply: (answerId: string, replyToId: string, content: string) =>
      request.post(apiUrl(`/api/answers/${answerId}/replies`), { headers, data: { content, replyToId } }),
  };
}
