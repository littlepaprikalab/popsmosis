'use client';

import { Dispatch, SetStateAction, useCallback, useEffect, useId, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { api, ApiError, DropletComment, DropletCommentPage } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useGuardedSubmit } from '@/lib/useGuardedSubmit';
import { timeAgo } from '@/lib/time';
import { useToast } from './Toast';
import { ConfirmDialog } from './ConfirmDialog';
import { UserAvatar } from './UserAvatar';
import { formatCount } from './LikeButton';

export const COMMENT_MAX_LENGTH = 1000; // api/src/lib/engagement.ts

// One key per comment attempt, kept until it lands: a resend after a lost answer then
// returns the comment the first send made instead of posting it twice.
function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

interface Props {
  dropletId: string;
  count: number;
  setCount: Dispatch<SetStateAction<number>>;
  initial?: DropletCommentPage; // the newest comments, read with the droplet: no second request to show them
  // On a card on the list: only the newest `preview`, folded, until the card opens them all in place.
  card?: { preview: number; open: boolean; setOpen: (open: boolean) => void };
}

/**
 * The public comments under a droplet, newest first. Anyone can read them; a signed-in
 * person can add one and delete their own, and an admin can delete any.
 */
export function DropletComments({ dropletId, count, setCount, initial, card }: Props) {
  const { user } = useAuth();
  const { showToast } = useToast();
  const pathname = usePathname();
  const ids = useId();
  const [comments, setComments] = useState<DropletComment[]>(initial?.data ?? []);
  const [cursor, setCursor] = useState<string | null>(initial?.nextCursor ?? null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>(initial ? 'ready' : 'loading');
  const hadInitial = useRef(!!initial);
  const [loadingMore, setLoadingMore] = useState(false);
  const [text, setText] = useState('');
  const [pendingDelete, setPendingDelete] = useState<DropletComment | null>(null);
  const { submitting, guard } = useGuardedSubmit();
  const attempt = useRef<{ key: string; content: string } | null>(null);

  // An unsent comment outlives the page: a reload, or a session that ended in another tab and
  // a trip to sign in and back, finds it waiting. Per tab, and gone once it posts.
  const draftKey = `popsmosis:comment-draft:${dropletId}`;
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(draftKey);
      if (saved) setText((current) => current || saved);
    } catch { /* storage blocked: start empty */ }
  }, [draftKey]);
  const changeText = (value: string) => {
    setText(value);
    try {
      if (value.trim()) sessionStorage.setItem(draftKey, value);
      else sessionStorage.removeItem(draftKey);
    } catch { /* storage blocked: the draft lives only on the page */ }
  };

  const load = useCallback(async () => {
    setState('loading');
    try {
      const res = await api.listDropletComments(dropletId);
      setComments(res.data);
      setCursor(res.nextCursor);
      setCount(res.commentCount);
      setState('ready');
    } catch {
      setState('error');
    }
  }, [dropletId, setCount]);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await api.listDropletComments(dropletId, cursor);
      setComments((prev) => [...prev, ...res.data.filter((c) => !prev.some((p) => p.id === c.id))]);
      setCursor(res.nextCursor);
      setCount(res.commentCount);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load more comments', 'error');
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, loadingMore, dropletId, setCount, showToast]);

  // Fetched here only when the droplet came without them; "Try again" calls load itself.
  // A card fetches nothing until it opens, and then the comments after the ones it shows.
  const whole = !card || card.open;
  const opened = useRef(false);
  useEffect(() => {
    if (!whole || opened.current) return;
    opened.current = true;
    if (!hadInitial.current) load();
    else if (card) loadMore();
  }, [whole, card, load, loadMore]);

  const submit = () => guard(async () => {
    const content = text.trim();
    if (!content) return;
    // The same words as an attempt that may have landed reuse its key; new words get a new one.
    if (!attempt.current || attempt.current.content !== content) attempt.current = { key: newIdempotencyKey(), content };
    try {
      const posted = await api.addDropletComment(dropletId, content, attempt.current.key);
      attempt.current = null;
      if (!comments.some((c) => c.id === posted.id)) {
        setComments((prev) => [posted, ...prev.filter((c) => c.id !== posted.id)]);
        setCount((n) => n + 1);
      }
      changeText('');
    } catch (err) {
      const gone = err instanceof ApiError && err.status === 404;
      showToast(gone ? 'This droplet is no longer on the Droplets tab.' : err instanceof Error ? err.message : 'Failed to post your comment', 'error');
    }
  });

  const remove = async (comment: DropletComment) => {
    try {
      await api.deleteDropletComment(dropletId, comment.id);
    } catch (err) {
      // Already gone is what was asked for; anything else leaves it where it is.
      if (!(err instanceof ApiError && err.status === 404)) {
        showToast(err instanceof Error ? err.message : 'Failed to delete the comment', 'error');
        return;
      }
    }
    setComments((prev) => prev.filter((c) => c.id !== comment.id));
    setCount((n) => Math.max(0, n - 1));
  };

  const canDelete = (c: DropletComment) => !!user && (c.mine || user.role === 'ADMIN');

  // "View all" and "Hide comments" each go once pressed: the keyboard follows to what took their place.
  const region = useRef<HTMLElement & HTMLDivElement>(null);
  const toggled = useRef(false);
  const toggle = (open: boolean) => { toggled.current = true; card?.setOpen(open); };
  useEffect(() => {
    if (!toggled.current) return;
    toggled.current = false;
    region.current?.focus({ preventScroll: true });
  }, [card?.open]);

  // Folded on a card: a line or two of each of the newest, and the way to the rest.
  if (card && !card.open) {
    const newest = comments.slice(0, card.preview);
    if (newest.length === 0) return null;
    return (
      <div ref={region} tabIndex={-1} className="px-4 pb-3 outline-none" data-testid="card-comment-preview">
        <ul className="space-y-1" aria-label="Newest comments">
          {newest.map((c) => (
            <li key={c.id} className="text-xs line-clamp-2 break-words" style={{ color: 'var(--text-primary)', lineHeight: 1.5 }} data-testid="comment">
              <span className="font-bold" style={{ color: 'var(--text-secondary)' }}>{c.author.name}</span> {c.content}
            </li>
          ))}
        </ul>
        {count > newest.length && (
          <button type="button" onClick={() => toggle(true)}
            className="text-xs mt-1.5 hover-text" style={{ color: 'var(--text-muted)' }}>
            View all {formatCount(count)} comments
          </button>
        )}
      </div>
    );
  }

  const headingId = `${ids}-heading`;
  const boxId = `${ids}-box`;

  return (
    <section id={card ? undefined : 'comments'} ref={region} tabIndex={card ? -1 : undefined}
      className={card ? 'px-4 pb-4 outline-none' : 'rounded-lg p-4 mt-6 scroll-mt-20'}
      style={card ? undefined : { background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
      data-testid={card ? 'card-comments-panel' : 'droplet-comments'}
      aria-labelledby={card ? undefined : headingId}>
      <ConfirmDialog
        open={!!pendingDelete}
        title="Delete comment"
        message={pendingDelete && !pendingDelete.mine ? 'Delete this comment? It is someone else’s; it goes for everyone.' : 'Delete your comment? It goes for everyone.'}
        confirmLabel="Delete"
        variant="danger"
        onConfirm={() => { if (pendingDelete) remove(pendingDelete); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />

      {/* On a card its count is already beside the like button. */}
      {!card && (
        <h2 id={headingId} className="text-xs uppercase tracking-wider font-bold mb-3" style={{ color: 'var(--accent-green)' }}>
          Comments <span style={{ color: 'var(--text-muted)' }} data-testid="comment-count">{formatCount(count)}</span>
        </h2>
      )}

      {user ? (
        <div className={card ? 'mb-3' : 'mb-4'}>
          <label htmlFor={boxId} className="sr-only">Add a comment</label>
          <div className="flex gap-2 items-start">
            <textarea
              id={boxId}
              rows={2}
              value={text}
              onChange={(e) => changeText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } }}
              placeholder="Add a comment… (Ctrl+Enter to post)"
              maxLength={COMMENT_MAX_LENGTH}
              className="flex-1 min-w-0 text-sm outline-none px-3 py-1.5 rounded resize-y"
              style={{ border: '1px solid var(--border)', color: 'var(--text-primary)', background: 'var(--bg-tertiary)', minHeight: 40, maxHeight: 240 }}
            />
            <button
              type="button"
              onClick={submit}
              disabled={!text.trim() || submitting}
              className="px-3 py-1.5 rounded text-[11px] font-bold uppercase tracking-wider transition-colors"
              style={{
                background: text.trim() ? 'var(--accent-brand)' : 'var(--bg-tertiary)',
                color: text.trim() ? 'var(--accent-brand-text)' : 'var(--text-muted)',
              }}
            >
              {submitting ? '…' : 'Comment'}
            </button>
          </div>
          <p className="text-[10px] mt-1 flex justify-between gap-2" style={{ color: 'var(--text-muted)' }}>
            <span>Comments are public: anyone can read them, and they show your first name and last initial.</span>
            {text.length > COMMENT_MAX_LENGTH - 200 && <span>{text.length}/{COMMENT_MAX_LENGTH}</span>}
          </p>
        </div>
      ) : (
        <p className={`text-xs ${card ? 'mb-3' : 'mb-4'}`} style={{ color: 'var(--text-muted)' }}>
          <Link href={`/auth/callback?returnTo=${encodeURIComponent(pathname)}`} className="underline hover-text" data-testid="comment-signin">Sign in</Link>{' '}
          to like this droplet and join the conversation.
        </p>
      )}

      {state === 'loading' ? (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Loading comments…</p>
      ) : state === 'error' ? (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          The comments did not load. <button type="button" onClick={load} className="underline hover-text">Try again</button>
        </p>
      ) : comments.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No comments yet{user ? '. Be the first.' : '.'}</p>
      ) : (
        // On a card a long thread scrolls inside it, so the cards after it stay in reach.
        <ul className={card ? 'space-y-3 max-h-80 overflow-y-auto overscroll-contain pr-1' : 'space-y-3'}>
          {comments.map((c) => (
            <li key={c.id} className="flex gap-2" data-testid="comment">
              <UserAvatar name={c.author.name} size={24} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-xs font-bold" style={{ color: 'var(--text-secondary)' }}>{c.author.name}</span>
                  {c.mine && <span className="badge-human text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold">you</span>}
                  <time className="text-[10px]" style={{ color: 'var(--text-muted)' }} dateTime={c.createdAt}>{timeAgo(c.createdAt)}</time>
                  {canDelete(c) && (
                    <button type="button" onClick={() => setPendingDelete(c)} className="text-[10px] uppercase tracking-wider hover-text ml-auto"
                      style={{ color: 'var(--text-muted)' }} data-testid="delete-comment">
                      delete
                    </button>
                  )}
                </div>
                <p className="text-sm whitespace-pre-wrap break-words" style={{ color: 'var(--text-primary)' }}>{c.content}</p>
              </div>
            </li>
          ))}
        </ul>
      )}

      {((cursor && state === 'ready') || card) && (
        <div className={card ? 'flex items-center justify-between gap-2 mt-3' : 'text-center mt-4'}>
          {cursor && state === 'ready' && (
            <button type="button" onClick={loadMore} disabled={loadingMore}
              className="px-3 py-1 rounded text-[10px] font-bold uppercase tracking-wider"
              style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>
              {loadingMore ? 'Loading…' : 'Older comments'}
            </button>
          )}
          {card && (
            <button type="button" onClick={() => toggle(false)}
              className="ml-auto text-xs hover-text" style={{ color: 'var(--text-muted)' }}>
              Hide comments
            </button>
          )}
        </div>
      )}
    </section>
  );
}
