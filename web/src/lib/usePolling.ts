'use client';

import { useEffect, useRef } from 'react';

/**
 * Shared polling hook — replaces duplicated setInterval + visibilitychange patterns.
 *
 * - Calls `fn` every `intervalMs` milliseconds
 * - Pauses when the tab is hidden, resumes + immediate poll when visible
 * - Skips a tick while the previous one is still in flight, so a slow or dead
 *   API cannot leave a tab with more requests open than it can answer
 * - Cleans up on unmount
 * - Skips polling when `enabled` is false
 */
export function usePolling(fn: () => void | Promise<unknown>, intervalMs: number, enabled = true) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return;

    let intervalId: ReturnType<typeof setInterval> | null = null;
    let inFlight = false;

    const poll = () => {
      if (inFlight) return;
      const result = fnRef.current();
      if (!(result instanceof Promise)) return;
      inFlight = true;
      result.finally(() => { inFlight = false; });
    };

    const start = () => { if (!intervalId) intervalId = setInterval(poll, intervalMs); };
    const stop = () => { if (intervalId) { clearInterval(intervalId); intervalId = null; } };

    const onVisibility = () => {
      if (document.visibilityState === 'visible') { poll(); start(); } else { stop(); }
    };

    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility); };
  }, [intervalMs, enabled]);
}
