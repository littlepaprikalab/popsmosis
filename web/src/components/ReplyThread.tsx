'use client';

import { useState, useCallback } from 'react';
import { api, Answer, Reply } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useToast } from './Toast';
import { ConfirmDialog } from './ConfirmDialog';
import { UserAvatar, AgentAvatar } from './UserAvatar';
import Markdown from './Markdown';
import AttachmentList from './AttachmentList';
import CommandList from './CommandList';
import { timeAgo } from '@/lib/time';

interface Props {
  answer: Answer;
  onChange: (answer: Answer) => void;
}

/**
 * The conversation under an agent answer: human replies and the agent's
 * responses, plus a box to add a reply. Shape follows sparks' ViewerComments
 * without votes.
 */
export function ReplyThread({ answer, onChange }: Props) {
  const { user } = useAuth();
  const { showToast } = useToast();
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Reply | null>(null);

  const submit = useCallback(async () => {
    const content = text.trim();
    if (!content || submitting) return;
    setSubmitting(true);
    try {
      const reply = await api.addReply(answer.id, content);
      onChange({ ...answer, replies: [...answer.replies, reply], awaitingAgent: true });
      setText('');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to post reply', 'error');
    } finally {
      setSubmitting(false);
    }
  }, [text, submitting, answer, onChange, showToast]);

  const remove = useCallback(async (reply: Reply) => {
    try {
      await api.deleteReply(reply.id);
      // The agent's response to this reply cascades away server-side too.
      const remaining = answer.replies.filter((r) => r.id !== reply.id && r.replyToId !== reply.id);
      const last = remaining[remaining.length - 1];
      onChange({ ...answer, replies: remaining, awaitingAgent: !!last && last.authorType === 'HUMAN' });
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to delete reply', 'error');
    }
  }, [answer, onChange, showToast]);

  return (
    <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
      <ConfirmDialog
        open={!!pendingDelete}
        title="Delete reply"
        message="Delete this reply? If the advisor already responded to it, that response is removed too."
        confirmLabel="Delete"
        variant="danger"
        onConfirm={() => { if (pendingDelete) remove(pendingDelete); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />

      {answer.replies.length > 0 && (
        <div className="space-y-2.5 mb-3">
          {answer.replies.map((r) => (
            <div key={r.id} className={`flex gap-2 ${r.authorType === 'AGENT' ? 'ml-4' : ''}`}>
              {r.authorType === 'AGENT'
                ? <AgentAvatar name={r.agent?.name ?? 'A'} size={22} />
                : <UserAvatar name={r.user?.name} avatarUrl={r.user?.avatarUrl} size={22} />}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-xs font-bold" style={{ color: 'var(--text-secondary)' }}>
                    {r.authorType === 'AGENT' ? (r.agent?.name ?? 'advisor') : (r.user?.name ?? 'someone')}
                  </span>
                  <span className={`${r.authorType === 'AGENT' ? 'badge-agent' : 'badge-human'} text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold`}>
                    {r.authorType === 'AGENT' ? 'agent' : 'human'}
                  </span>
                  <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{timeAgo(r.createdAt)}</span>
                  {r.canDelete && (
                    <button onClick={() => setPendingDelete(r)} className="text-[10px] uppercase tracking-wider hover-text ml-auto" style={{ color: 'var(--text-muted)' }}>
                      delete
                    </button>
                  )}
                </div>
                {r.authorType === 'AGENT' ? (
                  <>
                    <div className="md text-sm" style={{ color: 'var(--text-primary)' }}><Markdown>{r.content}</Markdown></div>
                    <CommandList commands={r.commands} />
                  </>
                ) : (
                  <>
                    <p className="text-sm whitespace-pre-wrap break-words" style={{ color: 'var(--text-primary)' }}>{r.content}</p>
                    <AttachmentList attachments={r.attachments} />
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {answer.awaitingAgent && (
        <p className="text-[10px] uppercase tracking-wider mb-2 flex items-center gap-1.5" style={{ color: 'var(--accent-yellow)' }} data-testid="awaiting-agent">
          <span className="inline-block w-1.5 h-1.5 rounded-full animate-live-pulse" style={{ background: 'var(--accent-cyan)' }} />
          Advisor will respond on its next pass
        </p>
      )}

      {user && (
        <div className="flex gap-2 items-start">
          <textarea
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } }}
            onInput={(e) => { const ta = e.currentTarget; ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`; }}
            placeholder="Reply to the advisor… (Ctrl+Enter to send)"
            maxLength={2000}
            className="flex-1 text-sm outline-none px-3 py-1.5 rounded resize-none"
            style={{ border: '1px solid var(--border)', color: 'var(--text-primary)', background: 'var(--bg-tertiary)' }}
          />
          <button
            onClick={submit}
            disabled={!text.trim() || submitting}
            className="px-3 py-1.5 rounded text-[11px] font-bold uppercase tracking-wider transition-colors"
            style={{
              background: text.trim() ? 'var(--accent-blue)' : 'var(--bg-tertiary)',
              color: text.trim() ? 'var(--btn-filled-text)' : 'var(--text-muted)',
            }}
          >
            {submitting ? '...' : 'Reply'}
          </button>
        </div>
      )}
    </div>
  );
}
