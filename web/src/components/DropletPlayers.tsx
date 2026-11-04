'use client';

import type { DropletTags, DropletVideos, DropletViews, Video } from '@/lib/api';
import { useState } from 'react';

import { Drop } from '@/components/DropletMeter';
import { TagList } from '@/components/TagList';
import { ViewCount } from '@/components/ViewCount';
import { allTags } from '@/lib/tags';
import { countClip, countPlays, useViews, type PlayVideo } from '@/lib/plays';

/**
 * A droplet's videos: the karaoke song and the narrated lesson, or the lesson alone when it was made with no song
 * (STUDIO_VIDEOS=lesson), with both videos' hashtags once under the pair (most are shared, and two copies took a lot
 * of room).
 * H.264 with +faststart, so a plain <video> starts playing before the file has
 * downloaded; preload="none" keeps a page of them from fetching anything until one is played.
 * How far each play gets is counted, with nothing about who watched (lib/plays.ts), and each video's views show
 * beside its name, as on YouTube or TikTok; none from an API older than them.
 */
export function DropletPlayers({ dropletId, video, tags, title, views, note }: {
  dropletId: string; video: DropletVideos; tags?: DropletTags; title?: string; views?: DropletViews;
  note?: string | null; // why a video is missing; the droplet's own `video.note` when not given
}) {
  const missing = video.songVideoUrl ? null : (note ?? video.note ?? null);
  const players = [
    { label: 'SONG', kind: 'song' as const, src: video.songVideoUrl, poster: video.songPosterUrl, color: 'var(--accent-red)' },
    { label: 'LESSON', kind: 'lesson' as const, src: video.lessonVideoUrl, poster: video.lessonPosterUrl, color: 'var(--accent-blue)' },
  ].filter((p): p is typeof p & { src: string } => !!p.src);
  if (players.length === 0) return null;
  return (
    <>
    <div className={`grid gap-3 ${players.length > 1 ? 'sm:grid-cols-2' : ''}`} data-testid="droplet-players">
      {players.map((p) => (
        <Player key={p.label} dropletId={dropletId} {...p} views={views?.[p.kind]} />
      ))}
    </div>
    {missing && <SongMissing note={missing} className="mt-1.5" />}
    <TagList tags={allTags(tags)} className="mt-1.5" />
    {video.hookVideoUrl && <HookClip dropletId={dropletId} src={video.hookVideoUrl} title={title} />}
    </>
  );
}

/** Why the droplet has no song, under its videos here and on its card on the Droplets tab. */
export function SongMissing({ note, className = '' }: { note: string; className?: string }) {
  return (
    <p className={`text-xs ${className}`} style={{ color: 'var(--text-muted)' }} data-testid="videos-missing">
      <MissingNote text={note} /> The lesson video still teaches all of it.
    </p>
  );
}

/** "The song could not be made: why." with its first part in bold, as the failed videos' line has it. */
function MissingNote({ text }: { text: string }) {
  const line = `${text.trim().replace(/[.!?\s]+$/, '')}.`;
  const colon = line.indexOf(': ');
  if (colon < 0) return <span className="font-bold" style={{ color: 'var(--accent-red)' }}>{line}</span>;
  return (
    <>
      <span className="font-bold" style={{ color: 'var(--accent-red)' }}>{line.slice(0, colon)}</span>
      {line.slice(colon)}
    </>
  );
}

function Player({ dropletId, label, kind, src, poster, color, views }: {
  dropletId: string; label: string; kind: PlayVideo; src: string; poster: string | null; color: string; views?: number;
}) {
  const shown = useViews(views ?? 0);
  return (
    <figure className="min-w-0" data-testid={`player-${kind}`}>
      <figcaption className="flex items-baseline justify-between gap-2 mb-1">
        <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color }}>{label}</span>
        {views !== undefined && <ViewCount views={shown.views} />}
      </figcaption>
      <video
        controls
        playsInline
        preload="none"
        poster={poster ?? undefined}
        src={src}
        {...countPlays(dropletId, kind, shown.onStart)}
        aria-label={`${label.toLowerCase()} video`}
        className="w-full rounded-md aspect-video"
        style={{ background: '#000', border: '1px solid var(--border)' }}
      />
    </figure>
  );
}

