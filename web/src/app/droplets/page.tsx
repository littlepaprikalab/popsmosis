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
import { ClipFeed } from '@/components/ClipFeed';
import { DropletComments } from '@/components/DropletComments';
import { ChatBubbleLeftIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { PlayIcon } from '@heroicons/react/24/solid';

// How many of a droplet's hashtags its card shows; its page shows them all, under each video.
const CARD_TAGS = 4;
// How many of its newest comments a card shows folded (api/src/lib/engagement.ts, COMMENTS_ON_CARD).
const CARD_COMMENTS = 2;

/**
 * The public Droplets tab (docs/PLAN.md, section 10): every finished droplet, for
 * anyone, signed in or not. A card plays its song (or its lesson) in place, shows the
 * title the studio gave it, a few lines of the lesson, its first hashtags and its newest
 * comments, which open in place to all of them; never who asked, or how they asked it.
 * Its page has both videos, the whole lesson and the lyrics. `?tag=` lists only the
 * droplets tagged with it, as a hashtag search does.
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
  const params = useSearchParams();
  const tag = tagOf(params.get('tag') ?? '') || null;
  // ?view=clips: the hook cuts, one upright clip at a time (ClipFeed); else the grid of cards.
  const clips = params.get('view') === 'clips';
  const shown = `${clips ? 'clips' : 'grid'}|${tag ?? ''}`;
  const [query, setQuery] = useState(tag ? `#${tag}` : '');
  const [droplets, setDroplets] = useState<DropletCard[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  // The first page of this search did not load: never leave another search's cards under its name.
  const [failed, setFailed] = useState(false);
  // The search the list now shows: a page that comes back after the search changed is dropped.
  const current = useRef(shown);

  const load = useCallback(async () => {
    current.current = shown;
    setLoading(true);
    try {
      const res = await api.listDroplets(null, tag, clips);
      if (current.current !== shown) return;
      setDroplets(res.data);
      setCursor(res.nextCursor);
      setUnreachable(false);
      setFailed(false);
    } catch (err) {
      if (current.current !== shown) return;
      setDroplets([]);
      setCursor(null);
      if (isUnreachable(err)) setUnreachable(true);
      else {
        setFailed(true);
        showToast(err instanceof Error ? err.message : 'Failed to load droplets', 'error');
      }
    } finally {
      if (current.current === shown) setLoading(false);
    }
  }, [showToast, tag, clips, shown]);

  useEffect(() => { load(); }, [load]);
  // A tag clicked on a card changes the search without a new page: show it in the box too.
  useEffect(() => { setQuery(tag ? `#${tag}` : ''); }, [tag]);

  // One card plays at a time: starting one pauses whichever was playing. A media event does
  // not bubble, so this listens on the way down.
  useEffect(() => {
    const onPlay = (e: Event) => {
      for (const video of document.querySelectorAll('video')) if (video !== e.target) video.pause();
    };
    document.addEventListener('play', onPlay, true);
    return () => document.removeEventListener('play', onPlay, true);
  }, []);

  const loadMore = async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await api.listDroplets(cursor, tag, clips);
      if (current.current !== shown) return;
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

  // Grid or Clips, keeping the tag searched.
  const viewHref = (view: 'grid' | 'clips') => {
    const q = new URLSearchParams();
    if (tag) q.set('tag', tag);
    if (view === 'clips') q.set('view', 'clips');
    const qs = q.toString();
    return `/droplets${qs ? `?${qs}` : ''}`;
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

      <nav aria-label="Show droplets as" className="mb-4 inline-flex rounded-md overflow-hidden text-xs font-bold uppercase tracking-wider"
        style={{ border: '1px solid var(--border)' }} data-testid="droplets-view">
        {(['grid', 'clips'] as const).map((view) => {
          const on = (view === 'clips') === clips;
          return (
            <Link key={view} href={viewHref(view)} aria-current={on ? 'page' : undefined} className="px-3 py-1.5"
              style={on ? { background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' } : { color: 'var(--text-secondary)' }}>
              {view === 'grid' ? 'Grid' : 'Clips'}
            </Link>
          );
        })}
      </nav>

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
              <p>{clips ? `No droplets tagged #${tag} have a clip yet.` : `No droplets are tagged #${tag} yet.`}</p>
              <p className="text-xs mt-2">
                Try another tag, or <Link href="/learn" className="underline hover-text">ask for this topic</Link> to make one.
              </p>
            </>
          ) : (
            <>
              <p>{clips ? 'No clips yet.' : 'No droplets yet.'}</p>
              <p className="text-xs mt-2">
                {clips ? 'New droplets come with a short clip of their song.' : 'A droplet lands here once its videos are done.'}{' '}
                <Link href="/learn" className="underline hover-text">Ask for a topic</Link> to make one.
              </p>
            </>
          )}
        </div>
      ) : clips ? (
        <ClipFeed droplets={droplets} onEnd={loadMore} />
      ) : (
        // Each card is as tall as it is: one opening its comments grows alone, and its row does not stretch the rest.
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 items-start">
          {droplets.map((d, i) => <Card key={d.id} droplet={d} index={i} />)}
        </div>
      )}

      {cursor && !loading && !clips && (
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

/**
 * One droplet on the list. The video plays here, so watching takes no trip to its page;
 * the title and "Full page" go there. The like button, the tags and the comments sit
 * beside the links, not inside them: a button or a link within a link is not valid HTML,
 * and a click on it would open the droplet too.
 */
function Card({ droplet: d, index }: { droplet: DropletCard; index: number }) {
  const [commentCount, setCommentCount] = useState(d.commentCount ?? 0);
  const [commentsOpen, setCommentsOpen] = useState(false);
  const href = `/droplets/${d.id}`;
  const heading = d.course && d.topic ? d.topic : d.title;
  // The song if it has one: it is the hook. Its own still, else the other video's.
  const song = !!d.video.songVideoUrl;
  const src = d.video.songVideoUrl || d.video.lessonVideoUrl;
  const poster = (song ? d.video.songPosterUrl : d.video.lessonPosterUrl) || d.video.songPosterUrl || d.video.lessonPosterUrl;
  return (
    <article data-testid="droplet-card"
      className="flex flex-col rounded-lg overflow-hidden animate-fade-in"
      style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', animationDelay: `${Math.min(index, 12) * 60}ms` }}>
      {src ? (
        <CardVideo src={src} poster={poster} kind={song ? 'song' : 'lesson'} heading={heading} />
      ) : (
        <Link href={href} className="aspect-video flex items-center justify-center" style={{ background: 'var(--bg-tertiary)' }}>
          <span className="text-xs uppercase tracking-wider font-bold" style={{ color: 'var(--accent-red)' }}>▶ Watch</span>
        </Link>
      )}
      <Link href={href} className="block p-4 transition-colors hover:brightness-110">
        {d.course && d.topic && (
          <p className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: 'var(--accent-purple)' }}>{d.course}</p>
        )}
        <h3 className="text-sm font-bold mb-2" style={{ color: 'var(--text-primary)' }}>{heading}</h3>
        <p className="text-xs line-clamp-3" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>{d.excerpt}</p>
        <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>{timeAgo(d.createdAt)}</p>
      </Link>
      <TagList tags={allTags(d.tags)} max={CARD_TAGS} className="px-4 pb-3 -mt-1" />
      <div className="px-4 pb-3 flex items-center gap-3">
        {/* An API older than likes and comments sends neither count: leave both out rather than show zeros. */}
        {d.likeCount !== undefined && (
          <>
            <LikeButton dropletId={d.id} liked={!!d.liked} likeCount={d.likeCount} />
            <button type="button" onClick={() => setCommentsOpen((open) => !open)} aria-expanded={commentsOpen}
              className="inline-flex items-center gap-1 text-xs hover-text" style={{ color: 'var(--text-secondary)' }}
              aria-label={`${commentCount} ${commentCount === 1 ? 'comment' : 'comments'}`} data-testid="card-comments">
              <ChatBubbleLeftIcon className="w-4 h-4" aria-hidden /> {formatCount(commentCount)}
            </button>
          </>
        )}
        <Link href={href} className="ml-auto text-[10px] uppercase tracking-wider font-bold hover-text" style={{ color: 'var(--accent-blue)' }}
          aria-label={`Full page: ${heading}`} data-testid="card-full-page">
          Full page →
        </Link>
      </div>
      {d.commentCount !== undefined && (
        <DropletComments dropletId={d.id} count={commentCount} setCount={setCommentCount} initial={d.comments}
          card={{ preview: CARD_COMMENTS, open: commentsOpen, setOpen: setCommentsOpen }} />
      )}
    </article>
  );
}

