/**
 * Clears a deleted droplet's files from R2 (DELETE /api/questions/:id?everything=true): everything under <answerId>/ in
 * both buckets, the public videos, hook cut and posters, and the private re-render kit (art, masks, audio, timings).
 *
 * The route records the droplet in storage_deletions in the transaction that deletes it (queueFileDeletion), then has
 * its files cleared at once (clearSoon), and the API sweeps the table again every hour (sweepDeletedFiles). A row stays
 * until a pass that starts after every upload URL signed for the droplet has run out (SETTLE_MS) clears its folders: a
 * render still running when the learner deleted the topic holds URLs it can still upload with, and that later pass
 * takes whatever it put there. When R2 refuses or does not answer, the row keeps the reason and the next sweep tries
 * again.
 *
 * Cloudflare's cache can go on serving a public file it already holds for a while after R2 lets it go (they are sent
 * as immutable, lib/storage.ts); nothing on the site links to one any more.
 */
import type { FastifyBaseLogger } from 'fastify';
import type prisma from './prisma.js';
import type { TxClient } from './prisma.js';
import { BUCKETS, fileError, presign, r2Reason, storageConfig, UPLOAD_URL_SECONDS, type Bucket, type StorageConfig } from './storage.js';

// Past this, no URL signed before the delete can still start an upload (R2 checks the time as one starts), with half
// an hour for an upload still going and for clocks that disagree.
export const SETTLE_MS = UPLOAD_URL_SECONDS * 1000 + 30 * 60 * 1000;
export const SWEEP_EVERY_MS = 60 * 60 * 1000;

const LIST_MAX_KEYS = 1000; // S3's most per page
const MAX_ROUNDS = 20; // 20,000 files: far past what a droplet has
const PARALLEL = 8;
const TIMEOUT_MS = 30_000;
const SWEEP_BATCH = 100;
const ANSWER_ID_RE = /^[A-Za-z0-9_-]{1,64}$/; // one plain folder name: never "", "." or a path

export type ClearReport = { cleared: { answerId: string; files: number; done: boolean }[]; failed: { answerId: string; reason: string }[] };

/** Records droplets whose files are to be cleared, inside the transaction that deletes them. None on an API with no storage. */
export async function queueFileDeletion(tx: TxClient, answerIds: string[], env: NodeJS.ProcessEnv = process.env): Promise<void> {
  // A misconfigured storage still records them, for when it is fixed; an API with none never signed an upload.
  if (answerIds.length === 0 || storageConfig(env) === null) return;
  await tx.storageDeletion.createMany({ data: answerIds.map((answerId) => ({ answerId })), skipDuplicates: true });
}

