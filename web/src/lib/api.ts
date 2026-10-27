const API_BASE = '/api';
const REQUEST_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  status: number;
  code: string | undefined;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// The API is a separate Render service. It spins down when idle on the free
// plan and is briefly unroutable while a deploy swaps the instance, and Render
// answers both windows itself — a ~160KB HTML error page with a 502/503/504,
// never the JSON this client expects. That says nothing about the session, so
// safe requests ride the window out instead of surfacing it as a failure.
const GATEWAY_STATUSES = new Set([502, 503, 504]);
const MAX_GATEWAY_ATTEMPTS = 3;
const GATEWAY_BACKOFF_MS = [1000, 3000, 6000];
const GATEWAY_BUDGET_MS = 15_000; // never begin a retry past this much time in the call
const SAFE_METHODS = new Set(['GET', 'HEAD']);

// One client-wide view of whether the API is answering at all. The pages poll,
// so a sustained outage means several callers a second each finding out on their
// own; retrying per caller multiplies the load on an API that is already down or
// waking, and downloads Render's ~160KB error page every time. The first caller
// to exhaust its retries latches this, and while it holds everyone makes one
// attempt and takes the answer — enough to notice recovery, without the storm.
const BREAKER_COOLDOWN_MS = 10_000;
let downUntil = 0;
const breakerOpen = () => Date.now() < downUntil;

// Spread the wave: without this every client in the outage retries in lockstep
// and hits the API together the moment it comes back.
const jitter = (ms: number) => Math.round(ms * (0.75 + Math.random() * 0.5));

const UNREACHABLE_CODE = 'SERVER_UNAVAILABLE';
const UNREACHABLE_MESSAGE = 'Cannot reach the server. It may be starting up — try again in a moment.';

