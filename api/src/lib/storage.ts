/**
 * Signed upload URLs for Cloudflare R2 (docs/PLAN.md, section 10).
 *
 * The studio uploads its files straight to R2, so they never pass through the API, and
 * the R2 key stays here: the API signs a one-file PUT URL and hands only that to the
 * studio. The signature is made here, with AWS Signature Version 4 (which R2's S3 API
 * takes), so signing asks nothing of R2 and cannot fail once the settings are whole. A
 * wrong key or a missing bucket shows when the studio uploads, and in the API log at boot
 * (storageReachable).
 *
 * Two buckets, created by hand in the Cloudflare dashboard (docs/DEPLOY.md, section 6):
 *   droplets  public through its custom domain (R2_PUBLIC_URL): the videos and their posters
 *   studio    private: the art, masks, audio, timings and timelines, kept for a re-render
 */
import { createHash, createHmac } from 'node:crypto';

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

// What the public bucket serves to anyone: the two videos and their posters. Nothing else may be put there, so the
// agent key cannot use the bucket to host a page or a file of any other kind. R2 has no per-bucket list of types, so
// the type is signed into each upload URL (signUpload): R2 refuses the bytes under any other Content-Type.
const PUBLIC_EXTENSIONS = ['.mp4', '.jpg', '.jpeg', '.png', '.webp'];

const CONTENT_TYPES: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.mp3': 'audio/mpeg',
  '.json': 'application/json',
};

/** The Content-Type a file is stored and served with, from its extension. */
export function contentType(file: string): string {
  const dot = file.lastIndexOf('.');
  return (dot >= 0 && CONTENT_TYPES[file.slice(dot).toLowerCase()]) || 'application/octet-stream';
}

// A public file never changes once a viewer can see it: each render attempt writes its own a<n>/ folder, and a
// droplet's files are frozen once READY (no upload URL after it). So browsers and Cloudflare's cache keep them. Sent
// with the upload but not signed into it (signUpload).
export const PUBLIC_CACHE_CONTROL = 'public, max-age=31536000, immutable';

/** Why a file may not go in this bucket, or null. */
export function bucketFileError(bucket: Bucket, file: string): string | null {
  if (bucket !== PUBLIC_BUCKET) return null;
  const lower = file.toLowerCase();
  return PUBLIC_EXTENSIONS.some((ext) => lower.endsWith(ext))
    ? null
    : `the ${PUBLIC_BUCKET} bucket takes only ${PUBLIC_EXTENSIONS.join(', ')} files`;
}

/**
 * The droplets bucket of the Supabase project in SUPABASE_URL, where the videos were stored before R2, while that
 * setting is still there; else null. A render that spans the switch (it started on the old API, or the new studio
 * met an API not yet redeployed) can report files from there, and lib/storage-move.ts moves them to R2.
 */
export function legacyPublicPrefix(env: NodeJS.ProcessEnv = process.env): string | null {
  const raw = env.SUPABASE_URL?.trim().replace(/\/+$/, '');
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' && url.pathname === '/' && !url.search ? `${url.origin}/storage/v1/object/public/${PUBLIC_BUCKET}/` : null;
  } catch {
    return null;
  }
}

/**
 * Why a video or poster URL is not one of this droplet's own files in the public bucket, or null. A droplet on the
 * Droplets tab plays and shows only what this API signed an upload for, under <R2_PUBLIC_URL>/<answerId>/ (or, while
 * SUPABASE_URL is set, the same folder of the old Supabase bucket), so the agent key cannot point a public page at
 * another host. Without storage set up there is nothing to check against: a local API takes any http(s) URL (the
 * seeded sample clips), and a production one takes none.
 */
export function dropletFileUrlError(
  value: string,
  field: string,
  answerId: string,
  env: NodeJS.ProcessEnv = process.env,
): string | null {
  const config = storageConfig(env);
  if (!config || 'problem' in config) {
    return env.NODE_ENV === 'production' ? `${field}: file storage is not set up on this API, so it takes no video URLs` : null;
  }
  const own = `${config.publicUrl}/${answerId}/`;
  const legacy = legacyPublicPrefix(env);
  const prefix = [own, ...(legacy ? [`${legacy}${answerId}/`] : [])].find((p) => value.startsWith(p));
  const file = prefix ? value.slice(prefix.length) : null;
  return file === null || fileError(file) || bucketFileError(PUBLIC_BUCKET, file)
    ? `${field} must be a file uploaded for this droplet, under ${own}`
    : null;
}

