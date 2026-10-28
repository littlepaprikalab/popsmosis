/**
 * Signed upload URLs for Supabase Storage (docs/PLAN.md, section 10).
 *
 * The studio uploads its files straight to Supabase, so they never pass through
 * the API, and the service-role key stays here: the API asks Supabase for a
 * one-file upload URL and hands only that to the studio. Supabase's URL is good
 * for two hours and, because it is minted with x-upsert, overwrites the file, so
 * a re-render replaces its videos in place.
 *
 * Two buckets, created by hand in the Supabase dashboard:
 *   droplets  public: the videos and their posters, served to anyone
 *   studio    private: the art, masks, audio, timings and timelines, kept for a re-render
 */
export const BUCKETS = ['droplets', 'studio'] as const;
export type Bucket = (typeof BUCKETS)[number];
export const PUBLIC_BUCKET: Bucket = 'droplets';

// A path inside the answer's folder: segments of letters, digits, dot, dash and
// underscore, each starting with a letter or digit (so never "." or ".."). The
// studio slugs structure names before it uploads a mask.
const FILE_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9][A-Za-z0-9._-]*)*$/;
export const FILE_PATH_MAX_LENGTH = 300;

export function fileError(file: unknown): string | null {
  if (typeof file !== 'string' || !file) return 'file is required';
  if (file.length > FILE_PATH_MAX_LENGTH) return `file must be ${FILE_PATH_MAX_LENGTH} characters or less`;
  if (!FILE_RE.test(file)) return 'file must be a relative path of letters, digits, ".", "-" and "_", such as "out/song_h.mp4"';
  return null;
}

/**
 * SUPABASE_SERVICE_ROLE_KEY takes one of the two keys that may write to storage, and
 * each is sent the way Supabase takes it:
 *   sb_secret_…  the secret key (Project Settings > API Keys). Only as apikey: it is not
 *                a JWT, and Supabase refuses one sent as a bearer token.
 *   eyJ…         the legacy service_role JWT, as apikey and as a bearer token. Supabase
 *                retires the legacy keys by the end of 2026.
 * The publishable key (sb_publishable_…) and the legacy anon JWT are browser keys that
 * storage refuses every upload, so they are named as the problem before any upload.
 */
export type StorageConfig = {
  url: string; // the project's origin, https://<project-ref>.supabase.co
  headers: Record<string, string>;
  legacy: boolean; // the legacy service_role JWT, which stops working when Supabase retires it
};
export type StorageProblem = { problem: string };

const NEEDS = 'set it to the secret key (sb_secret_…) from Supabase > Project Settings > API Keys';

/** The legacy JWT's role claim, or null when the key is not a JWT. */
function jwtRole(key: string): string | null {
  const parts = key.split('.');
  if (parts.length !== 3) return null;
  try {
    const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8')) as { role?: unknown } | null;
    return typeof claims?.role === 'string' ? claims.role : null;
  } catch {
    return null;
  }
}

function keyHeaders(key: string): { headers: Record<string, string> } | StorageProblem {
  if (key.startsWith('sb_secret_')) return { headers: { apikey: key } };
  if (key.startsWith('sb_publishable_')) {
    return { problem: `SUPABASE_SERVICE_ROLE_KEY is the publishable key, which cannot upload; ${NEEDS}` };
  }
  const role = jwtRole(key);
  if (role === 'service_role') return { headers: { apikey: key, authorization: `Bearer ${key}` } };
  if (role) return { problem: `SUPABASE_SERVICE_ROLE_KEY is the legacy ${role} key, which cannot upload; ${NEEDS}` };
  return { problem: `SUPABASE_SERVICE_ROLE_KEY is not a Supabase secret key; ${NEEDS}` };
}

// The project URL alone: the upload and public URLs are built on it, so a path pasted
// with it (such as /storage/v1) would break both.
function projectOrigin(url: string): string | StorageProblem {
  const problem = { problem: 'SUPABASE_URL must be the project URL alone, https://<project-ref>.supabase.co, with no path' };
  try {
    const parsed = new URL(url);
    const bare = parsed.pathname === '/' && !parsed.search && !parsed.hash && !parsed.username && !parsed.password;
    return bare && (parsed.protocol === 'https:' || parsed.protocol === 'http:') ? parsed.origin : problem;
  } catch {
    return problem;
  }
}

/**
 * SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY: null when uploads are not set up, the
 * problem when they cannot sign them.
 */
export function storageConfig(env: NodeJS.ProcessEnv = process.env): StorageConfig | StorageProblem | null {
  const rawUrl = env.SUPABASE_URL?.trim().replace(/\/+$/, '');
  const key = env.SUPABASE_SERVICE_ROLE_KEY?.trim();
  if (!rawUrl || !key) return null;
  const url = projectOrigin(rawUrl);
  if (typeof url !== 'string') return url;
  const auth = keyHeaders(key);
  return 'problem' in auth ? auth : { url, headers: auth.headers, legacy: !key.startsWith('sb_secret_') };
}

/** Supabase did not give an upload URL. retryable: a moment may fix it (no answer, 429, a 5xx). */
export class StorageError extends Error {
  retryable: boolean;
  constructor(message: string, retryable: boolean) {
    super(message);
    this.retryable = retryable;
  }
}

export type SignedUpload = {
  uploadUrl: string; // PUT the file's bytes here, with its Content-Type
  path: string; // bucket/answerId/file
  publicUrl: string | null; // where anyone can read it, for the public bucket only
};

// Storage's error bodies come in a few shapes: { statusCode, error, message },
// { code, message } and { httpStatusCode, code, message }.
async function refusal(res: Response): Promise<StorageError> {
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  const said = [body?.message, body?.error, body?.code].find((v): v is string => typeof v === 'string' && v !== '');
  const inner = Number(body?.statusCode ?? body?.httpStatusCode);
  const transient = [res.status, inner].some((s) => s === 429 || s >= 500);
  return new StorageError(`storage answered ${res.status}${said ? `: ${said.slice(0, 200)}` : ''}`, transient);
}

export async function signUpload(
  config: StorageConfig,
  bucket: Bucket,
  answerId: string,
  file: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SignedUpload> {
  const object = `${bucket}/${answerId}/${file}`;
  let res: Response;
  try {
    res = await fetchImpl(`${config.url}/storage/v1/object/upload/sign/${object}`, {
      method: 'POST',
      headers: { ...config.headers, 'content-type': 'application/json', 'x-upsert': 'true' },
      body: '{}',
      signal: AbortSignal.timeout(10_000),
    });
  } catch (err) {
    throw new StorageError(`storage unreachable: ${(err as Error).message}`, true);
  }
  if (!res.ok) throw await refusal(res);
  const body = (await res.json().catch(() => null)) as { url?: unknown } | null;
  // A path relative to /storage/v1, carrying the upload token as ?token=.
  if (typeof body?.url !== 'string' || !body.url.startsWith('/')) throw new StorageError('storage sent no upload URL', true);
  return {
    uploadUrl: `${config.url}/storage/v1${body.url}`,
    path: object,
    publicUrl: bucket === PUBLIC_BUCKET ? `${config.url}/storage/v1/object/public/${object}` : null,
  };
}
