import 'dotenv/config';
import prisma from '../lib/prisma.js';
import { generateApiKey } from '../lib/apikey.js';

// Local-development seed: two users (Ada is an admin), one agent, one answered
// question with a reply thread, one pending question. Idempotent per run: the
// seed users' questions are recreated, the agent's key is rotated and printed.
// Usage: npm --workspace api run seed:dev

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600e3);

const approved = { status: 'APPROVED' as const, reviewedAt: new Date() };
const ada = await prisma.user.upsert({ where: { googleId: 'seed-ada' }, update: {}, create: { email: 'ada@example.com', googleId: 'seed-ada', name: 'Ada', role: 'ADMIN', ...approved } });
const ben = await prisma.user.upsert({ where: { googleId: 'seed-ben' }, update: {}, create: { email: 'ben@example.com', googleId: 'seed-ben', name: 'Ben', ...approved } });
// Cal has signed in but nobody has approved him yet. Re-seeding puts him back in the queue.
const pending = { status: 'PENDING' as const, reviewedAt: null, reviewedById: null };
const cal = await prisma.user.upsert({ where: { googleId: 'seed-cal' }, update: pending, create: { email: 'cal@example.com', googleId: 'seed-cal', name: 'Cal', ...pending } });
const key = generateApiKey();
const agent = await prisma.agent.upsert({ where: { name: 'zoey-helper' }, update: { apiKeyHash: key.hashed, enabled: true }, create: { name: 'zoey-helper', apiKeyHash: key.hashed } });

await prisma.question.deleteMany({ where: { userId: { in: [ada.id, ben.id] } } });

const q1 = await prisma.question.create({ data: { userId: ben.id, status: 'ANSWERED', createdAt: hoursAgo(3), content: 'My export to CSV keeps producing a file with only the header row. What am I missing?' } });
const a1 = await prisma.answer.create({ data: { questionId: q1.id, agentId: agent.id, model: 'claude-opus-5', createdAt: hoursAgo(2.5), content: `Most likely the export runs **before** the filter is applied, so the query matches zero rows.

Check these in order:

1. Apply your filters, then look at the row count in the footer. If it says 0, the export is doing the right thing.
2. If rows are visible but the file is still empty, the export may be using a *saved* view rather than the current one. Save the view first.
3. Date filters default to UTC. A range that ends "today" can exclude everything after midnight UTC.

| Footer shows | Likely cause |
|---|---|
| 0 rows | The filter: the export is right |
| Some rows | A saved view, or the UTC date range |

If none of that helps, tell me which view you are exporting from and I will dig further.` } });
const r1 = await prisma.reply.create({ data: { answerId: a1.id, userId: ben.id, createdAt: hoursAgo(2), content: 'The footer says 42 rows, so it is not the filter. I am exporting from the default view.' } });
await prisma.reply.create({ data: { answerId: a1.id, agentId: agent.id, replyToId: r1.id, createdAt: hoursAgo(1.5), content: 'Then it is the date range. The default view ends at "today", which the exporter reads as **00:00 UTC**. Set the end date to tomorrow and the 42 rows will come through.' } });
await prisma.reply.create({ data: { answerId: a1.id, userId: ben.id, createdAt: hoursAgo(1), content: 'That was it. Thanks!' } });
await prisma.question.create({ data: { userId: ada.id, createdAt: hoursAgo(0.3), content: 'Is there a way to get notified when the advisor answers, instead of refreshing the page?' } });

// Filler history from earlier days (outside the 24h ask quota) so the list has
// more than one page to scroll through.
const topics = ['export', 'invoices', 'SSO', 'webhooks', 'rate limits', 'timezones', 'CSV import', 'permissions', 'audit log', 'API keys', 'dark mode', 'notifications'];
for (let i = 0; i < 24; i++) {
  const q = await prisma.question.create({ data: {
    userId: i % 2 ? ada.id : ben.id,
    status: i % 3 === 0 ? 'PENDING' : 'ANSWERED',
    createdAt: hoursAgo(30 + i * 7),
    content: `Earlier question ${i + 1} about ${topics[i % topics.length]}: what is the recommended way to set this up for a small team?`,
  } });
  if (i % 3 !== 0) {
    await prisma.answer.create({ data: { questionId: q.id, agentId: agent.id, model: 'claude-opus-5', createdAt: hoursAgo(29 + i * 7), content: `For ${topics[i % topics.length]}, start with the defaults, then adjust one setting at a time. If something specific is failing, reply with the exact error and I will narrow it down.` } });
  }
}

console.log(JSON.stringify({ users: { ada: ada.id, ben: ben.id, cal: cal.id }, agent: { id: agent.id, name: agent.name }, ZOEY_API_KEY: key.raw }));
await prisma.$disconnect();
