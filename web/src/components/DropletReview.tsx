'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type ArtFindings, type ArtSceneReview, type DropletReview as Review, type FactcheckLine } from '@/lib/api';

/** How each kind of the art checker's finding reads on the page. */
const FINDINGS: { key: keyof ArtFindings; label: string }[] = [
  { key: 'notes', label: 'teaches something false' },
  { key: 'labels', label: 'label wrong' },
  { key: 'misplaced', label: 'missing or out of place' },
  { key: 'unnamed', label: 'unnamed thing' },
  { key: 'text', label: 'writing in it' },
  { key: 'wrong', label: 'wrong' },
  { key: 'unfounded', label: 'note dropped (names no fact)' },
];

const VERDICT_COLORS: Record<string, string> = {
  SUPPORTED: 'var(--accent-green)',
  FILLER: 'var(--text-muted)',
  UNSUPPORTED: 'var(--accent-red)',
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

type Piece = { text: string; change: 'same' | 'removed' | 'added' };

/**
 * A fix as one passage, the words it took out and put in marked: a correction to a scene's prompt changes three
 * words of a hundred, which two whole passages bury. A longest-common-subsequence diff by word, small enough for a
 * fact-check's passages (a few hundred words at most).
 */
export function wordDiff(before: string, after: string): Piece[] {
  // Words with the space after them, compared without it.
  const a = before.match(/\S+\s*/g) ?? [];
  const b = after.match(/\S+\s*/g) ?? [];
  const same = (x: string, y: string) => x.trim() === y.trim();
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) lcs[i][j] = same(a[i], b[j]) ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  }
  // Each stretch of changes reads as what it took out, then what it put in.
  const pieces: Piece[] = [];
  let removed = '';
  let added = '';
  const flush = () => {
    if (removed) pieces.push({ text: removed, change: 'removed' });
    if (added) pieces.push({ text: added, change: 'added' });
    removed = '';
    added = '';
  };
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && same(a[i], b[j])) {
      flush();
      const last = pieces[pieces.length - 1];
      if (last?.change === 'same') last.text += b[j];
      else pieces.push({ text: b[j], change: 'same' });
      i++;
      j++;
    } else if (j < b.length && (i === a.length || lcs[i][j + 1] >= lcs[i + 1][j])) added += b[j++];
    else removed += a[i++];
  }
  flush();
  return pieces;
}

function Fix({ before, after }: { before: string; after: string }) {
  return (
    <p className="pl-3 break-words" style={{ color: 'var(--text-muted)' }}>
      {wordDiff(shown(before), shown(after)).map((piece, i) => piece.change === 'same' ? <span key={i}>{piece.text}</span>
        : piece.change === 'removed' ? <del key={i} style={{ color: 'var(--accent-red)' }}>{piece.text}</del>
          : <ins key={i} className="no-underline font-semibold" style={{ color: 'var(--accent-green)' }}>{piece.text}</ins>)}
    </p>
  );
}
// `?? []`: an API deployed before a kind was added (unfounded, Nov 3) leaves it out.
const findingsOf = (f: ArtFindings) => FINDINGS.flatMap(({ key, label }) => (f[key] ?? []).map((text) => ({ label, text })));
const shown = (text: string) => text.replace(/\n/g, ' / '); // a couplet on one line

function Section({ title, summary, children }: { title: string; summary: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h4 className="flex flex-wrap items-baseline gap-x-2 text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-secondary)' }}>
        {title}
        <span className="normal-case tracking-normal font-normal" style={{ color: 'var(--text-muted)' }}>{summary}</span>
      </h4>
      {children}
    </section>
  );
}

function Missing({ children }: { children: React.ReactNode }) {
  return <p className="italic" style={{ color: 'var(--text-muted)' }}>{children}</p>;
}

