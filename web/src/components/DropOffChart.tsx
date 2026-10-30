'use client';

import { useLayoutEffect, useRef, useState } from 'react';
import type { PlayCounts, PlayVideo } from '@/lib/api';

// The points along a play, as the counts name them (api/src/lib/plays.ts).
// `axis` is short enough for a phone's width: "Finished" beside "75%" runs into it.
const STEPS = [
  { key: 'started', label: 'Start', axis: 'Start' },
  { key: 'reached25', label: '25%', axis: '25%' },
  { key: 'reached50', label: '50%', axis: '50%' },
  { key: 'reached75', label: '75%', axis: '75%' },
  { key: 'finished', label: 'Finished', axis: 'End' },
] as const;

// Each video's colour (globals.css, --chart-*): three hues checked together for colour-blind readers, in both themes.
const SERIES: { video: PlayVideo; label: string; short: string; color: string }[] = [
  { video: 'song', label: 'Song', short: 'Song', color: 'var(--chart-song)' },
  { video: 'lesson', label: 'Lesson', short: 'Lesson', color: 'var(--chart-lesson)' },
  { video: 'hook', label: 'Hook clip', short: 'Hook', color: 'var(--chart-hook)' },
];

const MARGIN = { top: 10, right: 84, bottom: 26, left: 40 };
const HEIGHT = 220;
const pct = (n: number) => `${Math.round(n)}%`;

/**
 * Where plays drop off: of every play of each video, across all droplets, the share still going at its start, a
 * quarter, half, three quarters and the end (95% played). One line a video. Hovering a point, or tabbing to it, lists
 * every video's count there; the same numbers are in the table under it.
 */
