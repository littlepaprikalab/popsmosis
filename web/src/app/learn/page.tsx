'use client';

import { useEffect, useState, useRef, useCallback, useMemo, memo, Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams, useRouter } from 'next/navigation';
import { LightBulbIcon } from '@heroicons/react/24/outline';
import { api, Question, Answer } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useGuardedSubmit } from '@/lib/useGuardedSubmit';
import { usePolling } from '@/lib/usePolling';
import { timeAgo } from '@/lib/time';
import { LoadingMessage } from '@/components/LoadingMessage';
import PageContainer from '@/components/PageContainer';
import Markdown from '@/components/Markdown';
import AttachmentList from '@/components/AttachmentList';
import { useToast } from '@/components/Toast';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { UserAvatar, AgentAvatar } from '@/components/UserAvatar';
import { ReplyThread } from '@/components/ReplyThread';
import { CollapsibleCard, CollapseButton } from '@/components/CollapsibleCard';
import { ServerUnavailable } from '@/components/ServerUnavailable';
import { LessonVideos } from '@/components/DropletPlayers';
import { DropletBreakdown } from '@/components/DropletBreakdown';

const PAGE_SIZE = 20;
const POLL_MS = 10_000;
// Cards that are waiting on the teacher (a topic with no lesson yet, or a thread
// where a human spoke last) re-fetch themselves on this interval and update in place,
// so the answer or reply appears without the global refresh banner.
const ARMED_POLL_MS = 15_000;
const QUESTION_MIN = 3; // a topic can be one short word: "DNA"
const QUESTION_MAX = 2000;
// The videos start rendering a few minutes after the lesson is posted (docs/PLAN.md,
// section 5), so a lesson this new with no videos yet is watched for them too.
const VIDEO_WATCH_MS = 15 * 60_000;

/**
 * A card the armed poll re-fetches: a topic with no lesson yet, a thread where a
 * human spoke last, videos still rendering, or a lesson new enough that its videos
 * may be about to start.
 */
function isArmed(q: Question, now = Date.now()): boolean {
  if (q.status === 'PENDING' || q.answer?.awaitingAgent) return true;
  const answer = q.answer;
  if (!answer) return false;
  if (answer.video?.status === 'RENDERING') return true;
  return !answer.video && now - new Date(answer.createdAt).getTime() < VIDEO_WATCH_MS;
}

/** Whether a re-fetched card differs from the one on screen in anything a card shows. */
function videoKey(a: Answer | null): string {
  const v = a?.video;
  const bill = a?.breakdown ? `${a.breakdown.steps.length}|${a.breakdown.seconds}` : 'no-bill';
  return v ? [v.status, v.songVideoUrl, v.lessonVideoUrl, v.songPosterUrl, v.lessonPosterUrl, v.error, a?.hidden, bill].join('|') : `none|${a?.hidden}|${bill}`;
}

export default function LearnPage() {
  return (
    <Suspense fallback={<LoadingMessage />}>
      <LearnContent />
    </Suspense>
  );
}

/** Newest createdAt across questions, answers, and replies: what /status reports. */
function latestTimestamp(questions: Question[]): string | null {
  let max: string | null = null;
  const bump = (t: string) => { if (!max || t > max) max = t; };
  for (const q of questions) {
    bump(q.createdAt);
    if (q.answer) {
      bump(q.answer.createdAt);
      for (const r of q.answer.replies) bump(r.createdAt);
    }
  }
  return max;
}

/**
 * GFM tables as plain cells: each delimiter row goes, and so do the pipes of the
 * header above it and the body below it (to the first blank line). Anchoring on
 * the delimiter row catches tables written without outer pipes while leaving a
 * pipe in ordinary prose alone.
 */
function plainTables(text: string): string {
  const lines = text.split('\n');
  const unpipe = (line: string) => line.replace(/\|/g, ' ');
  for (let i = 1; i < lines.length; i++) {
    const row = lines[i];
    if (!/^[ \t\r|:-]+$/.test(row) || !row.includes('|') || !row.includes('-')) continue;
    lines[i] = '';
    lines[i - 1] = unpipe(lines[i - 1]);
    for (let j = i + 1; j < lines.length && lines[j].trim(); j++) lines[j] = unpipe(lines[j]);
  }
  return lines.join('\n');
}

