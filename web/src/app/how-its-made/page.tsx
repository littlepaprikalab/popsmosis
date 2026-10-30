import type { Metadata } from 'next';
import Link from 'next/link';
import PageContainer from '@/components/PageContainer';
import { Wordmark } from '@/components/Wordmark';
import { MakerChip } from '@/components/MakerChip';
import { PlayIcon } from '@heroicons/react/24/solid';

export const metadata: Metadata = {
  title: 'How it’s made',
  description: 'How a droplet is written, painted and sung by AI, what checks it before you see it, and what those checks still miss.',
};

// What this page says is what the studio does: when a step changes what it checks (docs/PLAN.md, section 5), change
// the page with it. The examples are from docs/test-rounds.md, and the models are the Studio page's defaults on the
// date below.

const card = { background: 'var(--bg-secondary)', border: '1px solid var(--border)' } as const;
const tint = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, transparent)`;
// An accent for small text, blended toward the text colour so it reads in both themes (as on the About page).
const ink = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, var(--text-primary))`;

type Kind = 'makes' | 'checks' | 'gate';

const KINDS: Record<Kind, { label: string; color: string }> = {
  makes: { label: 'Makes', color: 'var(--accent-blue)' },
  checks: { label: 'Checks', color: 'var(--accent-green)' },
  gate: { label: 'Must pass', color: 'var(--accent-orange)' },
};

interface Step { name: string; kind: Kind; text: string }

const LESSON_STEPS: Step[] = [
  {
    name: 'Plan',
    kind: 'makes',
    text: 'The writer model first plans the lesson as one explanation: the question it answers, the one idea to '
      + 'remember, the chain of causes behind it, the facts, and a picture for each scene. The text lesson, the '
      + 'narration and the song are then all written from that plan.',
  },
  {
    name: 'Code checks',
    kind: 'checks',
    text: 'Code checks what a rule can count: every part a line names is in the scene that shows it, and every song '
      + 'line is short enough to sing. It repairs what it can and records the rest.',
  },
  {
    name: 'Learner test',
    kind: 'checks',
    text: 'A second model plays a student who has never met the topic. It reads only the lesson, then explains the big '
      + 'idea, why it works, and what would happen if a part stopped working. If it misses the main idea or most of '
      + 'the why, the lesson is rewritten once and read again.',
  },
  {
    name: 'Fact audit',
    kind: 'gate',
    text: 'A fresh call to the writer model, with none of the writing conversation, judges every fact on its own '
      + 'against what textbooks teach at the learner’s level, reading it the way an exam would. A wrong fact is '
      + 'corrected, and the correction is judged again. Notes a learner sends show what their course covers, but '
      + 'never make a fact true.',
  },
  {
    name: 'Fact-check',
    kind: 'gate',
    text: 'Another fresh call holds every line to the audited facts: each sentence of the lesson and the narration, '
      + 'each couplet of the song, and every caption, on-screen line and title. A line the facts don’t support is '
      + 'rewritten and checked again, for up to three rounds; if one still fails, the droplet isn’t posted. A line '
      + 'not fit for a classroom fails too, and a hashtag that fails is dropped.',
  },
];

const VIDEO_STEPS: Step[] = [
  {
    name: 'Pictures',
    kind: 'makes',
    text: 'An image model paints each scene as a textbook diagram with no writing in it: the video writes every label '
      + 'and word itself. With Claude writing, our default, charts and anything read to scale are drawn as code from '
      + 'the facts’ numbers instead of painted.',
  },
  {
    name: 'Picture check',
    kind: 'checks',
    text: 'A second model looks at every painted picture for parts that are missing, out of place or facing the wrong '
      + 'way, and for any writing. A picture with those problems is painted once more, and one with writing in it is '
      + 'never used. Mistakes it finds that could teach something false are shown in the video (below).',
  },
  {
    name: 'Labels',
    kind: 'checks',
    text: 'A segmentation model finds each part in the picture, and only where the picture has that part’s colour. A '
      + 'model then looks at the picture with every label’s pin on it, to catch one on the wrong part. A part that '
      + 'can’t be found is left unlabelled rather than guessed.',
  },
  {
    name: 'Song and voice',
    kind: 'makes',
    text: 'AI voices sing the song and speak the narration. Each word is then lined up with the audio, so the lyrics, '
      + 'labels and moving parts land on time. A song whose lines mostly can’t be made out is made again.',
  },
  {
    name: 'Video',
    kind: 'makes',
    text: 'Code draws every frame from the plan: the labels, the captions, the moving dots that show what flows where '
      + '(fact-checked like a line), and the notes about mistakes.',
  },
];

