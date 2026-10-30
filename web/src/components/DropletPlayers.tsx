'use client';

import type { DropletTags, DropletVideos, Video } from '@/lib/api';
import { useState } from 'react';

import { TagList } from '@/components/TagList';
import { allTags } from '@/lib/tags';
import { countClip, countPlays } from '@/lib/plays';

/**
 * A droplet's videos: the karaoke song and the narrated lesson, or the lesson alone when it was made with no song
 * (STUDIO_VIDEOS=lesson), with both videos' hashtags once under the pair (most are shared, and two copies took a lot
 * of room).
 * H.264 with +faststart, so a plain <video> starts playing before the file has
 * downloaded; preload="none" keeps a page of them from fetching anything until one is played.
 * How far each play gets is counted, with nothing about who watched (lib/plays.ts).
 */
export function DropletPlayers({ dropletId, video, tags, title }: { dropletId: string; video: DropletVideos; tags?: DropletTags; title?: string }) {
  const players = [
    { label: 'SONG', kind: 'song' as const, src: video.songVideoUrl, poster: video.songPosterUrl, color: 'var(--accent-red)' },
    { label: 'LESSON', kind: 'lesson' as const, src: video.lessonVideoUrl, poster: video.lessonPosterUrl, color: 'var(--accent-blue)' },
  ].filter((p): p is typeof p & { src: string } => !!p.src);
  if (players.length === 0) return null;
  return (
    <>
    <div className={`grid gap-3 ${players.length > 1 ? 'sm:grid-cols-2' : ''}`} data-testid="droplet-players">
      {players.map((p) => (
        <figure key={p.label} className="min-w-0">
          <figcaption className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: p.color }}>{p.label}</figcaption>
          <video
            controls
            playsInline
            preload="none"
            poster={p.poster ?? undefined}
            src={p.src}
            {...countPlays(dropletId, p.kind)}
            aria-label={`${p.label.toLowerCase()} video`}
            className="w-full rounded-md aspect-video"
            style={{ background: '#000', border: '1px solid var(--border)' }}
          />
        </figure>
      ))}
    </div>
    <TagList tags={allTags(tags)} className="mt-1.5" />
    {video.hookVideoUrl && <HookClip dropletId={dropletId} src={video.hookVideoUrl} poster={video.hookPosterUrl ?? null} title={title} />}
    </>
  );
}

/**
 * The hook cut: the song's pre-chorus and first chorus as a short upright clip, the part students post. A small
 * tile beside its two buttons, so it never competes with the full videos: Share hands the clip's link to the
 * phone's share sheet with #popsmosis (or copies the link where there is none), Download saves the file. A share is
 * counted once it went through (a share sheet closed without sharing is not), a download when it is pressed.
 */
export function HookClip({ dropletId, src, poster, title }: { dropletId: string; src: string; poster: string | null; title?: string }) {
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
    <figure className="mt-3 flex items-end gap-3" data-testid="hook-clip">
      <video
        controls
        playsInline
        loop
        preload="none"
        poster={poster ?? undefined}
        src={src}
        {...countPlays(dropletId, 'hook')}
        aria-label="hook clip"
        className="w-32 rounded-md"
        style={{ aspectRatio: '9 / 16', background: '#000', border: '1px solid var(--border)' }}
      />
      <figcaption className="flex flex-col gap-2 pb-1">
        <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--accent-red)' }}>Hook clip</span>
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>The catchiest bit, ready to post.</span>
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
      </figcaption>
    </figure>
  );
}

/** Under a lesson on /learn: the players once READY, else where the render stands. */
export function LessonVideos({ answerId, video, tags }: { answerId: string; video: Video | null; tags?: DropletTags }) {
  if (!video) return null;
  if (video.status === 'READY') {
    return <div className="mt-3"><DropletPlayers dropletId={answerId} video={video} tags={tags} /></div>;
  }
  if (video.status === 'RENDERING') {
    return (
      <p className="text-xs mt-3 flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }} data-testid="videos-rendering">
        <span className="inline-block w-1.5 h-1.5 rounded-full animate-live-pulse" style={{ background: 'var(--accent-cyan)' }} />
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
