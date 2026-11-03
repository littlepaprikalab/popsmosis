'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import type { DropletExample } from '@/lib/api';
import { timeAgo } from '@/lib/time';
import Markdown from '@/components/Markdown';
import { AgentAvatar } from '@/components/UserAvatar';
import { CollapsibleCard, CollapseButton } from '@/components/CollapsibleCard';
import { DropletPlayers } from '@/components/DropletPlayers';

/**
 * A droplet from the public Droplets tab, drawn the way a topic card on the Learn page is: for someone signed out,
 * and for a learner with no topics yet, so they see what asking gets them before they ask. Its videos play and its
 * lesson reads, but it takes no follow-ups and has nothing to delete, as it is not theirs. It shows only what the
 * Droplets tab shows anyone: the request line is the droplet's own topic, never who asked or how they asked.
 *
 * Which droplet, and the copy of it every visitor gets, is the web server's (app/learn/example/route.ts): one request
 * there, none to the API. Nothing shows if none comes back: the page reads fine without it.
 */
export function ExampleDroplet() {
  const [droplet, setDroplet] = useState<DropletExample | null>(null);

  useEffect(() => {
    let live = true;
    fetch('/learn/example')
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { droplet: DropletExample | null } | null) => { if (live && body?.droplet) setDroplet(body.droplet); })
      .catch(() => {});
    return () => { live = false; };
  }, []);

  if (!droplet) return null;
  const d = droplet;
  const topic = d.course && d.topic ? d.topic : d.title;

  return (
    <div data-testid="example-droplet">
      <p className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
        Here is one the teacher made, from the <Link href="/droplets" className="underline hover-text">Droplets tab</Link>:
      </p>
      <CollapsibleCard
        className="animate-fade-in"
        style={{ background: 'var(--bg-secondary)', border: '1px dashed var(--border)' }}
        headerClassName="px-4 pt-4 pb-3"
        header={(collapsed) => (
          <>
            <div className="flex items-center justify-between mb-2 gap-2">
              <span className="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded-full font-semibold"
                style={{ color: 'var(--accent-purple)', border: '1px solid var(--accent-purple)' }}>
                example
              </span>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-[10px] uppercase font-bold" style={{ color: 'var(--accent-green)' }}>READY</span>
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{timeAgo(d.createdAt)}</span>
              </div>
            </div>
            <p className={`text-sm break-words ${collapsed ? 'line-clamp-3' : ''}`} style={{ color: 'var(--text-primary)' }}>{topic}</p>
          </>
        )}
        preview={
          <p className="text-sm line-clamp-3" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <span className="font-bold" style={{ color: 'var(--accent-blue)' }}>Teacher:</span> &ldquo;{d.excerpt}&rdquo;
          </p>
        }
      >
        {(collapse) => (
          <>
            <div className="rounded-md p-3" style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}>
              <div className="flex items-center gap-2 mb-2">
                <AgentAvatar name="Teacher" />
                <span className="text-xs font-bold" style={{ color: 'var(--text-secondary)' }}>Teacher</span>
                <span className="badge-agent text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold">agent</span>
              </div>
              <div className="mt-3">
                <DropletPlayers dropletId={d.id} video={d.video} tags={d.tags} title={d.title} views={d.views} />
              </div>
              <div className="md text-sm mt-3" style={{ color: 'var(--text-primary)' }}>
                <Markdown>{d.content}</Markdown>
              </div>
              <p className="text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>
                <Link href={`/droplets/${d.id}`} className="underline hover-text">On the public Droplets tab</Link>
              </p>
              {/* Where a learner's own card has its follow-ups: this one is not theirs to reply to. */}
              <p className="text-xs mt-3 pt-3" style={{ color: 'var(--text-muted)', borderTop: '1px solid var(--border)' }}
                data-testid="example-no-follow-ups">
                Under your own droplets you can ask the teacher follow-ups, to go deeper.
              </p>
            </div>
            <div className="flex justify-end mt-2">
              <CollapseButton onClick={collapse} />
            </div>
          </>
        )}
      </CollapsibleCard>
    </div>
  );
}
