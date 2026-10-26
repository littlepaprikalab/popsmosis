import { execFileSync } from 'child_process';
import { writeFileSync, readFileSync, existsSync } from 'fs';
import { resolve } from 'path';

// Seeds the database and stores ids + the agent key for the tests. Loads the
// repo-root .env so JWT_SECRET / DATABASE_URL match the running servers.
export default async function globalSetup() {
  const root = resolve(__dirname, '..', '..');
  const envPath = resolve(root, '.env');
  if (existsSync(envPath)) {
    for (const line of readFileSync(envPath, 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
    }
  }
  if (!process.env.JWT_SECRET) throw new Error('JWT_SECRET must be set for e2e (same value as the API)');

  const out = execFileSync('npm', ['--workspace', 'api', 'run', '-s', 'seed:dev'], { cwd: root, env: process.env, encoding: 'utf8' });
  const json = out.trim().split('\n').pop()!;
  writeFileSync(resolve(__dirname, '.seed.json'), json);
}
