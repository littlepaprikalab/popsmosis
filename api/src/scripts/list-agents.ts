import 'dotenv/config';
import prisma from '../lib/prisma.js';

const agents = await prisma.agent.findMany({ orderBy: { createdAt: 'asc' } });
if (agents.length === 0) {
  console.log('No agents. Create one with: npm --workspace api run agent:create -- --name <name>');
} else {
  for (const a of agents) {
    console.log(`${a.id}  ${a.enabled ? 'enabled ' : 'DISABLED'}  ${a.name}  last seen: ${a.lastSeenAt?.toISOString() ?? 'never'}`);
  }
}
await prisma.$disconnect();
