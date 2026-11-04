'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, dropletHeading, PlayCounts, PlayRanking, PlayVideo } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import PageContainer from '@/components/PageContainer';
import { LoadingMessage } from '@/components/LoadingMessage';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { useToast } from '@/components/Toast';
import { formatCount } from '@/components/LikeButton';
import { DropOffChart } from '@/components/DropOffChart';

const VIDEOS: { video: PlayVideo; label: string; color: string }[] = [
  { video: 'song', label: 'Song', color: 'var(--accent-red)' },
  { video: 'lesson', label: 'Lesson', color: 'var(--accent-blue)' },
  { video: 'hook', label: 'Hook clip', color: 'var(--accent-pink)' },
];
// The fewest plays a droplet needs to be ranked: a rate from a handful of plays says little.
const MINIMUMS = [1, 10, 50];
const STEPS: { key: keyof PlayCounts; label: string }[] = [
  { key: 'reached25', label: '25%' },
  { key: 'reached50', label: '50%' },
  { key: 'reached75', label: '75%' },
];

const cell = 'py-1.5 pr-3 align-middle';
const num = `${cell} text-right tabular-nums whitespace-nowrap`;
const percent = (part: number, whole: number) => (whole > 0 ? `${Math.round((part / whole) * 100)}%` : '—');
const tint = (color: string, amount: number) => `color-mix(in srgb, ${color} ${amount}%, transparent)`;

/**
 * Admin page: which droplets students watch to the end. Each droplet's plays of one video, ranked by the share that
 * finished (95% of it played), and how far the rest got; for the hook clip, its shares and downloads too. Counted
 * with nothing about who watched (api/src/routes/plays.ts, web/src/lib/plays.ts), and never an admin's own plays.
 */
