'use client';

import { ReactNode } from 'react';
import { useAuth } from '@/lib/auth';
import { usePolling } from '@/lib/usePolling';
import { UserAvatar } from '@/components/UserAvatar';

// The API decides once per request whether a user is approved. This is the one
// place the web app decides: a signed-in but unapproved user sees the waiting
// screen on every route instead of the page (which would only 403 anyway).
const PENDING_POLL_MS = 15_000;

export function AccessGate({ children }: { children: ReactNode }) {
  const { user, refresh, logout } = useAuth();
  const pending = user?.status === 'PENDING';

  // While pending, ask /auth/me now and then so approval flips the page without a reload.
  usePolling(refresh, PENDING_POLL_MS, pending);

  if (!user || user.status === 'APPROVED') return <>{children}</>;

  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]" data-testid="access-gate">
      <div className="text-center p-8 rounded-lg max-w-md animate-fade-in"
        style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <div className="flex justify-center mb-4">
          <UserAvatar name={user.name} avatarUrl={user.avatarUrl} size={48} />
        </div>
        {pending ? (
          <>
            <h1 className="text-xl font-bold mb-2 tracking-wider" style={{ color: 'var(--accent-orange)' }}>WAITING FOR APPROVAL</h1>
            <p className="text-sm mb-2" style={{ color: 'var(--text-secondary)' }}>
              You are signed in as <span style={{ color: 'var(--text-primary)' }}>{user.email}</span>, but an admin has to let you in first.
            </p>
            <p className="text-xs mb-6 flex items-center justify-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
              <span className="inline-block w-1.5 h-1.5 rounded-full animate-liveness-pulse" style={{ background: 'var(--accent-orange)' }} />
              This page updates itself once you are approved.
            </p>
          </>
        ) : (
          <>
            <h1 className="text-xl font-bold mb-2 tracking-wider" style={{ color: 'var(--accent-red)' }}>ACCESS DENIED</h1>
            <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
              An admin has denied <span style={{ color: 'var(--text-primary)' }}>{user.email}</span> access to this site.
              If that is a mistake, ask them to change it.
            </p>
          </>
        )}
        <button onClick={() => logout()} className="px-5 py-2 rounded text-xs font-bold uppercase tracking-wider"
          style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
          Sign out
        </button>
      </div>
    </div>
  );
}
