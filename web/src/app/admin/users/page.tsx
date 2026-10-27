'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, User } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { UserAvatar } from '@/components/UserAvatar';
import { timeAgo } from '@/lib/time';

type Action = { user: User; role: 'ADMIN' | 'USER' };

const chip = (bg: string, border: string, color: string) => ({ background: bg, border: `1px solid ${border}`, color });
const outline = (color: string) => ({ color: `var(--accent-${color})`, border: `1px solid var(--accent-${color})` });

/**
 * Admin page. Anyone with a Google account can sign in and use the site; this
 * is where admins decide who else is an admin.
 */
export default function AdminUsersPage() {
  const { user: me, loading: authLoading, unreachable, retry } = useAuth();
  const { showToast } = useToast();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [action, setAction] = useState<Action | null>(null);

  const load = useCallback(async (query: string) => {
    setLoading(true);
    try {
      setUsers((await api.listUsers(query || undefined)).data);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load users', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    if (authLoading) return;
    if (me?.role === 'ADMIN') load(''); else setLoading(false);
  }, [authLoading, me, load]);

  const apply = async (a: Action) => {
    const who = a.user.name || a.user.email;
    try {
      const updated = await api.setUserRole(a.user.id, a.role);
      setUsers((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
      showToast(`${who} is now ${a.role === 'ADMIN' ? 'an admin' : 'a regular user'}`, 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to update user', 'error');
    }
  };

  if (authLoading || (loading && me?.role === 'ADMIN')) return <LoadingMessage />;
  if (unreachable) return <ServerUnavailable onRetry={retry} />;
  if (me?.role !== 'ADMIN') {
    return (
      <PageContainer className="py-20 text-center">
        <p style={{ color: 'var(--accent-red)' }}>Admin access required.</p>
      </PageContainer>
    );
  }

  const admins = users.filter((u) => u.role === 'ADMIN').length;
  const dialog = action && describe(action);

  return (
    <PageContainer className="py-6">
      <ConfirmDialog
        open={!!action}
        title={dialog?.title ?? ''}
        message={dialog?.message ?? ''}
        confirmLabel={dialog?.title ?? ''}
        variant={dialog?.danger ? 'danger' : 'default'}
        onConfirm={() => { if (action) apply(action); setAction(null); }}
        onCancel={() => setAction(null)}
      />

      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">USERS</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Anyone with a Google account can sign in. Admins manage agents and other admins.
        </p>
      </div>

      <div className="flex gap-3 mb-4">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') load(q); }}
          placeholder="search by name or email"
          className="flex-1 text-sm outline-none px-3 py-2 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-primary)', background: 'var(--bg-tertiary)' }}
        />
        <button onClick={() => load(q)} className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider" style={outline('blue')}>
          Search
        </button>
      </div>

      {users.length === 0 ? (
        <p className="text-center py-12 text-sm" style={{ color: 'var(--text-muted)' }}>No users match.</p>
      ) : (
        <div className="space-y-2">
          {users.map((u) => {
            const isMe = u.id === me.id;
            const isAdmin = u.role === 'ADMIN';
            const lastAdmin = isAdmin && admins <= 1;
            return (
              <div key={u.id} className="rounded-lg p-3 flex items-center gap-3" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }} data-testid="user-row">
                <UserAvatar name={u.name} avatarUrl={u.avatarUrl} size={28} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold truncate" style={{ color: 'var(--text-primary)' }}>{u.name || u.email}</span>
                    {isAdmin && (
                      <span className="text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold" style={chip('rgba(175, 82, 222, 0.12)', 'rgba(175, 82, 222, 0.4)', 'var(--accent-purple)')}>admin</span>
                    )}
                    {isMe && <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>(you)</span>}
                  </div>
                  <div className="text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>
                    {u.email} · joined {timeAgo(u.createdAt)}
                  </div>
                </div>
                <button
                  onClick={() => setAction({ user: u, role: isAdmin ? 'USER' : 'ADMIN' })}
                  disabled={isMe || lastAdmin}
                  title={isMe ? 'You cannot change your own role' : lastAdmin ? 'Cannot remove the last admin' : undefined}
                  className="px-3 py-1.5 rounded text-[11px] font-bold uppercase tracking-wider disabled:opacity-40"
                  style={outline(isAdmin ? 'red' : 'purple')}
                >
                  {isAdmin ? 'Remove admin' : 'Make admin'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </PageContainer>
  );
}

function describe(a: Action): { title: string; message: string; danger: boolean } {
  const who = a.user.name || a.user.email;
  return a.role === 'ADMIN'
    ? { title: 'Make admin', message: `${who} will be able to manage agents, API keys, and other users.`, danger: false }
    : { title: 'Remove admin', message: `${who} will lose access to the AGENTS and USERS pages.`, danger: true };
}