/** A markdown-free, single-line lead-in for a collapsed card. */
function snippetOf(markdown: string, max = 240): string {
  const blocks = markdown
    // Strip from a bounded slice. A lesson runs to 5000 characters and the
    // link pattern below is quadratic in unmatched brackets — a lot of work for
    // a 240-character teaser. Markdown only ever shrinks the text, so a slice
    // this size fills the budget for anything short of wall-to-wall link syntax.
    .slice(0, max * 8)
    .replace(/```[\s\S]*?```/g, ' ')            // fenced code blocks
    .replace(/```[\s\S]*$/, ' ')                // ...and any fence the slice cut open
    .replace(/`([^`]*)`/g, '$1')                 // inline code
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')  // links and images
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')          // headings
    .replace(/^\s{0,3}>\s?/gm, '');              // block quotes
  const text = plainTables(blocks)
    .replace(/^\s{0,3}[-*+]\s+/gm, '')           // bullets (numbered lists keep their number)
    .replace(/\*\*|__|\*/g, '')                  // emphasis
    .replace(/\s+/g, ' ')
    .trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

const STATUS_BADGES: Record<Question['status'], { label: string; color: string }> = {
  PENDING: { label: 'BREWING', color: 'var(--accent-yellow)' },
  ANSWERED: { label: 'READY', color: 'var(--accent-green)' },
  FAILED: { label: 'FAILED', color: 'var(--accent-red)' },
};

/**
 * One topic request, its lesson, and the follow-ups under it. Memoized on purpose: the armed poll hands
 * back a fresh questions array every 15s, so without this every card on the
 * page — markdown, thread and all — re-renders on a tick that changed one of
 * them. Callers must pass stable callbacks for that to hold.
 */
const QuestionCard = memo(function QuestionCard({ q, index, isAdmin, onAnswerChange, onRequestDelete, onSetHidden }: {
  q: Question;
  index: number;
  isAdmin: boolean;
  onAnswerChange: (questionId: string, answer: Answer) => void;
  onRequestDelete: (questionId: string) => void;
  onSetHidden: (questionId: string, answer: Answer, hidden: boolean) => void;
}) {
  const answer = q.answer;
  // Only re-derived when the answer text itself changes; a re-render must not
  // pay to strip markdown for a teaser that is hidden while the card is open.
  const snippet = useMemo(() => (answer ? snippetOf(answer.content) : ''), [answer?.content]);

  return (
    <CollapsibleCard
      testId="question-card"
      className="animate-fade-in"
      style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', animationDelay: `${index * 60}ms` }}
      headerClassName="px-4 pt-4 pb-3"
      header={(collapsed) => (
        <>
          {/* Header */}
          <div className="flex items-center justify-between mb-2 gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <UserAvatar name={q.user.name} avatarUrl={q.user.avatarUrl} />
              <span className="text-xs font-bold truncate" style={{ color: 'var(--text-secondary)' }}>
                {q.user.name || 'someone'}
              </span>
              <span className="badge-human text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold">human</span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-[10px] uppercase font-bold" style={{ color: STATUS_BADGES[q.status].color }}>
                {STATUS_BADGES[q.status].label}
              </span>
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{timeAgo(q.createdAt)}</span>
              {q.canDelete && (
                <button onClick={(e) => { e.stopPropagation(); onRequestDelete(q.id); }} className="text-[10px] uppercase tracking-wider hover-text" style={{ color: 'var(--text-muted)' }}>
                  delete
                </button>
              )}
            </div>
          </div>

          {/* The request — clamped while collapsed so a folded card stays one glance */}
          <p className={`text-sm whitespace-pre-wrap break-words ${collapsed ? 'line-clamp-3' : ''}`} style={{ color: 'var(--text-primary)' }}>{q.content}</p>
          {!collapsed && <AttachmentList attachments={q.attachments} />}
        </>
      )}
      preview={answer ? (
        <>
          <p className="text-sm line-clamp-3" style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }} data-testid="answer-preview">
            <span className="font-bold" style={{ color: 'var(--accent-blue)' }}>{answer.agent.name}:</span>{' '}
            &ldquo;{snippet}&rdquo;
          </p>
          <p className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
            {answer.replies.length === 0
              ? 'Tap to read the lesson'
              : `${answer.replies.length === 1 ? '1 follow-up' : `${answer.replies.length} follow-ups`} — tap to read the thread`}
          </p>
        </>
      ) : undefined}
    >
      {(collapse) => (
        <>
          {/* Lesson */}
          {answer ? (
            <>
              <div className="rounded-md p-3" style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}>
                <div className="flex items-center gap-2 mb-2">
                  <AgentAvatar name={answer.agent.name} />
                  <span className="text-xs font-bold" style={{ color: 'var(--text-secondary)' }}>{answer.agent.name}</span>
                  <span className="badge-agent text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold">agent</span>
                  <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{timeAgo(answer.createdAt)}</span>
                  {answer.model && <span className="text-[10px] ml-auto truncate" style={{ color: 'var(--text-muted)' }}>{answer.model}</span>}
                </div>
                <div className="md text-sm" style={{ color: 'var(--text-primary)' }}>
                  <Markdown>{answer.content}</Markdown>
                </div>
                <LessonVideos video={answer.video} />
                {answer.video?.status === 'READY' && (
                  <p className="text-[10px] mt-2 flex items-center gap-3" style={{ color: 'var(--text-muted)' }}>
                    {answer.hidden ? <span>Hidden from the Droplets tab.</span> : <Link href={`/droplets/${answer.id}`} className="underline hover-text">On the public Droplets tab</Link>}
                    {isAdmin && (
                      <button onClick={() => onSetHidden(q.id, answer, !answer.hidden)} className="uppercase tracking-wider hover-text"
                        style={{ color: answer.hidden ? 'var(--accent-green)' : 'var(--accent-red)' }} data-testid="toggle-hidden">
                        {answer.hidden ? 'put back' : 'hide'}
                      </button>
                    )}
                  </p>
                )}
                {answer.breakdown && <DropletBreakdown breakdown={answer.breakdown} video={answer.video} />}
                <ReplyThread answer={answer} onChange={(a) => onAnswerChange(q.id, a)} />
              </div>
              <div className="flex justify-end mt-2">
                <CollapseButton onClick={collapse} />
              </div>
            </>
          ) : q.status === 'FAILED' ? (
            <p className="text-xs" style={{ color: 'var(--text-muted)' }} data-testid="failed">
              The teacher couldn&rsquo;t make this droplet{q.failedReason ? `: ${q.failedReason}` : '.'} Ask again, or try the topic another way.
            </p>
          ) : (
            <p className="text-xs flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }} data-testid="waiting">
              <span className="inline-block w-1.5 h-1.5 rounded-full animate-live-pulse" style={{ background: 'var(--accent-cyan)' }} />
              The teacher is preparing this droplet… this card refreshes itself.
            </p>
          )}
        </>
      )}
    </CollapsibleCard>
  );
});

