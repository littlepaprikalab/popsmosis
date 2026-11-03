import type { Brain } from './brains';

const API_BASE = '/api';
const REQUEST_TIMEOUT_MS = 30_000;

/** True when the API has no route for this request: one from before the web app, mid-deploy or rolled back. */
export function isMissingRoute(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404 && err.message === 'Route not found';
}

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
  termsAcceptedAt: string | null; // when they last agreed to the Terms of Use and Privacy Policy
  termsCurrent: boolean; // false: they have not agreed to the current version yet (components/TermsGate.tsx)
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
  hookVideoUrl?: string | null; // the hook cut: the song's pre-chorus and first chorus, upright, to share
  hookPosterUrl?: string | null;
  error: string | null;
  updatedAt: string | null;
}

/** One model's part in a step: how many calls, what they cost (api/src/lib/breakdown.ts). */
export interface BreakdownModel {
  model: string;
  maker: string | null; // who made it: Anthropic, Meta, Google, ElevenLabs...; null when the API does not know
  calls: number;
  costUsd: number | null; // null: charged in credits, with no price set for them
  credits: number | null; // what the provider charged in its own credits (ElevenLabs)
  via?: 'session'; // answered by the teacher's own Claude session after the API failed: on its plan, not the API key
}

/** Software a step ran on the studio's machine besides its models, at the version installed there. */
export interface BreakdownTool {
  name: string; // demucs, torchaudio, torch, skia-python, ffmpeg
  version: string | null;
  maker: string | null;
}

export interface BreakdownStep {
  step: string; // the studio's name: bundle, factcheck, art, audio, timing, render...
  part: 'lesson' | 'videos';
  seconds: number | null; // every try together
  tries: number;
  costUsd: number;
  models: BreakdownModel[]; // empty: code alone
  tools?: BreakdownTool[]; // empty: none, or a bill from before they were listed; absent from an API older than this page
}

/** How the droplet was made, step by step: its bill of materials. */
export interface Breakdown {
  steps: BreakdownStep[];
  seconds: number | null; // wall time; the art and the audio are made side by side
  // The lesson's and the videos' wall time, adding up to `seconds`; null: that part's bill is not here. Absent from
  // an API older than this page.
  partSeconds?: { lesson: number | null; videos: number | null };
  costUsd: number;
  credits: number | null; // credits of the calls with no price
  unpriced: number; // models with neither a price nor credits: the provider did not say
  complete: boolean; // every call priced
}

/**
 * How long a droplet took from its request, in seconds (api/src/lib/breakdown.ts): to the lesson posted, and to the
 * videos READY. Unlike the bill's time, which is the studio's steps alone, it counts the waits too: for the studio to
 * start, and between the lesson and its videos.
 */
export interface EndToEnd {
  lesson: number;
  videos: number | null; // null: the videos are not READY, or there are none
}

/** One line the fact-check judged: a sentence of the lesson or the narration, a couplet, a key line, a tag... */
export interface FactcheckLine {
  kind: string; // lesson, narration, lyric, keyline, scene, caption, title, style, tag, motion, avoid
  id: string;
  text: string; // a couplet's two lines joined by "\n"
  verdict: string | null; // SUPPORTED, FILLER or UNSUPPORTED
  source: string; // the fact it rests on, or "course knowledge"
  round: number | null;
  problem: string;
}

/** What the fact-check changed, and why. */
export interface FactcheckCorrection {
  round: number | null;
  kind: string;
  ids: string[];
  before: string;
  after: string;
  problem: string;
}

/** What the art checker reports on a picture: each kind of finding, a few words each. */
export interface ArtFindings {
  labels: string[]; // a label the picture got wrong, flagged on it in the video
  text: string[]; // writing in the picture (checks made before Oct 31, when a picture was to have none)
  misplaced: string[]; // a structure missing, out of place or facing the wrong way
  unnamed: string[]; // a small round thing that is no listed structure
  wrong: string[]; // anything else wrong for the scene
  notes: string[]; // what would teach something false, written for the student
}

