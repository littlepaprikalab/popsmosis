import 'dotenv/config';
import prisma from '../lib/prisma.js';
import { generateUserToken } from '../lib/apikey.js';

// Usage: npm --workspace api run token:create -- --email them@example.com --name "zed panel"
// Prints the raw token exactly once. Only its sha256 hash is stored.

const args = process.argv.slice(2);
const argValue = (flag: string): string | undefined => {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1]?.trim() : undefined;
};

const email = argValue('--email')?.toLowerCase();
const name = argValue('--name') ?? 'cli';

if (!email) {
  console.error('Usage: token:create -- --email <user email> [--name <label>]');
  process.exit(1);
}

const user = await prisma.user.findUnique({ where: { email } });
if (!user) {
  console.error(`No user with the email "${email}". They have to sign in once before a token can be minted for them.`);
  await prisma.$disconnect();
  process.exit(1);
}
if (user.status !== 'APPROVED') {
  console.error(`"${email}" is ${user.status}, so a token would be refused on every request. Approve them first.`);
  await prisma.$disconnect();
  process.exit(1);
}

const { raw, hashed } = generateUserToken();
try {
  const token = await prisma.userToken.create({ data: { userId: user.id, name, tokenHash: hashed } });
  console.log(`Created token "${token.name}" (${token.id}) for ${user.email}`);
  console.log('');
  console.log('Access token (shown once, store it now):');
  console.log('');
  console.log(`  ZOEY_API_TOKEN=${raw}`);
  console.log('');
} finally {
  await prisma.$disconnect();
}
