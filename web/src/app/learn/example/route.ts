import type { Droplet, DropletCard, DropletExample } from '@/lib/api';

/**
 * GET /learn/example: the droplet the Learn page shows as an example (components/ExampleDroplet.tsx), the same for
 * every visitor. It is the front door (/ comes here), so the web server asks the API for it at most once a minute
 * and hands everyone that copy, rather than every visitor's browser spending two of the public reads the Droplets tab
 * shares (api/src/lib/public-limit.ts: per visitor's address, and per API instance). A minute, not more: a droplet an
 * admin takes off the tab leaves the front door about as soon (browsers keep their copy a minute more).
 *
 * The newest droplet on the tab with a song (the song is the hook), else the newest. A copy past its time is still
 * handed out while the next one is fetched, and kept when that fails, so an API that is asleep or mid-deploy costs
 * the page nothing; with no copy yet, a failure is tried again only after a pause.
 */
export const dynamic = 'force-dynamic'; // read when asked, never at build: the build has no droplets to show

const FRESH_MS = 60_000;
const RETRY_MS = 30_000;
const TIMEOUT_MS = 8_000;
const API_URL = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3001').replace(/\/+$/, '');

type Held = { value: DropletExample | null; at: number };
let held: Held | null = null;
let refreshing: Promise<Held | null> | null = null;
let retryAt = 0;

async function get<T>(path: string): Promise<T> {
  const headers: Record<string, string> = { accept: 'application/json' };
  // Through the API's gate as the web app's proxy is (web/src/middleware.ts); no cookie, so nothing in it is anyone's.
  if (process.env.PROXY_SECRET) headers['x-proxy-token'] = process.env.PROXY_SECRET;
  const res = await fetch(`${API_URL}${path}`, { headers, cache: 'no-store', signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return res.json() as Promise<T>;
}

async function fetchExample(): Promise<DropletExample | null> {
  const { data } = await get<{ data: DropletCard[] }>('/api/droplets?limit=6');
  const pick = data.find((d) => d.video.songVideoUrl) ?? data[0];
  if (!pick) return null;
  const d = await get<Droplet>(`/api/droplets/${encodeURIComponent(pick.id)}`);
  // Only what the card draws: the comments and likes that came with it differ by viewer, and are not shown.
  return {
    id: d.id, title: d.title, course: d.course, topic: d.topic, excerpt: d.excerpt, content: d.content,
    video: d.video, tags: d.tags, createdAt: d.createdAt,
  };
}

/** One fetch at a time, however many visitors arrive while it runs. */
function refresh(): Promise<Held | null> {
  refreshing ??= fetchExample()
    .then((value) => (held = { value, at: Date.now() }))
    .catch((err) => {
      console.warn('[learn/example] could not fetch the example droplet:', err instanceof Error ? err.message : err);
      retryAt = Date.now() + RETRY_MS;
      return held;
    })
    .finally(() => { refreshing = null; });
  return refreshing;
}

export async function GET() {
  const now = Date.now();
  if ((!held || now - held.at >= FRESH_MS) && now >= retryAt) void refresh();
  // Nothing held yet: wait for the first copy. Otherwise answer at once with the one held, even while it is renewed.
  const current = held ?? (refreshing ? await refreshing : null);
  if (!current) return Response.json({ droplet: null }, { headers: { 'cache-control': 'no-store' } });
  return Response.json({ droplet: current.value }, { headers: { 'cache-control': 'public, max-age=60' } });
}
