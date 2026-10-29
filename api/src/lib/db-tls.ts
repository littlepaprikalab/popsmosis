/**
 * How the API's pool talks TLS to Postgres (lib/prisma.ts): with the server's certificate checked, chain and host name,
 * whenever the database is not on this machine. Without the check, anyone on the path between Render and the database
 * could stand in for it and read or change every row.
 *
 * Supabase signs its database certificates with a CA of its own, outside Node's trust store: give it to the API as
 * DATABASE_CA_CERT, either the PEM text itself or the path to a file holding it (Supabase > Project Settings > Database
 * > SSL Configuration > Download certificate; on Render, a Secret File or the text in the variable). A database whose
 * certificate a public CA signed needs nothing. `sslmode=no-verify` in DATABASE_URL turns the check off, and the API
 * says so at boot: for a move between providers, never to keep.
 *
 * The URL's own TLS parameters (sslmode, sslrootcert...) are read here and taken out of the string handed to the
 * driver, since node-postgres lets them override this setting wholesale.
 */
import { readFileSync } from 'fs';

export type DatabaseTls = {
  connectionString: string;
  ssl?: { rejectUnauthorized: boolean; ca?: string };
  warning?: string; // said once at boot
};

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]', 'db']);
const TLS_PARAMS = ['sslmode', 'sslrootcert', 'sslcert', 'sslkey', 'ssl', 'uselibpqcompat', 'sslaccept'];

function caFrom(value: string | undefined): string | undefined {
  const raw = value?.trim();
  if (!raw) return undefined;
  if (raw.includes('-----BEGIN CERTIFICATE-----')) return `${raw.replace(/\\n/g, '\n').trim()}\n`;
  try {
    return readFileSync(raw, 'utf8');
  } catch (err) {
    throw new Error(`DATABASE_CA_CERT is neither a PEM certificate nor a file that can be read: ${(err as Error).message}`);
  }
}

/** The connection string for the driver, and how to check the server's certificate. */
export function databaseTls(url: string, env: NodeJS.ProcessEnv = process.env): DatabaseTls {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    // Not a URL (a key=value string): the driver reads it as it is, and checks a certificate when it asks for TLS.
    return { connectionString: url };
  }
  const mode = parsed.searchParams.get('sslmode')?.toLowerCase() ?? '';
  const rootcert = parsed.searchParams.get('sslrootcert') ?? undefined;
  for (const name of TLS_PARAMS) parsed.searchParams.delete(name);
  const connectionString = parsed.toString();

  if (mode === 'disable') return { connectionString };
  const local = LOCAL_HOSTS.has(parsed.hostname);
  if (local && !['require', 'verify-ca', 'verify-full', 'no-verify'].includes(mode)) return { connectionString };

  if (mode === 'no-verify') {
    return {
      connectionString,
      ssl: { rejectUnauthorized: false },
      warning: 'DATABASE_URL has sslmode=no-verify: the database\'s TLS certificate is not checked. Set DATABASE_CA_CERT and remove it.',
    };
  }
  const ca = caFrom(env.DATABASE_CA_CERT) ?? caFrom(rootcert);
  return { connectionString, ssl: { rejectUnauthorized: true, ...(ca ? { ca } : {}) } };
}
