import 'dotenv/config';
import prisma from '../lib/prisma.js';
import { generateApiKey } from '../lib/apikey.js';

// Usage: npm --workspace api run agent:create -- --name "zoey-helper"
// Prints the raw key exactly once. Only its sha256 hash is stored.

const args = process.argv.slice(2);
const nameIdx = args.indexOf('--name');
const name = (nameIdx >= 0 ? args[nameIdx + 1] : args[0])?.trim();

if (!name) {
  console.error('Usage: agent:create -- --name <agent name>');
  process.exit(1);
}

const { raw, hashed } = generateApiKey();
try {
  const agent = await prisma.agent.create({ data: { name, apiKeyHash: hashed } });
  console.log(`Created agent "${agent.name}" (${agent.id})`);
  console.log('');
  console.log('API key (shown once, store it now):');
  console.log('');
  console.log(`  ZOEY_API_KEY=${raw}`);
  console.log('');
} catch (err: unknown) {
  if (err && typeof err === 'object' && 'code' in err && (err as { code: string }).code === 'P2002') {
    console.error(`An agent named "${name}" already exists. Pick another name or rotate its key via the admin API.`);
    process.exit(1);
  }
  throw err;
} finally {
  await prisma.$disconnect();
}
