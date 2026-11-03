'use client';

import { Fragment, useEffect, useState, type ReactNode } from 'react';
import type { Breakdown, BreakdownModel, BreakdownStep, EndToEnd, Progress, Video } from '@/lib/api';
import { MakerChip } from '@/components/MakerChip';

/** The studio's step names, as the bill reads them. A step not here shows its own name. */
const STEP_LABELS: Record<string, string> = {
  request: 'Read the request',
  bundle: 'Write the lesson',
  audit: 'Audit the facts',
  factcheck: 'Fact-check',
  post: 'Post the lesson',
  art: 'Draw the diagrams',
  locate: 'Find the labels',
  audio: 'Make the audio', // the song and the voice, or the voice alone (STUDIO_VIDEOS=lesson): its models say which
  timing: 'Time the words',
  timeline: 'Plan the videos',
  arrows: 'Place the arrows',
  render: 'Render',
  upload: 'Upload',
};

export function usd(v: number): string {
  if (v === 0) return '$0';
  if (v < 0.001) return '<$0.001'; // a Haiku check costs a few hundredths of a cent: not "$0.000"
  return v < 0.01 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`;
}

export function duration(seconds: number | null): string {
  if (seconds === null) return '—';
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  // A time from the request counts the queue, which can be a night or a weekend: not "2880.0 min".
  if (seconds < 90 * 60) return `${(seconds / 60).toFixed(1)} min`;
  if (seconds < 48 * 3600) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} days`;
}

const credits = (n: number) => `${n.toLocaleString('en-US')} cr`;

function modelCost(m: BreakdownModel): string {
  if (m.via === 'session') return 'in session';
  if (m.costUsd === null) return m.credits !== null ? credits(m.credits) : 'n/a';
  return m.costUsd === 0 ? 'free' : usd(m.costUsd);
}

/** "$0.80 + 3,214 cr": every priced call, and what was charged in credits with no price set. */
export function totalCost(b: Breakdown): string {
  let text = usd(b.costUsd);
  if (b.credits !== null) text += ` + ${credits(b.credits)}`;
  if (b.unpriced > 0) text += ' + n/a';
  return text;
}

const cell = 'py-1 pr-3 align-top';
const num = `${cell} text-right tabular-nums whitespace-nowrap`;

/** One line of a step on the bill: a model it called, or a tool it ran, at the version installed. */
interface Line {
  name: string;
  version: string | null; // a tool's; a model's name is its version
  maker: string | null;
  calls: number | null; // a model's; a tool is run, not called
  cost: string | null; // a model's; a tool is not billed
}

/** A step's models, then the tools it ran on the studio's machine. */
function linesOf(s: BreakdownStep): Line[] {
  return [
    ...s.models.map((m) => ({ name: m.model, version: null, maker: m.maker, calls: m.calls, cost: modelCost(m) })),
    ...(s.tools ?? []).map((t) => ({ name: t.name, version: t.version, maker: t.maker, calls: null, cost: null })),
  ];
}

/** A line's name, with a tool's version after it in a lighter colour. */
function LineName({ line }: { line: Line }) {
  return <>{line.name}{line.version && <span style={{ color: 'var(--text-muted)' }}> {line.version}</span>}</>;
}


// --- while the droplet is made ------------------------------------------------------------

/** Every step the studio can run, in the order the bill lists them. */
const ORDER = Object.keys(STEP_LABELS);
// The steps the page expects before any has run. The post is left out: it makes the answer the
// lesson's bill is sent with, so a bill never has it.
const LESSON_STEPS = ['request', 'bundle', 'audit', 'factcheck'];
const VIDEO_STEPS = ['art', 'locate', 'audio', 'timing', 'timeline', 'arrows', 'render', 'upload'];
// What comes right after each step: the art and the audio are made side by side, and meet at the timeline.
const NEXT: Record<string, string[]> = {
  request: ['bundle'], bundle: ['audit'], audit: ['factcheck'], factcheck: ['post', 'art', 'audio'], post: ['art', 'audio'],
  art: ['locate'], locate: ['timeline'], audio: ['timing'], timing: ['timeline'], timeline: ['arrows'],
  arrows: ['render'], render: ['upload'],
};

type Status = 'done' | 'running' | 'failed' | 'waiting';

