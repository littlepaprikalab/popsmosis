'use client';

import { Fragment, useState } from 'react';
import type { Breakdown, BreakdownModel, BreakdownStep, Video } from '@/lib/api';

/** The studio's step names, as the bill reads them. A step not here shows its own name. */
const STEP_LABELS: Record<string, string> = {
  request: 'Read the request',
  bundle: 'Write the lesson',
  factcheck: 'Fact-check',
  post: 'Post the lesson',
  art: 'Draw the diagrams',
  locate: 'Find the labels',
  audio: 'Song and voice',
  timing: 'Time the words',
  timeline: 'Plan the videos',
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
  return `${(seconds / 60).toFixed(1)} min`;
}

const credits = (n: number) => `${n.toLocaleString('en-US')} cr`;

function modelCost(m: BreakdownModel): string {
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

/** Each maker's colour on the bill, from the theme's accents so it holds in light and dark. A maker not here is grey. */
const MAKER_COLORS: Record<string, string> = {
  Meta: 'var(--accent-blue)',
  Anthropic: 'var(--accent-orange)',
  Google: 'var(--accent-green)',
  ElevenLabs: 'var(--accent-purple)',
  FFmpeg: 'var(--accent-pink)',
};

const tint = (color: string, percent: number) => `color-mix(in srgb, ${color} ${percent}%, transparent)`;

/** Who made a model or tool, as a chip in the maker's colour. Nothing when the API does not know. */
function MakerChip({ maker, className = '' }: { maker: string | null; className?: string }) {
  if (!maker) return null;
  const color = MAKER_COLORS[maker];
  const style = color
    ? { background: tint(color, 14), border: `1px solid ${tint(color, 45)}`, color }
    : { background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' };
  return (
    <span data-testid="maker" style={style}
      className={`inline-block align-middle whitespace-nowrap text-[9px] uppercase px-1.5 py-px rounded-full font-semibold ${className}`}>
      {maker}
    </span>
  );
}

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

/**
 * How the droplet was made, as a bill of materials: each step's line, the models that ran it
 * with how many calls and what they cost, the tools it ran with their versions, who made each,
 * how long it took, and the totals. Folded until opened,
 * and only built once opened: a page of cards would otherwise carry every bill's rows, twice.
 */
export function DropletBreakdown({ breakdown, video }: { breakdown: Breakdown; video: Video | null }) {
  const [open, setOpen] = useState(false);
  const { steps } = breakdown;
  if (steps.length === 0) return null;
  const stepSeconds = steps.reduce((sum, s) => sum + (s.seconds ?? 0), 0);
  const notes = [
    breakdown.seconds !== null && stepSeconds > breakdown.seconds + 1
      && 'The times add up to more than the total: the diagrams and the audio are made side by side.',
    steps.some((s) => s.tries > 1) && 'A step tried more than once counts every try, in time and in cost.',
    steps.some((s) => s.models.some((m) => m.costUsd === 0)) && 'free: run on the studio’s own machine.',
    steps.some((s) => s.tools?.some((t) => t.version)) && 'Versions: as installed on the studio’s machine that made the videos.',
    breakdown.credits !== null && 'cr: credits on the ElevenLabs plan, which bills by credit.',
    breakdown.unpriced > 0 && 'n/a: the provider did not say what it charged.',
    video?.status === 'RENDERING' && (steps.some((s) => s.part === 'videos')
      ? 'The videos’ lines are from an earlier try; they are brought up to date when the videos are done.'
      : 'The videos’ lines are added when they are done.'),
  ].filter((n): n is string => !!n);

  return (
    <details className="mt-3 text-[11px] group" data-testid="breakdown" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="cursor-pointer select-none flex items-center gap-2 flex-wrap" style={{ color: 'var(--text-muted)' }}>
        <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--accent-yellow)' }}>Bill of materials</span>
        <span data-testid="breakdown-total">{duration(breakdown.seconds)} · {totalCost(breakdown)}</span>
        <span className="text-[10px] group-open:hidden">show</span>
        <span className="text-[10px] hidden group-open:inline">hide</span>
      </summary>
      {open && <div className="mt-2 rounded-md p-2" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <PhoneBill breakdown={breakdown} />
        <table className="w-full border-collapse hidden sm:table" style={{ color: 'var(--text-secondary)' }}>
          <thead>
            <tr className="text-[9px] uppercase tracking-wider text-left" style={{ color: 'var(--text-muted)', borderBottom: '1px dashed var(--border)' }}>
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
            {steps.map((s, i) => {
              const lines = linesOf(s);
              const rows = lines.length > 0 ? lines : [null];
              return (
                <Fragment key={`${s.part}-${s.step}`}>
                  {rows.map((l, j) => (
                    <tr key={j} data-testid={j === 0 ? 'breakdown-step' : undefined}
                      style={j === rows.length - 1 && i < steps.length - 1 ? { borderBottom: '1px solid var(--border)' } : undefined}>
                      <td className={`${cell} tabular-nums`} style={{ color: 'var(--text-muted)' }}>{j === 0 ? String(i + 1).padStart(2, '0') : ''}</td>
                      <td className={cell} style={{ color: 'var(--text-primary)' }}>
                        {j === 0 && (
                          <>
                            {STEP_LABELS[s.step] ?? s.step}
                            {s.tries > 1 && <span className="ml-1" style={{ color: 'var(--accent-orange)' }}>×{s.tries} tries</span>}
                          </>
                        )}
                      </td>
                      <td className={`${cell} break-all`}>{l ? <LineName line={l} /> : <span style={{ color: 'var(--text-muted)' }}>code</span>}</td>
                      <td className={cell}><MakerChip maker={l?.maker ?? null} /></td>
                      <td className={num}>{l?.calls ?? ''}</td>
                      <td className={num}>{j === 0 ? duration(s.seconds) : ''}</td>
                      <td className={`${num} !pr-0`} style={{ color: 'var(--text-primary)' }}>{l?.cost ?? '—'}</td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="font-bold" style={{ borderTop: '1px dashed var(--border)', color: 'var(--text-primary)' }}>
              <td className={cell} colSpan={5}>Total</td>
              <td className={num}>{duration(breakdown.seconds)}</td>
              <td className={`${num} !pr-0`}>{totalCost(breakdown)}</td>
            </tr>
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
function PhoneBill({ breakdown }: { breakdown: Breakdown }) {
  return (
    <div className="sm:hidden" style={{ color: 'var(--text-secondary)' }}>
      <ol>
        {breakdown.steps.map((s, i) => (
          <li key={`${s.part}-${s.step}`} className="py-1" data-testid="breakdown-step-phone"
            style={i < breakdown.steps.length - 1 ? { borderBottom: '1px solid var(--border)' } : undefined}>
            <div className="flex gap-2">
              <span className="tabular-nums" style={{ color: 'var(--text-muted)' }}>{String(i + 1).padStart(2, '0')}</span>
              <span className="min-w-0" style={{ color: 'var(--text-primary)' }}>
                {STEP_LABELS[s.step] ?? s.step}
                {s.tries > 1 && <span className="ml-1" style={{ color: 'var(--accent-orange)' }}>×{s.tries}</span>}
              </span>
              <span className="ml-auto tabular-nums whitespace-nowrap">{duration(s.seconds)}</span>
            </div>
            {linesOf(s).map((l) => (
              <div key={`${l.name} ${l.version ?? ''}`} className="flex gap-2 pl-6">
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
        <span>Total</span>
        <span className="ml-auto text-right tabular-nums">{duration(breakdown.seconds)} · {totalCost(breakdown)}</span>
      </div>
    </div>
  );
}
