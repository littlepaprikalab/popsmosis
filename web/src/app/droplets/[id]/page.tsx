'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api, isUnreachable, ApiError, Droplet } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import Markdown from '@/components/Markdown';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { useToast } from '@/components/Toast';
import { DropletPlayers, Lyrics } from '@/components/DropletPlayers';
import { LikeButton, formatCount } from '@/components/LikeButton';
import { DropletComments } from '@/components/DropletComments';
import { ChatBubbleLeftIcon } from '@heroicons/react/24/outline';
import { CONTENT_LICENSE } from '@/lib/legal';

/** One droplet on the public tab: both videos, the lesson and the song's words. */
export default function DropletPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const { showToast } = useToast();
  const [droplet, setDroplet] = useState<Droplet | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'missing' | 'unreachable'>('loading');
  const [confirmHide, setConfirmHide] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [commentCount, setCommentCount] = useState(0);

  const load = useCallback(async () => {
    setState('loading');
    try {
      const loaded = await api.getDroplet(id);
      setDroplet(loaded);
      setCommentCount(loaded.commentCount ?? 0);
      setState('ready');
    } catch (err) {
      if (isUnreachable(err)) setState('unreachable');
      else if (err instanceof ApiError && err.status === 404) setState('missing');
      else {
        showToast(err instanceof Error ? err.message : 'Failed to load the droplet', 'error');
        setState('missing');
      }
    }
  }, [id, showToast]);

  useEffect(() => { load(); }, [load]);

  // A card's comment count links to #comments, which only exists once the droplet has loaded.
  // The lesson's markdown renders a moment later still (it is loaded lazily) and pushes the
  // comments down, so follow them while the page settles, until the reader scrolls themselves.
  useEffect(() => {
    if (state !== 'ready' || window.location.hash !== '#comments') return;
    const jump = () => document.getElementById('comments')?.scrollIntoView({ block: 'start' });
    jump();
    const resized = new ResizeObserver(jump);
    resized.observe(document.body);
    const events = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;
    const stop = () => {
      resized.disconnect();
      for (const e of events) window.removeEventListener(e, stop);
    };
    for (const e of events) window.addEventListener(e, stop, { passive: true });
    const timer = setTimeout(stop, 3000);
    return () => { clearTimeout(timer); stop(); };
  }, [state]);

  const setOnTab = async (onTab: boolean) => {
    try {
      await api.setDropletHidden(id, !onTab);
      setHidden(!onTab);
      showToast(onTab ? 'Back on the Droplets tab.' : 'Taken off the Droplets tab.', 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to change it', 'error');
    }
  };

  if (state === 'loading') return <LoadingMessage />;
  if (state === 'unreachable') return <ServerUnavailable onRetry={load} />;
  if (state === 'missing' || !droplet) {
    return (
      <PageContainer className="py-6">
        <div className="text-center py-20" style={{ color: 'var(--text-muted)' }}>
          <p>This droplet is not on the tab. It may have been taken down.</p>
          <p className="text-xs mt-2"><Link href="/droplets" className="underline hover-text">See all droplets</Link></p>
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="py-6">
      <ConfirmDialog
        open={confirmHide}
        title="Take off the Droplets tab"
        message="Hide this droplet from the public tab? The learner still sees it on their Learn page, and you can put it back."
        confirmLabel="Hide"
        variant="danger"
        onConfirm={() => { setConfirmHide(false); setOnTab(false); }}
        onCancel={() => setConfirmHide(false)}
      />

      <Link href="/droplets" className="text-[10px] uppercase tracking-wider hover-text" style={{ color: 'var(--text-muted)' }}>
        ← All droplets
      </Link>

      <div className="mt-3 mb-5 flex items-start justify-between gap-3">
        <div className="min-w-0">
          {droplet.course && droplet.topic && (
            <p className="text-xs uppercase tracking-wider font-bold mb-1" style={{ color: 'var(--accent-purple)' }}>{droplet.course}</p>
          )}
          <h1 className="text-2xl font-bold tracking-wide" style={{ color: 'var(--text-primary)' }}>
            {droplet.course && droplet.topic ? droplet.topic : droplet.title}
          </h1>
        </div>
        {user?.role === 'ADMIN' && (
          <button onClick={() => (hidden ? setOnTab(true) : setConfirmHide(true))}
            className="shrink-0 px-3 py-1 rounded text-[10px] font-bold uppercase tracking-wider"
            style={{ color: hidden ? 'var(--accent-green)' : 'var(--accent-red)', border: `1px solid ${hidden ? 'var(--accent-green)' : 'var(--accent-red)'}` }}>
            {hidden ? 'Put back on the tab' : 'Hide from tab'}
          </button>
        )}
      </div>

      {hidden && (
        <p className="text-xs mb-4" style={{ color: 'var(--accent-orange)' }}>Hidden: no longer listed on the Droplets tab.</p>
      )}

      <DropletPlayers dropletId={droplet.id} video={droplet.video} tags={droplet.tags} title={droplet.title} />

      {/* An API older than likes and comments sends neither count: leave both out rather than show zeros. */}
      {droplet.likeCount !== undefined && (
        <div className="mt-3 flex items-center gap-3" data-testid="droplet-engagement">
          <LikeButton dropletId={droplet.id} liked={!!droplet.liked} likeCount={droplet.likeCount} size="md" />
          <a href="#comments" className="inline-flex items-center gap-1.5 text-sm hover-text" style={{ color: 'var(--text-secondary)' }}
            onClick={(e) => { e.preventDefault(); document.getElementById('comments')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>
            <ChatBubbleLeftIcon className="w-5 h-5" aria-hidden />
            {formatCount(commentCount)} {commentCount === 1 ? 'comment' : 'comments'}
          </a>
        </div>
      )}

      <div className="grid gap-4 mt-6 md:grid-cols-[2fr_1fr]">
        <section className="rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <h2 className="text-xs uppercase tracking-wider font-bold mb-3" style={{ color: 'var(--accent-blue)' }}>Lesson</h2>
          <div className="md text-sm" style={{ color: 'var(--text-primary)' }}>
            <Markdown>{droplet.content}</Markdown>
          </div>
        </section>
        {droplet.lyrics.length > 0 && (
          <section className="rounded-lg p-4 self-start" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
            <h2 className="text-xs uppercase tracking-wider font-bold mb-3" style={{ color: 'var(--accent-red)' }}>Lyrics</h2>
            <Lyrics lyrics={droplet.lyrics} />
          </section>
        )}
      </div>

      {droplet.commentCount !== undefined && (
        <DropletComments dropletId={droplet.id} count={commentCount} setCount={setCommentCount} initial={droplet.comments} />
      )}

      <p className="text-[10px] mt-6" style={{ color: 'var(--text-muted)', lineHeight: 1.6 }} data-testid="ai-notice">
        Made by AI: the lesson, the diagrams, the voice and the song. Every line passed an AI fact-check before it was
        posted, but AI can still be wrong, so check anything important.{' '}
        <Link href="/learn" className="underline hover-text">Ask for your own topic</Link>.
      </p>
      <p className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
        Free to share for non-commercial use, with credit, under{' '}
        <a href={CONTENT_LICENSE.url} rel="license noopener noreferrer" target="_blank" className="underline hover-text">{CONTENT_LICENSE.name}</a>
        {' '}(<Link href="/terms#license" className="underline hover-text">how</Link>).
      </p>
    </PageContainer>
  );
}