// A note the art check wrote on a real picture, round 16's mitosis hook (docs/test-rounds.md).
const NOTE = 'The purple new cells sit in the deeper skin as a loose row; in truth, dividing cells lie in the base of '
  + 'the top layer, just under the scrape.';

interface Hard { title: string; happened: string; changed: string }

const HARD: Hard[] = [
  {
    title: 'One wrong fact spreads everywhere',
    happened: 'The lesson, the narration and the song are all written from one set of facts, so a wrong fact turns up '
      + 'in all three, on screen and sung. A check that only holds lines to the facts would pass every one of them.',
    changed: 'The fact audit judges the facts themselves before anything is checked against them. It is tested on a '
      + 'fact like “every electron hand-off pumps a proton” (only one of them does): the fact must be corrected, and '
      + 'so must the lyric written from it.',
  },
  {
    title: 'A rhyme pulls toward nonsense',
    happened: 'To land a rhyme, a model in testing wrote “Up to NADP plus into the sky” and “Calvin builds the sugar '
      + 'from that text”. A song is replayed until it sticks, so a wrong line gets learned.',
    changed: 'Every couplet is fact-checked as something a student will come away believing. One that fails is '
      + 'rewritten by a songwriter pass that keeps its rhythm and rhyme, then checked again.',
  },
  {
    title: 'Image models draw what looks right, not what is right',
    happened: 'Asked for Earth with its axis “at right angles to the equator”, an image model drew a level equator '
      + 'with a tilted rod through it, six times out of six.',
    changed: 'Geometry is drawn as code, where “at right angles” is a number. Drawn that way, the axis and the tilt '
      + 'came out right in every try.',
  },
  {
    title: 'Numbers can’t be painted',
    happened: 'A compound-interest chart came back with the simple-interest bars taller than the compound ones, and '
      + 'every year’s interest the same height.',
    changed: 'Charts are drawn from the facts’ numbers, $1,100, $1,210 and $1,331 each at exactly its height, and each '
      + 'amount is written on the chart as the narrator says it.',
  },
  {
    title: 'Labels land on the wrong thing',
    happened: '“Mountain” was pinned on a band of rock underground. Another time, “Southern Hemisphere” went on '
      + 'Earth’s northern half.',
    changed: 'A part is found only where the picture has its colour, and a model looks at every label’s pin on the '
      + 'picture before the video is made.',
  },
  {
    title: 'The checker is AI too',
    happened: 'On a mitosis picture, the check caught chromosomes bent the wrong way, but missed that there were '
      + 'eight on each side where there should have been four.',
    changed: 'Every AI picture is tagged, whether a mistake was found or not, and the card says what a check found, '
      + 'never that a picture is right.',
  },
];

interface Model { name: string; maker: string }
interface Job { job: string; uses: Model[]; or?: Model[]; orWhen?: string; note?: string }

const MODELS_AS_OF = 'October 2026';

const JOBS: Job[] = [
  {
    job: 'Plans, writes, audits and fact-checks the lesson',
    uses: [{ name: 'Claude Opus 5.5', maker: 'Anthropic' }],
    or: [{ name: 'Muse Wonder 1.0', maker: 'Meta' }, { name: 'Muse Spark 1.3', maker: 'Meta' }],
  },
  {
    job: 'Plays the student and checks the pictures',
    uses: [{ name: 'Claude Sonnet 5.5', maker: 'Anthropic' }],
    or: [{ name: 'Muse Spark 1.3', maker: 'Meta' }],
    orWhen: 'when a Muse model writes',
  },
  {
    job: 'Paints the pictures',
    uses: [{ name: 'Gemini 3.1 Flash Image', maker: 'Google' }],
    or: [{ name: 'Muse Image', maker: 'Meta' }],
  },
  { job: 'Finds each part in a picture', uses: [{ name: 'SAM 3.1', maker: 'Meta' }] },
  {
    job: 'Sings the song',
    uses: [{ name: 'Eleven Music', maker: 'ElevenLabs' }],
    or: [{ name: 'Lyria 3.5', maker: 'Google' }],
  },
  { job: 'Speaks the narration', uses: [{ name: 'an ElevenLabs voice', maker: 'ElevenLabs' }] },
  {
    job: 'Lines the sung words up with the music',
    uses: [{ name: 'Demucs', maker: 'Meta' }, { name: 'MMS', maker: 'Meta' }],
    note: 'run on our own machine',
  },
];

