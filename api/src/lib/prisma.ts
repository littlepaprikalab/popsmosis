import { PrismaClient } from '../generated/prisma/client.js';
import { PrismaPg } from '@prisma/adapter-pg';
import { withDbRetry, inTransaction, transactionScope } from './db-retry.js';

// Prisma 7 driver adapter over node-postgres. Cloud providers (Supabase,
// Render, Neon) require TLS and use CAs outside Node's trust store, so TLS is
// on without certificate verification for any non-local host, or whenever the
// URL asks for it. Local Postgres (localhost / 127.0.0.1) has no TLS at all.
const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  throw new Error('DATABASE_URL environment variable is required');
}

function needsTls(url: string): boolean {
  if (/sslmode=(require|verify-full|verify-ca|no-verify|prefer)/.test(url)) return true;
  if (/sslmode=disable/.test(url)) return false;
  try {
    const host = new URL(url).hostname;
    return !(host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === 'db');
  } catch {
    return false;
  }
}
const wantsSsl = needsTls(connectionString);

const adapter = new PrismaPg({
  connectionString,
  max: parseInt(process.env.DATABASE_POOL_SIZE || '5', 10),
  idleTimeoutMillis: 10 * 60 * 1000,
  connectionTimeoutMillis: 10_000,
  ...(wantsSsl ? { ssl: { rejectUnauthorized: false } } : {}),
});

const basePrisma = new PrismaClient({
  log: process.env.NODE_ENV === 'development' ? ['warn', 'error'] : ['error'],
  adapter,
});

const logRetry = (err: unknown, attempt: number) => {
  const code = (err as { code?: string })?.code ?? 'n/a';
  console.warn(`[prisma] transient error (${code}); retry ${attempt}`);
};

// Every standalone query goes through the transient-error retry. Statements
// inside an interactive transaction do not: once one fails, Postgres has
// aborted the transaction, so the unit of retry is the transaction itself.
const prisma = basePrisma.$extends({
  query: {
    $allOperations({ args, query }) {
      if (inTransaction()) return query(args);
      return withDbRetry(() => query(args), { onRetry: logRetry });
    },
  },
});

export type TxClient = Parameters<Parameters<typeof prisma.$transaction>[0]>[0];

/**
 * Run an interactive transaction, retrying the whole thing on transient
 * failures (deadlock, serialization conflict, dropped connection). Safe because
 * a failed transaction rolled back; the callback must not have side effects
 * outside the database.
 */
export function runTransaction<T>(fn: (tx: TxClient) => Promise<T>): Promise<T> {
  return withDbRetry(
    () => transactionScope.run(true, () => prisma.$transaction((tx) => fn(tx))),
    { onRetry: logRetry },
  );
}

export default prisma;
