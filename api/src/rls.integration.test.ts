/**
 * Row-level security, against a real Postgres (DATABASE_URL). Skipped unless INTEGRATION=1.
 *
 * Covers: every table in the schema has it on (prisma/migrations/20261112120000_row_level_security), so a migration
 * that adds a table without it fails here; none FORCEs it, so the owner the API connects as is not held to it; and a
 * role that is granted a table but does not own it, as Supabase's Data API roles are, reads and writes no row.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';

const RUN = process.env.INTEGRATION === '1';
const d = RUN ? describe : describe.skip;

d('row-level security', () => {
  let prisma: (typeof import('./lib/prisma.js'))['default'];
  const role = `rls_probe_${process.pid}`;

  beforeAll(async () => {
    prisma = (await import('./lib/prisma.js')).default;
  });
  afterAll(async () => {
    await prisma.$executeRawUnsafe(`DROP OWNED BY ${role}`).catch(() => {});
    await prisma.$executeRawUnsafe(`DROP ROLE IF EXISTS ${role}`).catch(() => {});
    await prisma.$disconnect();
  });

  it('is on for every table of the schema, and forced on none', async () => {
    const tables = await prisma.$queryRaw<{ name: string; rls: boolean; forced: boolean }[]>`
      SELECT c.relname AS name, c.relrowsecurity AS rls, c.relforcerowsecurity AS forced
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')
      ORDER BY c.relname
    `;
    expect(tables.length).toBeGreaterThanOrEqual(12);
    expect(tables.filter((t) => !t.rls).map((t) => t.name)).toEqual([]);
    expect(tables.filter((t) => t.forced).map((t) => t.name)).toEqual([]);
  });

  it('gives a role that does not own the tables no row, whatever it is granted', async () => {
    await prisma.user.create({ data: { email: `${role}@example.com`, googleId: role, name: 'Probe' } });
    await prisma.$executeRawUnsafe(`CREATE ROLE ${role} NOLOGIN NOBYPASSRLS`);
    // So a role that is not a superuser (the API's owner on Supabase) may SET ROLE to it: Postgres 16 grants its
    // creator only the ADMIN option.
    await prisma.$executeRawUnsafe(`GRANT ${role} TO CURRENT_USER`);
    await prisma.$executeRawUnsafe(`GRANT USAGE ON SCHEMA ${'public'} TO ${role}`);
    await prisma.$executeRawUnsafe(`GRANT SELECT, INSERT, UPDATE, DELETE ON users, settings TO ${role}`);
    const seen = await prisma.$transaction(async (tx) => {
      await tx.$executeRawUnsafe(`SET LOCAL ROLE ${role}`);
      const [{ n }] = await tx.$queryRawUnsafe<{ n: bigint }[]>('SELECT count(*) AS n FROM users');
      const changed = await tx.$executeRawUnsafe(`UPDATE users SET name = 'taken' WHERE email = '${role}@example.com'`);
      return { n: Number(n), changed };
    });
    expect(seen).toEqual({ n: 0, changed: 0 });
    await expect(prisma.$transaction(async (tx) => {
      await tx.$executeRawUnsafe(`SET LOCAL ROLE ${role}`);
      await tx.$executeRawUnsafe(`INSERT INTO settings (key, value, "updatedAt") VALUES ('${role}', '{}', now())`);
    })).rejects.toThrow(/row-level security/);
    // The owner, as the API connects, still sees and changes its rows.
    expect((await prisma.user.findUniqueOrThrow({ where: { email: `${role}@example.com` } })).name).toBe('Probe');
    await prisma.user.delete({ where: { email: `${role}@example.com` } });
  });
});