/**
 * The four R2 settings. R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY are the pair Cloudflare shows once when an R2 API
 * token is made (R2 > Overview > API Tokens > Manage), not the token's own value.
 */
export type StorageConfig = {
  endpoint: string; // R2's S3 API for the account, https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com
  accessKeyId: string;
  secretAccessKey: string;
  publicUrl: string; // where anyone reads the droplets bucket: its custom domain, such as https://media.popsmosis.com
};
export type StorageProblem = { problem: string };

export const STORAGE_SETTINGS = ['R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_PUBLIC_URL'] as const;

// The droplets bucket's public address alone: the public URLs are built on it, so a path or a query would break them.
function publicOrigin(url: string): string | StorageProblem {
  const problem = { problem: 'R2_PUBLIC_URL must be the droplets bucket\'s public address alone, such as https://media.popsmosis.com, with no path' };
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return problem;
  }
  const bare = parsed.pathname === '/' && !parsed.search && !parsed.hash && !parsed.username && !parsed.password;
  if (!bare) return problem;
  if (parsed.hostname.endsWith('.r2.cloudflarestorage.com')) {
    return { problem: 'R2_PUBLIC_URL is the S3 API endpoint, which nobody can read without the key; set it to the droplets bucket\'s custom domain, such as https://media.popsmosis.com' };
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
  if (parsed.protocol === 'https:' || (parsed.protocol === 'http:' && local)) return parsed.origin;
  return { problem: 'R2_PUBLIC_URL must start with https://' };
}

/**
 * R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_PUBLIC_URL: null when none is set (uploads are not set
 * up), the problem when some are missing or one cannot be right.
 */
export function storageConfig(env: NodeJS.ProcessEnv = process.env): StorageConfig | StorageProblem | null {
  const value = (name: (typeof STORAGE_SETTINGS)[number]) => env[name]?.trim() ?? '';
  const missing = STORAGE_SETTINGS.filter((name) => !value(name));
  if (missing.length === STORAGE_SETTINGS.length) return null;
  if (missing.length) return { problem: `${missing.join(', ')} ${missing.length === 1 ? 'is' : 'are'} not set; uploads need all of ${STORAGE_SETTINGS.join(', ')}` };

  const accountId = value('R2_ACCOUNT_ID').toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(accountId)) {
    return { problem: 'R2_ACCOUNT_ID must be the 32-character account ID (R2 > Overview > Account Details), not a URL' };
  }
  const publicUrl = publicOrigin(value('R2_PUBLIC_URL').replace(/\/+$/, ''));
  if (typeof publicUrl !== 'string') return publicUrl;
  return {
    endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
    accessKeyId: value('R2_ACCESS_KEY_ID'),
    secretAccessKey: value('R2_SECRET_ACCESS_KEY'),
    publicUrl,
  };
}

// --- AWS Signature Version 4, query-signed (a presigned URL), as R2's S3 API takes it ---------------------------------

const REGION = 'auto'; // R2 has one region, and calls it this

