'use client';

import { Suspense, useCallback, useEffect, useId, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { api, dropletHeading, isUnreachable, DropletCard } from '@/lib/api';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { timeAgo } from '@/lib/time';
import { allTags, tagHref, tagOf } from '@/lib/tags';
import { LikeButton, formatCount } from '@/components/LikeButton';
import { TagList } from '@/components/TagList';
import { MadeWith } from '@/components/MadeWith';
import { ClipFeed } from '@/components/ClipFeed';
import { DropletComments } from '@/components/DropletComments';
import { ChatBubbleLeftIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { AcademicCapIcon, MusicalNoteIcon, PlayIcon } from '@heroicons/react/24/solid';
import { countPlays, useViews } from '@/lib/plays';
import { viewsLabel } from '@/components/ViewCount';

// How many of a droplet's hashtags its card shows; its page shows them all, under each video.
const CARD_TAGS = 4;
// How many of its newest comments a card shows folded (api/src/lib/engagement.ts, COMMENTS_ON_CARD).
const CARD_COMMENTS = 2;

/**
 * The public Droplets tab (docs/PLAN.md, section 10): every finished droplet, for
 * anyone, signed in or not. A card plays its song and its lesson in place, a swipe or a tab apart, shows the
 * title the studio gave it, a few lines of the lesson, its first hashtags, the makers of
 * the models that made it, and its newest comments, which open in place to all of them;
 * never who asked, or how they asked it.
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
 * the line, leaning 25° toward the P. The real O stays in the text, see-through, so the
 * heading still copies and searches as "DROPLETS". Both stay in the line of text (taking
 * either out of it splits the word for screen readers): the drop's box is one character
 * wide (1ch, in a monospace font) and its negative margin puts the O back under it.
 *
 * The drop is drawn 1.5 times the size of its box, turned and grown about its round
 * belly, with a thinner stroke so the line is as thick as the letters'. It spills out of
 * the box, so the box and the O each take a little room to keep it off the R and the P.
 * The fall moves and squashes the whole box, so the drop still falls straight down.
 *
 * The heading around it never wraps, since the drop is a place the word could break, and
 * carries the word as a label, since some screen readers read it in pieces around the drop.
 * In high-contrast mode, which shows every letter, the drop hides and the plain O shows.
 */
function DropO() {
  return (
    <>
      <svg aria-hidden="true" viewBox="14 7 36 48" className="animate-drop-fall relative inline overflow-visible forced-colors:hidden"
        style={{ width: '1ch', marginLeft: '0.12em', marginRight: '-1ch', height: '0.8em', verticalAlign: '-0.03em', top: '-0.26em', color: 'var(--accent-brand)' }}>
        <path d="M32 12.5C32 12.5 20.3 28 20.3 38a11.7 11.7 0 0 0 23.4 0C43.7 28 32 12.5 32 12.5Z"
          transform="translate(32 38) rotate(25) scale(1.5) translate(-32 -38)"
          fill="none" stroke="currentColor" strokeWidth="4.4" strokeLinejoin="round" />
      </svg>
      <span className="text-transparent" style={{ marginRight: '0.12em' }}>O</span>
    </>
  );
}

/**
 * One droplet on the list. The videos play here, so watching takes no trip to its page;
 * the title and "Full page" go there. The like button, the tags and the comments sit
 * beside the links, not inside them: a button or a link within a link is not valid HTML,
 * and a click on it would open the droplet too.
 */
function Card({ droplet: d, index }: { droplet: DropletCard; index: number }) {
  const [commentCount, setCommentCount] = useState(d.commentCount ?? 0);
  const [commentsOpen, setCommentsOpen] = useState(false);
  const href = `/droplets/${d.id}`;
  const heading = dropletHeading(d);
  // The song first, as it is the hook, then the lesson, each with its own still. A card with one video may show the
  // other's still rather than none; with both, the song's picture never stands in under the Lesson tab.
  const slides = ([
    { kind: 'song', src: d.video.songVideoUrl, poster: d.video.songPosterUrl },
    { kind: 'lesson', src: d.video.lessonVideoUrl, poster: d.video.lessonPosterUrl },
  ] as const).filter((s): s is Slide => !!s.src)
    .map((s, _, all) => (all.length > 1 ? s : { ...s, poster: s.poster || d.video.songPosterUrl || d.video.lessonPosterUrl }));
  const [shown, setShown] = useState<CardKind>(slides[0]?.kind ?? 'song');
  // The views of the video on show, as YouTube puts them under a title.
  const songViews = useViews(d.views?.song ?? 0);
  const lessonViews = useViews(d.views?.lesson ?? 0);
  const views = shown === 'song' ? songViews : lessonViews;
  return (
    <article data-testid="droplet-card"
      className="flex flex-col rounded-lg overflow-hidden animate-fade-in"
      style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', animationDelay: `${Math.min(index, 12) * 60}ms` }}>
      {slides.length > 0 ? (
        <CardVideos dropletId={d.id} heading={heading} slides={slides} shown={shown} onShow={setShown}
          onStart={{ song: songViews.onStart, lesson: lessonViews.onStart }} />
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
        <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>
          {/* An API older than the views sends none: leave them out rather than show zero. */}
          {slides.length > 0 && d.views && <><span data-testid="view-count">{viewsLabel(views.views)}</span> · </>}
          {timeAgo(d.createdAt)}
        </p>
      </Link>
      <TagList tags={allTags(d.tags)} max={CARD_TAGS} className="px-4 pb-3 -mt-1" />
      <MadeWith makers={d.madeWith} compact className="px-4 pb-3 -mt-1" />
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

type CardKind = 'song' | 'lesson';
type Slide = { kind: CardKind; src: string; poster: string | null };

// Each video's name and colour, as its player on the droplet's page has them (components/DropletPlayers.tsx).
const SLIDE = {
  song: { label: 'Song', color: 'var(--accent-red)', Icon: MusicalNoteIcon },
  lesson: { label: 'Lesson', color: 'var(--accent-blue)', Icon: AcademicCapIcon },
} as const;

/**
 * A card's videos, side by side in a row one video wide, the song first. A finger (or a trackpad) swipes from one to
 * the other, snapping to each, and the tabs under the row go to either: they also say which one is on show, with a
 * bar under it that follows a swipe as it goes. The tab lights up once the other video is more than half in. The
 * video slid away pauses, and keeps its place for when it comes back. The one out of view is inert, so the keyboard
 * and a screen reader meet only the one on show; the tabs take the arrow keys, as tabs do.
 * A droplet with one video shows it alone, with its name under it.
 */
function CardVideos({ dropletId, heading, slides, shown, onShow, onStart }: {
  dropletId: string; heading: string; slides: Slide[]; shown: CardKind; onShow: (kind: CardKind) => void;
  onStart: Record<CardKind, (counted: Promise<boolean>) => void>;
}) {
  const row = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLSpanElement>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();
  const many = slides.length > 1;
  const showing = SLIDE[shown];

  const onScroll = () => {
    const el = row.current;
    if (!el || !el.clientWidth) return;
    const at = el.scrollLeft / el.clientWidth;
    if (bar.current) bar.current.style.transform = `translateX(${at * 100}%)`;
    const kind = slides[Math.round(at)]?.kind;
    if (kind && kind !== shown) onShow(kind);
  };
  const go = (i: number) => {
    const el = row.current;
    if (!el) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollTo({ left: i * el.clientWidth, behavior: still ? 'auto' : 'smooth' });
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    const now = slides.findIndex((s) => s.kind === shown);
    const to = { ArrowLeft: now - 1, ArrowRight: now + 1, Home: 0, End: slides.length - 1 }[e.key];
    if (to === undefined || to < 0 || to >= slides.length) return;
    e.preventDefault();
    tabs.current[to]?.focus();
    go(to);
  };

  return (
    <div>
      {/* overscroll-x-contain: a swipe past the last video does not turn into the browser's swipe back. */}
      <div ref={row} onScroll={many ? onScroll : undefined} data-testid="card-videos"
        className="flex overflow-x-auto overscroll-x-contain snap-x snap-mandatory swipe-row">
        {slides.map((s) => (
          <div key={s.kind} className="w-full shrink-0 snap-start snap-always" data-testid={`card-${s.kind}`}
            {...(many && { role: 'tabpanel', id: `${id}-${s.kind}`, 'aria-labelledby': `${id}-${s.kind}-tab`, inert: s.kind !== shown })}>
            <CardVideo dropletId={dropletId} src={s.src} poster={s.poster} kind={s.kind} heading={heading}
              onStart={onStart[s.kind]} active={s.kind === shown} />
          </div>
        ))}
      </div>
      {many ? (
        <div role="tablist" aria-label={`${heading}: videos`} onKeyDown={onKeyDown} className="relative flex"
          style={{ borderBottom: '1px solid var(--border)' }}>
          {slides.map((s, i) => {
            const on = s.kind === shown;
            const { label, color, Icon } = SLIDE[s.kind];
            return (
              <button key={s.kind} ref={(el) => { tabs.current[i] = el; }} type="button" role="tab" id={`${id}-${s.kind}-tab`}
                aria-selected={on} aria-controls={`${id}-${s.kind}`} tabIndex={on ? 0 : -1} onClick={() => go(i)}
                className="flex-1 inline-flex items-center justify-center gap-1.5 py-2 text-[10px] uppercase tracking-wider font-bold transition-colors"
                style={{ color: on ? color : 'var(--text-muted)' }}>
                <Icon className="w-3.5 h-3.5" aria-hidden /> {label}
              </button>
            );
          })}
          {/* High contrast paints every background the page's own colour: the bar keeps the system's highlight. */}
          <span ref={bar} aria-hidden className="absolute top-0 left-0 h-0.5 transition-colors forced-colors:[forced-color-adjust:none] forced-colors:bg-[Highlight]!"
            style={{ width: `${100 / slides.length}%`, background: showing.color }} />
        </div>
      ) : (
        <p className="flex items-center justify-center gap-1.5 py-2 text-[10px] uppercase tracking-wider font-bold"
          style={{ color: showing.color, borderBottom: '1px solid var(--border)' }} data-testid="card-video-name">
          <showing.Icon className="w-3.5 h-3.5" aria-hidden /> {showing.label}
        </p>
      )}
    </div>
  );
}

/**
 * A card's video: its still with a big play button, which plays it right there; the
 * player's own controls take over once it starts. A click on a <video> that has not
 * loaded yet does nothing in Chrome, and its own play button is small, so the still
 * is the button. Until then the still is a lazy <img>: a <video>'s poster loads at once,
 * for every card on the page, and preload="none" keeps the video itself from loading.
 * How far it plays is counted as on the droplet's page (lib/plays.ts). It pauses when
 * it is swiped out of view (`active` false).
 */
function CardVideo({ dropletId, src, poster, kind, heading, onStart, active }: {
  dropletId: string; src: string; poster: string | null; kind: CardKind; heading: string;
  onStart: (counted: Promise<boolean>) => void; active: boolean;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [started, setStarted] = useState(false);
  const pressed = useRef(false);
  // The button goes once it is pressed: hand the keyboard to the player, to pause it with.
  useEffect(() => { if (started && pressed.current) video.current?.focus(); }, [started]);
  useEffect(() => { if (!active) video.current?.pause(); }, [active]);
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
        {...countPlays(dropletId, kind, onStart)}
        aria-label={`${heading}: ${kind} video`}
        className="block w-full h-full"
      />
      {!started && (
        <button type="button" aria-label={`Play the ${kind} video: ${heading}`} data-testid="card-play"
          // A play that fails still shows the controls, to try again with.
          onClick={() => { pressed.current = true; setStarted(true); video.current?.play().catch(() => {}); }}
          className="group absolute inset-0 flex items-center justify-center">
          {poster && (
            // eslint-disable-next-line @next/next/no-img-element -- a remote R2 still, no optimizer in front
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