/**
 * The hook cut: the song's pre-chorus and first chorus as a short upright clip, the part students post. Only its two
 * buttons here, no player: the clip is a cut of the song just above, and the Clips feed plays it full size. Share
 * hands the clip's link to the phone's share sheet with #popsmosis (or copies the link where there is none), Download
 * saves the file. A share is counted once it went through (a share sheet closed without sharing is not), a download
 * when it is pressed.
 */
export function HookClip({ dropletId, src, title }: { dropletId: string; src: string; title?: string }) {
  const [copied, setCopied] = useState(false);
  async function share() {
    const data = { title: title ? `${title}: the hook` : 'The hook', text: '#popsmosis', url: src };
    if (typeof navigator.share === 'function') {
      try {
        await navigator.share(data);
        countClip(dropletId, 'shared');
      } catch {
        // the learner closed the share sheet
      }
      return;
    }
    try {
      await navigator.clipboard.writeText(src);
      countClip(dropletId, 'shared');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // no clipboard either: the Download link still works
    }
  }
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2" data-testid="hook-clip">
      <p className="flex flex-col gap-0.5">
        <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--accent-red)' }}>Hook clip</span>
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>The catchiest bit, ready to post.</span>
      </p>
      <div className="flex gap-2">
        <button type="button" onClick={share} className="text-xs font-bold px-3 py-1.5 rounded-md"
          style={{ background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' }} data-testid="hook-share">
          {copied ? 'Link copied' : 'Share clip'}
        </button>
        <a href={src} download onClick={() => countClip(dropletId, 'downloaded')} className="text-xs font-bold px-3 py-1.5 rounded-md"
          style={{ border: '1px solid var(--border)', color: 'var(--text-primary)' }} data-testid="hook-download">
          Download
        </a>
      </div>
    </div>
  );
}

/** Under a lesson on /learn: the players once READY, else where the render stands. */
export function LessonVideos({ answerId, video, tags, views }: { answerId: string; video: Video | null; tags?: DropletTags; views?: DropletViews }) {
  if (!video) return null;
  if (video.status === 'READY') {
    return <div className="mt-3"><DropletPlayers dropletId={answerId} video={video} tags={tags} views={views} note={video.error} /></div>;
  }
  if (video.status === 'RENDERING') {
    return (
      <p className="text-xs mt-3 flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }} data-testid="videos-rendering">
        <Drop state="now" className="w-2 h-3 shrink-0" />
        The videos are rendering… they appear here when they are done.
      </p>
    );
  }
  return (
    <p className="text-xs mt-3" style={{ color: 'var(--text-muted)' }} data-testid="videos-failed">
      <span className="font-bold" style={{ color: 'var(--accent-red)' }}>The videos could not be made</span>
      {video.error ? `: ${video.error.replace(/[.!?\s]+$/, '')}.` : '.'} The lesson below is complete without them.
    </p>
  );
}

/** The song's words, verse by verse. */
export function Lyrics({ lyrics }: { lyrics: { section: string; text: string }[] }) {
  if (lyrics.length === 0) return null;
  // Consecutive lines of one section sing as one block.
  const blocks: { section: string; lines: string[] }[] = [];
  for (const l of lyrics) {
    const last = blocks[blocks.length - 1];
    if (last && last.section === l.section) last.lines.push(l.text);
    else blocks.push({ section: l.section, lines: [l.text] });
  }
  return (
    <div className="space-y-3" data-testid="lyrics">
      {blocks.map((b, i) => (
        <div key={i}>
          {b.section && (
            <p className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: 'var(--text-muted)' }}>{b.section}</p>
          )}
          {b.lines.map((line, j) => (
            <p key={j} className="text-sm" style={{ color: 'var(--text-primary)', lineHeight: 1.7 }}>{line}</p>
          ))}
        </div>
      ))}
    </div>
  );
}
