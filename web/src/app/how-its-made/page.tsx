import type { Metadata } from 'next';
import Link from 'next/link';
import PageContainer from '@/components/PageContainer';
import { Wordmark } from '@/components/Wordmark';
import { MakerChip } from '@/components/MakerChip';
import { CheckIcon, PlayIcon } from '@heroicons/react/24/solid';

export const metadata: Metadata = {
  title: 'How it’s made',
  description: 'How AI makes each droplet, how we check it before you see it, and what our checks can still miss.',
};

// What this page says is what the studio does: when a step changes what it checks (docs/PLAN.md, section 5), change
// the page with it. The examples are from docs/test-rounds.md, and the models are the Studio page's defaults on the
// date below. It is written for a high school student, the droplets' default audience, in words a parent reads as
// easily: short sentences and everyday words, the studio's own terms left out.

const card = { background: 'var(--bg-secondary)', border: '1px solid var(--border)' } as const;
const tint = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, transparent)`;
// An accent for small text, blended toward the text colour so it reads in both themes (as on the About page).
const ink = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, var(--text-primary))`;
// The rail's dashed line.
const LINE = 'color-mix(in srgb, var(--text-muted) 55%, transparent)';

type Kind = 'makes' | 'checks' | 'gate';

const KINDS: Record<Kind, { label: string; color: string }> = {
  makes: { label: 'Makes', color: 'var(--accent-blue)' },
  checks: { label: 'Checks', color: 'var(--accent-green)' },
  gate: { label: 'Must pass', color: 'var(--accent-orange)' },
};

// `discloses`: what the step cannot fix, the video says (the picture check's notes, Timeline.notices).
interface Step { name: string; kind: Kind; text: string; discloses?: boolean }

// The studio's steps, as the rail shows them: the lesson's one after another (a "gate" stops the droplet when it
// fails), then the pictures and the sound side by side, as the studio makes them, then the video from both.
const LESSON_STEPS: Step[] = [
  {
    name: 'Plan',
    kind: 'makes',
    text: 'The AI writer plans the whole lesson first: the big idea, the facts, and a picture for each part. The '
      + 'lesson, the spoken lesson and the song all come from this one plan.',
  },
  {
    name: 'Fact check',
    kind: 'gate',
    text: 'While the lesson is written from the plan, the AI starts fresh and checks each fact on its own against what '
      + 'textbooks teach at your level. Wrong facts are fixed, and the fixes are checked again.',
  },
  {
    name: 'Rule check',
    kind: 'checks',
    text: 'Simple computer rules check what can be counted, like whether every song line is short enough to sing. '
      + 'Some problems get fixed right here.',
  },
  {
    name: 'Student test',
    kind: 'checks',
    text: 'A second AI plays a student who has never seen the topic. It reads only the lesson and explains it back, '
      + 'and if it misses the big idea or why it works, the lesson is rewritten once.',
  },
  {
    name: 'Line check',
    kind: 'gate',
    text: 'Starting fresh again, it checks every line of the lesson, the song and the captions against those facts, '
      + 'and for whether it is fit for a classroom. A line that fails is rewritten and checked again, up to three rounds.',
  },
];

const PICTURE_STEPS: Step[] = [
  {
    name: 'Pictures',
    kind: 'makes',
    text: 'An AI paints each picture like a textbook diagram, with its parts labeled. Graphs, charts and math pictures '
      + 'are drawn instead as exact shapes, to scale, and code checks that every part is there.',
  },
  {
    name: 'Picture check',
    kind: 'checks',
    text: 'A second AI looks at each painted picture for parts that are missing or out of place, and for wrong labels. '
      + 'A picture with a part missing or out of place is painted once more. A wrong label stays, but the video '
      + 'circles it and says what’s true.',
    discloses: true,
  },
  {
    name: 'Finding the parts',
    kind: 'checks',
    text: 'Another AI finds each part in the picture, so the camera can zoom in on it and the video can point it out. '
      + 'It checks every spot it found, and a part it can’t find is left out, not guessed.',
  },
];

const SOUND_STEPS: Step[] = [
  {
    name: 'Song and voice',
    kind: 'makes',
    text: 'AI voices sing the song and read the spoken lesson, and each word is matched to the moment it is heard. If '
      + 'most of the song can’t be made out, it is made again.',
  },
];

const VIDEO_STEP: Step = {
  name: 'Video',
  kind: 'makes',
  text: 'Code puts it all together, frame by frame: the pictures, labels, captions, and the notes about mistakes.',
};

