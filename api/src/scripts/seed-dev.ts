import 'dotenv/config';
import prisma from '../lib/prisma.js';
import { generateApiKey } from '../lib/apikey.js';

// Local-development seed: two users (Ada is an admin), one agent, one topic with
// its lesson and a follow-up thread, one topic still waiting. Idempotent per run: the
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
const agent = await prisma.agent.upsert({ where: { name: 'popsmosis-teacher' }, update: { apiKeyHash: key.hashed, enabled: true }, create: { name: 'popsmosis-teacher', apiKeyHash: key.hashed } });

await prisma.question.deleteMany({ where: { userId: { in: [ada.id, ben.id] } } });

const q1 = await prisma.question.create({ data: { userId: ben.id, status: 'ANSWERED', createdAt: hoursAgo(3), content: 'ATP synthesis: how do cells actually make ATP?' } });
const a1 = await prisma.answer.create({ data: { questionId: q1.id, agentId: agent.id, model: 'claude', createdAt: hoursAgo(2.5), content: `**ATP synthase** is a tiny rotary motor in the inner membrane of the mitochondrion.

1. The electron transport chain pumps protons (H+) out of the matrix, building up a **gradient** across the membrane.
2. The protons can only flow back in through ATP synthase, and that flow spins its rotor.
3. Each full turn presses ADP and phosphate together into about three ATP.

| Stage | What it does |
|---|---|
| Electron transport chain | Pumps protons out, building the gradient |
| ATP synthase | Lets the protons back in, making ATP |

Ask a follow-up if you want any step slowed down.` } });
const r1 = await prisma.reply.create({ data: { answerId: a1.id, userId: ben.id, createdAt: hoursAgo(2), content: 'Why does the protons flowing back in make the rotor spin?' } });
await prisma.reply.create({ data: { answerId: a1.id, agentId: agent.id, replyToId: r1.id, createdAt: hoursAgo(1.5), content: 'Think of a water wheel. Each proton can only cross through a channel between the rotor and the fixed part, and every one that passes pushes the rotor **one notch** round. Thousands of protons a second keep it turning.' } });
await prisma.reply.create({ data: { answerId: a1.id, userId: ben.id, createdAt: hoursAgo(1), content: 'The water wheel picture did it. Thanks!' } });
await prisma.question.create({ data: { userId: ada.id, createdAt: hoursAgo(0.3), content: 'Photosynthesis: how do the light reactions and the Calvin cycle fit together?' } });

// Filler history from earlier days (outside the 24h ask quota) so the list has
// more than one page to scroll through.
const topics = ['mitosis', 'the French Revolution', "Bayes' theorem", 'plate tectonics', 'supply and demand', 'the Krebs cycle', "Newton's laws", 'DNA replication', 'the water cycle', 'black holes', 'the immune system', 'compound interest'];
for (let i = 0; i < 24; i++) {
  const q = await prisma.question.create({ data: {
    userId: i % 2 ? ada.id : ben.id,
    status: i % 3 === 0 ? 'PENDING' : 'ANSWERED',
    createdAt: hoursAgo(30 + i * 7),
    content: `Earlier topic ${i + 1}: ${topics[i % topics.length]}`,
  } });
  if (i % 3 !== 0) {
    await prisma.answer.create({ data: { questionId: q.id, agentId: agent.id, model: 'claude', createdAt: hoursAgo(29 + i * 7), content: `**${topics[i % topics.length]}**, the big picture first, then the parts. Ask a follow-up for any step you want slowed down.` } });
  }
}

console.log(JSON.stringify({ users: { ada: ada.id, ben: ben.id, cal: cal.id }, agent: { id: agent.id, name: agent.name }, POPSMOSIS_API_KEY: key.raw }));
await prisma.$disconnect();
