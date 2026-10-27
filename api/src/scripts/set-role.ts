import 'dotenv/config';
import prisma from '../lib/prisma.js';

// Bootstrap or repair admin access from the shell, e.g. when nobody is in
// ADMIN_EMAILS. The user must have signed in at least once.
// Usage:
//   npm --workspace api run user:promote -- --email you@example.com
//   npm --workspace api run user:demote  -- --email you@example.com

const args = process.argv.slice(2);
const emailIdx = args.indexOf('--email');
const email = (emailIdx >= 0 ? args[emailIdx + 1] : args[0])?.trim().toLowerCase();
const role = process.env.ROLE === 'USER' ? 'USER' : 'ADMIN';

if (!email) {
  console.error('Usage: -- --email <address>');
  process.exit(1);
}

const user = await prisma.user.findUnique({ where: { email } });
if (!user) {
  console.error(`No user with email ${email}. They must sign in once before they can be given a role.`);
  await prisma.$disconnect();
  process.exit(1);
}
if (role === 'USER') {
  const admins = await prisma.user.count({ where: { role: 'ADMIN' } });
  if (user.role === 'ADMIN' && admins <= 1) {
    console.error('Refusing to demote the last admin.');
    await prisma.$disconnect();
    process.exit(1);
  }
}
await prisma.user.update({ where: { id: user.id }, data: { role } });
console.log(`${email} is now ${role}. Takes effect within 30s on running instances (auth cache), immediately after their next sign-in.`);
await prisma.$disconnect();