async function ask(fetchImpl: typeof fetch, url: string, init: RequestInit = {}): Promise<Response> {
  try {
    return await fetchImpl(url, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (err) {
    throw new Error(`R2 did not answer: ${(err as Error).message}`);
  }
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
const xmlText = (s: string) => s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, e: string) => (
  e[0] === '#' ? String.fromCodePoint(Number(e[1].toLowerCase() === 'x' ? `0x${e.slice(2)}` : e.slice(1))) : ENTITIES[e.toLowerCase()]
));

/** Up to LIST_MAX_KEYS of the keys under <answerId>/ in a bucket. */
async function listKeys(config: StorageConfig, bucket: Bucket, answerId: string, fetchImpl: typeof fetch): Promise<string[]> {
  const prefix = `${answerId}/`;
  const url = presign(config, 'GET', `/${bucket}`, {}, { 'list-type': '2', prefix, 'max-keys': String(LIST_MAX_KEYS) }, 60);
  const res = await ask(fetchImpl, url);
  if (!res.ok) throw new Error(`R2 refused to list ${bucket}/${prefix} (${await r2Reason(res)})`);
  const keys = [...(await res.text()).matchAll(/<Key>([^<]*)<\/Key>/g)].map((m) => xmlText(m[1]));
  // Only a path this API could have signed an upload for (lib/storage.ts), so a delete can never reach outside the folder.
  const odd = keys.filter((key) => !key.startsWith(prefix) || fileError(key.slice(prefix.length)));
  if (odd.length) throw new Error(`${bucket}/${prefix} holds ${odd.length} file(s) this API never signed, such as ${odd[0]}; delete them by hand`);
  return keys;
}

async function deleteKey(config: StorageConfig, bucket: Bucket, key: string, fetchImpl: typeof fetch): Promise<void> {
  const res = await ask(fetchImpl, presign(config, 'DELETE', `/${bucket}/${key}`, {}, {}, 60), { method: 'DELETE' });
  // 404: gone already, which is the point.
  if (!res.ok && res.status !== 404) throw new Error(`R2 refused to delete ${bucket}/${key} (${await r2Reason(res)})`);
  await res.body?.cancel();
}

/** Deletes every file under <answerId>/ in both buckets: how many there were. Throws when R2 refuses or does not answer. */
export async function clearDropletFiles(config: StorageConfig, answerId: string, fetchImpl: typeof fetch = fetch): Promise<number> {
  if (!ANSWER_ID_RE.test(answerId)) throw new Error(`${JSON.stringify(answerId)} is not an answer id`);
  let files = 0;
  for (const bucket of BUCKETS) {
    let emptied = false;
    // Listed again after each page is deleted, rather than paged through: what is left is what to do.
    for (let round = 0; round < MAX_ROUNDS && !emptied; round++) {
      const keys = await listKeys(config, bucket, answerId, fetchImpl);
      for (let i = 0; i < keys.length; i += PARALLEL) {
        await Promise.all(keys.slice(i, i + PARALLEL).map((key) => deleteKey(config, bucket, key, fetchImpl)));
      }
      files += keys.length;
      emptied = keys.length === 0;
    }
    if (!emptied) throw new Error(`${bucket}/${answerId}/ still had files after ${MAX_ROUNDS} rounds; the next sweep goes on`);
  }
  return files;
}

/**
 * Clears the files of the droplets in storage_deletions (only `answerIds`, when given), oldest first. A row goes once
 * a pass that began past its SETTLE_MS clears it; one that fails keeps the reason for the next sweep.
 */
export async function clearDeletedFiles(
  config: StorageConfig,
  db: typeof prisma,
  { answerIds, fetchImpl = fetch, now = () => new Date() }: { answerIds?: string[]; fetchImpl?: typeof fetch; now?: () => Date } = {},
): Promise<ClearReport> {
  const rows = await db.storageDeletion.findMany({
    where: answerIds ? { answerId: { in: answerIds } } : {},
    orderBy: { createdAt: 'asc' },
    take: SWEEP_BATCH,
  });
  const report: ClearReport = { cleared: [], failed: [] };
  for (const row of rows) {
    const began = now().getTime();
    try {
      const files = await clearDropletFiles(config, row.answerId, fetchImpl);
      const done = row.createdAt.getTime() <= began - SETTLE_MS;
      if (done) await db.storageDeletion.deleteMany({ where: { answerId: row.answerId } });
      report.cleared.push({ answerId: row.answerId, files, done });
    } catch (err) {
      const reason = (err as Error).message;
      report.failed.push({ answerId: row.answerId, reason });
      await db.storageDeletion.updateMany({ where: { answerId: row.answerId }, data: { tries: { increment: 1 }, lastError: reason.slice(0, 500) } });
    }
  }
  return report;
}

export function logClearReport(log: FastifyBaseLogger, report: ClearReport): void {
  for (const { answerId, files } of report.cleared) {
    if (files) log.info({ answerId }, `File storage: cleared ${files} file(s) of a deleted droplet from R2`);
  }
  for (const { answerId, reason } of report.failed) {
    log.error({ answerId }, `File storage: a deleted droplet's files stay on R2 for now (the next sweep tries again): ${reason}`);
  }
}

/** Clears these droplets' files now, in the background: the route does not wait on R2. Never throws. */
export function clearSoon(answerIds: string[], db: typeof prisma, log: FastifyBaseLogger, fetchImpl: typeof fetch = fetch): Promise<void> {
  const config = storageConfig();
  if (answerIds.length === 0 || !config || 'problem' in config) return Promise.resolve();
  return clearDeletedFiles(config, db, { answerIds, fetchImpl })
    .then((report) => logClearReport(log, report))
    .catch((err) => log.error({ err }, 'File storage: clearing a deleted droplet\'s files failed; the next sweep tries again'));
}

let sweeping = false;

/** clearDeletedFiles over the whole table, unless a sweep is still running in this process (then nothing). */
export async function sweepDeletedFiles(config: StorageConfig, db: typeof prisma, fetchImpl: typeof fetch = fetch): Promise<ClearReport | null> {
  if (sweeping) return null;
  sweeping = true;
  try {
    return await clearDeletedFiles(config, db, { fetchImpl });
  } finally {
    sweeping = false;
  }
}