/** The request never got an answer from the API: down, waking up, or mid-deploy. */
export function isUnreachable(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 0 || GATEWAY_STATUSES.has(err.status));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * `idempotent`: this write lands once however many times it is sent (a PUT of the
 * caller's own state, a POST carrying an Idempotency-Key), so it may ride out a gateway
 * window the way a GET does. Never set it on a write whose second send does something new.
 */
type RequestOptions = RequestInit & { idempotent?: boolean };
const isSafeRequest = (options: RequestOptions) => !!options.idempotent || SAFE_METHODS.has((options.method ?? 'GET').toUpperCase());

interface Attempts {
  gateway: number;
  rateLimit: number;
  startedAt: number; // first attempt, so the retries share one time budget
}

async function request<T>(path: string, options: RequestOptions = {}, attempts?: Attempts): Promise<T> {
  const tries: Attempts = attempts ?? { gateway: 0, rateLimit: 0, startedAt: Date.now() };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  // Retry only what a retry can help: a safe request, tries left, a budget that
  // still fits one, and no other caller having already found the API missing.
  const backoffMs = jitter(GATEWAY_BACKOFF_MS[Math.min(tries.gateway, GATEWAY_BACKOFF_MS.length - 1)]);
  const retryGateway = () =>
    isSafeRequest(options)
    && tries.gateway < MAX_GATEWAY_ATTEMPTS
    && !breakerOpen()
    && Date.now() - tries.startedAt + backoffMs < GATEWAY_BUDGET_MS;

  const again = async (): Promise<T> => {
    await sleep(backoffMs);
    return request<T>(path, options, { ...tries, gateway: tries.gateway + 1 });
  };

  let res: Response;
  const { idempotent: _idempotent, ...init } = options;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: 'include', // send the session cookie
      signal: controller.signal,
      headers: {
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        'X-Requested-With': 'XMLHttpRequest', // CSRF signal
        ...init.headers,
      },
    });
  } catch (err) {
    // Nothing answered at all — a refused connection, a dropped proxy hop, or
    // our own timeout. Same treatment as a gateway status for a safe request.
    clearTimeout(timer); // the backoff below outlives this call's timeout
    if (retryGateway()) return again();
    downUntil = Date.now() + BREAKER_COOLDOWN_MS;
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError('Request timed out', 0);
    }
    throw new ApiError(UNREACHABLE_MESSAGE, 0, UNREACHABLE_CODE);
  } finally {
    clearTimeout(timer);
  }

  const gateway = GATEWAY_STATUSES.has(res.status);
  if (gateway && retryGateway()) return again();
  // The API answered, whatever it said, so it is not the thing that is missing.
  if (!gateway) downUntil = 0;
  else downUntil = Date.now() + BREAKER_COOLDOWN_MS;

  if (!res.ok) {
    // Render's error page is HTML and megabytes of it arrive during an outage;
    // only spend a parse on a body that claims to be ours.
    const err = res.headers.get('content-type')?.includes('json') ? await res.json().catch(() => null) : null;

    // Retry transient 429s with backoff unless the server marked it non-retryable.
    if (res.status === 429 && tries.rateLimit < 3 && err?.error?.retryable !== false) {
      const retryAfter = res.headers.get('retry-after');
      const delayMs = retryAfter ? parseInt(retryAfter, 10) * 1000 : jitter(1000 * 2 ** tries.rateLimit);
      await sleep(delayMs);
      return request<T>(path, options, { ...tries, rateLimit: tries.rateLimit + 1 });
    }

    const message: string = err?.error?.message || (gateway ? UNREACHABLE_MESSAGE : res.statusText || 'Request failed');
    const code: string | undefined = err?.error?.code || (gateway ? UNREACHABLE_CODE : undefined);
    if (res.status === 401 && typeof window !== 'undefined') {
      if (message === 'User not found') {
        fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include', headers: { 'X-Requested-With': 'XMLHttpRequest' } }).catch(() => {});
        window.location.href = '/auth/callback';
      } else if (message === 'Missing authorization token' || message === 'Invalid token') {
        window.dispatchEvent(new CustomEvent('session-expired'));
        throw new ApiError('Your session has expired — please sign in again.', 401);
      }
    }
    throw new ApiError(message, res.status, code);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---- Types (mirror api/src/lib/serialize.ts) ----

export interface User {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  role: 'USER' | 'ADMIN';
  createdAt: string;
}

export interface PublicUser {
  id: string;
  name: string | null;
  avatarUrl: string | null;
}

export interface AgentRef {
  id: string;
  name: string;
}

/** A file a person sent with a question or reply. `url` serves it while it is kept; `expired` once it is not. */
export interface Attachment {
  id: string;
  name: string;
  mimeType: string;
  size: number;
  url: string;
  expired: boolean;
}

export interface Reply {
  id: string;
  answerId: string;
  authorType: 'HUMAN' | 'AGENT';
  replyToId: string | null;
  content: string;
  attachments: Attachment[]; // a human reply's
  createdAt: string;
  user: PublicUser | null;
  agent: AgentRef | null;
  canDelete: boolean;
}

/** The droplet's two videos; RENDERING past the API's stale limit already reads as FAILED. */
export interface Video {
  status: 'RENDERING' | 'READY' | 'FAILED';
  songVideoUrl: string | null;
  lessonVideoUrl: string | null;
  songPosterUrl: string | null;
  lessonPosterUrl: string | null;
  error: string | null;
  updatedAt: string | null;
}

/** One model's part in a step: how many calls, what they cost (api/src/lib/breakdown.ts). */
export interface BreakdownModel {
  model: string;
  calls: number;
  costUsd: number | null; // null: charged in credits, with no price set for them
  credits: number | null; // what the provider charged in its own credits (ElevenLabs)
}

export interface BreakdownStep {
  step: string; // the studio's name: bundle, factcheck, art, audio, timing, render...
  part: 'lesson' | 'videos';
  seconds: number | null; // every try together
  tries: number;
  costUsd: number;
  models: BreakdownModel[]; // empty: code alone
}

/** How the droplet was made, step by step: its bill of materials. */
export interface Breakdown {
  steps: BreakdownStep[];
  seconds: number | null; // wall time; the art and the audio are made side by side
  costUsd: number;
  credits: number | null; // credits of the calls with no price
  unpriced: number; // models with neither a price nor credits: the provider did not say
  complete: boolean; // every call priced
}

export interface Answer {
  id: string;
  questionId: string;
  agent: AgentRef;
  content: string;
  model: string | null;
  createdAt: string;
  video: Video | null; // null: a text-only droplet
  breakdown: Breakdown | null; // null: the studio sent none (a lesson written by hand, or an older one)
  hidden: boolean; // an admin took it off the public Droplets tab
  replies: Reply[];
  awaitingAgent: boolean;
}

export interface Question {
  id: string;
  content: string;
  status: 'PENDING' | 'ANSWERED' | 'FAILED';
  failedReason: string | null; // FAILED: why the teacher gave up
  attachments: Attachment[];
  createdAt: string;
  user: PublicUser | null; // null once the learner took it back: only an admin still sees it, with content ''
  answer: Answer | null;
  isOwner: boolean;
  canDelete: boolean;
}

/** A droplet on the public tab (api/src/lib/droplet.ts). Nothing about who asked for it. */
export interface DropletVideos {
  songVideoUrl: string | null;
  lessonVideoUrl: string | null;
  songPosterUrl: string | null;
  lessonPosterUrl: string | null;
}

export interface DropletCard {
  id: string;
  title: string; // "AP Biology: ATP synthesis"
  course: string;
  topic: string;
  excerpt: string; // the lesson's first lines, plain text
  video: DropletVideos;
  // Absent from an API older than the likes and comments: the page then leaves them out.
  likeCount?: number;
  commentCount?: number;
  liked?: boolean; // the viewer's own thumbs-up; false when signed out
  createdAt: string;
}

/** The viewer's thumbs-up on one droplet, as the API last confirmed it. */
export interface LikeState {
  liked: boolean;
  likeCount: number;
}

/** A public comment on a droplet: the author's first name and initial, nothing else about them. */
export interface DropletComment {
  id: string;
  content: string;
  createdAt: string;
  author: { name: string };
  mine: boolean; // the viewer wrote it
}

/** A page of a droplet's comments, newest first; nextCursor brings the older ones. */
export interface DropletCommentPage {
  data: DropletComment[];
  nextCursor: string | null;
}

export interface Droplet extends DropletCard {
  content: string; // the lesson, markdown
  lyrics: { section: string; text: string }[];
  comments?: DropletCommentPage; // the first page, so the page needs one request; absent from an older API
}

export interface Agent {
  id: string;
  name: string;
  enabled: boolean;
  hasKey: boolean; // false after revoke, until a new key is minted
  createdAt: string;
  lastSeenAt: string | null;
  apiKey?: string; // only on create / mint / rotate
}

export const api = {
  // Wake a spun-down API before something single-use depends on it.
  health: () => request<{ status: string; timestamp: string }>('/health'),

  // Auth
  authGoogle: (code: string) => request<{ user: User }>('/auth/google', { method: 'POST', body: JSON.stringify({ code }) }),
  getMe: () => request<User>('/auth/me'),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  // Deletes the signed-in account (api/src/routes/auth.ts); the lessons already made stay public, naming no one.
  deleteAccount: () => request<void>('/auth/me', { method: 'DELETE' }),

  // Questions
  listQuestions: (params?: Record<string, string>) =>
    request<{ data: Question[]; nextCursor: string | null }>(`/questions?${new URLSearchParams(params)}`),
  getQuestion: (id: string) => request<Question>(`/questions/${id}`),
  getQuestionsByIds: (ids: string[]) =>
    request<{ data: Question[]; nextCursor: null }>(`/questions?ids=${encodeURIComponent(ids.join(','))}`),
  askQuestion: (content: string) => request<Question>('/questions', { method: 'POST', body: JSON.stringify({ content }) }),
  deleteQuestion: (id: string) => request<void>(`/questions/${id}`, { method: 'DELETE' }),
  // mine scopes latestAt to the viewer's own questions, matching the list the
  // page is showing, so the "new activity" banner cannot promise a refresh
  // that changes nothing. since is the caller's watermark: the server then only
  // looks at rows written after it, which is all a poller needs to know.
  getStatus: (mine?: boolean, since?: string | null) => {
    const params = new URLSearchParams();
    if (mine) params.set('mine', 'true');
    if (since) params.set('since', since);
    const q = params.toString();
    return request<{ latestAt: string | null; agentLastSeenAt: string | null }>(`/questions/status${q ? `?${q}` : ''}`);
  },

  // Replies
  addReply: (answerId: string, content: string) =>
    request<Reply>(`/answers/${answerId}/replies`, { method: 'POST', body: JSON.stringify({ content }) }),
  deleteReply: (id: string) => request<void>(`/replies/${id}`, { method: 'DELETE' }),

  // The public Droplets tab: no sign-in needed
  listDroplets: (cursor?: string | null) =>
    request<{ data: DropletCard[]; nextCursor: string | null }>(`/droplets${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  getDroplet: (id: string) => request<Droplet>(`/droplets/${encodeURIComponent(id)}`),
  // Thumbs-up: idempotent both ways, so each may be retried through a gateway blip.
  setDropletLiked: (id: string, liked: boolean) =>
    request<LikeState>(`/droplets/${encodeURIComponent(id)}/like`, { method: liked ? 'PUT' : 'DELETE', idempotent: true }),
  listDropletComments: (id: string, cursor?: string | null) =>
    request<DropletCommentPage & { commentCount: number }>(
      `/droplets/${encodeURIComponent(id)}/comments${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  // The key makes a resend return the comment the first send made, so this one may be retried too.
  addDropletComment: (id: string, content: string, idempotencyKey: string) =>
    request<DropletComment>(`/droplets/${encodeURIComponent(id)}/comments`, {
      method: 'POST', body: JSON.stringify({ content }), headers: { 'Idempotency-Key': idempotencyKey }, idempotent: true,
    }),
  deleteDropletComment: (id: string, commentId: string) =>
    request<void>(`/droplets/${encodeURIComponent(id)}/comments/${encodeURIComponent(commentId)}`, { method: 'DELETE' }),
  // Admin: take a droplet off the tab, or put it back
  setDropletHidden: (answerId: string, hidden: boolean) =>
    request<{ id: string; hidden: boolean }>(`/answers/${answerId}`, { method: 'PATCH', body: JSON.stringify({ hidden }) }),

  // Admin: agents
  listAgents: () => request<{ data: Agent[] }>('/agents'),
  createAgent: (name: string) => request<Agent>('/agents', { method: 'POST', body: JSON.stringify({ name }) }),
  rotateAgentKey: (id: string) => request<Agent>(`/agents/${id}/rotate-key`, { method: 'POST' }),
  revokeAgentKey: (id: string) => request<Agent>(`/agents/${id}/revoke-key`, { method: 'POST' }),
  setAgentEnabled: (id: string, enabled: boolean) =>
    request<Agent>(`/agents/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  agentSkillUrl: (id: string) => `${API_BASE}/agents/${id}/skill`,

  // Admin: users
  listUsers: (q?: string) => request<{ data: User[] }>(`/users?${new URLSearchParams(q ? { q } : {})}`),
  setUserRole: (id: string, role: 'ADMIN' | 'USER') =>
    request<User>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify({ role }) }),
};