const LIMITS = [
  'No sources are cited. Facts are judged by AI against what textbooks teach at the learner’s level, not looked up, '
    + 'and the lesson doesn’t link to where a fact comes from.',
  'The checkers are AI too. The fact audit and the fact-check run on the same model that wrote the lesson, each '
    + 'starting fresh, without the writer’s reasoning. They catch a lot, not everything.',
  'A person doesn’t review each droplet before it’s shared on the Droplets tab.',
  'Charts and diagrams drawn as code aren’t looked over by the picture check. They’re built from the plan and its '
    + 'numbers, and carry the AI tag like every picture.',
  'Where the facts say nothing, a song couplet can pass on what’s true at the learner’s level, since a song speaks '
    + 'in images. The lesson and the narration are held to the facts alone.',
  'The song is fact-checked before it’s sung, but the singing isn’t checked word by word: only that most of its '
    + 'lines can be made out.',
];

/** How a droplet is made: every step and what it checks, what the video says when a picture is wrong, why this is
 *  hard, the models, and what the checks still miss. */
export default function HowItsMadePage() {
  return (
    <PageContainer maxWidth="4xl" className="py-8 px-4">
      <header className="mb-10">
        <h1 className="text-2xl font-bold tracking-wider mb-4" style={{ color: 'var(--text-primary)' }}>
          How a droplet is made
        </h1>
        <div className="flex flex-col gap-3 text-sm leading-relaxed max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          <p>
            Every droplet is written, painted and sung by AI. AI is fast and fluent, and sometimes confidently wrong,
            so most of what <Wordmark size="sm" /> does is checking. This page shows what runs on every droplet, what
            it catches, and what it still misses.
          </p>
        </div>
        <ul className="grid gap-3 mt-6 sm:grid-cols-3">
          <Pledge color="var(--accent-green)" title="Every fact audited">
            judged on its own against what textbooks teach
          </Pledge>
          <Pledge color="var(--accent-orange)" title="Every line fact-checked">
            lesson, narration, song and captions; nothing is posted until it passes
          </Pledge>
          <Pledge color="var(--accent-yellow)" title="Every picture tagged">
            as AI-made, with any mistake the check found shown beside it
          </Pledge>
        </ul>
      </header>

      <section aria-labelledby="steps-title" className="mb-12">
        <SectionTitle id="steps-title">The steps</SectionTitle>
        <div className="grid gap-6 md:grid-cols-2">
          <StepList title="The lesson" sub="before anything is posted" steps={LESSON_STEPS} start={1} />
          <StepList title="The videos" sub="while you read the lesson" steps={VIDEO_STEPS} start={LESSON_STEPS.length + 1} />
        </div>
        <p className="text-xs leading-relaxed mt-4 max-w-2xl" style={{ color: 'var(--text-muted)' }}>
          Changes to how droplets are written are tried on the same 12 topics, from the light reactions of
          photosynthesis to the French Revolution, and graded against a written rubric, so a fix for one subject
          doesn&rsquo;t quietly make another worse.
        </p>
      </section>

      <section aria-labelledby="notice-title" className="mb-12">
        <SectionTitle id="notice-title">When a picture is wrong, the video says so</SectionTitle>
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
          <div className="flex flex-col gap-3 text-sm leading-relaxed lg:flex-1" style={{ color: 'var(--text-secondary)' }}>
            <p>
              Image models are the least reliable part of a droplet, and painting a picture again often
              doesn&rsquo;t help: for problems other than a missing or misplaced part, a second try was no better 9
              times in 11. So when the picture check finds a mistake that could teach something false, it writes a
              sentence for the student, and the video shows it on a card beside the picture for as long as the
              picture is on screen. Matters of look, like a cell drawn oval, are left out.
            </p>
            <p>
              The check can miss a mistake too, so every other AI picture carries a small tag saying it is one, the
              drawn ones included. An analogy&rsquo;s picture, an everyday stand-in like a tug-of-war for mitosis,
              gets the tag but never a card: it can&rsquo;t teach the topic wrong.
            </p>
          </div>
          <figure className="lg:w-[22rem] lg:shrink-0">
            <div className="rounded-lg p-4 sm:p-5 flex flex-col gap-4" style={{ background: '#28374B' }}>
              <NoticeCard notes={[NOTE]} />
              <div><AiTag /></div>
            </div>
            <figcaption className="text-xs leading-relaxed mt-2" style={{ color: 'var(--text-muted)' }}>
              The card as the video draws it, with a note the check wrote on a real picture while we tested a lesson
              on mitosis; under it, the tag on every other picture.
            </figcaption>
          </figure>
        </div>
      </section>

      <section aria-labelledby="hard-title" className="mb-12">
        <SectionTitle id="hard-title">Why this is hard</SectionTitle>
        <p className="text-sm leading-relaxed mb-6 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          A lesson is one thing to get right. A droplet is a lesson, a narrated video and a song, written, painted,
          sung and timed by different models, and a mistake at any step reaches the learner, sometimes sung back to
          them on repeat. Each of these happened in the 22 rounds of test topics we ran end to end, and each changed
          how <Wordmark size="sm" /> works.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          {HARD.map((h) => <HardCard key={h.title} hard={h} />)}
        </div>
      </section>

      <section aria-labelledby="models-title" className="mb-12">
        <SectionTitle id="models-title">The models</SectionTitle>
        <p className="text-sm leading-relaxed mb-4 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          Our defaults as of {MODELS_AS_OF}. Every droplet on the{' '}
          <Link href="/droplets" className="underline" style={{ color: 'var(--accent-blue)' }}>Droplets tab</Link> names
          the models that made it, by the version that ran, so you can tell one made on a newer model from an older one.
        </p>
        <ul className="rounded-lg overflow-hidden" style={card} aria-label="The models">
          {JOBS.map((j, i) => (
            <li key={j.job} className="p-4 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-4"
              style={i > 0 ? { borderTop: '1px solid var(--border)' } : undefined}>
              <span className="text-sm sm:w-64 sm:shrink-0" style={{ color: 'var(--text-secondary)' }}>{j.job}</span>
              <span className="text-sm flex flex-col gap-1 min-w-0">
                <ModelNames models={j.uses} strong />
                {j.or && (
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                    or <ModelNames models={j.or} />{j.orWhen && ` ${j.orWhen}`}
                  </span>
                )}
                {j.note && <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{j.note}</span>}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="limits-title" className="mb-12">
        <SectionTitle id="limits-title">What we don&rsquo;t do yet</SectionTitle>
        <ul className="flex flex-col gap-3 max-w-2xl">
          {LIMITS.map((limit) => (
            <li key={limit} className="flex gap-3 text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              <span aria-hidden className="mt-[0.55em] w-1.5 h-1.5 rounded-full shrink-0" style={{ background: 'var(--accent-orange)' }} />
              <span>{limit}</span>
            </li>
          ))}
        </ul>
      </section>

      <div className="rounded-lg p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" style={card}>
        <p className="font-bold" style={{ color: 'var(--text-primary)' }}>See what comes out of it.</p>
        <div className="flex flex-wrap gap-2 shrink-0">
          <Link href="/droplets" className="inline-flex items-center gap-1.5 px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-all hover:brightness-110"
            style={{ background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' }}>
            <PlayIcon aria-hidden="true" className="w-3.5 h-3.5" />Watch a droplet
          </Link>
          <Link href="/learn" className="inline-block px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-colors hover-border"
            style={{ border: '1px solid var(--accent-brand)', color: 'var(--accent-brand)' }}>
            Ask for a topic
          </Link>
        </div>
      </div>
    </PageContainer>
  );
}

function SectionTitle({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="text-xs font-bold uppercase tracking-wider mb-4" style={{ color: ink('var(--accent-yellow)', 55) }}>
      {children}
    </h2>
  );
}

function Pledge({ color, title, children }: { color: string; title: string; children: React.ReactNode }) {
  return (
    <li className="rounded-lg p-4" style={{ ...card, borderTop: `3px solid ${color}` }}>
      <p className="font-bold text-sm mb-1" style={{ color: 'var(--text-primary)' }}>{title}</p>
      <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{children}</p>
    </li>
  );
}

function StepList({ title, sub, steps, start }: { title: string; sub: string; steps: Step[]; start: number }) {
  return (
    <div className="rounded-lg p-4 sm:p-5" style={card}>
      <h3 className="font-bold mb-0.5" style={{ color: 'var(--text-primary)' }}>{title}</h3>
      <p className="text-xs mb-4" style={{ color: ink('var(--accent-purple)', 85) }}>{sub}</p>
      <ol start={start} className="flex flex-col gap-4">
        {steps.map((step, i) => (
          <li key={step.name} className="flex gap-3">
            <span aria-hidden className="w-6 h-6 rounded-full shrink-0 flex items-center justify-center text-[11px] font-bold"
              style={{ background: 'var(--bg-tertiary)', color: 'var(--text-primary)' }}>
              {start + i}
            </span>
            <div className="min-w-0">
              <p className="flex flex-wrap items-center gap-2 mb-1">
                <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{step.name}</span>
                <KindChip kind={step.kind} />
              </p>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{step.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function KindChip({ kind }: { kind: Kind }) {
  const { label, color } = KINDS[kind];
  return (
    <span className="inline-block whitespace-nowrap text-[9px] uppercase px-1.5 py-px rounded-full font-semibold"
      style={{ background: tint(color, 14), border: `1px solid ${tint(color, 45)}`, color: ink(color, 85) }}>
      {label}
    </span>
  );
}

/** The card the video draws beside a picture with a known mistake (draw_notice in studio/render/primitives.py), in
 *  its own colours, which are the video's in either theme. */
function NoticeCard({ notes }: { notes: string[] }) {
  return (
    <div className="rounded-xl overflow-hidden flex" style={{ background: '#FFFFFF', boxShadow: '0 4px 12px rgba(0,0,0,0.35)' }}>
      <span aria-hidden className="w-1.5 shrink-0" style={{ background: '#F59E0B' }} />
      <div className="p-4 min-w-0">
        <p className="flex items-center gap-2 font-extrabold text-sm" style={{ color: '#0F172A' }}>
          <svg aria-hidden viewBox="0 0 20 18" className="w-4 h-4 shrink-0">
            <path d="M10 0 20 18H0Z" fill="#F59E0B" />
            <text x="10" y="15.5" textAnchor="middle" fontSize="12" fontWeight="800" fill="#0F172A">!</text>
          </svg>
          AI-generated picture
        </p>
        <p className="text-xs font-semibold mt-2" style={{ color: '#475569' }}>A check found these mistakes:</p>
        <ul className="mt-1 flex flex-col gap-1">
          {notes.map((note) => (
            <li key={note} className="text-xs font-semibold leading-relaxed flex gap-2" style={{ color: '#0F172A' }}>
              <span aria-hidden>•</span><span>{note}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** The tag the video draws on every other AI picture (draw_ai_tag in studio/render/primitives.py). */
function AiTag() {
  return (
    <span className="inline-block rounded-full text-[11px] font-semibold px-2.5 py-1"
      style={{ background: 'rgba(15,23,42,0.72)', color: 'rgba(255,255,255,0.92)' }}>
      AI-generated picture
    </span>
  );
}

function HardCard({ hard }: { hard: Hard }) {
  return (
    <article className="rounded-lg p-4 sm:p-5 flex flex-col gap-3" style={card} aria-label={hard.title}>
      <h3 className="font-bold" style={{ color: 'var(--text-primary)' }}>{hard.title}</h3>
      <div>
        <p className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: ink('var(--accent-red)', 70) }}>What happened</p>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{hard.happened}</p>
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: ink('var(--accent-green)', 70) }}>What changed</p>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{hard.changed}</p>
      </div>
    </article>
  );
}

function ModelNames({ models, strong = false }: { models: Model[]; strong?: boolean }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      {models.map((m, i) => (
        <span key={m.name} className="inline-flex items-center gap-1.5">
          <MakerChip maker={m.maker} />
          <span style={{ color: strong ? 'var(--text-primary)' : undefined }}>{m.name}{i < models.length - 1 ? ',' : ''}</span>
        </span>
      ))}
    </span>
  );
}
