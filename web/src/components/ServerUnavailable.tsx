'use client';

import { useState } from 'react';

// Shown instead of the sign-in card when /auth/me never got an answer. The API
// is a free Render instance: it spins down when idle and is unroutable for a
// moment while a deploy swaps it, and either way Render answers with its own
// error page. Telling a signed-in user to sign in again would be a lie.
export function ServerUnavailable({ onRetry }: { onRetry: () => Promise<void> }) {
  const [retrying, setRetrying] = useState(false);

  const retry = async () => {
    setRetrying(true);
    try { await onRetry(); } finally { setRetrying(false); }
  };

  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
      <div className="text-center p-8 rounded-lg max-w-md animate-fade-in" data-testid="server-unavailable"
        style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h1 className="text-xl font-bold mb-2 tracking-wider" style={{ color: 'var(--accent-orange)' }}>SERVER UNAVAILABLE</h1>
        <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
          POPSMOSIS cannot reach its API right now. It may be starting back up after being idle, or a deploy may be
          in progress. Your sign-in is fine — this usually clears in under a minute.
        </p>
        <button onClick={retry} disabled={retrying}
          className="px-6 py-3 rounded font-bold text-sm uppercase tracking-wider transition-colors cursor-pointer disabled:opacity-60"
          style={{ background: 'var(--accent-green)', color: 'var(--btn-filled-text)' }}>
          {retrying ? 'Retrying...' : 'Try again'}
        </button>
      </div>
    </div>
  );
}