// Frames of the lesson videos of two test droplets on mitosis, made for this page with the models listed below (Nov
// 2's Claude calls answered in a Claude Code session). The note: Nov 2's video at 152 s, on a cell dividing wrong,
// where the check found the picture's "Spindle fibers" label pointing at a chromosome, ringed with its sticky note,
// and one chromatid too many at the right end, its note in a corner. The tag: Oct 30's video, the hook at 8 s, where
// the check found nothing.
const FRAMES = {
  note: {
    src: '/how-its-made/mitosis-sticky-note-frame.webp', width: 1600, height: 900,
    alt: 'A frame from a lesson video on mitosis: a cell dividing, with its chromosomes pulled toward both ends and '
      + 'its parts labeled in the picture itself. The label Spindle fibers points at a chromosome, so the video draws '
      + 'a ring around it and sticks a yellow note beside it, marked with a red exclamation point, that says: Spindle '
      + 'fibers are the green threads. In the top right corner, a second note says: Only three chromatids belong '
      + 'here. The narrator says: Neither matches the parent cell, and such cells often die or work badly.',
  },
  tag: {
    src: '/how-its-made/mitosis-hook-frame.webp', width: 960, height: 640,
    alt: 'The bottom left of a frame from an earlier video: a scraped knee, labeled Scraped knee, with a small '
      + 'AI-generated picture tag in the corner.',
  },
};

interface Hard { title: string; happened: string; changed: string }

const HARD: Hard[] = [
  {
    title: 'One wrong fact spreads everywhere',
    happened: 'The lesson, the spoken lesson and the song are all written from the same facts. So one wrong fact shows '
      + 'up in all three. Checking the lines against the facts won’t catch it, because the lines match the facts.',
    changed: 'We added the fact check, which checks the facts themselves first. We test it with a wrong fact like '
      + '“every electron hand-off pumps a proton” (only one of them does). The fact has to be fixed, and so does the '
      + 'song line written from it.',
  },
  {
    title: 'Rhymes pull toward nonsense',
    happened: 'To make a rhyme work, one AI in our tests wrote “Up to NADP plus into the sky.” Students replay a song '
      + 'until it sticks, so a wrong line gets learned.',
    changed: 'Every pair of song lines is checked for what a student would come away believing. If it fails, a '
      + 'songwriter step rewrites it, keeping the beat and the rhyme, and it is checked again.',
  },
  {
    title: 'AI draws what looks right, not what is right',
    happened: 'We asked for Earth with its axis “at right angles to the equator.” The image AI drew a flat equator with '
      + 'a tilted stick through it. It got this wrong six times out of six.',
    changed: 'Pictures that need exact angles or sizes are now drawn as exact shapes, where “at right angles” is an '
      + 'exact number. In our tests, the axis and the tilt came out right every time.',
  },
  {
    title: 'Numbers can’t be painted',
    happened: 'An AI painted a chart about compound interest. It got the bars wrong: simple interest looked bigger than '
      + 'compound interest, and every year’s interest was the same height.',
    changed: 'Charts are now drawn as exact shapes from the real numbers. $1,100, $1,210 and $1,331 are each drawn at '
      + 'exactly the right height, and each amount pops up as the narrator says it.',
  },
  {
    title: 'Labels land on the wrong thing',
    happened: 'The label “Mountain” once landed on a layer of rock underground. Another time, “Southern Hemisphere” '
      + 'landed on Earth’s northern half.',
    changed: 'Now the picture labels its own parts, and the video points only where the picture has the part’s color. '
      + 'When the check finds a wrong label, the video circles it and says what’s true.',
  },
  {
    title: 'The checker is AI too',
    happened: 'On a picture of mitosis, the check caught chromosomes bent the wrong way. But it missed that there '
      + 'were eight on each side instead of four.',
    changed: 'That’s why every AI picture is marked as AI-made, whether a mistake was found or not. A note only says '
      + 'what a check found. It never says a picture is right.',
  },
];

interface Model { name: string; maker: string }
interface Job { job: string; uses: Model[]; or?: Model[]; orWhen?: string; note?: string }

const MODELS_AS_OF = 'October 2026';