/** One scene's picture, as the art checker saw it. */
export interface ArtSceneReview {
  scene: string;
  imageUrl: string | null; // the kept picture, served to admins; null when it is not in storage or the API cannot read it
  model: string | null; // who painted it; null: drawn by the brain, and not looked at
  kept: number | null; // the try kept
  check: ArtFindings | null;
  tries: (ArtFindings & { try: number | null; declined: string | null; redrawFailed: string | null })[];
}

/**
 * What the studio's checks found in a droplet, for an admin (GET /api/answers/:id/review): the fact-check, the code
 * checks on the bundle and the art checker's look at each picture. Null parts: made before they were sent.
 */
export interface DropletReview {
  id: string;
  factcheck: {
    status: string;
    reason: string;
    rounds: number | null;
    counts: { supported: number; filler: number; unsupported: number };
    lines: FactcheckLine[];
    corrections: FactcheckCorrection[];
  } | null;
  checks: { problems: { lineId: string; rule: string; detail: string; repaired: boolean }[] } | null;
  art: { scenes: ArtSceneReview[] } | null;
}

/** A step the studio is running now. */
export interface RunningStep {
  step: string;
  startedAt: string; // by the API's clock: what tells one try from the next
  seconds?: number; // how long it had run when the API answered; absent from an API older than this page
  since?: number; // when it started by this browser's clock, worked out from `seconds` as the answer arrived
}

/** Where the studio is while it makes a droplet (api/src/lib/progress.ts): its bill filling in. */
export interface Progress {
  running: RunningStep[];
  failed: string[]; // steps whose last try failed and that are not running again yet
  videos: boolean; // whether the videos' steps are part of this droplet
  // How long the droplet has been made so far when the API answered, counting on while a step runs; null: not
  // known. Absent from an API older than this page.
  seconds?: number | null;
  since?: number; // while a step runs: where that time counts on from, by this browser's clock, worked out from `seconds`
  breakdown: Breakdown | null; // the bill so far: the lesson's steps, then the videos'
}

/**
 * Each running step's start by this browser's clock, from how long the API says it has run, and the same for the
 * droplet's time so far: a computer whose clock is minutes off the API's would otherwise show them minutes off too.
 */
function localClock(q: Question): Question {
  const p = q.progress;
  const running = p?.running;
  if (!p || !running?.length) return q;
  const now = Date.now();
  return {
    ...q,
    progress: {
      ...p,
      running: running.map((r) => ({ ...r, since: now - (r.seconds ?? Math.max(0, (now - Date.parse(r.startedAt)) / 1000)) * 1000 })),
      ...(typeof p.seconds === 'number' && { since: now - p.seconds * 1000 }),
    },
  };
}

function onLocalClock<T extends { data: Question[] }>(page: T): T {
  return { ...page, data: page.data.map(localClock) };
}

/**
 * What the learner has left of their topics (GET /api/questions/allowance, api/src/lib/usage.ts). Each topic's
 * place comes back 24 hours after it was asked for, so the times are by this browser's clock, worked out from the
 * seconds the API gave as the answer arrived, and shown in the learner's own time zone.
 */
export interface TopicAllowance {
  perDay: number; // topics in any 24 hours
  left: number; // what the learner may ask for now, by their own limit
  nextAt: number | null; // with some left, when one more comes back; with none, when they may ask again; null: none coming
  siteFull: boolean; // every learner's topics together reached the site's day, whatever this one has left
  siteOpensAt: number | null; // siteFull: when the site takes topics again
}

interface TopicAllowanceAnswer extends Omit<TopicAllowance, 'nextAt' | 'siteOpensAt'> {
  nextInSeconds: number | null;
  siteOpensInSeconds: number | null;
}

function allowanceClock({ nextInSeconds, siteOpensInSeconds, ...rest }: TopicAllowanceAnswer): TopicAllowance {
  const now = Date.now();
  const at = (seconds: number | null) => (seconds === null ? null : now + seconds * 1000);
  return { ...rest, nextAt: at(nextInSeconds), siteOpensAt: at(siteOpensInSeconds) };
}