/** One step on the bill: what it has cost so far, and, while it runs, when it started. */
interface Row {
  step: string;
  status: Status;
  bill: BreakdownStep | null; // null: nothing spent on it yet
  since: number | null; // while it runs: when it started, by this browser's clock
}

const rank = (step: string) => (ORDER.includes(step) ? ORDER.indexOf(step) : ORDER.length);

/**
 * Every step of a droplet being made: the ones done, the ones running now, and the ones still to come. A step
 * still to come that a later step has gone past is left out: this run had it done already (a render resumed on
 * another machine), or does not run it.
 */
export function progressRows(p: Progress): Row[] {
  const billed = new Map((p.breakdown?.steps ?? []).map((s) => [s.step, s]));
  const running = new Map(p.running.map((r) => [r.step, r.since ?? Date.parse(r.startedAt)]));
  const failed = new Set(p.failed);
  const started = (step: string) => billed.has(step) || running.has(step) || failed.has(step);
  const passed = (step: string, seen = new Set<string>()): boolean => (NEXT[step] ?? []).some((next) => {
    if (seen.has(next)) return false;
    seen.add(next);
    return started(next) || passed(next, seen);
  });
  const names = [...LESSON_STEPS, ...(p.videos ? VIDEO_STEPS : [])];
  for (const step of [...billed.keys(), ...running.keys(), ...failed]) if (!names.includes(step)) names.push(step);
  return names
    .sort((a, b) => rank(a) - rank(b))
    .filter((step) => started(step) || !passed(step))
    .map((step) => ({
      step,
      status: running.has(step) ? 'running' : failed.has(step) ? 'failed' : billed.has(step) ? 'done' : 'waiting',
      bill: billed.get(step) ?? null,
      since: running.get(step) ?? null,
    }));
}

/** How far along a droplet being made is, for a line of text: steps done, and steps in all. */
export function progressCount(p: Progress): { done: number; total: number } {
  const rows = progressRows(p);
  return { done: rows.filter((r) => r.status === 'done').length, total: rows.length };
}

/** A running step's time, counting up every second: its earlier tries and this one so far; or the droplet's.
 *  Only this text re-renders each second, never the bill around it. */
function Elapsed({ base, since }: { base: number; since: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <>{duration(base + Math.max(0, (now - since) / 1000))}</>;
}

/** A step's time: every try together, and for one running now, counting up; nothing yet for one to come. */
function RowTime({ row }: { row: Row }) {
  if (row.status === 'waiting') return <>—</>;
  if (row.status === 'running' && row.since !== null) return <Elapsed base={row.bill?.seconds ?? 0} since={row.since} />;
  return <>{duration(row.bill?.seconds ?? null)}</>;
}

const MARKS: Record<Status, { label: string; color: string }> = {
  done: { label: 'done', color: 'var(--accent-green)' },
  running: { label: 'running now', color: 'var(--accent-cyan)' },
  failed: { label: 'failed; to be tried again', color: 'var(--accent-red)' },
  waiting: { label: 'still to come', color: 'var(--text-muted)' },
};

/** Where a step stands, as a mark before it: a tick, a pulsing dot, a cross, or a hollow dot. */
function StatusMark({ status }: { status: Status }) {
  const { label, color } = MARKS[status];
  return (
    <span role="img" aria-label={label} title={label} className="inline-flex w-3 h-[1lh] shrink-0 items-center justify-center" style={{ color }}>
      {status === 'running' ? <span className="inline-block w-1.5 h-1.5 rounded-full animate-live-pulse" style={{ background: color }} />
        : status === 'waiting' ? <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ border: `1px solid ${color}` }} />
          : status === 'done' ? '✓' : '✕'}
    </span>
  );
}

/** A step's name on the bill, with its tries when it took more than one. */
function StepName({ row, short = false }: { row: Row; short?: boolean }) {
  const tries = row.bill?.tries ?? 0;
  return (
    <>
      {STEP_LABELS[row.step] ?? row.step}
      {tries > 1 && <span className="ml-1" style={{ color: 'var(--accent-orange)' }}>×{tries}{short ? '' : ' tries'}</span>}
    </>
  );
}

// --- from the request -----------------------------------------------------------------------

/** A stretch of the time from the request: a wait, the studio at work, or both when the bill cannot tell them apart. */
interface Stretch {
  label: string;
  seconds: number;
  aside: string | null; // after the label, lighter: when the lesson was posted
}

