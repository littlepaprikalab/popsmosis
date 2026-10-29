import { describe, it, expect } from 'vitest';
import { mkdtempSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { databaseTls } from './db-tls.js';

const PEM = '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n';
const SUPABASE = 'postgresql://postgres.abc:p%40ss@aws-0-us-west-1.pooler.supabase.com:5432/postgres';

describe('databaseTls', () => {
  it('checks the certificate of a database elsewhere, with the CA it is given', () => {
    expect(databaseTls(SUPABASE, {})).toEqual({ connectionString: SUPABASE, ssl: { rejectUnauthorized: true } });
    expect(databaseTls(SUPABASE, { DATABASE_CA_CERT: PEM }).ssl).toEqual({ rejectUnauthorized: true, ca: PEM });
    // A PEM pasted into one line with \n for its breaks, as some dashboards keep it.
    expect(databaseTls(SUPABASE, { DATABASE_CA_CERT: PEM.replace(/\n/g, '\\n') }).ssl?.ca).toBe(PEM);
    const dir = mkdtempSync(join(tmpdir(), 'db-tls-'));
    writeFileSync(join(dir, 'ca.pem'), PEM);
    expect(databaseTls(SUPABASE, { DATABASE_CA_CERT: join(dir, 'ca.pem') }).ssl?.ca).toBe(PEM);
    expect(() => databaseTls(SUPABASE, { DATABASE_CA_CERT: join(dir, 'missing.pem') })).toThrow(/DATABASE_CA_CERT/);
  });

  it('takes the URL\'s own TLS parameters out of what the driver sees, so they cannot undo the check', () => {
    const dir = mkdtempSync(join(tmpdir(), 'db-tls-'));
    writeFileSync(join(dir, 'root.crt'), PEM);
    const tls = databaseTls(`${SUPABASE}?sslmode=require&sslrootcert=${join(dir, 'root.crt')}&application_name=api`, {});
    expect(tls.connectionString).toBe(`${SUPABASE}?application_name=api`);
    expect(tls.ssl).toEqual({ rejectUnauthorized: true, ca: PEM });
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
    expect(databaseTls('postgresql://postgres:postgres@127.0.0.1:5432/popsmosis?sslmode=require', { DATABASE_CA_CERT: PEM }))
      .toEqual({ connectionString: 'postgresql://postgres:postgres@127.0.0.1:5432/popsmosis', ssl: { rejectUnauthorized: true, ca: PEM } });
  });
});