export interface Answer {
  id: string;
  questionId: string;
  agent: AgentRef;
  content: string;
  model: string | null;
  createdAt: string;
  video: Video | null; // null: a text-only droplet
  tags?: DropletTags; // each video's hashtags; absent from an API older than them
  breakdown: Breakdown | null; // null: the studio sent none (a lesson written by hand, or an older one)
  // How long it took from the request, by the API's clock; on a question's answer only, absent from an older API.
  endToEnd?: EndToEnd | null;
  hidden: boolean; // an admin took it off the public Droplets tab
  awaitingReview?: boolean; // READY, and off the Droplets tab until an admin has looked at it; absent from an older API
  // The thumbs-up and comments on the Droplets tab, which deleting the topic with everything takes too; absent from an
  // older API.
  likeCount?: number;
  commentCount?: number;
  replies: Reply[];
  awaitingAgent: boolean;
  // True while the page holds the lesson text back until its videos are READY or FAILED (at most an hour after
  // the lesson was posted); absent from an API older than this page, which shows the text at once.
  awaitingVideos?: boolean;
}

export interface Question {
  id: string;
  content: string;
  status: 'PENDING' | 'ANSWERED' | 'FAILED';
  failedReason: string | null; // FAILED: why the teacher gave up
  // The brain an admin picked for it (lib/brains.ts); null: the teacher's own. Absent from an API older than this page.
  brain?: Brain | null;
  attachments: Attachment[];
  createdAt: string;
  user: PublicUser | null; // null once the learner took it back: only an admin still sees it, with content ''
  answer: Answer | null;
  // While the droplet is being made: the steps running and the bill so far. Null once there is nothing left
  // to make; absent from an API older than this page.
  progress?: Progress | null;
  isOwner: boolean;
  canDelete: boolean;
}

/** Who paints a droplet's pictures, by the provider of the brain that wrote it (api/src/lib/studio-settings.ts). */
export type Painter = 'gemini' | 'muse' | 'svg';
export type ArtSettings = { claude: Painter; meta: Painter };
export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max';
// The brain that writes a topic no admin sent to a brain of its own, and Claude's effort when it is Claude's.
export type BrainSettings = { default: Brain; effort: Effort };
// Who makes the audio, the song and the narration together: ElevenLabs (Eleven Music and its voices) or Gemini (Lyria and
// Gemini's text-to-speech). Named `song` from before the narration followed it.
export type SongProvider = 'elevenlabs' | 'gemini';
export type SongSettings = { provider: SongProvider };
/** The most one topic may cost in model calls, its lesson, videos and retries together, in dollars; 0 for no ceiling. */
export type SpendSettings = { topicUsd: number };
/** Whether a person looks at each droplet before it lists on the public Droplets tab. */
export type ReviewSettings = { required: boolean };

/**
 * An environment variable an admin can set on /admin/studio instead (api/src/lib/knobs.ts): set there, it wins over
 * the variable; null, the variable is read again, else the default.
 */
export type KnobValue = number | string;
export interface Knob {
  where: 'api' | 'studio'; // the API's own apply at once; the studio's from the teacher's next run
  kind: 'int' | 'number' | 'text' | 'choice';
  default: KnobValue | null; // null: none (ELEVENLABS_USD_PER_1K_CREDITS: credits only)
  min?: number; max?: number; // int and number
  choices?: string[]; // choice
  rule?: string; // text: what it takes, in words
  value: KnobValue | null; // set here; null: not set here
  env?: string | null; // the API's own: what its environment has, null when unset
  inForce?: KnobValue; // the API's own: what it uses now
}

export interface StudioSettings {
  brain: BrainSettings;
  song: SongSettings;
  art: ArtSettings;
  knobs: Record<string, Knob>; // by the variable's name
  spend?: SpendSettings; // absent from an API older than the ceiling
  review?: ReviewSettings; // absent from an API older than the review
  updatedAt: string | null; // null: never changed, the defaults
  defaults: { brain: BrainSettings; song: SongSettings; art: ArtSettings; spend?: SpendSettings; review?: ReviewSettings };
  choices: {
    brain: { default: Brain[]; effort: Effort[] }; song: { provider: SongProvider[] };
    art: { claude: Painter[]; meta: Painter[] }; spend?: { topicUsd: { min: number; max: number } };
  };
}

/**
 * The announcement an admin sets on /admin/studio, shown to everyone under the nav (api/src/lib/announcement.ts).
 * `updatedAt` changes with every one put up, so a banner closed on this browser shows again for the next.
 */
