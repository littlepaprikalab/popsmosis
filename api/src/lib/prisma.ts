import { PrismaClient } from '../generated/prisma/client.js';
import { PrismaPg } from '@prisma/adapter-pg';
import { withDbRetry, inTransaction, transactionScope } from './db-retry.js';
import { databaseTls } from './db-tls.js';

// Prisma 7 driver adapter over node-postgres. A database that is not on this machine is reached over TLS with its
// certificate checked, chain and host name; Supabase's CA, which Node does not trust by itself, comes from
// DATABASE_CA_CERT (lib/db-tls.ts). Local Postgres (localhost / 127.0.0.1) has no TLS at all.
if (!process.env.DATABASE_URL) {
  throw new Error('DATABASE_URL environment variable is required');
}
const tls = databaseTls(process.env.DATABASE_URL);
if (tls.warning) console.warn(`[prisma] ${tls.warning}`);

const adapter = new PrismaPg({
  connectionString: tls.connectionString,
  max: parseInt(process.env.DATABASE_POOL_SIZE || '5', 10),
  idleTimeoutMillis: 10 * 60 * 1000,
  connectionTimeoutMillis: 10_000,
  ...(tls.ssl ? { ssl: tls.ssl } : {}),
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