/** The time from the request, under the bill's work: the stretches it was spent in, and the line they add up to. */
interface Timeline {
  stretches: Stretch[]; // none when there would be one: the line under them says it
  line: { label: string; seconds: number; end: boolean }; // end: the droplet done, its videos READY or its lesson posted when it has none
}

// How far a part's work may run past its stretch of the clock and still be set against it: the database's clock
// stamps the request and the lesson, the API's the READY, and the studio's times the work.
const CLOCKS_APART = 60;

/** A time as the bill shows it while it reads in minutes: to the tenth of a minute, in seconds. */
const toTenth = (s: number) => Math.round(Number((s / 60).toFixed(1)) * 10) * 6;

/**
 * The time from the request, split where the bill allows into the waits and the studio's work: up to the lesson,
 * the wait for the studio to start and the lesson's writing; after it, the wait for the videos (the hand-off, and the
 * gap before a retry) and their making. The studio starts the videos only once the lesson is posted, so each part's
 * work fits in its stretch. One with no time on the bill (a READY sent without its bill: too big, unreadable, or by
 * hand; an API older than `partSeconds`), or more than its stretch by more than the clocks can be apart, leaves the
 * stretch whole rather than count its work as waiting. A droplet without videos is done with its lesson; while its
 * videos are made, or when they failed, the time runs to the lesson only.
 *
 * While the end reads in minutes, every time is rounded to the tenth the bill shows before it is split, so the
 * stretches add up to the end as shown, and the lesson's and the videos' work to the studio's work, not a tenth off.
 */
function timelineOf(endToEnd: EndToEnd | null | undefined, bill: Breakdown | null, video: Video | null, making: boolean): Timeline | null {
  if (!endToEnd) return null;
  const { lesson, videos } = endToEnd;
  const end = videos ?? lesson;
  const shown = end >= 60 && end < 90 * 60 ? toTenth : (s: number) => s;
  const parts = [
    { length: lesson, work: bill?.partSeconds?.lesson ?? null,
      wait: 'Waiting for the studio to start', working: 'Writing the lesson', whole: 'Request to lesson' },
    ...(videos === null ? [] : [{ length: videos - lesson, work: bill?.partSeconds?.videos ?? null,
      wait: 'Waiting between the lesson and the videos', working: 'Making the videos', whole: 'Lesson to videos' }]),
  ];
  const fits = parts.map((p) => p.work !== null && p.work <= p.length + CLOCKS_APART);
  const lengths = videos === null ? [shown(lesson)] : [shown(lesson), shown(videos) - shown(lesson)];
  // Each part's work as shown, never more than its stretch.
  const work = parts.map((p, i) => (fits[i] ? Math.min(shown(p.work!), lengths[i]) : 0));
  if (fits.length === 2 && fits[0] && fits[1] && bill?.seconds != null) {
    // Both parts split: the lesson's work as near its own as the two stretches allow, and the videos' the rest of
    // the studio's work as shown, so the two add up to it.
    const total = shown(bill.seconds);
    const low = Math.max(0, total - lengths[1]);
    const high = Math.min(lengths[0], total);
    if (low <= high) {
      work[0] = Math.min(Math.max(shown(parts[0].work!), low), high);
      work[1] = total - work[0];
    }
  }
  const stretches = parts
    .flatMap((p, i): Stretch[] => (fits[i]
      ? [{ label: p.wait, seconds: lengths[i] - work[i], aside: null },
        { label: p.working, seconds: work[i], aside: i === 0 && videos !== null ? `posted at ${duration(lesson)}` : null }]
      : [{ label: p.whole, seconds: lengths[i], aside: null }]))
    .filter((st) => st.seconds > 0);
  const done = videos !== null || (!video && !making);
  return {
    stretches: stretches.length > 1 ? stretches : [],
    line: { label: done ? 'End to end' : 'Request to lesson', seconds: end, end: done },
  };
}

// Over the stretches: what sets them apart from the steps' times above.
const CLOCK = 'From the request, by the clock';

function StretchName({ stretch }: { stretch: Stretch }) {
  return <>{stretch.label}{stretch.aside && <span style={{ color: 'var(--text-muted)' }}> ({stretch.aside})</span>}</>;
}