export type AnnouncementTone = 'info' | 'warning';
export interface Announcement { message: string; tone: AnnouncementTone; updatedAt: string }

/** A droplet on the public tab (api/src/lib/droplet.ts). Nothing about who asked for it. */
export interface DropletVideos {
  songVideoUrl: string | null;
  lessonVideoUrl: string | null;
  songPosterUrl: string | null;
  lessonPosterUrl: string | null;
  hookVideoUrl?: string | null; // the hook cut: the song's pre-chorus and first chorus, upright, to share
  hookPosterUrl?: string | null;
}

/** Each video's hashtags, without the "#", most useful first (api/src/lib/tags.ts). The tab searches by them. */
export interface DropletTags {
  song: string[];
  lesson: string[];
}

/** One maker's models in what made a droplet, from its bills (api/src/lib/breakdown.ts, madeWithOf). */
export interface MadeWithMaker {
  maker: string | null; // Anthropic, Google, ElevenLabs, Meta...; null: the models no maker is listed for
  models: string[]; // as the studio ran them: claude-opus-5-5, gemini-3.1-flash-image...
}

export interface DropletCard {
  id: string;
  title: string; // "AP Biology: ATP synthesis"
  course: string;
  topic: string;
  excerpt: string; // the lesson's first lines, plain text
  video: DropletVideos;
  tags?: DropletTags; // absent from an API older than the tags
  // The models that made it, by maker, the brain's first. Null: no bill to say; absent from an API older than this.
  madeWith?: MadeWithMaker[] | null;
  // Absent from an API older than the likes and comments: the page then leaves them out.
  likeCount?: number;
  commentCount?: number;
  liked?: boolean; // the viewer's own thumbs-up; false when signed out
  createdAt: string;
  // Its newest comments: two on a card on the list, the first page on its own page. Absent from an older API.
  comments?: DropletCommentPage;
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
}

/**
 * The droplet the Learn page shows as an example (app/learn/example/route.ts): what its card draws, and nothing that
 * differs by viewer (likes, comments), so the web server can hand every visitor the same copy.
 */
export type DropletExample = Pick<Droplet, 'id' | 'title' | 'course' | 'topic' | 'excerpt' | 'content' | 'video' | 'tags' | 'createdAt'>;

/**
 * One video's play counts on a droplet (api/src/lib/plays.ts): totals, nothing about who watched. Each step counts
 * the plays that got that far, so none is above the one before it. shared and downloaded: the hook tile's buttons.
 */
export interface PlayCounts {
  started: number;
  reached25: number;
  reached50: number;
  reached75: number;
  finished: number; // 95% of it played
  shared: number;
  downloaded: number;
  finishRate: number | null; // finished / started; null before any start
}

export type PlayVideo = 'song' | 'lesson' | 'hook';

/** The owner's ranking of droplets by one video's finish rate, best first (GET /api/plays). */
export interface PlayRanking {
  video: PlayVideo;
  min: number; // the fewest starts a droplet needs to be ranked
  data: { id: string; title: string; course: string; topic: string; hidden: boolean; createdAt: string; plays: PlayCounts }[];
  fewer: number; // droplets with plays of this video, but fewer than min
  // Each video's counts over every droplet, whatever min: the drop-off curve. Only when asked (?totals=1), and absent
  // when they could not be read or from an API older than them.
  totals?: Record<PlayVideo, PlayCounts>;
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
  // acceptTerms: the person ticked the box agreeing to the Terms of Use and Privacy Policy; the API refuses without it.
  authGoogle: (code: string, acceptTerms: boolean) =>
    request<{ user: User }>('/auth/google', { method: 'POST', body: JSON.stringify({ code, acceptTerms }) }),
  // Agree to the current Terms, when the account's record is older or missing (components/TermsGate.tsx).
  acceptTerms: () => request<User>('/auth/terms', { method: 'POST', body: JSON.stringify({ accept: true }) }),
  getMe: () => request<User>('/auth/me'),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  // Deletes the signed-in account (api/src/routes/auth.ts); the lessons already made stay public, naming no one.
  deleteAccount: () => request<void>('/auth/me', { method: 'DELETE' }),