// RFC 3986: everything but letters, digits and - . _ ~ is percent-encoded, as SigV4 wants.
function uriEncode(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

const hmac = (key: Buffer | string, data: string) => createHmac('sha256', key).update(data, 'utf8').digest();
const sha256Hex = (data: string) => createHash('sha256').update(data, 'utf8').digest('hex');

/**
 * A URL that lets whoever holds it make one request to R2 until it expires: `method` on `path` (/<bucket>[/<key>])
 * with `query`, carrying exactly `headers`, whose names and values are signed into it. The body is not signed.
 */
export function presign(
  config: Pick<StorageConfig, 'endpoint' | 'accessKeyId' | 'secretAccessKey'>,
  method: string,
  path: string,
  headers: Record<string, string>,
  query: Record<string, string>,
  expiresSeconds: number,
  now: Date = new Date(),
): string {
  const endpoint = new URL(config.endpoint);
  const amzDate = now.toISOString().replace(/[-:]|\.\d{3}/g, ''); // 20261029T120000Z
  const scope = `${amzDate.slice(0, 8)}/${REGION}/s3/aws4_request`;

  const signing = new Map<string, string>([['host', endpoint.host]]);
  for (const [name, value] of Object.entries(headers)) signing.set(name.toLowerCase(), value.trim().replace(/\s+/g, ' '));
  const names = [...signing.keys()].sort();
  const signedHeaders = names.join(';');

  const params: Record<string, string> = {
    ...query,
    'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
    'X-Amz-Credential': `${config.accessKeyId}/${scope}`,
    'X-Amz-Date': amzDate,
    'X-Amz-Expires': String(expiresSeconds),
    'X-Amz-SignedHeaders': signedHeaders,
  };
  const canonicalQuery = Object.entries(params)
    .map(([k, v]) => [uriEncode(k), uriEncode(v)])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join('&');
  const canonicalUri = path.split('/').map(uriEncode).join('/');
  const canonicalRequest = [
    method,
    canonicalUri,
    canonicalQuery,
    names.map((name) => `${name}:${signing.get(name)}\n`).join(''),
    signedHeaders,
    'UNSIGNED-PAYLOAD',
  ].join('\n');

  const stringToSign = ['AWS4-HMAC-SHA256', amzDate, scope, sha256Hex(canonicalRequest)].join('\n');
  const key = ['s3', 'aws4_request'].reduce(
    (k, part) => hmac(k, part),
    hmac(hmac(`AWS4${config.secretAccessKey}`, amzDate.slice(0, 8)), REGION),
  );
  const signature = createHmac('sha256', key).update(stringToSign, 'utf8').digest('hex');
  return `${endpoint.origin}${canonicalUri}?${canonicalQuery}&X-Amz-Signature=${signature}`;
}

// --- The upload URL ---------------------------------------------------------------------------------------------------

// The studio signs a fresh URL for each try of each file and starts the PUT at once; R2 checks the time when the
// upload starts, so a long one is not cut off.
export const UPLOAD_URL_SECONDS = 2 * 60 * 60;

export type SignedUpload = {
  uploadUrl: string; // PUT the file's bytes here,
  headers: Record<string, string>; // with these headers; Content-Type is signed into uploadUrl
  path: string; // bucket/answerId/file
  publicUrl: string | null; // where anyone can read it, for the public bucket only
};

export function signUpload(config: StorageConfig, bucket: Bucket, answerId: string, file: string, now: Date = new Date()): SignedUpload {
  const key = `${answerId}/${file}`;
  // The type is signed, so R2 refuses the bytes under any other (a page under a video's name). The cache header is
  // not: it is the studio's to send, so a studio from before the move to R2, which sends its own, still uploads.
  const signed = { 'Content-Type': contentType(file) };
  const headers: Record<string, string> = bucket === PUBLIC_BUCKET ? { ...signed, 'Cache-Control': PUBLIC_CACHE_CONTROL } : signed;
  return {
    uploadUrl: presign(config, 'PUT', `/${bucket}/${key}`, signed, {}, UPLOAD_URL_SECONDS, now),
    headers,
    path: `${bucket}/${key}`,
    publicUrl: bucket === PUBLIC_BUCKET ? `${config.publicUrl}/${key}` : null,
  };
}

// --- The check at boot ------------------------------------------------------------------------------------------------

// R2 answers an S3 error as XML: <Error><Code>NoSuchBucket</Code><Message>The specified bucket does not exist.</Message>
export async function r2Reason(res: Response): Promise<string> {
  const body = await res.text().catch(() => '');
  const code = /<Code>([^<]{1,100})<\/Code>/.exec(body)?.[1];
  const message = /<Message>([^<]{1,300})<\/Message>/.exec(body)?.[1];
  return `${res.status}${code ? ` ${code}` : ''}${message ? `: ${message}` : ''}`;
}

/**
 * Asks R2 to list one file of each bucket with the API's key, so a wrong key, a token that cannot reach a bucket or a
 * bucket that does not exist is named in the log at boot rather than at the studio's first upload. Null when both
 * answer. It reads only; whether the token may write shows at the first upload.
 */
export async function storageReachable(config: StorageConfig, fetchImpl: typeof fetch = fetch): Promise<string | null> {
  for (const bucket of BUCKETS) {
    const url = presign(config, 'GET', `/${bucket}`, {}, { 'list-type': '2', 'max-keys': '1' }, 60);
    let res: Response;
    try {
      res = await fetchImpl(url, { signal: AbortSignal.timeout(10_000) });
    } catch (err) {
      return `R2 did not answer for the ${bucket} bucket: ${(err as Error).message}`;
    }
    if (!res.ok) {
      const reason = await r2Reason(res);
      if (res.status === 404) return `R2 refused the ${bucket} bucket (${reason}); check that the bucket is named exactly "${bucket}" in the account R2_ACCOUNT_ID names`;
      if (res.status === 401 || res.status === 403) {
        return `R2 refused the ${bucket} bucket (${reason}); check R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY, and that the R2 token may read and write this bucket`;
      }
      return `R2 did not take the request for the ${bucket} bucket (${reason}); it may be down for a moment, so the settings may be fine`;
    }
    await res.body?.cancel();
  }
  return null;
}
