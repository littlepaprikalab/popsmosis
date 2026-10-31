/**
 * Retry policy for transient database failures (pooler dropped an idle
 * connection, connection reset, serialization failure). Copied in spirit from
 * sparks: retry a handful of times with a short backoff, never on logical
 * errors (unique violations, not found, validation) nor on a database
 * certificate the pool refuses.
 */

import { AsyncLocalStorage } from 'async_hooks';

export const MAX_ATTEMPTS = 3;

// Set while an interactive transaction callback is running. Inside it, a failed
// statement has already aborted the transaction, so retrying the statement is
// pointless; the caller retries the whole transaction instead (runTransaction).
export const transactionScope = new AsyncLocalStorage<boolean>();
export const inTransaction = () => transactionScope.getStore() === true;

// Prisma error codes that are safe to retry.
const TRANSIENT_CODES = new Set([
  'P1001', // Can't reach database server
  'P1002', // Database server timed out
  'P1008', // Operations timed out
  'P1011', // Error opening a TLS connection
  'P1017', // Server has closed the connection
  'P2024', // Timed out fetching a new connection from the pool
  'P2034', // Transaction failed due to write conflict / deadlock — must retry
]);

const TRANSIENT_MESSAGES = [
  'server closed the connection unexpectedly',
  'connection reset by peer',
  'connection terminated',
  'terminating connection due to administrator command',
  'error opening a tls connection',
  "can't reach database server",
  'timed out fetching a new connection',
  'econnreset',
  'econnrefused',
  'epipe',
];

/**
 * The pool refused the database's certificate: no CA for it, another CA, expired, or another host's name
 * (lib/db-tls.ts). Prisma reports it as P1011 (P2010 from a raw query), as it does a TLS handshake the network cut
 * short, but it fails the same way on every try: a retry only triples the handshakes against the pooler and adds the
 * backoff to each failing request. A handshake cut short names no certificate, and is still retried.
 */
function refusesCertificate(err: object): boolean {
  const e = err as { code?: unknown; message?: unknown; meta?: { driverAdapterError?: { cause?: { kind?: unknown; reason?: unknown } } } };
  const cause = e.meta?.driverAdapterError?.cause;
  const message = String(e.message ?? '').toLowerCase();
  const tls = e.code === 'P1011' || cause?.kind === 'TlsConnectionError' || message.includes('error opening a tls connection');
  return tls && `${message} ${String(cause?.reason ?? '').toLowerCase()}`.includes('certificate');
}

export function isTransientDbError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  if (refusesCertificate(err)) return false;
  const code = (err as { code?: unknown }).code;
  if (typeof code === 'string' && TRANSIENT_CODES.has(code)) return true;
  // pg's own errors carry the Postgres SQLSTATE: 57P01 admin shutdown, 08xxx connection errors, 40001 serialization, 40P01 deadlock.
  if (typeof code === 'string' && (/^08/.test(code) || code === '57P01' || code === '40001' || code === '40P01')) return true;
  const message = String((err as { message?: unknown }).message ?? '').toLowerCase();
  if (TRANSIENT_MESSAGES.some((m) => message.includes(m))) return true;
  // Prisma wraps driver errors; look one level down.
  const cause = (err as { cause?: unknown }).cause;
  if (cause && cause !== err) return isTransientDbError(cause);
  return false;
}

export function backoffMs(attempt: number): number {
  return 100 * 2 ** attempt; // 100ms, 200ms
}

export async function withDbRetry<T>(
  operation: () => Promise<T>,
  opts: { attempts?: number; sleep?: (ms: number) => Promise<void>; onRetry?: (err: unknown, attempt: number) => void } = {},
): Promise<T> {
  const attempts = opts.attempts ?? MAX_ATTEMPTS;
  const sleep = opts.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return await operation();
    } catch (err) {
      lastError = err;
      if (!isTransientDbError(err) || attempt === attempts - 1) throw err;
      opts.onRetry?.(err, attempt + 1);
      await sleep(backoffMs(attempt));
    }
  }
  throw lastError;
}
