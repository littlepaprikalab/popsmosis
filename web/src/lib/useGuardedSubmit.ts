import { useState, useRef, useCallback } from 'react';

/**
 * Hook that prevents duplicate submissions from rapid clicks/keypresses.
 * Uses a ref for synchronous guard (immune to React state batching)
 * and exposes a boolean state for UI feedback (disabling buttons).
 */
export function useGuardedSubmit() {
  const [submitting, setSubmitting] = useState(false);
  const guardRef = useRef(false);

  const guard = useCallback(async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    if (guardRef.current) return undefined;
    guardRef.current = true;
    setSubmitting(true);
    try {
      return await fn();
    } finally {
      guardRef.current = false;
      setSubmitting(false);
    }
  }, []);

  return { submitting, guard } as const;
}
