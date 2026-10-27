import 'dotenv/config';
import prisma from '../lib/prisma.js';
import { generateApiKey } from '../lib/apikey.js';
import { termsAccepted } from '../lib/terms.js';

// Local-development seed: three users (Ada is an admin), one agent, one topic with
// its lesson, its two videos, a follow-up thread, and a like and a comment from Ada on
// the Droplets tab (not Ben's: he asked for it, and the tab never says who asked), one
// topic still waiting, and older topics with videos rendering and failed. Cal has one
// finished droplet of their own and nothing else: the browser tests delete Cal's
// account. Idempotent per run: the seed users (Cal too) and their questions are
// recreated, the agent's key is rotated and printed.
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

// The bill of materials under the lesson (lib/breakdown.ts), in the numbers of the first live runs (docs/PLAN.md, section 12).
const SAMPLE_BREAKDOWN = {
  lessonBreakdown: {
    steps: [
      { step: 'request', seconds: 0.6, tries: 1, costUsd: 0, models: [] },
      { step: 'bundle', seconds: 196.0, tries: 1, costUsd: 0.5312, models: [{ model: 'claude-opus-5-5', calls: 1, costUsd: 0.5312, credits: null }] },
      { step: 'factcheck', seconds: 80.4, tries: 1, costUsd: 0.1788, models: [{ model: 'claude-opus-5-5', calls: 1, costUsd: 0.1788, credits: null }] },
    ],
    seconds: 277.0,
  },
  videoBreakdown: {
    steps: [
      { step: 'art', seconds: 76.0, tries: 1, costUsd: 0.0081, models: [{ model: 'claude-haiku-5-5', calls: 3, costUsd: 0.0081, credits: null }],
        tools: [{ name: 'skia-python', version: '144.1' }] },
      { step: 'locate', seconds: 0.1, tries: 1, costUsd: 0, models: [] },
      { step: 'audio', seconds: 94.6, tries: 1, costUsd: 0.08, models: [
        { model: 'lyria-3.5', calls: 1, costUsd: 0.08, credits: null },
        { model: 'eleven_multilingual_v2', calls: 2, costUsd: null, credits: 3214 },
      ], tools: [{ name: 'ffmpeg', version: '6.1.1-3ubuntu5' }] },
      { step: 'timing', seconds: 110.2, tries: 1, costUsd: 0, models: [
        { model: 'htdemucs', calls: 1, costUsd: 0, credits: null },
        { model: 'mms-fa', calls: 1, costUsd: 0, credits: null },
      ], tools: [
        { name: 'demucs', version: '4.1.0' }, { name: 'torchaudio', version: '2.11.0+cpu' },
        { name: 'torch', version: '2.11.0+cpu' }, { name: 'ffmpeg', version: '6.1.1-3ubuntu5' },
      ] },
      { step: 'timeline', seconds: 0.3, tries: 1, costUsd: 0, models: [] },
      { step: 'render', seconds: 157.0, tries: 1, costUsd: 0, models: [],
        tools: [{ name: 'skia-python', version: '144.1' }, { name: 'ffmpeg', version: '6.1.1-3ubuntu5' }] },
      { step: 'upload', seconds: 14.2, tries: 1, costUsd: 0, models: [] },
    ],
    seconds: 376.3,
  },
};

// Ada and Ben have agreed to the current Terms; Cal has no record of it, like an account from before the box, so the
// web app asks Cal first (components/TermsGate.tsx).
const agreed = termsAccepted();
const ada = await prisma.user.upsert({ where: { googleId: 'seed-ada' }, update: agreed, create: { email: 'ada@example.com', googleId: 'seed-ada', name: 'Ada', role: 'ADMIN', ...agreed } });
const ben = await prisma.user.upsert({ where: { googleId: 'seed-ben' }, update: agreed, create: { email: 'ben@example.com', googleId: 'seed-ben', name: 'Ben', ...agreed } });
const unagreed = { termsVersion: null, termsAcceptedAt: null };
const cal = await prisma.user.upsert({ where: { googleId: 'seed-cal' }, update: unagreed, create: { email: 'cal@example.com', googleId: 'seed-cal', name: 'Cal', ...unagreed } });
const key = generateApiKey();
const agent = await prisma.agent.upsert({ where: { name: 'popsmosis-teacher' }, update: { apiKeyHash: key.hashed, enabled: true }, create: { name: 'popsmosis-teacher', apiKeyHash: key.hashed } });

await prisma.question.deleteMany({ where: { userId: { in: [ada.id, ben.id, cal.id] } } });

const q1 = await prisma.question.create({ data: { userId: ben.id, status: 'ANSWERED', createdAt: hoursAgo(3), content: 'ATP synthesis: how do cells actually make ATP?' } });
const a1 = await prisma.answer.create({ data: { questionId: q1.id, agentId: agent.id, model: 'claude', createdAt: hoursAgo(2.5), bundle: ATP_BUNDLE, factcheck: PASSED, ...SAMPLE_VIDEO, ...SAMPLE_BREAKDOWN, content: `**ATP synthase** is a tiny rotary motor in the inner membrane of the mitochondrion.

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

const q3 = await prisma.question.create({ data: { userId: cal.id, status: 'ANSWERED', createdAt: hoursAgo(5), content: 'Osmosis, for my biology test on Monday' } });
const a3 = await prisma.answer.create({ data: { questionId: q3.id, agentId: agent.id, model: 'claude', createdAt: hoursAgo(4.5), bundle: { ...ATP_BUNDLE, topic: 'Osmosis' }, factcheck: PASSED, ...SAMPLE_VIDEO, content: '**Osmosis** is water moving across a membrane toward the side with more dissolved stuff.' } });

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

console.log(JSON.stringify({ users: { ada: ada.id, ben: ben.id, cal: cal.id }, agent: { id: agent.id, name: agent.name }, droplet: a1.id, calDroplet: a3.id, POPSMOSIS_API_KEY: key.raw }));
await prisma.$disconnect();
