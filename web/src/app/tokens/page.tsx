'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, UserToken } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { timeAgo } from '@/lib/time';

/**
 * The caller's own personal access tokens: what lets a client with no cookie
 * jar — curl, a script, another app — act as them. The raw token is
 * shown once, right after minting, and never again.
 */
export default function TokensPage() {
  const { user, loading: authLoading, unreachable, retry } = useAuth();
  const { showToast } = useToast();
  const [tokens, setTokens] = useState<UserToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<{ id: string; token: string } | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<UserToken | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listTokens();
      setTokens(res.data);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load tokens', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    if (authLoading) return;
    if (user) load(); else setLoading(false);
  }, [authLoading, user, load]);

  const create = async () => {
    const n = name.trim();
    if (n.length < 2 || busy) return;
    setBusy(true);
    try {
      const token = await api.createToken(n);
      setTokens((prev) => [token, ...prev]);
      setRevealed({ id: token.id, token: token.token! });
      setName('');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to create token', 'error');
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (token: UserToken) => {
    try {
      const updated = await api.revokeToken(token.id);
      setTokens((prev) => prev.map((t) => (t.id === token.id ? updated : t)));
      if (revealed?.id === token.id) setRevealed(null);
      showToast(`"${token.name}" revoked. Anything using it stops working now.`, 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to revoke token', 'error');
    }
  };

  if (authLoading || loading) return <LoadingMessage />;
  if (unreachable) return <ServerUnavailable onRetry={retry} />;
  if (!user) {
    return (
      <PageContainer className="py-20 text-center">
        <p style={{ color: 'var(--accent-red)' }}>Sign in to manage your access tokens.</p>
      </PageContainer>
    );
  }

  const live = tokens.filter((t) => t.revokedAt === null);

  return (
    <PageContainer className="py-6">
      <ConfirmDialog
        open={!!pendingRevoke}
        title="Revoke access token"
        message="Anything using this token stops working within a few seconds. It cannot be un-revoked — mint a new one instead."
        confirmLabel="Revoke"
        variant="danger"
        onConfirm={() => { if (pendingRevoke) revoke(pendingRevoke); setPendingRevoke(null); }}
        onCancel={() => setPendingRevoke(null)}
      />

      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">ACCESS TOKENS</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          A token signs in as you from somewhere with no browser — curl, a script, another app.
          <span className="block text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
            It can ask, read and reply exactly as you can. It cannot manage tokens, so a leaked one cannot replace itself
            or revoke the token you would kill it with.
          </span>
        </p>
      </div>

      <div className="p-4 rounded-lg mb-6" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-xs font-bold uppercase tracking-wider mb-3" style={{ color: 'var(--accent-purple)' }}>NEW TOKEN</h3>
        <div className="flex gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') create(); }}
            placeholder="what it is for, e.g. my script"
            maxLength={60}
            className="flex-1 text-sm outline-none px-3 py-2 rounded"
            style={{ border: '1px solid var(--border)', color: 'var(--text-primary)', background: 'var(--bg-tertiary)' }}
          />
          <button onClick={create} disabled={name.trim().length < 2 || busy}
            className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider"
            style={{ background: name.trim().length >= 2 ? 'var(--accent-purple)' : 'var(--bg-tertiary)', color: name.trim().length >= 2 ? 'var(--btn-filled-text)' : 'var(--text-muted)' }}>
            Create
          </button>
        </div>
      </div>

      {revealed && (
        <div className="p-4 rounded-lg mb-6 animate-fade-in" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--accent-yellow)', boxShadow: 'var(--question-box-glow)' }}>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-yellow)' }}>ACCESS TOKEN — SHOWN ONCE</h3>
            <button onClick={() => setRevealed(null)} className="text-[10px] uppercase tracking-wider hover-text" style={{ color: 'var(--text-muted)' }}>dismiss</button>
          </div>
          <code className="block text-xs break-all p-2 rounded select-all" style={{ background: 'var(--code-block-bg)', color: 'var(--text-primary)' }}>{revealed.token}</code>
          <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>
            Store it as <code>POPSMOSIS_API_TOKEN</code> in the environment of whatever uses it. Only a hash is kept on the server.
          </p>
        </div>
      )}

      {tokens.length === 0 ? (
        <p className="text-center py-12 text-sm" style={{ color: 'var(--text-muted)' }}>No tokens yet.</p>
      ) : (
        <div className="space-y-3">
          {tokens.map((t) => (
            <div key={t.id} data-testid="token-row" className="rounded-lg p-4 flex flex-wrap items-center gap-3"
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', opacity: t.revokedAt ? 0.6 : 1 }}>
              <div className="flex-1 min-w-[12rem]">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{t.name}</span>
                  {t.revokedAt && (
                    <span className="text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold"
                      style={{ background: 'rgba(255, 59, 48, 0.12)', border: '1px solid rgba(255, 59, 48, 0.4)', color: 'var(--accent-red)' }}>
                      revoked
                    </span>
                  )}
                </div>
                <div className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
                  {t.revokedAt
                    ? `revoked ${timeAgo(t.revokedAt)} · created ${timeAgo(t.createdAt)}`
                    : `last used ${t.lastUsedAt ? timeAgo(t.lastUsedAt) : 'never'} · created ${timeAgo(t.createdAt)}`}
                </div>
              </div>
              {!t.revokedAt && (
                <button onClick={() => setPendingRevoke(t)} className="px-3 py-1.5 rounded text-[11px] font-bold uppercase tracking-wider"
                  style={{ color: 'var(--accent-red)', border: '1px solid var(--accent-red)' }}>
                  Revoke
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <p className="text-[10px] mt-6" style={{ color: 'var(--text-muted)' }}>
        {live.length} active. Last used is written at most once a minute, so a token in constant use may read a little stale.
      </p>
    </PageContainer>
  );
}
