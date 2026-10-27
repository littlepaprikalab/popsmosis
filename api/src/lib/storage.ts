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

export type StorageConfig = { url: string; key: string };

/** SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, or null when uploads are not set up. */
export function storageConfig(env: NodeJS.ProcessEnv = process.env): StorageConfig | null {
  const url = env.SUPABASE_URL?.trim().replace(/\/+$/, '');
  const key = env.SUPABASE_SERVICE_ROLE_KEY?.trim();
  return url && key ? { url, key } : null;
}

export class StorageError extends Error {}

export type SignedUpload = {
  uploadUrl: string; // PUT the file's bytes here, with its Content-Type
  path: string; // bucket/answerId/file
  publicUrl: string | null; // where anyone can read it, for the public bucket only
};

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
      headers: {
        authorization: `Bearer ${config.key}`,
        apikey: config.key,
        'content-type': 'application/json',
        'x-upsert': 'true',
      },
      body: '{}',
      signal: AbortSignal.timeout(10_000),
    });
  } catch (err) {
    throw new StorageError(`storage unreachable: ${(err as Error).message}`);
  }
  if (!res.ok) throw new StorageError(`storage answered ${res.status}`);
  const body = (await res.json().catch(() => null)) as { url?: unknown } | null;
  // A path relative to /storage/v1, carrying the upload token as ?token=.
  if (typeof body?.url !== 'string' || !body.url.startsWith('/')) throw new StorageError('storage sent no upload URL');
  return {
    uploadUrl: `${config.url}/storage/v1${body.url}`,
    path: object,
    publicUrl: bucket === PUBLIC_BUCKET ? `${config.url}/storage/v1/object/public/${object}` : null,
  };
}
