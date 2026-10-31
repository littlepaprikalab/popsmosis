import { describe, it, expect } from 'vitest';
import { mkdtempSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { X509Certificate } from 'crypto';
import { databaseTls } from './db-tls.js';

// A self-signed CA made for these tests alone (EC P-256, CN=Popsmosis Test CA, valid until 2126); its key is not kept.
const PEM = `-----BEGIN CERTIFICATE-----
MIIBkDCCATWgAwIBAgIUVJamXFLybDf7WN61YVBppRZLvmowCgYIKoZIzj0EAwIw
HDEaMBgGA1UEAwwRUG9wc21vc2lzIFRlc3QgQ0EwIBcNMjYxMDMxMDAwOTUwWhgP
MjEyNjEwMDcwMDA5NTBaMBwxGjAYBgNVBAMMEVBvcHNtb3NpcyBUZXN0IENBMFkw
EwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEwlSmEYf46qIjkPyhWnfSHh5vQjidD9FP
Bl/3vcYEm6i3GhVAJ49INIMFfNZYrYVNNMiobodsqZL5hyVpS6fNSKNTMFEwHQYD
VR0OBBYEFM4mxU4xK+uqMLgOx51ORpM4Q4yEMB8GA1UdIwQYMBaAFM4mxU4xK+uq
MLgOx51ORpM4Q4yEMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDSQAwRgIh
APo1a+Zpwalj9po0fqpXTMi8NODn1bqexfSxS3agVYb7AiEAnZl/LekNeSL9tftK
NgvjTbgSOa2LbOc5m3Bnp4nCOHQ=
-----END CERTIFICATE-----
`;
const SUPABASE = 'postgresql://postgres.abc:p%40ss@aws-0-us-west-1.pooler.supabase.com:5432/postgres';
const tmp = () => mkdtempSync(join(tmpdir(), 'db-tls-'));

describe('databaseTls', () => {
  it('checks the certificate of a database elsewhere, with the CA it is given, and names that CA', () => {
    const tls = databaseTls(SUPABASE, { DATABASE_CA_CERT: PEM });
    expect(tls).toEqual({
      connectionString: SUPABASE,
      ssl: { rejectUnauthorized: true, ca: PEM },
      note: "the database's certificate is checked against DATABASE_CA_CERT: Popsmosis Test CA, valid until 2126-10-07",
    });
    expect(tls.warning).toBeUndefined();
    const dir = tmp();
    writeFileSync(join(dir, 'ca.pem'), PEM);
    expect(databaseTls(SUPABASE, { DATABASE_CA_CERT: join(dir, 'ca.pem') }).ssl?.ca).toBe(PEM);
    // A DER file, as some downloads are.
    writeFileSync(join(dir, 'ca.der'), new X509Certificate(PEM).raw);
    expect(databaseTls(SUPABASE, { DATABASE_CA_CERT: join(dir, 'ca.der') }).ssl?.ca).toBe(PEM);
    expect(() => databaseTls(SUPABASE, { DATABASE_CA_CERT: join(dir, 'missing.pem') })).toThrow(/DATABASE_CA_CERT/);
  });

  // Node passes over a CA it cannot read and refuses the database as "self-signed certificate in certificate chain",
  // as if none were set: a PEM whose line breaks a dashboard lost is put back together, and what cannot be read stops the boot.
  it('reads a PEM whatever became of its line breaks, and refuses one it cannot read', () => {
    for (const pasted of [PEM.replace(/\n/g, '\\n'), PEM.replace(/\n/g, ' '), PEM.replace(/\n/g, '\r\n'), `  ${PEM.replace(/\n/g, '')}  `]) {
      expect(databaseTls(SUPABASE, { DATABASE_CA_CERT: pasted }).ssl?.ca).toBe(PEM);
    }
    expect(databaseTls(SUPABASE, { DATABASE_CA_CERT: PEM + PEM }).ssl?.ca).toBe(PEM + PEM);
    expect(() => databaseTls(SUPABASE, { DATABASE_CA_CERT: '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----' }))
      .toThrow(/DATABASE_CA_CERT: certificate 1 cannot be read/);
    expect(() => databaseTls(SUPABASE, { DATABASE_CA_CERT: PEM.slice(0, 200) })).toThrow(/no complete PEM certificate/);
    const dir = tmp();
    writeFileSync(join(dir, 'notes.txt'), 'not a certificate');
    expect(() => databaseTls(SUPABASE, { DATABASE_CA_CERT: join(dir, 'notes.txt') })).toThrow(/neither a PEM nor a DER/);
  });

  it('warns at boot when a Supabase database has no CA to be checked against', () => {
    const tls = databaseTls(SUPABASE, {});
    expect(tls.ssl).toEqual({ rejectUnauthorized: true });
    expect(tls.warning).toMatch(/DATABASE_CA_CERT is not set.*self-signed certificate in certificate chain/);
    // A database elsewhere may have a certificate a public CA signed, which needs none.
    const other = databaseTls('postgresql://u:p@db.example.com:5432/app', {});
    expect(other.ssl).toEqual({ rejectUnauthorized: true });
    expect(other.warning).toBeUndefined();
    expect(other.note).toMatch(/Node's own CAs/);
  });

  it('takes the URL\'s own TLS parameters out of what the driver sees, so they cannot undo the check', () => {
    const dir = tmp();
    writeFileSync(join(dir, 'root.crt'), PEM);
    const tls = databaseTls(`${SUPABASE}?sslmode=require&sslrootcert=${join(dir, 'root.crt')}&application_name=api`, {});
    expect(tls.connectionString).toBe(`${SUPABASE}?application_name=api`);
    expect(tls.ssl).toEqual({ rejectUnauthorized: true, ca: PEM });
    expect(tls.note).toMatch(/against sslrootcert: Popsmosis Test CA/);
  });

  it('turns the check off only when the URL says no-verify, and says so', () => {
    const tls = databaseTls(`${SUPABASE}?sslmode=no-verify`, { DATABASE_CA_CERT: PEM });
    expect(tls.ssl).toEqual({ rejectUnauthorized: false });
    expect(tls.warning).toMatch(/not checked/);
    expect(databaseTls(`${SUPABASE}?sslmode=disable`, {}).ssl).toBeUndefined();
  });

  it('leaves a database on this machine without TLS unless the URL asks for it', () => {
    const local = 'postgresql://postgres:postgres@localhost:5432/popsmosis';
    expect(databaseTls(local, { DATABASE_CA_CERT: PEM })).toEqual({ connectionString: local });
    expect(databaseTls('postgresql://postgres:postgres@127.0.0.1:5432/popsmosis?sslmode=require', { DATABASE_CA_CERT: PEM }).ssl)
      .toEqual({ rejectUnauthorized: true, ca: PEM });
  });
});