export default function AdminPlaysPage() {
  const { user: me, loading: authLoading, unreachable, retry } = useAuth();
  const { showToast } = useToast();
  const [video, setVideo] = useState<PlayVideo>('song');
  const [min, setMin] = useState(10);
  const [ranking, setRanking] = useState<PlayRanking | null>(null);
  // Every droplet's totals, for the chart: asked for once, not again with every switch of video or minimum, since
  // they sum every count the API holds and do not change with either. Asked again only while none have come.
  const [totals, setTotals] = useState<PlayRanking['totals'] | null>(null);
  const haveTotals = useRef(false);
  const [loading, setLoading] = useState(true);
  const shown = useRef('');

  const load = useCallback(async () => {
    const asked = `${video}|${min}`;
    shown.current = asked;
    setLoading(true);
    try {
      const res = await api.listPlays(video, min, !haveTotals.current);
      if (res.totals) {
        haveTotals.current = true;
        setTotals(res.totals); // the same whatever was asked, so kept even from an answer that came too late
      }
      if (shown.current === asked) setRanking(res);
    } catch (err) {
      if (shown.current === asked) showToast(err instanceof Error ? err.message : 'Failed to load the plays', 'error');
    } finally {
      if (shown.current === asked) setLoading(false);
    }
  }, [video, min, showToast]);

  useEffect(() => {
    if (authLoading) return;
    if (me?.role === 'ADMIN') load(); else setLoading(false);
  }, [authLoading, me, load]);

  if (authLoading || (loading && me?.role === 'ADMIN' && !ranking)) return <LoadingMessage />;
  if (unreachable) return <ServerUnavailable onRetry={retry} />;
  if (me?.role !== 'ADMIN') {
    return (
      <PageContainer className="py-20 text-center">
        <p style={{ color: 'var(--accent-red)' }}>Admin access required.</p>
      </PageContainer>
    );
  }

  const hook = video === 'hook';
  const color = VIDEOS.find((v) => v.video === video)!.color;
  const choice = (on: boolean) => (on ? { background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' } : { color: 'var(--text-secondary)' });

  return (
    <PageContainer maxWidth="6xl" className="py-6">
      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">PLAYS</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Which droplets students watch to the end: each video&rsquo;s plays, ranked by the share that finished (95% of it played),
          and how far the others got. Counted in totals only, with no cookie, account, device or address, and never an
          admin&rsquo;s own plays. A page view counts a video once, however often it is replayed.
        </p>
      </div>

      {/* Every droplet together, whatever the video and the minimum chosen below. An API older than it sends no totals. */}
      {totals && (
        <section className="mb-6 rounded-lg p-4" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--text-primary)' }}>Where plays drop off</h3>
          <p className="text-[11px] mt-1 mb-3" style={{ color: 'var(--text-muted)' }}>
            Of every play of each video, across all droplets, the share still going at each point.
          </p>
          <DropOffChart totals={totals} />
        </section>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <nav aria-label="Video" className="inline-flex rounded-md overflow-hidden text-xs font-bold uppercase tracking-wider"
          style={{ border: '1px solid var(--border)' }} data-testid="plays-video">
          {VIDEOS.map((v) => (
            <button key={v.video} type="button" onClick={() => setVideo(v.video)} aria-pressed={video === v.video}
              className="px-3 py-1.5" style={choice(video === v.video)}>{v.label}</button>
          ))}
        </nav>
        <label className="text-xs flex items-center gap-2" style={{ color: 'var(--text-secondary)' }}>
          At least
          <select value={min} onChange={(e) => setMin(Number(e.target.value))} data-testid="plays-min"
            className="px-2 py-1 rounded text-xs" style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
            {MINIMUMS.map((m) => <option key={m} value={m}>{m} {m === 1 ? 'play' : 'plays'}</option>)}
          </select>
        </label>
        {loading && <span className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>Loading…</span>}
      </div>

      {!ranking ? (
        <div className="text-center py-12">
          <button onClick={load} className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider"
            style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>Try again</button>
        </div>
      ) : ranking.data.length === 0 ? (
        <p className="text-center py-12 text-sm" style={{ color: 'var(--text-muted)' }} data-testid="plays-empty">
          {ranking.fewer > 0 ? `No droplet has ${ranking.min} plays of this video yet.` : 'No plays of this video counted yet.'}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg p-3" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <table className="w-full border-collapse text-xs" style={{ color: 'var(--text-secondary)' }} data-testid="plays-table">
            <thead>
              <tr className="text-[9px] uppercase tracking-wider text-left" style={{ color: 'var(--text-muted)', borderBottom: '1px dashed var(--border)' }}>
                <th scope="col" className={`${cell} font-semibold`}>#</th>
                <th scope="col" className={`${cell} font-semibold`}>Droplet</th>
                <th scope="col" className={`${num} font-semibold`}>Plays</th>
                {STEPS.map((s) => <th key={s.key} scope="col" className={`${num} font-semibold`}>{s.label}</th>)}
                <th scope="col" className={`${cell} font-semibold`}>Finished</th>
                {hook && <th scope="col" className={`${num} font-semibold`}>Shares</th>}
                {hook && <th scope="col" className={`${num} font-semibold !pr-0`}>Downloads</th>}
              </tr>
            </thead>
            <tbody>
              {ranking.data.map((d, i) => {
                const p = d.plays;
                const heading = dropletHeading(d);
                return (
                  <tr key={d.id} data-testid="plays-row" style={i < ranking.data.length - 1 ? { borderBottom: '1px solid var(--border)' } : undefined}>
                    <td className={`${cell} tabular-nums`} style={{ color: 'var(--text-muted)' }}>{String(i + 1).padStart(2, '0')}</td>
                    <td className={`${cell} min-w-40`}>
                      {d.course && d.topic && (
                        <span className="block text-[9px] uppercase tracking-wider font-bold" style={{ color: 'var(--accent-purple)' }}>{d.course}</span>
                      )}
                      {/* The droplet's page is the public one, which a hidden droplet is not on. */}
                      {d.hidden ? (
                        <span style={{ color: 'var(--text-primary)' }}>{heading} <span style={{ color: 'var(--text-muted)' }}>· hidden</span></span>
                      ) : (
                        <Link href={`/droplets/${d.id}`} className="hover-text" style={{ color: 'var(--text-primary)' }}>{heading}</Link>
                      )}
                    </td>
                    <td className={num} style={{ color: 'var(--text-primary)' }}>{formatCount(p.started)}</td>
                    {STEPS.map((s) => (
                      <td key={s.key} className={num} title={`${p[s.key]} of ${p.started} plays got to ${s.label}`}>{percent(p[s.key] as number, p.started)}</td>
                    ))}
                    <td className={`${cell} whitespace-nowrap`} title={`${p.finished} of ${p.started} plays finished`}>
                      <span className="inline-flex items-center gap-2">
                        {/* One hue: the share that finished on a lighter track of the same colour. */}
                        <span className="inline-block w-20 h-1.5 rounded-full overflow-hidden" style={{ background: tint(color, 22) }} aria-hidden>
                          <span className="block h-full rounded-full" style={{ width: `${(p.finishRate ?? 0) * 100}%`, background: color }} />
                        </span>
                        <span className="tabular-nums font-bold" style={{ color: 'var(--text-primary)' }}>{percent(p.finished, p.started)}</span>
                      </span>
                    </td>
                    {hook && <td className={num}>{formatCount(p.shared)}</td>}
                    {hook && <td className={`${num} !pr-0`}>{formatCount(p.downloaded)}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {ranking && ranking.fewer > 0 && ranking.min > 1 && (
        <p className="text-xs mt-3" style={{ color: 'var(--text-muted)' }} data-testid="plays-fewer">
          {ranking.fewer} more {ranking.fewer === 1 ? 'droplet has' : 'droplets have'} fewer than {ranking.min} plays of this video.{' '}
          <button type="button" onClick={() => setMin(1)} className="underline hover-text">Rank them too</button>
        </p>
      )}
    </PageContainer>
  );
}
