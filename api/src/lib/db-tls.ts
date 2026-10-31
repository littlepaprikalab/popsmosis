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
 * The boot log names the CA the certificate is checked against, or warns that a Supabase database has none, since
 * every way of getting the CA wrong ends in the same error on each query: "self-signed certificate in certificate
 * chain".
 *
 * The URL's own TLS parameters (sslmode, sslrootcert...) are read here and taken out of the string handed to the
 * driver, since node-postgres lets them override this setting wholesale.
 */
import { readFileSync } from 'fs';
import { X509Certificate } from 'crypto';

export type DatabaseTls = {
  connectionString: string;
  ssl?: { rejectUnauthorized: boolean; ca?: string };
  warning?: string; // said once at boot
  note?: string; // said once at boot: what the database's certificate is checked against
};

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]', 'db']);
const TLS_PARAMS = ['sslmode', 'sslrootcert', 'sslcert', 'sslkey', 'ssl', 'uselibpqcompat', 'sslaccept'];
const PEM_BLOCK = /-----BEGIN CERTIFICATE-----([\s\S]*?)-----END CERTIFICATE-----/g;

type Ca = { pem: string; names: string };

function caNames(certs: X509Certificate[]): string {
  return certs
    .map((c) => {
      const name = /(?:^|\n)CN=([^\n]+)/.exec(c.subject)?.[1] ?? c.subject.replace(/\n/g, ', ');
      return `${name}, valid until ${new Date(c.validTo).toISOString().slice(0, 10)}`;
    })
    .join('; ');
}

/**
 * The certificates in a PEM text, read and written out again. Node passes over a CA it cannot read without a word,
 * then refuses the database just as with no CA at all. A PEM pasted where its line breaks became spaces, or the two
 * characters `\n`, is one such, so its base64 is read whatever the spacing; anything still unreadable stops the boot.
 */
function caFromPem(text: string, from: string): Ca {
  const blocks = [...text.replace(/\\n/g, '\n').matchAll(PEM_BLOCK)];
  if (!blocks.length) throw new Error(`${from} has no complete PEM certificate (-----BEGIN CERTIFICATE----- to -----END CERTIFICATE-----)`);
  const certs = blocks.map(([, body], i) => {
    try {
      return new X509Certificate(Buffer.from(body.replace(/\s+/g, ''), 'base64'));
    } catch {
      throw new Error(`${from}: certificate ${i + 1} cannot be read (what lies between its BEGIN and END lines is not a certificate)`);
    }
  });
  return { pem: certs.map((c) => c.toString()).join(''), names: caNames(certs) };
}

function caFrom(value: string | undefined, from: string): Ca | undefined {
  const raw = value?.trim();
  if (!raw) return undefined;
  if (raw.includes('-----BEGIN CERTIFICATE-----')) return caFromPem(raw, from);
  let file: Buffer;
  try {
    file = readFileSync(raw);
  } catch (err) {
    throw new Error(`${from} is neither a PEM certificate nor a file that can be read: ${(err as Error).message}`);
  }
  const text = file.toString('utf8');
  if (text.includes('-----BEGIN')) return caFromPem(text, `${from} (${raw})`);
  try {
    const cert = new X509Certificate(file); // a DER file, as some downloads are
    return { pem: cert.toString(), names: caNames([cert]) };
  } catch {
    throw new Error(`${from} (${raw}) holds neither a PEM nor a DER certificate`);
  }
}

const isSupabase = (host: string) => /(^|\.)supabase\.(com|co)$/.test(host);

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
  const fromEnv = caFrom(env.DATABASE_CA_CERT, 'DATABASE_CA_CERT');
  const ca = fromEnv ?? caFrom(rootcert, 'sslrootcert in DATABASE_URL');
  if (ca) {
    return {
      connectionString,
      ssl: { rejectUnauthorized: true, ca: ca.pem },
      note: `the database's certificate is checked against ${fromEnv ? 'DATABASE_CA_CERT' : 'sslrootcert'}: ${ca.names}`,
    };
  }
  return {
    connectionString,
    ssl: { rejectUnauthorized: true },
    ...(isSupabase(parsed.hostname)
      ? {
          warning:
            'DATABASE_CA_CERT is not set. Node does not trust the CA Supabase signs its databases with, so every query ' +
            'will fail with "self-signed certificate in certificate chain". Set it to the certificate from Supabase > ' +
            'Project Settings > Database > SSL Configuration (docs/DEPLOY.md, section 8).',
        }
      : { note: "the database's certificate is checked against Node's own CAs (DATABASE_CA_CERT is not set)" }),
  };
}
