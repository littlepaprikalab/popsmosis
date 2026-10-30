/**
 * Moves the droplet videos and posters still in Supabase Storage to R2, so the old bucket can go (docs/DEPLOY.md,
 * section 6, "Videos made before the move"). The API runs it in the background once it is listening, and again every
 * hour (sweepSupabaseVideos); once nothing points at Supabase, a sweep costs one query.
 *
 * For each answer whose URLs still point at Supabase's public droplets bucket, each of those files is read from its
 * public URL, put on R2 at the same place (<answerId>/a<n>/out/…), and read back from R2_PUBLIC_URL. Only when every
 * one of an answer's files answers there are its URLs switched, and only if nothing changed them meanwhile. The files
 * stay in Supabase, so going back is the same switch the other way. A droplet whose move fails keeps playing from
 * Supabase, and the next sweep tries it again.
 *
 * Only the public files move: the private studio bucket's re-render kits are read by nothing yet, and cost nothing
 * to download while they sit in Supabase.
 */
import type prisma from './prisma.js';
import { VIDEO_FIELDS, type VideoField } from './droplet.js';
import { bucketFileError, fileError, PUBLIC_BUCKET, r2Reason, signUpload, type StorageConfig } from './storage.js';

// A public file in a Supabase project's droplets bucket, as the studio used to report it.
const SUPABASE_DROPLETS = /^https:\/\/[a-z0-9-]+\.supabase\.co\/storage\/v1\/object\/public\/droplets\//;
const MARKER = '/storage/v1/object/public/droplets/';
const FILE_TIMEOUT_MS = 120_000;

type Row = { id: string } & Record<VideoField, string | null>;
export type MoveReport = { moved: string[]; failed: { answerId: string; reason: string }[] };

/** Where in R2 a Supabase URL's file goes: its path under <answerId>/, or why it cannot be moved. */
function fileOf(row: Row, url: string): { file: string } | { problem: string } {
  const key = url.replace(SUPABASE_DROPLETS, '');
  if (!key.startsWith(`${row.id}/`)) return { problem: `${url} is not under this droplet's own folder` };
  const file = key.slice(row.id.length + 1);
  const problem = fileError(file) ?? bucketFileError(PUBLIC_BUCKET, file);
  return problem ? { problem: `${url}: ${problem}` } : { file };
}

// Cloudflare refuses some requests that do not say what they are (error 1010), as it once did the studio's.
const USER_AGENT = 'popsmosis-api/1 (moving videos to R2)';

async function ask(fetchImpl: typeof fetch, url: string, init: RequestInit, timeoutMs = FILE_TIMEOUT_MS): Promise<Response> {
  try {
    return await fetchImpl(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
  } catch (err) {
    throw new Error(`${url.split('?')[0]} did not answer: ${(err as Error).message}`);
  }
}

async function copyOne(config: StorageConfig, answerId: string, from: string, file: string, fetchImpl: typeof fetch): Promise<string> {
  const source = await ask(fetchImpl, from, { headers: { 'User-Agent': USER_AGENT } });
  if (!source.ok) throw new Error(`Supabase answered ${source.status} for ${from}`);
  const bytes = new Uint8Array(await source.arrayBuffer());

  const signed = signUpload(config, PUBLIC_BUCKET, answerId, file);
  const put = await ask(fetchImpl, signed.uploadUrl, { method: 'PUT', headers: signed.headers, body: bytes });
  if (!put.ok) throw new Error(`R2 refused ${answerId}/${file} (${await r2Reason(put)})`);

  // Read back from the address viewers will use, so a domain that is not Active yet never takes a droplet's videos away.
  const publicUrl = signed.publicUrl!;
  const check = await ask(fetchImpl, publicUrl, { method: 'HEAD', headers: { 'User-Agent': USER_AGENT } }, 30_000);
  if (!check.ok) {
    throw new Error(`${publicUrl} answered ${check.status}${check.status === 404 ? `; is ${config.publicUrl} Active on the droplets bucket?` : ''}`);
  }
  const length = check.headers.get('content-length');
  if (length !== null && Number(length) !== bytes.length) throw new Error(`${publicUrl} has ${length} bytes, not the ${bytes.length} copied`);
  return publicUrl;
}

// Every video field, as a select and as the condition that none of them changed.
const SELECT = Object.fromEntries([['id', true], ...VIDEO_FIELDS.map((field) => [field, true])]) as { id: true } & Record<VideoField, true>;
const unchanged = (row: Row) => Object.fromEntries(VIDEO_FIELDS.map((field) => [field, row[field]])) as Record<VideoField, string | null>;

export async function moveSupabaseVideos(config: StorageConfig, db: typeof prisma, fetchImpl: typeof fetch = fetch): Promise<MoveReport> {
  const rows: Row[] = await db.answer.findMany({
    where: { OR: VIDEO_FIELDS.map((field) => ({ [field]: { contains: MARKER } })) },
    select: SELECT,
    orderBy: { createdAt: 'asc' },
  });
  const report: MoveReport = { moved: [], failed: [] };

  for (const row of rows) {
    try {
      const switched: Partial<Record<VideoField, string>> = {};
      for (const field of VIDEO_FIELDS) {
        const from = row[field];
        if (!from || !SUPABASE_DROPLETS.test(from)) continue;
        const where = fileOf(row, from);
        if ('problem' in where) throw new Error(where.problem);
        switched[field] = await copyOne(config, row.id, from, where.file, fetchImpl);
      }
      if (!Object.keys(switched).length) continue;
      // Only if the URLs are still the ones this run copied: a change made meanwhile wins.
      const { count } = await db.answer.updateMany({ where: { id: row.id, ...unchanged(row) }, data: switched });
      if (count === 1) {
        report.moved.push(row.id);
        continue;
      }
      // Another API instance moving the same droplet got there first, to the same place: nothing is wrong.
      const now: Row | null = await db.answer.findUnique({ where: { id: row.id }, select: SELECT });
      const same = now && (Object.entries(switched) as [VideoField, string][]).every(([field, url]) => now[field] === url);
      if (!same) report.failed.push({ answerId: row.id, reason: 'its URLs changed while the files were copied; the next sweep tries again' });
    } catch (err) {
      report.failed.push({ answerId: row.id, reason: (err as Error).message });
    }
  }
  return report;
}

// How often the API looks again for videos still on Supabase: a droplet reported READY by an old API instance during
// a redeploy, or one whose move failed, is moved within the hour. Nothing left to move costs one query.
export const MOVE_EVERY_MS = 60 * 60 * 1000;

let sweeping = false;

/** moveSupabaseVideos, unless a sweep is still running in this process (then nothing: the running one covers it). */
export async function sweepSupabaseVideos(config: StorageConfig, db: typeof prisma, fetchImpl: typeof fetch = fetch): Promise<MoveReport | null> {
  if (sweeping) return null;
  sweeping = true;
  try {
    return await moveSupabaseVideos(config, db, fetchImpl);
  } finally {
    sweeping = false;
  }
}
