'use client';

import { useEffect, useState } from 'react';
import type { Progress } from '@/lib/api';

/**
 * About how many minutes a droplet takes to make, as the card tells the learner. docs/speed.md measured about half
 * an hour from request to videos, a few minutes of it before the studio starts.
 */
export const USUAL_MINUTES = 25;

// The drop from the Droplets heading (Highlights.tsx), with room in the box for a hollow drop's outline.
const DROP = 'M32 12.5C32 12.5 20.3 28 20.3 38a11.7 11.7 0 0 0 23.4 0C43.7 28 32 12.5 32 12.5Z';

/** A drop: blinking while a droplet is being made, hollow before the studio has started on it. */
export function Drop({ state, className = '' }: { state: 'now' | 'empty'; className?: string }) {
  return (
    <svg viewBox="18 10 28 42" aria-hidden="true" className={`${state === 'now' ? 'animate-drop-blink' : ''} ${className}`}
      data-state={state}>
      <path d={DROP} fill={state === 'empty' ? 'none' : 'var(--accent-brand)'}
        stroke={state === 'empty' ? 'var(--text-muted)' : 'var(--accent-brand)'} strokeWidth="2.5" />
    </svg>
  );
}

/** Whether the studio has started on the droplet: a step running or done, or one that failed. */
export function hasStarted(p: Progress): boolean {
  return p.running.length > 0 || p.failed.length > 0 || !!p.breakdown?.steps.length;
}

/**
 * The droplet's time so far, as the bill of materials counts it: while a step runs, counting on from `since`;
 * else the time the API last said.
 */
function makingSeconds(p: Progress, now: number): number {
  if (p.running.length > 0 && p.since !== undefined) return Math.max(0, (now - p.since) / 1000);
  return p.seconds ?? p.breakdown?.seconds ?? 0;
}

/** Whole minutes the droplet has been made for, moving on by itself while a step runs. */
function useMinutes(p: Progress): number {
  const [minutes, setMinutes] = useState(() => Math.floor(makingSeconds(p, Date.now()) / 60));
  useEffect(() => {
    const tick = () => setMinutes(Math.floor(makingSeconds(p, Date.now()) / 60)); // the same minute re-renders nothing
    tick();
    if (p.running.length === 0 || p.since === undefined) return;
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [p]);
  return minutes;
}

/** A number in the line beside the drop, in bold: the minute under way in the drop's aqua, the usual time in the text colour. */
function Num({ now = false, children }: { now?: boolean; children: React.ReactNode }) {
  return <span className="font-bold tabular-nums" style={{ color: now ? 'var(--accent-brand)' : 'var(--text-primary)' }}>{children}</span>;
}

/**
 * Where a droplet being made has got, by the minute, beside a blinking drop, and what to expect: about how long it
 * takes, and that the reader can leave and come back. The minute is a shade brighter than the rest and its numbers
 * bold, so they are what the eye finds. Past the usual time it says this one is taking longer. Before the studio
 * starts the drop is hollow and still.
 */
export function DropletMeter({ progress }: { progress: Progress }) {
  const minutes = useMinutes(progress);
  const started = hasStarted(progress);
  return (
    <p className="text-xs mt-2 flex items-start gap-1.5" style={{ color: 'var(--text-muted)' }}
      data-testid="droplet-meter" data-minutes={started ? minutes : undefined}>
      <Drop state={started ? 'now' : 'empty'} className="w-2 h-3 mt-0.5 shrink-0" />
      <span data-testid="droplet-meter-text">
        {!started ? <>A droplet takes about <Num>{USUAL_MINUTES}</Num> minutes to make.</> : (
          <>
            <span style={{ color: 'var(--text-secondary)' }}>
              Minute <Num now>{minutes + 1}</Num>{minutes < USUAL_MINUTES && <> of about <Num>{USUAL_MINUTES}</Num></>}
            </span>
            {minutes >= USUAL_MINUTES ? ' · this one is taking longer than most; you can leave and come back.' : ' · you can leave and come back.'}
          </>
        )}
      </span>
    </p>
  );
}
