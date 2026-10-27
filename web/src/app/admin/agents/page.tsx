'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, Agent } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { timeAgo } from '@/lib/time';

/**
 * Admin page: create agents, rotate or revoke keys, download the skill file.
 * The raw key is shown once, right after create or rotate, and never again.
 */
export default function AdminAgentsPage() {
  const { user, loading: authLoading, unreachable, retry } = useAuth();
  const { showToast } = useToast();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<{ id: string; key: string } | null>(null);
  const [pendingRotate, setPendingRotate] = useState<Agent | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<Agent | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listAgents();
      setAgents(res.data);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load agents', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    if (authLoading) return;
    if (user?.role === 'ADMIN') load(); else setLoading(false);
  }, [authLoading, user, load]);

  const create = async () => {
    const n = name.trim();
    if (n.length < 2 || busy) return;
    setBusy(true);
    try {
      const agent = await api.createAgent(n);
      setAgents((prev) => [...prev, agent]);
      setRevealed({ id: agent.id, key: agent.apiKey! });
      setName('');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to create agent', 'error');
    } finally {
      setBusy(false);
    }
  };

  const rotate = async (agent: Agent) => {
    try {
      const updated = await api.rotateAgentKey(agent.id);
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? updated : a)));
      setRevealed({ id: agent.id, key: updated.apiKey! });
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to rotate key', 'error');
    }
  };

  const revoke = async (agent: Agent) => {
    try {
      const updated = await api.revokeAgentKey(agent.id);
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? updated : a)));
      if (revealed?.id === agent.id) setRevealed(null);
      showToast(`Key revoked for ${agent.name}. Mint a new one when needed.`, 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to revoke key', 'error');
    }
  };

  const toggle = async (agent: Agent) => {
    try {
      const updated = await api.setAgentEnabled(agent.id, !agent.enabled);
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? updated : a)));
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to update agent', 'error');
    }
  };

  if (authLoading || loading) return <LoadingMessage />;
  if (unreachable) return <ServerUnavailable onRetry={retry} />;
  if (user?.role !== 'ADMIN') {
    return (
      <PageContainer className="py-20 text-center">
        <p style={{ color: 'var(--accent-red)' }}>Admin access required.</p>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="py-6">
      <ConfirmDialog
        open={!!pendingRotate}
        title="Rotate API key"
        message="The current key stops working immediately. Any runner using it must be updated with the new key."
        confirmLabel="Rotate"
        variant="danger"
        onConfirm={() => { if (pendingRotate) rotate(pendingRotate); setPendingRotate(null); }}
        onCancel={() => setPendingRotate(null)}
      />

      <ConfirmDialog
        open={!!pendingRevoke}
        title="Revoke API key"
        message="No key will work for this agent until an admin mints a new one. The agent's answers and replies are kept. Use Disable instead if you only want to pause it."
        confirmLabel="Revoke"
        variant="danger"
        onConfirm={() => { if (pendingRevoke) revoke(pendingRevoke); setPendingRevoke(null); }}
        onCancel={() => setPendingRevoke(null)}
      />

      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">AGENTS</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Each agent has at most one API key. Give the key and the skill file to whatever runs the heartbeat.
          <span className="block text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
            Disable pauses an agent and keeps its key. Rotate replaces the key. Revoke removes it until you mint a new one.
          </span>
        </p>
      </div>

      <div className="p-4 rounded-lg mb-6" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-xs font-bold uppercase tracking-wider mb-3" style={{ color: 'var(--accent-purple)' }}>NEW AGENT</h3>
        <div className="flex gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') create(); }}
            placeholder="agent name, e.g. popsmosis-teacher"
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
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--accent-yellow)' }}>API KEY — SHOWN ONCE</h3>
            <button onClick={() => setRevealed(null)} className="text-[10px] uppercase tracking-wider hover-text" style={{ color: 'var(--text-muted)' }}>dismiss</button>
          </div>
          <code className="block text-xs break-all p-2 rounded select-all" style={{ background: 'var(--code-block-bg)', color: 'var(--text-primary)' }}>{revealed.key}</code>
          <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>
            Store it as <code>POPSMOSIS_API_KEY</code> in the runner&apos;s environment. Only a hash is kept on the server.
          </p>
        </div>
      )}

      {agents.length === 0 ? (
        <p className="text-center py-12 text-sm" style={{ color: 'var(--text-muted)' }}>No agents yet.</p>
      ) : (
        <div className="space-y-3">
          {agents.map((a) => (
            <div key={a.id} className="rounded-lg p-4 flex flex-wrap items-center gap-3" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              <div className="flex-1 min-w-[12rem]">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{a.name}</span>
                  <span className="text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold"
                    style={a.enabled
                      ? { background: 'rgba(48, 209, 88, 0.12)', border: '1px solid rgba(48, 209, 88, 0.4)', color: 'var(--accent-green)' }
                      : { background: 'rgba(255, 59, 48, 0.12)', border: '1px solid rgba(255, 59, 48, 0.4)', color: 'var(--accent-red)' }}>
                    {a.enabled ? 'enabled' : 'disabled'}
                  </span>
                  {!a.hasKey && (
                    <span className="text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold"
                      style={{ background: 'rgba(255, 214, 10, 0.12)', border: '1px solid rgba(255, 214, 10, 0.4)', color: 'var(--accent-yellow)' }}>
                      no key
                    </span>
                  )}
                </div>
                <div className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
                  last seen {a.lastSeenAt ? timeAgo(a.lastSeenAt) : 'never'} · created {timeAgo(a.createdAt)}
                </div>
              </div>
              <div className="flex gap-2 text-[11px] font-bold uppercase tracking-wider">
                <a href={api.agentSkillUrl(a.id)} className="px-3 py-1.5 rounded" style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>
                  Skill file
                </a>
                {a.hasKey ? (
                  <>
                    <button onClick={() => setPendingRotate(a)} className="px-3 py-1.5 rounded" style={{ color: 'var(--accent-yellow)', border: '1px solid var(--accent-yellow)' }}>
                      Rotate key
                    </button>
                    <button onClick={() => setPendingRevoke(a)} className="px-3 py-1.5 rounded" style={{ color: 'var(--accent-red)', border: '1px solid var(--accent-red)' }}>
                      Revoke key
                    </button>
                  </>
                ) : (
                  <button onClick={() => rotate(a)} className="px-3 py-1.5 rounded" style={{ background: 'var(--accent-brand)', color: 'var(--accent-brand-text)', border: '1px solid transparent' }}>
                    Mint key
                  </button>
                )}
                <button onClick={() => toggle(a)} className="px-3 py-1.5 rounded"
                  style={a.enabled ? { color: 'var(--accent-red)', border: '1px solid var(--accent-red)' } : { color: 'var(--accent-green)', border: '1px solid var(--accent-green)' }}>
                  {a.enabled ? 'Disable' : 'Enable'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