function LearnContent() {
  const { user, loading: authLoading, unreachable, retry } = useAuth();
  const { showToast } = useToast();
  const searchParams = useSearchParams();
  const router = useRouter();
  const mine = searchParams.get('mine') === '1';

  const [questions, setQuestions] = useState<Question[]>([]);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const [agentLastSeenAt, setAgentLastSeenAt] = useState<string | null | undefined>(undefined);
  const [newQuestion, setNewQuestion] = useState('');
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const { submitting, guard } = useGuardedSubmit();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const latestSeenRef = useRef<string | null>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const setMine = useCallback((v: boolean) => {
    router.replace(v ? '/learn?mine=1' : '/learn', { scroll: false });
  }, [router]);

  const load = useCallback(async () => {
    setLoading(true);
    setHasNew(false);
    latestSeenRef.current = null;
    try {
      const params: Record<string, string> = { limit: String(PAGE_SIZE) };
      if (mine) params.mine = 'true';
      const [res, status] = await Promise.all([api.listQuestions(params), api.getStatus(mine).catch(() => null)]);
      setQuestions(res.data);
      setCursor(res.nextCursor);
      latestSeenRef.current = latestTimestamp(res.data);
      if (status) setAgentLastSeenAt(status.agentLastSeenAt);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load topics', 'error');
    } finally {
      setLoading(false);
    }
  }, [mine, showToast]);

  useEffect(() => {
    if (authLoading) return;
    if (!user) { setLoading(false); return; }
    load();
  }, [authLoading, user, load]);

  // Ask the cheap status endpoint; when something newer exists, show the banner.
  // Localized refresh: only the topics that are waiting on the teacher.
  const armedIds = questions.filter((q) => isArmed(q)).map((q) => q.id);
  const armedKey = armedIds.join(',');
  const questionsRef = useRef(questions);
  questionsRef.current = questions;
  usePolling(() => {
    const ids = questionsRef.current.filter((q) => isArmed(q)).map((q) => q.id);
    if (ids.length === 0) return;
    // The API caps ?ids= at 50; chunk so every armed card refreshes, not just the first 50.
    const chunks: string[][] = [];
    for (let i = 0; i < ids.length; i += 50) chunks.push(ids.slice(i, i + 50));
    return Promise.all(chunks.map((c) => api.getQuestionsByIds(c).catch(() => null))).then((results) => {
      const updated = new Map(results.flatMap((r) => r?.data ?? []).map((q) => [q.id, q]));
      if (updated.size === 0) return;
      setQuestions((prev) => prev.map((q) => {
        const next = updated.get(q.id);
        if (!next) return q;
        // Only swap when something actually changed, so the card does not re-animate.
        const changed = next.status !== q.status
          || (next.answer?.id ?? null) !== (q.answer?.id ?? null)
          || (next.answer?.replies.length ?? 0) !== (q.answer?.replies.length ?? 0)
          || next.answer?.awaitingAgent !== q.answer?.awaitingAgent
          || videoKey(next.answer) !== videoKey(q.answer);
        return changed ? next : q;
      }));
      // Keep the status watermark current so our own localized updates do not trip the banner.
      for (const q of updated.values()) {
        const ts = latestTimestamp([q]);
        if (ts && (!latestSeenRef.current || ts > latestSeenRef.current)) latestSeenRef.current = ts;
      }
    });
  }, ARMED_POLL_MS, !!user && armedKey.length > 0);

  // Hand the server our watermark: it only has to look at what came after it,
  // and anything it finds is by definition new to us.
  usePolling(() => api.getStatus(mine, latestSeenRef.current).then((res) => {
    if (res.latestAt && latestSeenRef.current && res.latestAt > latestSeenRef.current) setHasNew(true);
    setAgentLastSeenAt(res.agentLastSeenAt);
  }).catch(() => {}), POLL_MS, !!user);

  // How long since the teacher last called the API. Past two hours we say so,
  // so a stalled runner is visible to everyone, not just on the admin page.
  const teacherStale = agentLastSeenAt !== undefined && (!agentLastSeenAt || Date.now() - new Date(agentLastSeenAt).getTime() > 2 * 60 * 60 * 1000);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const params: Record<string, string> = { limit: String(PAGE_SIZE), cursor };
      if (mine) params.mine = 'true';
      const res = await api.listQuestions(params);
      setQuestions((prev) => [...prev, ...res.data]);
      setCursor(res.nextCursor);
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to load more', 'error');
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, loadingMore, mine, showToast]);

  // Infinite scroll: when the sentinel under the list comes into view, fetch
  // the next page. The Load more button stays as a fallback (keyboard, no IO).
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !cursor || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMoreRef.current();
    }, { rootMargin: '400px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [cursor]);

  const autoResize = () => {
    const ta = textareaRef.current;
    if (ta) { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight}px`; }
  };

  const handleAsk = async () => {
    const content = newQuestion.trim();
    if (content.length < QUESTION_MIN) return;
    try {
      await guard(async () => {
        const q = await api.askQuestion(content);
        setQuestions((prev) => [q, ...prev]);
        if (!latestSeenRef.current || q.createdAt > latestSeenRef.current) latestSeenRef.current = q.createdAt;
        setNewQuestion('');
        if (textareaRef.current) textareaRef.current.style.height = 'auto';
        showToast('Topic sent. The teacher picks it up on its next pass.', 'success');
      });
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to send topic', 'error');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteQuestion(id);
      setQuestions((prev) => prev.filter((q) => q.id !== id));
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to delete topic', 'error');
    }
  };

  // Stable identity: QuestionCard is memoized, and a callback rebuilt each
  // render would re-render every card on the page anyway.
  const updateAnswer = useCallback((questionId: string, answer: Answer) => {
    setQuestions((prev) => prev.map((q) => (q.id === questionId ? { ...q, answer } : q)));
    const last = answer.replies[answer.replies.length - 1];
    if (last && (!latestSeenRef.current || last.createdAt > latestSeenRef.current)) latestSeenRef.current = last.createdAt;
  }, []);

  const setHidden = useCallback(async (questionId: string, answer: Answer, hidden: boolean) => {
    try {
      await api.setDropletHidden(answer.id, hidden);
      setQuestions((prev) => prev.map((q) => (q.id === questionId && q.answer ? { ...q, answer: { ...q.answer, hidden } } : q)));
      showToast(hidden ? 'Taken off the Droplets tab.' : 'Back on the Droplets tab.', 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to change it', 'error');
    }
  }, [showToast]);

  if (authLoading) return <LoadingMessage />;

  if (unreachable) return <ServerUnavailable onRetry={retry} />;

  if (!user) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
        <div className="text-center p-8 rounded-lg max-w-md animate-fade-in"
          style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <h1 className="text-2xl font-bold mb-2 tracking-wider" style={{ color: 'var(--accent-red)' }}>POPSMOSIS</h1>
          <p className="text-sm mb-1 font-bold" style={{ color: 'var(--text-primary)' }}>
            Droplets for learning
          </p>
          <p className="text-sm mb-6" style={{ color: 'var(--text-secondary)' }}>
            Ask for any topic. An AI teacher makes you a short lesson, and you can ask follow-ups under it.
          </p>
          <Link href="/auth/callback?returnTo=%2Flearn"
            className="inline-block px-6 py-3 rounded font-bold text-sm uppercase tracking-wider transition-colors"
            style={{ background: 'var(--accent-green)', color: 'var(--btn-filled-text)' }}>
            Sign in with Google
          </Link>
          <p className="text-xs mt-6" style={{ color: 'var(--text-muted)' }}>
            Topics and lessons are visible to signed-in members. Finished droplets are free to watch on the{' '}
            <Link href="/droplets" className="underline hover-text">Droplets tab</Link>, no sign-in needed.
          </p>
        </div>
      </div>
    );
  }

  const canAsk = newQuestion.trim().length >= QUESTION_MIN && !submitting;

  return (
    <PageContainer className="py-6">
      <ConfirmDialog
        open={!!pendingDelete}
        title="Delete topic"
        message="Delete this topic request? This cannot be undone."
        confirmLabel="Delete"
        variant="danger"
        onConfirm={() => { if (pendingDelete) handleDelete(pendingDelete); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />

      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">LEARN</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Ask for any topic. The teacher makes each one a droplet, a short lesson; ask follow-ups under it to go deeper.
        </p>
        {agentLastSeenAt !== undefined && (
          <p className="text-[10px] uppercase tracking-wider mt-2 flex items-center gap-1.5" style={{ color: teacherStale ? 'var(--accent-orange)' : 'var(--text-muted)' }}>
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${teacherStale ? '' : 'animate-liveness-pulse'}`}
              style={{ background: teacherStale ? 'var(--accent-orange)' : 'var(--accent-green)' }} />
            {agentLastSeenAt ? `Teacher last active ${timeAgo(agentLastSeenAt)}` : 'Teacher has not connected yet'}
            {teacherStale && ' — lessons may be delayed'}
          </p>
        )}
      </div>

      {/* Ask */}
      <div className="p-4 rounded-lg mb-6"
        style={{ background: 'var(--bg-secondary)', border: '1px solid var(--accent-yellow)', boxShadow: 'var(--question-box-glow)' }}>
        <h3 className="text-xs font-bold uppercase tracking-wider mb-3 flex items-center gap-1.5" style={{ color: 'var(--accent-yellow)' }}>
          <LightBulbIcon className="w-4 h-4" />
          WHAT DO YOU WANT TO LEARN?
        </h3>
        <div className="flex gap-3 items-start">
          <textarea
            ref={textareaRef}
            rows={2}
            value={newQuestion}
            maxLength={QUESTION_MAX}
            onChange={(e) => { setNewQuestion(e.target.value); autoResize(); }}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); handleAsk(); } }}
            placeholder="A topic, e.g. ATP synthesis"
            className="flex-1 text-sm outline-none px-3 py-2 rounded resize-none"
            style={{ border: '1px solid var(--border)', color: 'var(--text-primary)', background: 'var(--bg-tertiary)', maxHeight: '40vh', overflowY: 'auto' }}
          />
          <button
            onClick={handleAsk}
            disabled={!canAsk}
            className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-colors"
            style={{
              background: canAsk ? 'var(--accent-green)' : 'var(--bg-tertiary)',
              color: canAsk ? 'var(--btn-filled-text)' : 'var(--text-muted)',
            }}
          >
            {submitting ? '...' : 'SUBMIT'}
          </button>
        </div>
        <p className="text-[10px] mt-2 flex justify-between" style={{ color: 'var(--text-muted)' }}>
          <span>No personal info. Min {QUESTION_MIN} characters. Enter adds a line; Ctrl+Enter (⌘+Enter) or Submit sends.</span>
          <span>{newQuestion.length}/{QUESTION_MAX}</span>
        </p>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-3 mb-4">
        <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}>Filter:</span>
        <button
          onClick={() => setMine(!mine)}
          className={`px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider transition-colors rounded-full border ${
            mine ? 'text-[var(--btn-filled-text)] border-transparent' : 'text-[var(--accent-purple)] border-[var(--accent-purple)] hover:bg-[var(--accent-purple)]/10'
          }`}
          style={mine ? { background: 'var(--accent-purple)' } : {}}
        >
          My Topics
        </button>
      </div>

      {hasNew && !loading && (
        <button
          onClick={load}
          className="w-full text-center text-xs font-medium py-2 px-3 rounded-md cursor-pointer transition-all duration-200 animate-slide-down hover:brightness-110 mb-4"
          style={{ background: 'rgba(50, 173, 230, 0.12)', border: '1px solid rgba(50, 173, 230, 0.3)', color: 'var(--accent-blue)' }}
        >
          &uarr; New activity &mdash; tap to refresh
        </button>
      )}

      {loading ? (
        <LoadingMessage />
      ) : questions.length === 0 ? (
        <div className="text-center py-20" style={{ color: 'var(--text-muted)' }}>
          <p>{mine ? 'You have not asked for a topic yet.' : 'No topics yet. Ask for the first one!'}</p>
          <p className="text-xs mt-2">Use the form above. The teacher picks up new topics on its next pass.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {questions.map((q, i) => (
            <QuestionCard
              key={q.id}
              q={q}
              index={i}
              isAdmin={user.role === 'ADMIN'}
              onAnswerChange={updateAnswer}
              onRequestDelete={setPendingDelete}
              onSetHidden={setHidden}
            />
          ))}
        </div>
      )}

      {cursor && !loading && (
        <div ref={sentinelRef} className="text-center mt-6" data-testid="load-more">
          <button onClick={loadMore} disabled={loadingMore}
            className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-colors"
            style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
      {!cursor && !loading && questions.length >= PAGE_SIZE && (
        <p className="text-center text-[10px] uppercase tracking-wider mt-6" style={{ color: 'var(--text-muted)' }}>End of list</p>
      )}
    </PageContainer>
  );
}