  // Questions
  listQuestions: (params?: Record<string, string>) =>
    request<{ data: Question[]; nextCursor: string | null }>(`/questions?${new URLSearchParams(params)}`).then(onLocalClock),
  getQuestion: (id: string) => request<Question>(`/questions/${id}`).then(localClock),
  getQuestionsByIds: (ids: string[]) =>
    request<{ data: Question[]; nextCursor: null }>(`/questions?ids=${encodeURIComponent(ids.join(','))}`).then(onLocalClock),
  // brain: an admin's pick for the topic (lib/brains.ts); left out, the teacher's own makes it.
  askQuestion: (content: string, brain?: Brain | null) =>
    request<Question>('/questions', { method: 'POST', body: JSON.stringify(brain ? { content, brain } : { content }) }).then(localClock),
  // everything: a topic with a lesson goes whole, the lesson, its videos, likes and comments too; without it, the
  // request is taken back and the lesson stays on the Droplets tab. A topic with no lesson goes whole either way.
  // Its own route, so an API from before it answers 404 ('Route not found') and deletes nothing (isMissingRoute).
  // expectNoLesson: the page shows the topic with no lesson; if one has landed since, the API answers 409 and
  // deletes nothing, so the learner can be shown the choice.
  deleteQuestion: (id: string, everything = false, expectNoLesson = false) =>
    request<void>(`/questions/${id}${everything ? '/everything' : expectNoLesson ? '?expect=no-lesson' : ''}`, { method: 'DELETE' }),
  getTopicAllowance: () => request<TopicAllowanceAnswer>('/questions/allowance').then(allowanceClock),
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
  // With a tag, only the droplets whose song or lesson video carries it.
  listDroplets: (cursor?: string | null, tag?: string | null, clips = false) => {
    const query = new URLSearchParams();
    if (tag) query.set('tag', tag);
    if (clips) query.set('clips', '1');
    if (cursor) query.set('cursor', cursor);
    const qs = query.toString();
    return request<{ data: DropletCard[]; nextCursor: string | null }>(`/droplets${qs ? `?${qs}` : ''}`);
  },
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
  // An admin having looked at a droplet that waits for review: true lists it on the Droplets tab (unless hidden).
  setDropletApproved: (answerId: string, approved: boolean) =>
    request<{ id: string; awaitingReview: boolean }>(`/answers/${answerId}`, { method: 'PATCH', body: JSON.stringify({ approved }) }),

  // Admin: what the studio's checks found in a droplet, under its lesson on /learn
  getDropletReview: (answerId: string) => request<DropletReview>(`/answers/${encodeURIComponent(answerId)}/review`),

  // Admin: droplets ranked by how many of one video's plays finished (api/src/routes/plays.ts)
  // withTotals: every droplet's totals too, for the drop-off chart; they sum every count, so the page asks once.
  listPlays: (video: PlayVideo, min: number, withTotals = false) =>
    request<PlayRanking>(`/plays?${new URLSearchParams({ video, min: String(min), limit: '200', ...(withTotals && { totals: '1' }) })}`),

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

  // Admin: the studio's settings (api/src/routes/studio.ts). A change reaches the teacher's next run.
  getStudioSettings: () => request<StudioSettings>('/studio/settings'),
  setStudioSettings: (change: {
    brain?: Partial<BrainSettings>; song?: SongSettings; art?: Partial<ArtSettings>; knobs?: Record<string, KnobValue | null>;
    spend?: SpendSettings; review?: ReviewSettings;
  }) =>
    request<StudioSettings>('/studio/settings', { method: 'PUT', body: JSON.stringify(change) }),

  // The announcement under the nav (api/src/routes/announcement.ts): anyone reads it, admins set it on /admin/studio.
  // Putting one up or taking it down lands once however many times it is sent.
  getAnnouncement: () => request<{ announcement: Announcement | null }>('/announcement'),
  setAnnouncement: (message: string, tone: AnnouncementTone) =>
    request<{ announcement: Announcement }>('/announcement', { method: 'PUT', body: JSON.stringify({ message, tone }), idempotent: true }),
  clearAnnouncement: () => request<{ announcement: null }>('/announcement', { method: 'DELETE', idempotent: true }),
};
