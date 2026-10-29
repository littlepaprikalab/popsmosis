'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import type { DropletCard } from '@/lib/api';
import { LikeButton } from '@/components/LikeButton';
import { SpeakerWaveIcon, SpeakerXMarkIcon } from '@heroicons/react/24/solid';

/**
 * The Droplets tab's Clips feed: each droplet's hook cut (the song's pre-chorus and first chorus, upright), one
 * at a time, full height, the next a swipe or a scroll away. The clip on screen plays, muted until the viewer
 * turns the sound on, which then stays on as they scroll; the others are paused. Under each, its topic, a like
 * and a link to the whole droplet. With reduced motion asked for, nothing plays until it is pressed.
 */
export function ClipFeed({ droplets, onEnd }: { droplets: DropletCard[]; onEnd: () => void }) {
  const [muted, setMuted] = useState(true);
  const clips = droplets.filter((d) => d.video.hookVideoUrl);
  return (
    <div className="mx-auto max-w-sm h-[78vh] overflow-y-auto snap-y snap-mandatory rounded-lg"
      style={{ background: '#000', border: '1px solid var(--border)' }} data-testid="clip-feed">
      {clips.map((d, i) => (
        <Clip key={d.id} droplet={d} muted={muted} setMuted={setMuted} last={i === clips.length - 1} onEnd={onEnd} />
      ))}
    </div>
  );
}

function Clip({ droplet: d, muted, setMuted, last, onEnd }: {
  droplet: DropletCard; muted: boolean; setMuted: (muted: boolean) => void; last: boolean; onEnd: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const heading = d.course && d.topic ? d.topic : d.title;
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const watch = new IntersectionObserver(([entry]) => {
      const v = video.current;
      if (!v) return;
      if (entry.isIntersecting) {
        if (!still) v.play().catch(() => undefined); // a browser that refuses to autoplay leaves the controls
        if (last) onEnd();
      } else {
        v.pause();
      }
    }, { threshold: 0.6 });
    watch.observe(el);
    return () => watch.disconnect();
  }, [last, onEnd]);
  return (
    <div ref={box} className="snap-start relative h-full w-full" data-testid="clip">
      <video
        ref={video}
        src={d.video.hookVideoUrl ?? undefined}
        poster={d.video.hookPosterUrl ?? undefined}
        muted={muted}
        loop
        playsInline
        controls={false}
        preload="none"
        onClick={(e) => { const v = e.currentTarget; if (v.paused) v.play().catch(() => undefined); else v.pause(); }}
        aria-label={`Clip: ${heading}`}
        className="h-full w-full object-contain"
      />
      <button type="button" onClick={() => setMuted(!muted)} aria-label={muted ? 'Turn the sound on' : 'Turn the sound off'}
        className="absolute top-3 right-3 rounded-full p-2" style={{ background: 'rgba(0,0,0,0.55)', color: '#fff' }}
        data-testid="clip-sound">
        {muted ? <SpeakerXMarkIcon className="w-5 h-5" aria-hidden /> : <SpeakerWaveIcon className="w-5 h-5" aria-hidden />}
      </button>
      <div className="absolute inset-x-0 bottom-0 p-4 flex items-end gap-3"
        style={{ background: 'linear-gradient(transparent, rgba(0,0,0,0.75))' }}>
        <div className="min-w-0 flex-1">
          {d.course && d.topic && (
            <p className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--accent-purple)' }}>{d.course}</p>
          )}
          <Link href={`/droplets/${d.id}`} className="block text-sm font-bold text-white">{heading}</Link>
          <Link href={`/droplets/${d.id}`} className="text-[10px] uppercase tracking-wider font-bold"
            style={{ color: 'var(--accent-brand)' }} data-testid="clip-full">
            Watch the full droplet →
          </Link>
        </div>
        {d.likeCount !== undefined && <LikeButton dropletId={d.id} liked={!!d.liked} likeCount={d.likeCount} />}
      </div>
    </div>
  );
}
