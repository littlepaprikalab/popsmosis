'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, isUnreachable, DropletCard } from '@/lib/api';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { timeAgo } from '@/lib/time';
import { LikeButton, formatCount } from '@/components/LikeButton';
import { ChatBubbleLeftIcon } from '@heroicons/react/24/outline';

/**
 * The public Droplets tab (docs/PLAN.md, section 10): every finished droplet, for
 * anyone, signed in or not. A card shows the title the studio gave it and a few
 * lines of the lesson; never who asked, or how they asked it.
 */
export default function DropletsPage() {
  const { showToast } = useToast();
  const [droplets, setDroplets] = useState<DropletCard[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [unreachable, setUnreachable] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listDroplets();
      setDroplets(res.data);
      setCursor(res.nextCursor);
      setUnreachable(false);
    } catch (err) {
      if (isUnreachable(err)) setUnreachable(true);
      else showToast(err instanceof Error ? err.message : 'Failed to load droplets', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const loadMore = async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await api.listDroplets(cursor);
      setDroplets((prev) => [...prev, ...res.data.filter((d) => !prev.some((p) => p.id === d.id))]);
      setCursor(res.nextCursor);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load more', 'error');
    } finally {
      setLoadingMore(false);
    }
  };

  if (unreachable) return <ServerUnavailable onRetry={load} />;

  return (
    <PageContainer maxWidth="6xl" className="py-6">
      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">DROPLETS</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Every finished droplet, free to watch: a fact-checked lesson, a song to remember it by, and a lesson video.
          No account needed to watch; sign in to like and comment. All made by AI.
        </p>
      </div>

      {loading ? (
        <LoadingMessage />
      ) : droplets.length === 0 ? (
        <div className="text-center py-20" style={{ color: 'var(--text-muted)' }}>
          <p>No droplets yet.</p>
          <p className="text-xs mt-2">
            A droplet lands here once its videos are done. <Link href="/learn" className="underline hover-text">Ask for a topic</Link> to make one.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {droplets.map((d, i) => {
            const poster = d.video.songPosterUrl || d.video.lessonPosterUrl;
            // The like button sits beside the link, not inside it: a button within a link is
            // not valid HTML, and a click on it would open the droplet too.
            return (
              <article key={d.id} data-testid="droplet-card"
                className="flex flex-col rounded-lg overflow-hidden animate-fade-in"
                style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', animationDelay: `${Math.min(i, 12) * 60}ms` }}>
                <Link href={`/droplets/${d.id}`} className="block flex-1 transition-colors hover:brightness-110">
                  <div className="aspect-video flex items-center justify-center" style={{ background: 'var(--bg-tertiary)' }}>
                    {poster ? (
                      // eslint-disable-next-line @next/next/no-img-element -- a remote Supabase still, no optimizer in front
                      <img src={poster} alt="" loading="lazy" className="w-full h-full object-cover" />
                    ) : (
                      <span className="text-xs uppercase tracking-wider font-bold" style={{ color: 'var(--accent-red)' }}>▶ Watch</span>
                    )}
                  </div>
                  <div className="p-4">
                    {d.course && d.topic && (
                      <p className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: 'var(--accent-purple)' }}>{d.course}</p>
                    )}
                    <h3 className="text-sm font-bold mb-2" style={{ color: 'var(--text-primary)' }}>{d.course && d.topic ? d.topic : d.title}</h3>
                    <p className="text-xs line-clamp-3" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>{d.excerpt}</p>
                    <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>{timeAgo(d.createdAt)}</p>
                  </div>
                </Link>
                {d.likeCount !== undefined && (
                  <div className="px-4 pb-3 flex items-center gap-3">
                    <LikeButton dropletId={d.id} liked={!!d.liked} likeCount={d.likeCount} />
                    <Link href={`/droplets/${d.id}#comments`} className="inline-flex items-center gap-1 text-xs hover-text" style={{ color: 'var(--text-secondary)' }}
                      aria-label={`${d.commentCount ?? 0} ${d.commentCount === 1 ? 'comment' : 'comments'}`} data-testid="card-comments">
                      <ChatBubbleLeftIcon className="w-4 h-4" aria-hidden /> {formatCount(d.commentCount ?? 0)}
                    </Link>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      {cursor && !loading && (
        <div className="text-center mt-6">
          <button onClick={loadMore} disabled={loadingMore}
            className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-colors"
            style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
    </PageContainer>
  );
}