/**
 * A card's video: its still with a big play button, which plays it right there; the
 * player's own controls take over once it starts. A click on a <video> that has not
 * loaded yet does nothing in Chrome, and its own play button is small, so the still
 * is the button. Until then the still is a lazy <img>: a <video>'s poster loads at once,
 * for every card on the page, and preload="none" keeps the video itself from loading.
 */
function CardVideo({ src, poster, kind, heading }: { src: string; poster: string | null; kind: 'song' | 'lesson'; heading: string }) {
  const video = useRef<HTMLVideoElement>(null);
  const [started, setStarted] = useState(false);
  const pressed = useRef(false);
  // The button goes once it is pressed: hand the keyboard to the player, to pause it with.
  useEffect(() => { if (started && pressed.current) video.current?.focus(); }, [started]);
  return (
    <div className="relative aspect-video" style={{ background: '#000' }}>
      <video
        ref={video}
        controls={started}
        playsInline
        preload="none"
        poster={started ? poster ?? undefined : undefined}
        src={src}
        onPlay={() => setStarted(true)}
        aria-label={`${heading}: ${kind} video`}
        className="block w-full h-full"
      />
      {!started && (
        <button type="button" aria-label={`Play the ${kind} video: ${heading}`} data-testid="card-play"
          // A play that fails still shows the controls, to try again with.
          onClick={() => { pressed.current = true; setStarted(true); video.current?.play().catch(() => {}); }}
          className="group absolute inset-0 flex items-center justify-center">
          {poster && (
            // eslint-disable-next-line @next/next/no-img-element -- a remote Supabase still, no optimizer in front
            <img src={poster} alt="" loading="lazy" className="absolute inset-0 w-full h-full object-cover" />
          )}
          <span className="relative w-14 h-14 rounded-full flex items-center justify-center transition-transform group-hover:scale-110 group-focus-visible:ring-2"
            style={{ background: 'rgba(0, 0, 0, 0.6)', color: '#fff' }}>
            <PlayIcon className="w-7 h-7 ml-0.5" aria-hidden />
          </span>
        </button>
      )}
    </div>
  );
}
