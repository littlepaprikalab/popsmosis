/**
 * Checks the database's certificate as the API does (lib/db-tls.ts), and lists the certificates the server sends, so
 * a refusal shows which CA it needed. Run it where DATABASE_URL and DATABASE_CA_CERT are set: on your machine, with
 * them in the repo's .env or on the command line, or in the Render Shell of popsmosis-api.
 *
 *   npm --workspace api run db:check-tls
 *
 * The list comes from a TLS handshake alone, before any login, so no password goes to a server that is not checked.
 */
import 'dotenv/config';
import net from 'net';
import tls from 'tls';
import { once } from 'events';
import { X509Certificate } from 'crypto';
import pg from 'pg';
import { databaseTls } from '../lib/db-tls.js';

const url = process.env.DATABASE_URL;
if (!url) {
  console.error('DATABASE_URL is not set.');
  process.exit(2);
}
const config = databaseTls(url);
console.log(config.warning ?? config.note ?? 'No TLS: the database is on this machine, or DATABASE_URL says sslmode=disable.');

const { hostname, port } = new URL(config.connectionString);
const host = hostname.replace(/^\[|\]$/g, '');
const cn = (subject: string) => /(?:^|\n)CN=([^\n]+)/.exec(subject)?.[1] ?? subject.replace(/\n/g, ', ');

/** The certificates the server sends, from a handshake on a Postgres SSLRequest; nothing is checked, nothing sent. */
async function serverChain(): Promise<X509Certificate[]> {
  const socket = net.connect({ host, port: Number(port) || 5432 });
  socket.setTimeout(10_000, () => socket.destroy(new Error('no answer in 10 seconds')));
  await once(socket, 'connect');
  socket.write(Buffer.from([0, 0, 0, 8, 0x04, 0xd2, 0x16, 0x2f])); // SSLRequest
  const [answer] = (await once(socket, 'data')) as [Buffer];
  if (answer[0] !== 0x53) throw new Error('the server does not offer TLS');
  const secure = tls.connect({ socket, servername: net.isIP(host) ? undefined : host, rejectUnauthorized: false });
  await once(secure, 'secureConnect');
  const chain: X509Certificate[] = [];
  for (let c = secure.getPeerCertificate(true); c?.raw && !chain.some((x) => x.fingerprint256 === c.fingerprint256); c = c.issuerCertificate) {
    chain.push(new X509Certificate(c.raw));
  }
  secure.destroy();
  return chain;
}

if (config.ssl) {
  try {
    const chain = await serverChain();
    console.log(`\nThe server (${host}) sends:`);
    for (const c of chain) console.log(`  ${cn(c.subject)}, signed by ${cn(c.issuer)}, valid until ${new Date(c.validTo).toISOString().slice(0, 10)}`);
    const top = chain.at(-1);
    const cas = [...(config.ssl.ca ?? '').matchAll(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g)].map(([pem]) => new X509Certificate(pem));
    if (top && cas.length) {
      const signer = cas.find((ca) => chain.some((c) => c.fingerprint256 === ca.fingerprint256) || top.checkIssued(ca));
      console.log(signer
        ? `The CA given (${cn(signer.subject)}) is the one this chain ends at.`
        : `None of the CAs given (${cas.map((ca) => cn(ca.subject)).join('; ')}) is the one this chain ends at (${cn(top.issuer)}).`);
    }
  } catch (err) {
    console.log(`\nCould not read the server's certificates: ${(err as Error).message}`);
  }
}

const client = new pg.Client({ connectionString: config.connectionString, ssl: config.ssl, connectionTimeoutMillis: 10_000 });
try {
  await client.connect();
  await client.query('SELECT 1');
  console.log('\nOK: the API will connect with these settings.');
  process.exitCode = 0;
} catch (err) {
  console.log(`\nFAILED: ${(err as Error).message}`);
  process.exitCode = 1;
} finally {
  await client.end().catch(() => {});
}