/**
 * How the droplet was made, as a bill of materials: each step's line, the models that ran it
 * with how many calls and what they cost, the tools it ran with their versions, who made each,
 * how long it took, and the studio's work in all; under it, the time from the request (`endToEnd`), split into the
 * waits and the work so that it adds up, since the bill's own time is the studio's steps alone, the diagrams and the
 * audio side by side. Folded until opened,
 * and only built once opened: a page of cards would otherwise carry every bill's rows, twice.
 *
 * With `progress`, the droplet is still being made, and the bill is how far it has got: every
 * step, each done, running (its time counting up), failed or still to come, and what it has cost
 * so far, with the time so far counting up too while a step runs. Open with `defaultOpen`, as it
 * is while the lesson waits for its videos; one that turns true later (the studio has started on a
 * waiting topic) opens it, unless the reader has opened or folded it themselves.
 */
export function DropletBreakdown({ breakdown, video, progress, endToEnd, defaultOpen = false }: {
  breakdown: Breakdown | null;
  video: Video | null;
  progress?: Progress | null;
  endToEnd?: EndToEnd | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (defaultOpen && !touched) setOpen(true);
  }, [defaultOpen, touched]);
  const making = !!progress;
  const bill = progress ? progress.breakdown : breakdown;
  const rows: Row[] = progress ? progressRows(progress)
    : (breakdown?.steps ?? []).map((s) => ({ step: s.step, status: 'done' as const, bill: s, since: null }));
  if (rows.length === 0) return null;

  const steps = bill?.steps ?? [];
  const stepSeconds = steps.reduce((sum, s) => sum + (s.seconds ?? 0), 0);
  // The droplet's time so far: while a step runs, counting up from what the API last said; else the bill's.
  const totalSince = progress && progress.running.length > 0 ? progress.since : undefined;
  const totalSeconds = progress?.seconds ?? bill?.seconds ?? null;
  const totalTime: ReactNode = totalSince !== undefined ? <Elapsed base={0} since={totalSince} /> : duration(totalSeconds);
  const timeline = timelineOf(endToEnd, bill, video, making);
  const end = timeline?.line.end ? timeline.line : undefined;
  const notes = [
    making && rows.some((r) => r.status === 'running')
      && 'A step running now counts its time up, the total with it, and shows what it has spent so far.',
    bill && bill.seconds !== null && stepSeconds > bill.seconds + 1
      && `The steps’ times add up to more than ${making ? 'the time so far' : 'the studio’s work'}: the diagrams and the audio are made at the same time.`,
    steps.some((s) => s.tries > 1) && 'A step tried more than once counts every try, in time and in cost.',
    steps.some((s) => s.models.some((m) => m.costUsd === 0 && m.via !== 'session'))
      && '“free”: the model runs on the studio’s own machine, so nothing is billed.',
    steps.some((s) => s.models.some((m) => m.via === 'session'))
      && '“in session”: the API could not answer, so the teacher’s own Claude session did, on its plan rather than the studio’s API key; those calls add nothing to the total.',
    steps.some((s) => s.tools?.some((t) => t.version))
      && 'The lighter number after a tool’s name is its version, as installed on the machine that made the videos.',
    bill && bill.credits !== null && 'cr: credits on the ElevenLabs plan, which bills by credit.',
    bill && bill.unpriced > 0 && 'n/a: the provider did not say what it charged.',
    // An API older than the progress reports sends the videos' lines only with READY.
    !making && video?.status === 'RENDERING' && (steps.some((s) => s.part === 'videos')
      ? 'The videos’ lines are from an earlier try; they are brought up to date when the videos are done.'
      : 'The videos’ lines are added when they are done.'),
  ].filter((n): n is string => !!n);

  const done = rows.filter((r) => r.status === 'done').length;
  const anyStarted = rows.some((r) => r.status !== 'waiting');
  const cost = bill ? totalCost(bill) : '$0';

  return (
    <details className="mt-3 text-[11px] group" data-testid="breakdown" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      {/* The flex line drops the browser's own triangle, so the chevron and the underlined show/hide say it opens. */}
      <summary className="group/summary cursor-pointer select-none flex items-center gap-2 flex-wrap list-none [&::-webkit-details-marker]:hidden"
        style={{ color: 'var(--text-muted)' }} onClick={() => setTouched(true)}>
        <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--accent-yellow)' }}>
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden="true"
            className="shrink-0 -rotate-90 group-open:rotate-0 transition-transform duration-200">
            <path d="M4 6L8 10L12 6" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Bill of materials
        </span>
        {making ? (
          // No blinking mark of its own: the card shows the droplet meter (DropletMeter), or the videos' line, for that.
          <span data-testid="breakdown-progress">
            {anyStarted ? (
              <span>
                Making it · {done} of {rows.length} steps done
                {(totalSince !== undefined || totalSeconds !== null) && <> · <span data-testid="breakdown-elapsed" className="tabular-nums">{totalTime}</span></>}
                {' '}· {cost} so far
              </span>
            ) : `${rows.length} steps to go`}
          </span>
        ) : (
          <span data-testid="breakdown-total">
            {end && <><span data-testid="breakdown-end-to-end" style={{ color: 'var(--text-secondary)' }}>{duration(end.seconds)} end to end</span> · </>}
            {duration(bill?.seconds ?? null)}{end ? ' of work' : ''} · {cost}
          </span>
        )}
        <span className="text-[10px] underline underline-offset-2 group-hover/summary:text-[var(--text-hover)] group-open:hidden">show</span>
        <span className="text-[10px] underline underline-offset-2 group-hover/summary:text-[var(--text-hover)] hidden group-open:inline">hide</span>
      </summary>
      {open && <div className="mt-2 rounded-md p-2" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <PhoneBill rows={rows} bill={bill} making={making} time={totalTime} timeline={timeline} />
        <table className="w-full border-collapse hidden sm:table" style={{ color: 'var(--text-secondary)' }}>
          <thead>
            <tr className="text-[9px] uppercase tracking-wider text-left" style={{ color: 'var(--text-muted)', borderBottom: '1px dashed var(--border)' }}>
              {making && <th className={`${cell} !pr-1 font-semibold`}><span className="sr-only">Status</span></th>}
              <th className={`${cell} font-semibold`}>#</th>
              <th className={`${cell} font-semibold`}>Step</th>
              <th className={`${cell} font-semibold`}>Model / tool</th>
              <th className={`${cell} font-semibold`}>Maker</th>
              <th className={`${num} font-semibold`}>Qty</th>
              <th className={`${num} font-semibold`}>Time</th>
              <th className={`${num} font-semibold !pr-0`}>Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const lines = r.bill ? linesOf(r.bill) : [];
              const items = lines.length > 0 ? lines : [null];
              return (
                <Fragment key={r.step}>
                  {items.map((l, j) => (
                    <tr key={j} data-testid={j === 0 ? 'breakdown-step' : undefined} data-status={j === 0 && making ? r.status : undefined}
                      style={{
                        ...(j === items.length - 1 && i < rows.length - 1 && { borderBottom: '1px solid var(--border)' }),
                        ...(r.status === 'waiting' && { opacity: 0.55 }),
                      }}>
                      {making && <td className={`${cell} !pr-1`}>{j === 0 && <StatusMark status={r.status} />}</td>}
                      <td className={`${cell} tabular-nums`} style={{ color: 'var(--text-muted)' }}>{j === 0 ? String(i + 1).padStart(2, '0') : ''}</td>
                      <td className={cell} style={{ color: 'var(--text-primary)' }}>{j === 0 && <StepName row={r} />}</td>
                      <td className={`${cell} break-all`}>
                        {l ? <LineName line={l} /> : <span style={{ color: 'var(--text-muted)' }}>{r.bill && r.status !== 'running' ? 'code' : '—'}</span>}
                      </td>
                      <td className={cell}><MakerChip maker={l?.maker ?? null} /></td>
                      <td className={num}>{l?.calls ?? ''}</td>
                      <td className={num} style={r.status === 'running' ? { color: 'var(--accent-cyan)' } : undefined}>
                        {j === 0 && <RowTime row={r} />}
                      </td>
                      <td className={`${num} !pr-0`} style={{ color: 'var(--text-primary)' }}>{l?.cost ?? '—'}</td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="font-bold" style={{ borderTop: '1px dashed var(--border)', color: 'var(--text-primary)' }}>
              <td className={cell} colSpan={making ? 6 : 5}>{making ? 'So far' : 'Studio’s work'}</td>
              <td className={num}>{totalTime}</td>
              <td className={`${num} !pr-0`}>{cost}</td>
            </tr>
            {timeline && <>
              <tr data-testid="breakdown-clock" className="text-[9px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                <td className={`${cell} !pt-3 font-semibold`} colSpan={making ? 8 : 7}>{CLOCK}</td>
              </tr>
              {timeline.stretches.map((s) => (
                <tr key={s.label} data-testid="breakdown-stretch" style={{ color: 'var(--text-secondary)' }}>
                  <td className={`${cell} pl-3`} colSpan={making ? 6 : 5}><StretchName stretch={s} /></td>
                  <td className={num}>{duration(s.seconds)}</td>
                  <td className={`${num} !pr-0`} />
                </tr>
              ))}
              <tr data-testid="breakdown-milestone" className="font-bold" style={{ color: 'var(--text-primary)' }}>
                <td className={cell} colSpan={making ? 6 : 5}>{timeline.line.label}</td>
                <td className={num}>{duration(timeline.line.seconds)}</td>
                <td className={`${num} !pr-0`} />
              </tr>
            </>}
          </tfoot>
        </table>
        {notes.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-[10px]" style={{ color: 'var(--text-muted)' }}>
            {notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        )}
      </div>}
    </details>
  );
}

/** The same bill on a phone, where seven columns do not fit: each step on a line with its time, its models and tools under it. */
function PhoneBill({ rows, bill, making, time, timeline }: {
  rows: Row[]; bill: Breakdown | null; making: boolean; time: ReactNode; timeline: Timeline | null;
}) {
  return (
    <div className="sm:hidden" style={{ color: 'var(--text-secondary)' }}>
      <ol>
        {rows.map((r, i) => (
          <li key={r.step} className="py-1" data-testid="breakdown-step-phone" data-status={making ? r.status : undefined}
            style={{
              ...(i < rows.length - 1 && { borderBottom: '1px solid var(--border)' }),
              ...(r.status === 'waiting' && { opacity: 0.55 }),
            }}>
            <div className="flex gap-2">
              {making && <StatusMark status={r.status} />}
              <span className="tabular-nums" style={{ color: 'var(--text-muted)' }}>{String(i + 1).padStart(2, '0')}</span>
              <span className="min-w-0" style={{ color: 'var(--text-primary)' }}><StepName row={r} short /></span>
              <span className="ml-auto tabular-nums whitespace-nowrap" style={r.status === 'running' ? { color: 'var(--accent-cyan)' } : undefined}>
                <RowTime row={r} />
              </span>
            </div>
            {r.bill && linesOf(r.bill).map((l) => (
              <div key={`${l.name} ${l.version ?? ''}`} className={`flex gap-2 ${making ? 'pl-11' : 'pl-6'}`}>
                <span className="min-w-0 break-all">
                  <LineName line={l} />{l.calls !== null && l.calls > 1 ? ` ×${l.calls}` : ''}<MakerChip maker={l.maker} className="ml-1.5" />
                </span>
                <span className="ml-auto tabular-nums whitespace-nowrap" style={{ color: 'var(--text-primary)' }}>{l.cost ?? ''}</span>
              </div>
            ))}
          </li>
        ))}
      </ol>
      <div className="flex gap-2 pt-1 font-bold" style={{ borderTop: '1px dashed var(--border)', color: 'var(--text-primary)' }}>
        <span>{making ? 'So far' : 'Studio’s work'}</span>
        <span className="ml-auto text-right tabular-nums">{time} · {bill ? totalCost(bill) : '$0'}</span>
      </div>
      {timeline && <>
        <div className="pt-2 text-[9px] uppercase tracking-wider font-semibold" style={{ color: 'var(--text-muted)' }}>{CLOCK}</div>
        {timeline.stretches.map((s) => (
          <div key={s.label} className="flex gap-2 pl-3" data-testid="breakdown-stretch-phone">
            <span className="min-w-0"><StretchName stretch={s} /></span>
            <span className="ml-auto text-right tabular-nums whitespace-nowrap">{duration(s.seconds)}</span>
          </div>
        ))}
        <div className="flex gap-2 pt-1 font-bold" data-testid="breakdown-milestone-phone" style={{ color: 'var(--text-primary)' }}>
          <span>{timeline.line.label}</span>
          <span className="ml-auto text-right tabular-nums">{duration(timeline.line.seconds)}</span>
        </div>
      </>}
    </div>
  );
}