function FactcheckSection({ factcheck }: { factcheck: Review['factcheck'] }) {
  if (!factcheck) return <Section title="Fact-check" summary=""><Missing>No fact-check: this lesson was posted without one.</Missing></Section>;
  const { counts, corrections, lines } = factcheck;
  const rounds = factcheck.rounds !== null ? ` in ${plural(factcheck.rounds, 'round')}` : '';
  return (
    <Section title="Fact-check" summary={`${factcheck.status}${rounds} · ${counts.supported} supported · ${counts.filler} filler · ${counts.unsupported} unsupported`}>
      {factcheck.reason && <p style={{ color: 'var(--text-secondary)' }}>{factcheck.reason}</p>}
      {corrections.length === 0 ? <Missing>Nothing fixed.</Missing> : (
        <ul className="space-y-2" data-testid="review-corrections">
          {corrections.map((c, i) => (
            <li key={i}>
              <p style={{ color: 'var(--text-secondary)' }}>
                <span style={{ color: 'var(--text-muted)' }}>round {c.round ?? '?'} · {c.kind} {c.ids.join(', ')}:</span> {c.problem}
              </p>
              <Fix before={c.before} after={c.after} />
            </li>
          ))}
        </ul>
      )}
      {lines.length > 0 && (
        <details>
          <summary className="cursor-pointer underline underline-offset-2" style={{ color: 'var(--text-muted)' }}>Every line judged ({lines.length})</summary>
          <div className="mt-1 overflow-x-auto">
            <table className="w-full border-collapse">
              <tbody>
                {lines.map((l: FactcheckLine, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td className="py-1 pr-2 align-top whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>{l.kind} {l.id}</td>
                    <td className="py-1 pr-2 align-top whitespace-nowrap font-semibold" style={{ color: VERDICT_COLORS[l.verdict ?? ''] ?? 'var(--text-muted)' }}>
                      {l.verdict?.toLowerCase() ?? '—'}
                    </td>
                    <td className="py-1 align-top break-words" style={{ color: 'var(--text-secondary)' }}>
                      {shown(l.text)}
                      {l.problem && <span className="block" style={{ color: 'var(--text-muted)' }}>{l.problem}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </Section>
  );
}

function ChecksSection({ checks }: { checks: Review['checks'] }) {
  if (!checks) return <Section title="Code checks" summary=""><Missing>Not sent: made before the studio sent its code checks.</Missing></Section>;
  const open = checks.problems.filter((p) => !p.repaired);
  const repaired = checks.problems.length - open.length;
  return (
    <Section title="Code checks" summary={`${open.length} for the prompt · ${repaired} repaired by code`}>
      {open.length === 0 ? <Missing>Nothing left for the prompt to fix.</Missing> : (
        <ul className="space-y-0.5" data-testid="review-checks">
          {open.map((p, i) => (
            <li key={i} className="break-words" style={{ color: 'var(--text-secondary)' }}>
              <span className="font-semibold" style={{ color: 'var(--accent-orange)' }}>{p.rule}</span>
              {p.lineId && <span style={{ color: 'var(--text-muted)' }}> {p.lineId}</span>} {p.detail}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Scene({ scene }: { scene: ArtSceneReview }) {
  const found = scene.check ? findingsOf(scene.check) : [];
  const refused = scene.tries.filter((t) => t.try !== scene.kept);
  return (
    <figure className="m-0 rounded-md overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }} data-testid="review-scene">
      {scene.imageUrl ? (
        <a href={scene.imageUrl} target="_blank" rel="noopener noreferrer" className="block">
          {/* eslint-disable-next-line @next/next/no-img-element -- served by the API to admins, never through Next's optimiser */}
          <img src={scene.imageUrl} alt={`The ${scene.scene} picture`} loading="lazy" className="block w-full aspect-[7/4] object-cover" />
        </a>
      ) : (
        <div className="w-full aspect-[7/4] grid place-items-center text-[10px]" style={{ color: 'var(--text-muted)' }}>no picture to show</div>
      )}
      <figcaption className="p-2 space-y-1">
        <p className="flex justify-between gap-2">
          <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>{scene.scene}</span>
          <span style={{ color: 'var(--text-muted)' }}>
            {scene.model ? `${scene.model}${scene.tries.length > 1 && scene.kept ? `, try ${scene.kept} of ${scene.tries.length}` : ''}` : 'drawn by the brain'}
          </span>
        </p>
        {!scene.check ? (
          <p style={{ color: 'var(--text-muted)' }}>Not looked at by the checker.</p>
        ) : found.length === 0 ? (
          <p style={{ color: 'var(--accent-green)' }}>Checker: nothing wrong</p>
        ) : (
          <ul className="space-y-0.5">
            {found.map((f, i) => (
              <li key={i} className="break-words" style={{ color: 'var(--text-secondary)' }}>
                <span className="font-semibold" style={{ color: 'var(--accent-red)' }}>{f.label}:</span> {f.text}
              </li>
            ))}
          </ul>
        )}
        {refused.length > 0 && (
          <ul className="space-y-0.5" style={{ color: 'var(--text-muted)' }}>
            {refused.map((t, i) => {
              const why = t.declined ? `declined by the painter: ${t.declined}`
                : t.redrawFailed ? `redraw failed, painted afresh: ${t.redrawFailed}`
                  : findingsOf(t).map((f) => `${f.label}: ${f.text.replace(/\.$/, '')}`).join('; ') || 'refused';
              return <li key={i} className="break-words">Try {t.try ?? '?'} {t.declined ? '' : 'repainted, '}{why}</li>;
            })}
          </ul>
        )}
      </figcaption>
    </figure>
  );
}

function ArtSection({ art }: { art: Review['art'] }) {
  if (!art) return <Section title="Pictures" summary=""><Missing>No art checks yet: they come with the videos, once they are ready.</Missing></Section>;
  const flagged = art.scenes.filter((s) => s.check && findingsOf(s.check).length > 0).length;
  const repainted = art.scenes.filter((s) => s.tries.length > 1).length;
  return (
    <Section title="Pictures" summary={`${plural(art.scenes.length, 'scene')} · ${flagged} flagged · ${repainted} repainted`}>
      <div className="grid gap-2 grid-cols-1 sm:grid-cols-2">
        {art.scenes.map((s) => <Scene key={s.scene} scene={s} />)}
      </div>
    </Section>
  );
}

/**
 * What the studio's checks found in a droplet, for an admin: the fact-check's verdicts and fixes, the code checks on
 * the bundle, and the art checker's look at each picture beside it. Folded under the lesson, and fetched only once
 * opened (GET /api/answers/:id/review): a page of cards should not load every droplet's checks and pictures. Only
 * an admin's page shows it, and only an admin can read it.
 */
export function DropletReview({ answerId }: { answerId: string }) {
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.getDropletReview(answerId).then(setReview, (e: unknown) => setError(e instanceof Error ? e.message : 'The review did not load'));
  }, [answerId]);
  useEffect(() => {
    if (open && !review && !error) load();
  }, [open, review, error, load]);

  return (
    <details className="mt-3 text-[11px] group" data-testid="droplet-review" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="group/summary cursor-pointer select-none flex items-center gap-2 flex-wrap list-none [&::-webkit-details-marker]:hidden"
        style={{ color: 'var(--text-muted)' }}>
        <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--accent-purple)' }}>
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden="true"
            className="shrink-0 -rotate-90 group-open:rotate-0 transition-transform duration-200">
            <path d="M4 6L8 10L12 6" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Review
        </span>
        <span>What the checks found · admins only</span>
        <span className="text-[10px] underline underline-offset-2 group-hover/summary:text-[var(--text-hover)] group-open:hidden">show</span>
        <span className="text-[10px] underline underline-offset-2 group-hover/summary:text-[var(--text-hover)] hidden group-open:inline">hide</span>
      </summary>
      {open && (
        <div className="mt-2 rounded-md p-2 space-y-3" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          {error ? (
            <p style={{ color: 'var(--accent-red)' }}>
              {error}{' '}
              <button onClick={load} className="uppercase tracking-wider underline hover-text" style={{ color: 'var(--text-secondary)' }}>try again</button>
            </p>
          ) : !review ? (
            <p style={{ color: 'var(--text-muted)' }}>Loading the checks…</p>
          ) : (
            <>
              <p style={{ color: 'var(--text-muted)' }}>
                The studio&rsquo;s own checks. They miss things, the pictures&rsquo; science most of all: look at each picture yourself.
              </p>
              <FactcheckSection factcheck={review.factcheck} />
              <ChecksSection checks={review.checks} />
              <ArtSection art={review.art} />
            </>
          )}
        </div>
      )}
    </details>
  );
}
