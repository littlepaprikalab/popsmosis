'use client';

import { useEffect, useState } from 'react';
import type { Progress } from '@/lib/api';

/**
 * How many minutes a droplet usually takes to make, and so how many drops the meter has. docs/speed.md measured
 * about half an hour from request to videos, a few minutes of it before the studio starts.
 */
export const METER_MINUTES = 25;

// The drop from the Droplets heading (Highlights.tsx), with room in the box for a hollow drop's outline.
const DROP = 'M32 12.5C32 12.5 20.3 28 20.3 38a11.7 11.7 0 0 0 23.4 0C43.7 28 32 12.5 32 12.5Z';

type DropState = 'full' | 'now' | 'empty';

/** One drop: filled for a minute gone, blinking for the minute under way, hollow for one to come. */
export function Drop({ state, className = '' }: { state: DropState; className?: string }) {
  return (
    <svg viewBox="18 10 28 42" aria-hidden="true" className={`${state === 'now' ? 'animate-drop-blink' : ''} ${className}`}
      data-state={state}>
      <path d={DROP} fill={state === 'empty' ? 'none' : 'var(--accent-brand)'}
        stroke={state === 'empty' ? 'var(--text-muted)' : 'var(--accent-brand)'} strokeWidth="2.5"
        strokeOpacity={state === 'empty' ? 0.5 : 1} />
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

/**
 * How far a droplet being made has got, a drop for each minute of the usual time: one fills each minute, and the
 * one for the minute under way blinks. Past the last drop they all stay filled and the last one blinks on. Before
 * the studio starts, every drop is hollow and none blinks. Under it, what to expect: about how long it takes, and
 * that the reader can leave and come back.
 */
export function DropletMeter({ progress }: { progress: Progress }) {
  const minutes = useMinutes(progress);
  const started = hasStarted(progress);
  const now = started ? Math.min(minutes, METER_MINUTES - 1) : -1; // the drop that blinks
  const state = (i: number): DropState => (i === now ? 'now' : started && i < minutes ? 'full' : 'empty');
  const over = started && minutes >= METER_MINUTES;

  return (
    <div className="mt-2" data-testid="droplet-meter" data-minutes={started ? minutes : undefined}>
      {/* In fives, so the minutes can be counted at a glance. The text under it says the same for a screen reader. */}
      <div className="grid grid-cols-5 gap-x-1.5 max-w-[20rem]" aria-hidden="true">
        {Array.from({ length: METER_MINUTES / 5 }, (_, g) => (
          <div key={g} className="grid grid-cols-5 gap-x-[2px]">
            {Array.from({ length: 5 }, (_, j) => <Drop key={j} state={state(g * 5 + j)} className="block w-full h-auto" />)}
          </div>
        ))}
      </div>
      <p className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }} data-testid="droplet-meter-text">
        {!started
          ? `A droplet takes about ${METER_MINUTES} minutes to make once the teacher starts on it, a drop a minute.`
          : over
            ? `Minute ${minutes + 1}: this one is taking longer than most. You can leave this page; the droplet waits here for you.`
            : `Minute ${minutes + 1} of about ${METER_MINUTES}. You can leave this page; the droplet waits here for you.`}
      </p>
    </div>
  );
}