const JOBS: Job[] = [
  {
    job: 'Plans, writes and checks the lesson',
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
  { job: 'Draws graphs, charts and math pictures', uses: [{ name: 'Claude Opus 5.5', maker: 'Anthropic' }] },
  { job: 'Finds each part in a picture', uses: [{ name: 'SAM 3.1', maker: 'Meta' }] },
  {
    job: 'Sings the song',
    uses: [{ name: 'Eleven Music', maker: 'ElevenLabs' }],
    or: [{ name: 'Lyria 3.5', maker: 'Google' }],
  },
  { job: 'Reads the spoken lesson', uses: [{ name: 'an ElevenLabs voice', maker: 'ElevenLabs' }] },
  {
    job: 'Matches the sung words to the music',
    uses: [{ name: 'Demucs', maker: 'Meta' }, { name: 'MMS', maker: 'Meta' }],
    note: 'runs on our own computer',
  },
];

const LIMITS = [
  'We don’t list sources. The AI checks facts against what textbooks teach, but it doesn’t look them up, and the '
    + 'lesson doesn’t show where a fact comes from.',
  'The fact check and the line check use the same AI that wrote the lesson. It starts fresh each time, without the '
    + 'writer’s notes, but it can still miss things.',
  'No person reviews each droplet before it goes on the Droplets tab. We test every change on the same 12 topics and '
    + 'watch the test droplets ourselves.',
  'The picture check doesn’t look at the exact drawings of graphs, charts and math. Code checks that each part is '
    + 'there, not that the drawing is right, and they still get the AI tag.',
  'Song lines get a little more room. If the facts don’t cover something, a song line can still pass if it’s true at '
    + 'your level. The lesson and the spoken lesson must stick to the facts.',
  'The song’s words are checked before it is sung. After, we only check that most of its lines can be heard, not '
    + 'every word.',
];

/** How a droplet is made, for anyone: every step as a flow, what the video says when a picture is wrong, why this is
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
            AI writes, draws and sings every droplet. AI is fast, but it can be wrong and still sound sure. So most of
            what <Wordmark size="sm" /> does is check its work. This page shows each check, what it catches, and what
            it can still miss.
          </p>
        </div>
        <ul className="grid gap-3 mt-6 sm:grid-cols-3">
          <Pledge color="var(--accent-orange)" title="Every fact checked">
            by an AI starting fresh, against what textbooks teach at your level
          </Pledge>
          <Pledge color="var(--accent-orange)" title="Every line checked">
            lesson, song and captions. Nothing is posted until they pass.
          </Pledge>
          <Pledge color="var(--accent-yellow)" title="Every picture marked as AI">
            and mistakes we find are shown right on it
          </Pledge>
        </ul>
        <p className="text-sm leading-relaxed mt-4 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          Our checks are AI too, so even the fact check can be wrong. We do our best to catch what it misses: we show
          you what the checks find, test the checks themselves, and let you point out a mistake in a droplet&rsquo;s
          comments, where the next learner will see it.
        </p>
      </header>

      <section aria-labelledby="steps-title" className="mb-12">
        <SectionTitle id="steps-title">How it works, step by step</SectionTitle>
        <p className="text-sm leading-relaxed mb-6 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          Each number is one step, in order. If a droplet fails a step marked <KindChip kind="gate" /> it stops
          there, and nothing is posted.
        </p>

        <div className="max-w-3xl">
          <RailLabel indent>Making the lesson</RailLabel>
          <ol aria-label="Making the lesson">
            {LESSON_STEPS.map((step, i) => <RailStep key={step.name} n={i + 1} step={step} />)}
          </ol>
          <RailMilestone>The lesson is posted. You can read it now, while the videos are made.</RailMilestone>
          <RailItem as="div" marker={<Junction />}>
            <div className="pt-2.5"><RailLabel>Then, at the same time</RailLabel></div>
            <div className="grid gap-x-8 gap-y-6 md:grid-cols-2">
              <Lane title="Pictures" steps={PICTURE_STEPS} first={LESSON_STEPS.length + 1} />
              <Lane title="Sound" steps={SOUND_STEPS} first={LESSON_STEPS.length + PICTURE_STEPS.length + 1} />
            </div>
          </RailItem>
          <RailStep as="div" n={LESSON_STEPS.length + PICTURE_STEPS.length + SOUND_STEPS.length + 1} step={VIDEO_STEP} />
          <RailMilestone last>Both videos are done. The droplet goes on the Droplets tab for everyone.</RailMilestone>
        </div>

        <p className="text-xs leading-relaxed mt-6 max-w-2xl" style={{ color: 'var(--text-muted)' }}>
          When we change how droplets are written, we test the change on the same 12 topics, from photosynthesis to
          the French Revolution. An AI grades each one with the same written checklist, so fixing one subject
          doesn&rsquo;t quietly break another.
        </p>
      </section>

      <section aria-labelledby="notice-title" className="mb-12">
        <SectionTitle id="notice-title">When a picture is wrong, the video tells you</SectionTitle>
        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-3 text-sm leading-relaxed max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
            <p>
              AI pictures are the weakest part of a droplet, and painting a picture again often doesn&rsquo;t fix it.
              For some kinds of mistakes, a second try was no better 9 times out of 11.
            </p>
            <p>
              So when the picture check finds a mistake that could teach you something false, it writes a short note.
              The video sticks it on the picture, right where the mistake is, for as long as the picture is on screen.
              A note about the whole picture, or one with no room beside its place, goes in a corner of the picture.
              Small things about looks, like a cell drawn oval, are left out.
            </p>
            <p>
              A painted picture labels its own parts, and sometimes it gets a label wrong. When the check finds one,
              the video circles that label and puts a note beside it saying what&rsquo;s true.
            </p>
            <p>
              The check can miss mistakes too. So every other AI picture has a small tag that says it&rsquo;s
              AI-made, even the exact drawings of graphs and charts. A picture that is only a comparison, like a
              tug-of-war standing in for mitosis, gets the tag but never a note. It can&rsquo;t teach the topic wrong.
            </p>
          </div>
          <figure>
            <Frame frame={FRAMES.note} />
            <figcaption className="text-xs leading-relaxed mt-2" style={{ color: 'var(--text-muted)' }}>
              A real frame from the lesson video of a test droplet on mitosis. The picture&rsquo;s own &ldquo;Spindle
              fibers&rdquo; label points at a chromosome, so the video circles it and sticks a note beside it saying
              what&rsquo;s true. The note in the corner is about the cell&rsquo;s right end, which has one chromatid
              too many: there was no room for the note beside it.
            </figcaption>
          </figure>
          <figure className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
            <div className="sm:w-64 sm:shrink-0"><Frame frame={FRAMES.tag} /></div>
            <figcaption className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              The bottom left of a frame from an earlier test video on mitosis. The check found nothing wrong in
              this picture, so it gets the small &ldquo;AI-generated picture&rdquo; tag in its corner instead of a
              note. Every AI picture gets one or the other.
            </figcaption>
          </figure>
        </div>
      </section>

      <section aria-labelledby="hard-title" className="mb-12">
        <SectionTitle id="hard-title">Why this is hard</SectionTitle>
        <div className="flex flex-col gap-3 text-sm leading-relaxed mb-6 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          <p>
            Getting one lesson right is hard enough. A droplet is a lesson, a video and a song. Different AIs write it,
            paint it, sing it and time it, and a mistake at any step reaches you. In a song, you might sing it again
            and again.
          </p>
          <p>
            Everything below really happened in our 22 rounds of tests, and each one changed
            how <Wordmark size="sm" /> works.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {HARD.map((h) => <HardCard key={h.title} hard={h} />)}
        </div>
      </section>

      <section aria-labelledby="models-title" className="mb-12">
        <SectionTitle id="models-title">The AI models we use</SectionTitle>
        <p className="text-sm leading-relaxed mb-4 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          These are our defaults as of {MODELS_AS_OF}. Each droplet on the{' '}
          <Link href="/droplets" className="underline" style={{ color: 'var(--accent-blue)' }}>Droplets tab</Link> lists
          the exact models that made it. That way you can tell a droplet made with a newer model from an older one.
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
        <p className="text-sm leading-relaxed mt-6 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          <span className="font-bold" style={{ color: 'var(--text-primary)' }}>Spotted a mistake?</span> Say so in the
          droplet&rsquo;s comments, so the next learner sees it too. Thank you for helping.
        </p>
      </section>

      <div className="rounded-lg p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" style={card}>
        <p className="font-bold" style={{ color: 'var(--text-primary)' }}>See it for yourself.</p>
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
    <h2 id={id} className="text-xs font-bold uppercase tracking-wider mb-4 scroll-mt-18" style={{ color: ink('var(--accent-yellow)', 55) }}>
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

function RailLabel({ indent = false, children }: { indent?: boolean; children: React.ReactNode }) {
  return (
    <p className={`text-[10px] uppercase tracking-wider font-bold mb-3 ${indent ? 'pl-13' : ''}`} style={{ color: 'var(--text-muted)' }}>
      {children}
    </p>
  );
}

/** A stop on the rail: its marker in the left column, with the dashed line running on from it to the next stop,
 *  and what it says beside it. The last stop has no line. */
function RailItem({ as: Tag = 'li', marker, line = true, children }: {
  as?: 'li' | 'div'; marker: React.ReactNode; line?: boolean; children: React.ReactNode;
}) {
  return (
    <Tag className="grid grid-cols-[2.25rem_minmax(0,1fr)] gap-x-4">
      <div aria-hidden className="flex flex-col items-center">
        {marker}
        {line && <span className="flex-1 w-0 my-1.5 border-l-2 border-dashed" style={{ borderColor: LINE }} />}
      </div>
      <div className={line ? 'pb-7' : ''}>{children}</div>
    </Tag>
  );
}

/** One step: its number in a ring of its kind's colour, its name and kind, what it does, and what follows from it
 *  when that matters: a step the droplet must pass stops it when it fails, and the picture check's mistakes it
 *  cannot fix are said in the video (linking to how). */
function RailStep({ n, step, as, line }: { n: number; step: Step; as?: 'li' | 'div'; line?: boolean }) {
  const { color } = KINDS[step.kind];
  return (
    <RailItem as={as} line={line} marker={
      <span className="w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-sm font-bold"
        style={{ border: `2px solid ${tint(color, 75)}`, color: ink(color, 80), background: 'var(--bg-primary)' }}>
        {n}
      </span>
    }>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 min-h-9 mb-1">
        <span className="text-base font-bold" style={{ color: 'var(--text-primary)' }}>{step.name}</span>
        <KindChip kind={step.kind} />
      </p>
      <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{step.text}</p>
      {step.kind === 'gate' && <Then color="var(--accent-red)">Fails? Nothing is posted.</Then>}
      {step.discloses && (
        <Then color="var(--accent-yellow)" href="#notice-title">
          Still wrong in a way that could mislead you? The video says so, right on the
          picture. <span className="not-italic underline whitespace-nowrap">See how ↓</span>
        </Then>
      )}
    </RailItem>
  );
}

/** What follows from a step, after an arrow, in italics. */
function Then({ color, href, children }: { color: string; href?: string; children: React.ReactNode }) {
  const body = <><span aria-hidden className="not-italic mr-1.5">→</span>{children}</>;
  const className = 'block mt-2 text-sm italic leading-relaxed';
  const style = { color: ink(color, 60) };
  return href
    ? <a href={href} className={`${className} hover:brightness-125`} style={style}>{body}</a>
    : <p className={className} style={style}>{body}</p>;
}

/** Where the droplet is ready for something: a check in a filled ring. */
function RailMilestone({ last = false, children }: { last?: boolean; children: React.ReactNode }) {
  const color = 'var(--accent-brand)';
  return (
    <RailItem as="div" line={!last} marker={
      <span className="w-9 h-9 shrink-0 rounded-full flex items-center justify-center"
        style={{ border: `2px solid ${tint(color, 75)}`, color: ink(color, 80), background: tint(color, 18) }}>
        <CheckIcon className="w-4 h-4" />
      </span>
    }>
      <p className="min-h-9 flex items-center text-sm font-bold" style={{ color: ink(color, 80) }}>{children}</p>
    </RailItem>
  );
}

/** Where the rail opens onto the two lanes made at the same time: a small hollow stop. */
function Junction() {
  return <span className="w-3.5 h-3.5 mt-2.5 shrink-0 rounded-full" style={{ border: `2px solid ${LINE}`, background: 'var(--bg-primary)' }} />;
}

/** One of the two short rails made at the same time, numbered on from the lesson's steps. */
function Lane({ title, steps, first }: { title: string; steps: Step[]; first: number }) {
  return (
    <div className="min-w-0">
      <RailLabel indent>{title}</RailLabel>
      <ol aria-label={title} start={first}>
        {steps.map((step, i) => <RailStep key={step.name} n={first + i} step={step} line={i < steps.length - 1} />)}
      </ol>
    </div>
  );
}

function KindChip({ kind }: { kind: Kind }) {
  const { label, color } = KINDS[kind];
  return (
    <span className="inline-block whitespace-nowrap align-middle text-[9px] uppercase px-1.5 py-px rounded-full font-semibold"
      style={{ background: tint(color, 14), border: `1px solid ${tint(color, 45)}`, color: ink(color, 85) }}>
      {label}
    </span>
  );
}

function Frame({ frame }: { frame: { src: string; width: number; height: number; alt: string } }) {
  return (
    <img src={frame.src} alt={frame.alt} width={frame.width} height={frame.height} loading="lazy"
      className="block w-full h-auto rounded-lg" style={{ border: '1px solid var(--border)' }} />
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
        <p className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: ink('var(--accent-green)', 70) }}>What we changed</p>
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
