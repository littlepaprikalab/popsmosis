import 'dotenv/config';
import prisma from '../lib/prisma.js';
import { generateApiKey } from '../lib/apikey.js';

// Local-development seed: two users (Ada is an admin), one agent, one topic with
// its lesson, its two videos, a follow-up thread, and a like and a comment from Ada on
// the Droplets tab (not Ben's: he asked for it, and the tab never says who asked), one
// topic still waiting, and older topics with videos rendering and failed. Idempotent
// per run: the seed users' questions are recreated, the agent's key is rotated and printed.
//
// The videos are web/public/samples/: two 8-second clips made with ffmpeg for this
// repo (H.264, +faststart, like the studio's), served by the web app itself.
// Usage: npm --workspace api run seed:dev

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600e3);

// What the studio saves with a lesson (docs/PLAN.md, step 1), cut down to what the
// pages read: the title comes from course and topic, the song's words from lyrics.
const ATP_BUNDLE = {
  course: 'AP Biology',
  topic: 'ATP synthesis',
  core_idea: 'A proton gradient spins ATP synthase, and each turn makes ATP.',
  facts: [
    { id: 'f1', text: 'The electron transport chain pumps protons out of the matrix.', source_quote: '' },
    { id: 'f2', text: 'Protons flowing back through ATP synthase spin its rotor.', source_quote: '' },
  ],
  misconception: 'ATP synthase does not pump protons; it lets them back in.',
  lyrics: [
    { id: 'v1', section: 'verse', text: 'Pump the protons out, build the gradient high', names: [], fact_id: 'f1' },
    { id: 'c1', section: 'chorus', text: 'Spin the rotor, make the ATP', names: ['ATP synthase'], fact_id: 'f2' },
    { id: 'c2', section: 'chorus', text: 'Every turn a little energy for me', names: [], fact_id: 'f2' },
  ],
};
const PASSED = { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] };
const SAMPLE_VIDEO = {
  videoStatus: 'READY' as const,
  songVideoUrl: '/samples/song.mp4',
  lessonVideoUrl: '/samples/lesson.mp4',
  songPosterUrl: '/samples/song.jpg',
  lessonPosterUrl: '/samples/lesson.jpg',
  videoUpdatedAt: hoursAgo(2.4),
};

const ada = await prisma.user.upsert({ where: { googleId: 'seed-ada' }, update: {}, create: { email: 'ada@example.com', googleId: 'seed-ada', name: 'Ada', role: 'ADMIN' } });
const ben = await prisma.user.upsert({ where: { googleId: 'seed-ben' }, update: {}, create: { email: 'ben@example.com', googleId: 'seed-ben', name: 'Ben' } });
const key = generateApiKey();
const agent = await prisma.agent.upsert({ where: { name: 'popsmosis-teacher' }, update: { apiKeyHash: key.hashed, enabled: true }, create: { name: 'popsmosis-teacher', apiKeyHash: key.hashed } });

await prisma.question.deleteMany({ where: { userId: { in: [ada.id, ben.id] } } });

const q1 = await prisma.question.create({ data: { userId: ben.id, status: 'ANSWERED', createdAt: hoursAgo(3), content: 'ATP synthesis: how do cells actually make ATP?' } });
const a1 = await prisma.answer.create({ data: { questionId: q1.id, agentId: agent.id, model: 'claude', createdAt: hoursAgo(2.5), bundle: ATP_BUNDLE, factcheck: PASSED, ...SAMPLE_VIDEO, content: `**ATP synthase** is a tiny rotary motor in the inner membrane of the mitochondrion.

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
// The triggers count these on the answer, as they do for the API's own writes.
await prisma.dropletLike.create({ data: { answerId: a1.id, userId: ada.id, createdAt: hoursAgo(1.2) } });
await prisma.dropletComment.create({ data: { answerId: a1.id, userId: ada.id, createdAt: hoursAgo(1.1), content: 'The chorus is the only way I remember which side the protons start on.' } });
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
    // The first two lessons show the other video states: still rendering, and failed.
    const video = i === 1 ? { videoStatus: 'RENDERING' as const, videoUpdatedAt: new Date() }
      : i === 2 ? { videoStatus: 'FAILED' as const, videoError: 'render: ffmpeg exited 1 on the song', videoUpdatedAt: hoursAgo(28) }
        : {};
    await prisma.answer.create({ data: { questionId: q.id, agentId: agent.id, model: 'claude', createdAt: hoursAgo(29 + i * 7), ...video, content: `**${topics[i % topics.length]}**, the big picture first, then the parts. Ask a follow-up for any step you want slowed down.` } });
  }
}

console.log(JSON.stringify({ users: { ada: ada.id, ben: ben.id }, agent: { id: agent.id, name: agent.name }, droplet: a1.id, POPSMOSIS_API_KEY: key.raw }));
await prisma.$disconnect();