export function DropOffChart({ totals }: { totals: Record<PlayVideo, PlayCounts> }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [active, setActive] = useState<number | null>(null);

  // Measured before the first paint, so a phone never sees the chart at the default width.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth || 640);
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  const all = SERIES.map((s) => {
    const counts = totals[s.video];
    return { ...s, counts, values: STEPS.map((step) => (counts.started > 0 ? (counts[step.key] / counts.started) * 100 : 0)) };
  });
  const lines = all.filter((l) => l.counts.started > 0);

  const plotW = Math.max(width - MARGIN.left - MARGIN.right, 120);
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const x = (i: number) => MARGIN.left + (i * plotW) / (STEPS.length - 1);
  const y = (value: number) => MARGIN.top + plotH - (value / 100) * plotH;
  const band = plotW / (STEPS.length - 1);
  const last = STEPS.length - 1;
  // Labels at the line ends only where they stand apart; where two ends meet, the legend and the tooltip name them.
  const ends = lines.map((l) => y(l.values[last])).sort((a, b) => a - b);
  const endLabels = ends.every((e, i) => i === 0 || e - ends[i - 1] >= 13);

  return (
    <div ref={box} data-testid="drop-off-chart">
      <ul className="flex flex-wrap gap-x-4 gap-y-1 mb-2 text-xs" style={{ color: 'var(--text-secondary)' }} aria-label="Videos">
        {all.map((l) => (
          <li key={l.video} className="inline-flex items-center gap-1.5">
            <svg width="16" height="8" aria-hidden><line x1="1" y1="4" x2="15" y2="4" stroke={l.color} strokeWidth="2" strokeLinecap="round" /></svg>
            <span style={{ color: 'var(--text-primary)' }}>{l.label}</span>
            <span style={{ color: 'var(--text-muted)' }}>
              {l.counts.started > 0 ? `${l.counts.started.toLocaleString()} ${l.counts.started === 1 ? 'play' : 'plays'}` : 'no plays yet'}
            </span>
          </li>
        ))}
      </ul>

      {lines.length === 0 ? (
        <p className="text-xs py-6" style={{ color: 'var(--text-muted)' }}>No plays counted yet.</p>
      ) : (
        <div className="relative w-full">
          <svg width={width} height={HEIGHT} role="group" aria-label="Share of plays still going at each point, by video" className="block">
            {[0, 25, 50, 75, 100].map((tick) => (
              <g key={tick}>
                <line x1={MARGIN.left} x2={MARGIN.left + plotW} y1={y(tick)} y2={y(tick)} stroke="var(--border)" strokeWidth="1" />
                <text x={MARGIN.left - 6} y={y(tick)} dy="0.32em" textAnchor="end" fontSize="10" fill="var(--text-muted)">{tick}%</text>
              </g>
            ))}
            {STEPS.map((step, i) => (
              <text key={step.key} x={x(i)} y={HEIGHT - 8} textAnchor={i === 0 ? 'start' : i === last ? 'end' : 'middle'} fontSize="10"
                fill="var(--text-muted)">{step.axis}</text>
            ))}
            {active !== null && (
              <line x1={x(active)} x2={x(active)} y1={MARGIN.top} y2={MARGIN.top + plotH} stroke="var(--text-muted)" strokeWidth="1" />
            )}
            {lines.map((l) => (
              <g key={l.video}>
                <polyline points={l.values.map((v, i) => `${x(i)},${y(v)}`).join(' ')} fill="none" stroke={l.color} strokeWidth="2"
                  strokeLinejoin="round" strokeLinecap="round" />
                {l.values.map((v, i) => (
                  <circle key={i} cx={x(i)} cy={y(v)} r="4" fill={l.color} stroke="var(--bg-secondary)" strokeWidth="2" />
                ))}
                {endLabels && (
                  <text x={x(last) + 10} y={y(l.values[last])} dy="0.32em" fontSize="10" fill="var(--text-secondary)">
                    {l.short} {pct(l.values[last])}
                  </text>
                )}
              </g>
            ))}
            {/* One hit area a point along the line, wider than the marks: the pointer or the keyboard finds the step,
                and the tooltip lists every video there. */}
            {STEPS.map((step, i) => (
              <rect key={step.key} x={i === 0 ? MARGIN.left - band / 2 : x(i) - band / 2} y={MARGIN.top} width={band} height={plotH}
                fill="transparent" tabIndex={0} data-testid="drop-off-point"
                aria-label={`${step.label}: ${lines.map((l) => `${l.label} ${pct(l.values[i])}, ${l.counts[step.key]} of ${l.counts.started}`).join('; ')}`}
                onPointerEnter={() => setActive(i)} onPointerLeave={() => setActive(null)}
                onFocus={() => setActive(i)} onBlur={() => setActive(null)} style={{ outline: 'none' }} />
            ))}
          </svg>
          {active !== null && (
            <div role="presentation" data-testid="drop-off-tooltip"
              className="absolute pointer-events-none rounded-md px-2.5 py-2 text-xs whitespace-nowrap"
              style={{
                top: MARGIN.top,
                left: x(active),
                transform: x(active) > width / 2 ? 'translateX(calc(-100% - 10px))' : 'translateX(10px)',
                background: 'var(--bg-primary)', border: '1px solid var(--border)', boxShadow: 'var(--toast-shadow)',
              }}>
              <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: 'var(--text-muted)' }}>
                {active === 0 ? 'Plays started' : active === last ? 'Finished (95% played)' : `${STEPS[active].label} played`}
              </p>
              {lines.map((l) => (
                <p key={l.video} className="flex items-center gap-1.5 leading-5">
                  <svg width="12" height="6" aria-hidden><line x1="1" y1="3" x2="11" y2="3" stroke={l.color} strokeWidth="2" strokeLinecap="round" /></svg>
                  <strong style={{ color: 'var(--text-primary)' }}>{pct(l.values[active])}</strong>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {l.label} · {l.counts[STEPS[active].key].toLocaleString()} of {l.counts.started.toLocaleString()}
                  </span>
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {lines.length > 0 && (
        <details className="mt-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
          <summary className="cursor-pointer hover-text" style={{ color: 'var(--text-muted)' }}>The numbers</summary>
          <table className="mt-2 border-collapse" data-testid="drop-off-table">
            <thead>
              <tr className="text-[9px] uppercase tracking-wider text-left" style={{ color: 'var(--text-muted)' }}>
                <th scope="col" className="py-1 pr-4 font-semibold">Video</th>
                {STEPS.map((step) => <th key={step.key} scope="col" className="py-1 pr-4 font-semibold text-right">{step.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.video} style={{ borderTop: '1px solid var(--border)' }}>
                  <th scope="row" className="py-1 pr-4 text-left font-normal" style={{ color: 'var(--text-primary)' }}>{l.label}</th>
                  {STEPS.map((step, i) => (
                    <td key={step.key} className="py-1 pr-4 text-right tabular-nums">
                      {l.counts[step.key].toLocaleString()}{i > 0 && <span style={{ color: 'var(--text-muted)' }}> · {pct(l.values[i])}</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
