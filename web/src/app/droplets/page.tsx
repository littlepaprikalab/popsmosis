'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { api, isUnreachable, DropletCard } from '@/lib/api';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { timeAgo } from '@/lib/time';
import { allTags, tagHref, tagOf } from '@/lib/tags';
import { LikeButton, formatCount } from '@/components/LikeButton';
import { TagList } from '@/components/TagList';
import { ChatBubbleLeftIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline';

// How many of a droplet's hashtags its card shows; its page shows them all, under each video.
const CARD_TAGS = 4;

/**
 * The public Droplets tab (docs/PLAN.md, section 10): every finished droplet, for
 * anyone, signed in or not. A card shows the title the studio gave it, a few lines of
 * the lesson and its first hashtags; never who asked, or how they asked it. `?tag=`
 * lists only the droplets tagged with it, as a hashtag search does.
 */
export default function DropletsPage() {
  // useSearchParams needs a boundary, or the page cannot be prerendered.
  return (
    <Suspense fallback={<LoadingMessage />}>
      <Droplets />
    </Suspense>
  );
}

function Droplets() {
  const { showToast } = useToast();
  const router = useRouter();
  const tag = tagOf(useSearchParams().get('tag') ?? '') || null;
  const [query, setQuery] = useState(tag ? `#${tag}` : '');
  const [droplets, setDroplets] = useState<DropletCard[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  // The first page of this search did not load: never leave another search's cards under its name.
  const [failed, setFailed] = useState(false);
  // The search the list now shows: a page that comes back after the search changed is dropped.
  const current = useRef(tag);

  const load = useCallback(async () => {
    current.current = tag;
    setLoading(true);
    try {
      const res = await api.listDroplets(null, tag);
      if (current.current !== tag) return;
      setDroplets(res.data);
      setCursor(res.nextCursor);
      setUnreachable(false);
      setFailed(false);
    } catch (err) {
      if (current.current !== tag) return;
      setDroplets([]);
      setCursor(null);
      if (isUnreachable(err)) setUnreachable(true);
      else {
        setFailed(true);
        showToast(err instanceof Error ? err.message : 'Failed to load droplets', 'error');
      }
    } finally {
      if (current.current === tag) setLoading(false);
    }
  }, [showToast, tag]);

  useEffect(() => { load(); }, [load]);
  // A tag clicked on a card changes the search without a new page: show it in the box too.
  useEffect(() => { setQuery(tag ? `#${tag}` : ''); }, [tag]);

  const loadMore = async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await api.listDroplets(cursor, tag);
      if (current.current !== tag) return;
      setDroplets((prev) => [...prev, ...res.data.filter((d) => !prev.some((p) => p.id === d.id))]);
      setCursor(res.nextCursor);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load more', 'error');
    } finally {
      setLoadingMore(false);
    }
  };

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    const wanted = tagOf(query);
    router.push(wanted ? tagHref(wanted) : '/droplets');
  };

  if (unreachable) return <ServerUnavailable onRetry={load} />;

  return (
    <PageContainer maxWidth="6xl" className="py-6">
      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1 whitespace-nowrap" aria-label="DROPLETS">DR<DropO />PLETS</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Every finished droplet, free to watch: a lesson checked line by line by AI, a song to remember it by, and a lesson video.
          No account needed to watch; sign in to like and comment. All made by AI.
        </p>
      </div>

      <form role="search" onSubmit={search} className="mb-5 flex gap-2 max-w-md">
        <label className="relative flex-1">
          <span className="sr-only">Search droplets by tag</span>
          <MagnifyingGlassIcon className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--text-muted)' }} aria-hidden />
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by tag, like #apbio"
            maxLength={100} enterKeyHint="search"
            className="w-full pl-8 pr-3 py-2 rounded text-sm outline-none focus:ring-1"
            style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
        </label>
        <button type="submit" className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider shrink-0"
          style={{ background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' }}>
          Search
        </button>
      </form>

      {tag && (
        <p className="mb-4 text-sm flex flex-wrap items-baseline gap-x-3" data-testid="tag-search">
          <span style={{ color: 'var(--text-secondary)' }}>
            Tagged <span className="font-bold" style={{ color: 'var(--accent-blue)' }}>#{tag}</span>
          </span>
          <Link href="/droplets" className="text-xs underline hover-text" style={{ color: 'var(--text-muted)' }}>Show all droplets</Link>
        </p>
      )}

      {loading ? (
        <LoadingMessage />
      ) : failed ? (
        <div className="text-center py-20" style={{ color: 'var(--text-muted)' }} data-testid="droplets-failed">
          <p>{tag ? `The droplets tagged #${tag} did not load.` : 'The droplets did not load.'}</p>
          <button onClick={load} className="text-xs mt-2 underline hover-text">Try again</button>
        </div>
      ) : droplets.length === 0 ? (
        <div className="text-center py-20" style={{ color: 'var(--text-muted)' }}>
          {tag ? (
            <>
              <p>No droplets are tagged #{tag} yet.</p>
              <p className="text-xs mt-2">
                Try another tag, or <Link href="/learn" className="underline hover-text">ask for this topic</Link> to make one.
              </p>
            </>
          ) : (
            <>
              <p>No droplets yet.</p>
              <p className="text-xs mt-2">
                A droplet lands here once its videos are done. <Link href="/learn" className="underline hover-text">Ask for a topic</Link> to make one.
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {droplets.map((d, i) => {
            const poster = d.video.songPosterUrl || d.video.lessonPosterUrl;
            // The like button and the tags sit beside the link, not inside it: a button or a
            // link within a link is not valid HTML, and a click on it would open the droplet too.
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
                <TagList tags={allTags(d.tags)} max={CARD_TAGS} className="px-4 pb-3 -mt-1" />
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

/**
 * The heading's O, drawn as an outlined drop (the one in the tab icon, src/app/icon.svg)
 * that falls into the gap between R and P when the page opens, and stays a little above
 * the line. The real O stays in the text, see-through, so the heading still copies and
 * searches as "DROPLETS". Both stay in the line of text (taking either out of it splits
 * the word for screen readers): the drop is one character wide (1ch, in a monospace font)
 * and its negative margin puts the O back under it.
 *
 * The heading around it never wraps, since the drop is a place the word could break, and
 * carries the word as a label, since some screen readers read it in pieces around the drop.
 * In high-contrast mode, which shows every letter, the drop hides and the plain O shows.
 */
function DropO() {
  return (
    <>
      <svg aria-hidden="true" viewBox="14 7 36 48" className="animate-drop-fall relative inline forced-colors:hidden"
        style={{ width: '1ch', marginRight: '-1ch', height: '0.8em', verticalAlign: '-0.03em', top: '-0.32em', color: 'var(--accent-brand)' }}>
        <path d="M32 12.5C32 12.5 20.3 28 20.3 38a11.7 11.7 0 0 0 23.4 0C43.7 28 32 12.5 32 12.5Z"
          fill="none" stroke="currentColor" strokeWidth="6.6" strokeLinejoin="round" />
      </svg>
      <span className="text-transparent">O</span>
    </>
  );
}
