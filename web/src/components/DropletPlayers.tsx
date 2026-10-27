'use client';

import type { DropletVideos, Video } from '@/lib/api';

/**
 * A droplet's two videos: the karaoke song and the narrated lesson. H.264 with
 * +faststart, so a plain <video> starts playing before the file has downloaded;
 * preload="none" keeps a page of them from fetching anything until one is played.
 */
export function DropletPlayers({ video }: { video: DropletVideos }) {
  const players = [
    { label: 'SONG', src: video.songVideoUrl, poster: video.songPosterUrl, color: 'var(--accent-red)' },
    { label: 'LESSON', src: video.lessonVideoUrl, poster: video.lessonPosterUrl, color: 'var(--accent-blue)' },
  ].filter((p): p is typeof p & { src: string } => !!p.src);
  if (players.length === 0) return null;
  return (
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
            aria-label={`${p.label.toLowerCase()} video`}
            className="w-full rounded-md aspect-video"
            style={{ background: '#000', border: '1px solid var(--border)' }}
          />
        </figure>
      ))}
    </div>
  );
}

/** Under a lesson on /learn: the players once READY, else where the render stands. */
export function LessonVideos({ video }: { video: Video | null }) {
  if (!video) return null;
  if (video.status === 'READY') {
    return <div className="mt-3"><DropletPlayers video={video} /></div>;
  }
  if (video.status === 'RENDERING') {
    return (
      <p className="text-xs mt-3 flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }} data-testid="videos-rendering">
        <span className="inline-block w-1.5 h-1.5 rounded-full animate-live-pulse" style={{ background: 'var(--accent-cyan)' }} />
        The song and lesson videos are rendering… they appear here when they are done.
      </p>
    );
  }
  return (
    <p className="text-xs mt-3" style={{ color: 'var(--text-muted)' }} data-testid="videos-failed">
      <span className="font-bold" style={{ color: 'var(--accent-red)' }}>The videos could not be made</span>
      {video.error ? `: ${video.error.replace(/[.!?\s]+$/, '')}.` : '.'} The lesson above is complete without them.
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
