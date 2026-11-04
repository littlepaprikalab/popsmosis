'use client';

import { useEffect, useLayoutEffect, useState, useRef, useCallback, useMemo, memo, Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams, useRouter } from 'next/navigation';
import { LightBulbIcon } from '@heroicons/react/24/outline';
import { api, ApiError, isMissingRoute, Question, Answer, type TopicAllowance } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useGuardedSubmit } from '@/lib/useGuardedSubmit';
import { usePolling } from '@/lib/usePolling';
import { timeAgo, clockLabel } from '@/lib/time';
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
import { DropletBreakdown, progressCount } from '@/components/DropletBreakdown';
import { Drop, DropletMeter, hasStarted, USUAL_MINUTES } from '@/components/DropletMeter';
import { DropletReview } from '@/components/DropletReview';
import { ExampleDroplet } from '@/components/ExampleDroplet';
import { DropletPill, Marked, SongChip } from '@/components/Highlights';
import { MIN_AGE } from '@/lib/legal';
import { inkmoryRecordsUrl } from '@/lib/inkmory';
import { topicDraft } from '@/lib/topicDraft';
import { BRAIN_MODELS, brainLabel, brainOptionsFor, rememberedBrain, rememberBrain, type Brain } from '@/lib/brains';

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
const SIGN_IN_HREF = '/auth/callback?returnTo=%2Flearn';

/**
 * A card the armed poll re-fetches: a topic with no lesson yet, a thread where a
 * human spoke last, a lesson waiting for its videos, videos still rendering, or a
 * lesson new enough that its videos may be about to start.
 */
function isArmed(q: Question, now = Date.now()): boolean {
  if (q.status === 'PENDING' || q.answer?.awaitingAgent || q.answer?.awaitingVideos) return true;
  const answer = q.answer;
  if (!answer) return false;
  if (answer.video?.status === 'RENDERING') return true;
  return !answer.video && now - new Date(answer.createdAt).getTime() < VIDEO_WATCH_MS;
}

/** Whether a re-fetched card differs from the one on screen in anything a card shows. */
function videoKey(a: Answer | null): string {
  const v = a?.video;
  const bill = a?.breakdown ? `${a.breakdown.steps.length}|${a.breakdown.seconds}` : 'no-bill';
  return v
    ? [v.status, v.songVideoUrl, v.lessonVideoUrl, v.songPosterUrl, v.lessonPosterUrl, v.error, a?.hidden, a?.awaitingReview, bill].join('|')
    : `none|${a?.hidden}|${bill}`;
}

/** The same for where the studio is with the droplet: the steps running and every line of the bill so far. */
function progressKey(q: Question): string {
  const p = q.progress;
  if (!p) return `none|${q.answer?.awaitingVideos}`;
  const bill = (p.breakdown?.steps ?? []).map((s) => `${s.step}:${s.tries}:${s.seconds}:${s.costUsd}:${s.models.length}`).join(',');
  return [p.running.map((r) => `${r.step}@${r.startedAt}`).join(','), p.failed.join(','), p.videos, bill, q.answer?.awaitingVideos].join('|');
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
 * What the teacher wrote about this topic on Inkmory: its journal entries and cycle logs from the
 * runs that worked on it, and its life story as it stood then (lib/inkmory.ts). Its record is
 * written at the end of each run, so a run still going has added nothing yet.
 */
function TeacherNotesLink({ href }: { href: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="underline hover-text" data-testid="teacher-notes"
      title="The teacher's journal, cycle logs and life story for this droplet, on Inkmory">
      Teacher&rsquo;s notes on Inkmory &#8599;
    </a>
  );
}

/**
 * What deleting a topic with a lesson takes: everything (the API's /everything route), or only the learner's request,
 * leaving the lesson on the Droplets tab naming no one (api/src/lib/privacy.ts). When people have liked or commented
 * on it, Everything says how many, so what it takes from them is plain before it is chosen.
 */
function DeleteChoice({ everything, onChange, typed, onType, likes = 0, comments = 0 }: {
  everything: boolean;
  onChange: (everything: boolean) => void;
  typed: string; // what is in the box Everything asks the word in
  onType: (typed: string) => void;
  likes?: number;
  comments?: number;
}) {
  const counted = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const taken = [likes ? counted(likes, 'like') : '', comments ? counted(comments, 'comment') : ''].filter(Boolean).join(' and ');
  const options = [
    {
      value: true, label: 'Everything', testId: 'delete-everything',
      detail: 'Your request, your files and the follow-ups, and the lesson: its text and videos, and the likes and '
        + 'comments people left on it. It leaves the Droplets tab.',
    },
    {
      value: false, label: 'Only my request', testId: 'delete-request-only',
      detail: 'Your words, your files and the follow-ups. The lesson stays on the public Droplets tab for others to '
        + 'learn from, with nothing linking it to you.',
    },
  ];
  return (
    <fieldset>
      <legend className="mb-3">What should go? This cannot be undone.</legend>
      <div className="space-y-2">
        {options.map((o) => (
          <label key={o.label} className="flex gap-2.5 items-start cursor-pointer rounded-lg p-2.5"
            style={{ border: `1px solid ${everything === o.value ? 'var(--accent-red)' : 'var(--border)'}` }}>
            <input type="radio" name="delete-scope" className="mt-1 shrink-0" style={{ accentColor: 'var(--accent-red)' }}
              checked={everything === o.value} onChange={() => onChange(o.value)} data-testid={o.testId} />
            <span>
              <span className="block font-semibold" style={{ color: 'var(--text-primary)' }}>{o.label}</span>
              <span className="block text-[13px]">{o.detail}</span>
              {o.value && taken && (
                <span className="block text-[13px] mt-1 font-semibold" style={{ color: 'var(--text-primary)' }} data-testid="delete-engagement">
                  This also deletes {taken} on it.
                </span>
              )}
            </span>
          </label>
        ))}
      </div>
      {/* Everything cannot be undone and takes what other people left on it: a word typed, not a click that slipped. */}
      {everything && (
        <div className="mt-3">
          <label htmlFor="delete-confirm-text" className="block mb-1.5 text-[13px]">
            To delete everything, type <strong style={{ color: 'var(--text-primary)' }}>{DELETE_WORD}</strong> below.
          </label>
          <input id="delete-confirm-text" data-testid="delete-confirm-text" value={typed} onChange={(e) => onType(e.target.value)}
            placeholder={DELETE_WORD} autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={20}
            className="w-full px-3 py-2 rounded text-sm outline-none focus:ring-1"
            style={{ background: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
        </div>
      )}
    </fieldset>
  );
}

/** The word Everything asks to be typed before it deletes; any case, so a phone's capital still counts. */
const DELETE_WORD = 'delete';
const typedDeleteWord = (typed: string) => typed.trim().toLowerCase() === DELETE_WORD;

/**
 * One topic request, its lesson, and the follow-ups under it. Memoized on purpose: the armed poll hands
 * back a fresh questions array every 15s, so without this every card on the
 * page — markdown, thread and all — re-renders on a tick that changed one of
 * them. Callers must pass stable callbacks for that to hold.
 */
/**
 * How many topics the learner may still ask for, and when the next comes back, in their own time zone. Each topic's
 * place comes back 24 hours after it was sent (api/src/lib/usage.ts), so there is no midnight to count to.
 */
function AllowanceNote({ allowance }: { allowance: TopicAllowance }) {
  const { perDay, left, nextAt, siteFull, siteOpensAt } = allowance;
  const when = (at: number | null) => (at ? clockLabel(at) : 'soon');
  let text: string;
  if (siteFull) text = `Popsmosis has made all the droplets it can for now. It takes topics again ${when(siteOpensAt)}.`;
  else if (perDay === 0) text = 'New topics are paused for now.';
  else if (left === 0) text = `0 of ${perDay} topics left. You can send another ${when(nextAt)}.`;
  else if (!nextAt) text = `${left} of ${perDay} topics left. Each comes back 24 hours after you send it.`;
  else text = `${left} of ${perDay} topics left. You get one more ${clockLabel(nextAt)}.`;
  const none = siteFull || left === 0;
  return (
    <p className="text-xs mt-1" aria-live="polite" data-testid="topic-allowance"
      style={{ color: none ? 'var(--accent-orange)' : 'var(--text-secondary)' }}>
      {text}
    </p>
  );
}

const QuestionCard = memo(function QuestionCard({ q, index, isAdmin, onAnswerChange, onRequestDelete, onSetHidden, onApprove }: {
  q: Question;
  index: number;
  isAdmin: boolean;
  onAnswerChange: (questionId: string, answer: Answer) => void;
  onRequestDelete: (questionId: string) => void;
  onSetHidden: (questionId: string, answer: Answer, hidden: boolean) => void;
  onApprove: (questionId: string, answer: Answer) => void;
}) {
  const answer = q.answer;
  // Only re-derived when the answer text itself changes; a re-render must not
  // pay to strip markdown for a teaser that is hidden while the card is open.
  const snippet = useMemo(() => (answer ? snippetOf(answer.content) : ''), [answer?.content]);
  const notesUrl = inkmoryRecordsUrl(q.id);
  // The lesson is posted, and waits for its videos: the card shows the bill filling in instead of the text,
  // so the droplet is met through its videos (api/src/lib/progress.ts).
  const awaiting = !!answer?.awaitingVideos && !!q.progress;
  const made = awaiting && q.progress ? progressCount(q.progress) : null;
  // Brewing until there is something to watch or read: a lesson waiting for its videos is not ready yet.
  const badge = STATUS_BADGES[awaiting ? 'PENDING' : q.status];
  // The brain picked for it on /learn: a learner sees only their own topics, an admin tells test topics apart by it.
  const brain = brainLabel(q.brain);

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
              {q.user ? (
                <>
                  <UserAvatar name={q.user.name} avatarUrl={q.user.avatarUrl} />
                  <span className="text-xs font-bold truncate" style={{ color: 'var(--text-secondary)' }}>
                    {q.user.name || 'someone'}
                  </span>
                  <span className="badge-human text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold">human</span>
                </>
              ) : (
                <span className="text-xs font-bold" style={{ color: 'var(--text-muted)' }}>request removed</span>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-[10px] uppercase font-bold" style={{ color: badge.color }}>
                {badge.label}
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
          {q.user ? (
            <p className={`text-sm whitespace-pre-wrap break-words ${collapsed ? 'line-clamp-3' : ''}`} style={{ color: 'var(--text-primary)' }}>{q.content}</p>
          ) : (
            <p className="text-sm italic" style={{ color: 'var(--text-muted)' }} data-testid="request-removed">
              The learner took this request back. Their words, files and follow-ups are deleted; the lesson stays.
            </p>
          )}
          {/* On a line of its own: the header row is full on a phone. */}
          {brain && (
            <p className="mt-1.5 text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              Brain:{' '}
              <span className="text-[9px] px-1.5 py-0.5 rounded-full font-semibold" data-testid="topic-brain"
                style={{ color: 'var(--accent-purple)', border: '1px solid var(--accent-purple)' }}>
                {brain}
              </span>
            </p>
          )}
          {!collapsed && <AttachmentList attachments={q.attachments} />}
        </>
      )}
      preview={made ? (
        <p className="text-[10px] flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }} data-testid="making-preview">
          <Drop state="now" className="w-2 h-3 shrink-0" />
          Making the videos: {made.done} of {made.total} steps done — tap to follow along
        </p>
      ) : answer ? (
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
          {answer && awaiting && q.progress ? (
            <>
              <div className="rounded-md p-3" style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }} data-testid="making">
                <div className="flex items-center gap-2">
                  <AgentAvatar name={answer.agent.name} />
                  <span className="text-xs font-bold" style={{ color: 'var(--text-secondary)' }}>{answer.agent.name}</span>
                  <span className="badge-agent text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold">agent</span>
                  {answer.model && <span className="text-[10px] ml-auto truncate" style={{ color: 'var(--text-muted)' }}>{answer.model}</span>}
                </div>
                <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }} data-testid="awaiting-videos">
                  The lesson is written and checked; the teacher is making its videos.
                </p>
                <DropletMeter progress={q.progress} />
                <DropletBreakdown breakdown={answer.breakdown} video={answer.video} progress={q.progress} endToEnd={answer.endToEnd} defaultOpen />
                {isAdmin && <DropletReview answerId={answer.id} />}
              </div>
              <div className="flex justify-end mt-2">
                <CollapseButton onClick={collapse} />
              </div>
            </>
          ) : answer ? (
            <>
              <div className="rounded-md p-3" style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}>
                <div className="flex items-center gap-2 mb-2">
                  <AgentAvatar name={answer.agent.name} />
                  <span className="text-xs font-bold" style={{ color: 'var(--text-secondary)' }}>{answer.agent.name}</span>
                  <span className="badge-agent text-[9px] uppercase px-1.5 py-0.5 rounded-full font-semibold">agent</span>
                  <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{timeAgo(answer.createdAt)}</span>
                  {answer.model && <span className="text-[10px] ml-auto truncate" style={{ color: 'var(--text-muted)' }}>{answer.model}</span>}
                </div>
                {/* The videos first: the droplet is them, and the text is what they teach, to read after. */}
                <LessonVideos answerId={answer.id} video={answer.video} tags={answer.tags} views={answer.views} />
                <div className={`md text-sm ${answer.video ? 'mt-3' : ''}`} style={{ color: 'var(--text-primary)' }}>
                  <Markdown>{answer.content}</Markdown>
                </div>
                {(answer.video?.status === 'READY' || notesUrl) && (
                  <p className="text-[10px] mt-2 flex flex-wrap items-center gap-x-3 gap-y-1" style={{ color: 'var(--text-muted)' }}>
                    {answer.video?.status === 'READY' && answer.awaitingReview && (
                      <>
                        <span data-testid="awaiting-review">
                          {isAdmin ? 'Waiting for review: not on the Droplets tab until an admin approves it.'
                            : 'It goes on the public Droplets tab once a person has looked at it.'}
                        </span>
                        {isAdmin && (
                          <button onClick={() => onApprove(q.id, answer)} className="uppercase tracking-wider hover-text"
                            style={{ color: 'var(--accent-green)' }} data-testid="approve-droplet">
                            approve
                          </button>
                        )}
                      </>
                    )}
                    {answer.video?.status === 'READY' && !answer.awaitingReview && (
                      <>
                        {answer.hidden ? <span>Hidden from the Droplets tab.</span> : <Link href={`/droplets/${answer.id}`} className="underline hover-text">On the public Droplets tab</Link>}
                        {isAdmin && (
                          <button onClick={() => onSetHidden(q.id, answer, !answer.hidden)} className="uppercase tracking-wider hover-text"
                            style={{ color: answer.hidden ? 'var(--accent-green)' : 'var(--accent-red)' }} data-testid="toggle-hidden">
                            {answer.hidden ? 'put back' : 'hide'}
                          </button>
                        )}
                      </>
                    )}
                    {notesUrl && <TeacherNotesLink href={notesUrl} />}
                  </p>
                )}
                {/* Still rendering after the text was shown (a second attempt, or an hour on): the bill keeps filling in under it. */}
                {(answer.breakdown || q.progress) && (
                  <DropletBreakdown breakdown={answer.breakdown} video={answer.video} progress={answer.video?.status === 'RENDERING' ? q.progress : null}
                    endToEnd={answer.endToEnd} />
                )}
                {/* What the studio's checks found, beside approve and hide: only an admin's page has it. */}
                {isAdmin && <DropletReview answerId={answer.id} />}
                <ReplyThread answer={answer} canReply={q.isOwner} onChange={(a) => onAnswerChange(q.id, a)} />
              </div>
              <div className="flex justify-end mt-2">
                <CollapseButton onClick={collapse} />
              </div>
            </>
          ) : q.status === 'FAILED' ? (
            <p className="text-xs" style={{ color: 'var(--text-muted)' }} data-testid="failed">
              The teacher couldn&rsquo;t make this droplet{q.failedReason ? `: ${q.failedReason}` : '.'} Ask again, or try the topic another way.
              {notesUrl && <span className="block mt-1.5 text-[10px]"><TeacherNotesLink href={notesUrl} /></span>}
            </p>
          ) : (
            <>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }} data-testid="waiting">
                {q.progress && !hasStarted(q.progress) ? 'Waiting for the teacher to start on it' : 'The teacher is preparing this droplet'}
                … this card refreshes itself.
              </p>
              {/* The minutes so far, and the bill as a checklist of what is to come, open once the studio has started on it. */}
              {q.progress && <DropletMeter progress={q.progress} />}
              {q.progress && (
                <DropletBreakdown breakdown={null} video={null} progress={q.progress}
                  defaultOpen={q.progress.running.length > 0 || !!q.progress.breakdown?.steps.length} />
              )}
            </>
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
  const isAdmin = user?.role === 'ADMIN';
  // The API shows a learner only their own topics (lib/privacy.ts); an admin sees everyone's unless they filter.
  const ownOnly = mine || !isAdmin;

  const [questions, setQuestions] = useState<Question[]>([]);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const [agentLastSeenAt, setAgentLastSeenAt] = useState<string | null | undefined>(undefined);
  const [newQuestion, setNewQuestion] = useState('');
  // The pick of the brain for the next topic (lib/brains.ts), remembered on this browser; null: the default.
  const [brain, setBrain] = useState<Brain | null>(null);
  const [defaultBrain, setDefaultBrain] = useState<Brain | null>(null); // the Studio page's, for the Default button
  // Whether a topic may go to a brain of its own (GET /api/questions/brains): false while an admin has the default make
  // every topic, when the picker is hidden and no pick is sent. Null until known, or when the API could not say.
  const [picksOpen, setPicksOpen] = useState<boolean | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  // For a topic with a lesson: whether the lesson goes too (the API's /everything route), or only the request.
  const [deleteLesson, setDeleteLesson] = useState(true);
  const [typedDelete, setTypedDelete] = useState(''); // the word Everything asks for (DeleteChoice)
  // Submit pressed signed out: the box asks them to sign in, keeping what they typed.
  const [askSignIn, setAskSignIn] = useState(false);
  const signInRef = useRef<HTMLAnchorElement>(null);
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
    if (!user) {
      // Signed out, or just logged out: nothing of a signed-in list stays behind to show, page or poll.
      setQuestions([]);
      setCursor(null);
      setHasNew(false);
      setAgentLastSeenAt(undefined);
      setLoading(false);
      return;
    }
    load();
  }, [authLoading, user, load]);

  const brainOptions = brainOptionsFor(isAdmin);
  const chosen = brainOptions.find((o) => o.value === brain);
  useEffect(() => {
    setBrain(rememberedBrain(isAdmin)); // read again once an admin is known, as Muse Wonder is theirs alone
    if (!isAdmin) return;
    api.getStudioSettings().then((s) => setDefaultBrain(s.brain.default)).catch(() => {}); // the note says less without it
  }, [isAdmin]);

  const readPicks = useCallback(() => {
    api.getBrainPicks().then((r) => setPicksOpen(r.open)).catch(() => {}); // the picker shows; the API drops a pick if it must
  }, []);
  useEffect(readPicks, [readPicks]);

  const pickBrain = (value: Brain | null) => {
    setBrain(value);
    rememberBrain(value);
  };

  // What the learner has left of their topics, shown in the box: read on sign-in, after each send, and again when
  // the next place comes back (or the site takes topics again), so it never goes on saying none are left.
  const [allowance, setAllowance] = useState<TopicAllowance | null>(null);
  const readAllowance = useCallback(() => {
    api.getTopicAllowance().then(setAllowance).catch(() => {}); // without it the box just says nothing of it
  }, []);
  useEffect(() => {
    if (authLoading) return;
    if (!user) { setAllowance(null); return; }
    readAllowance();
  }, [authLoading, user, readAllowance]);
  const opensAt = allowance && (allowance.siteFull ? allowance.siteOpensAt : allowance.nextAt);
  useEffect(() => {
    if (!opensAt) return;
    const timer = setTimeout(readAllowance, Math.max(0, opensAt - Date.now()) + 1000);
    return () => clearTimeout(timer);
  }, [opensAt, readAllowance]);

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
          || videoKey(next.answer) !== videoKey(q.answer)
          || progressKey(next) !== progressKey(q);
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

  // The box grows with what is in it: typed, brought back from before a sign-in, or cleared once sent.
  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (ta) { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight}px`; }
  }, [newQuestion, authLoading, unreachable]);

  // A topic typed before a sign-in (or a reload) is back in the box. Back from a sign-in that Submit asked for,
  // the page says so: it waits for Submit rather than sending itself, so nothing goes out unseen.
  useEffect(() => {
    const saved = topicDraft.read();
    if (saved) setNewQuestion((current) => current || saved);
  }, []);
  useEffect(() => {
    if (!user || !topicDraft.takeSignIn() || !topicDraft.read()) return;
    showToast('You are signed in. Your topic is still in the box: press Submit to send it.', 'success');
    textareaRef.current?.focus();
  }, [user, showToast]);
  // A logout empties the box too (its stored copy goes in lib/auth.tsx), for whoever uses this computer next.
  const signedIn = useRef(false);
  useEffect(() => {
    if (signedIn.current && !user) { setNewQuestion(''); setAskSignIn(false); }
    signedIn.current = !!user;
  }, [user]);

  const changeQuestion = (value: string) => {
    setNewQuestion(value);
    topicDraft.write(value);
  };

  // Hand the keyboard to the sign-in link the moment it shows.
  useEffect(() => { if (askSignIn) signInRef.current?.focus(); }, [askSignIn]);

  const handleAsk = async () => {
    const content = newQuestion.trim();
    if (content.length < QUESTION_MIN) return;
    if (!user) { setAskSignIn(true); return; }
    try {
      await guard(async () => {
        const picked = picksOpen === false ? null : chosen?.value ?? null;
        const q = await api.askQuestion(content, picked);
        const sentTo = q.brain === undefined ? picked : q.brain; // the API's word: a pick is dropped while picks are off
        setQuestions((prev) => [q, ...prev]);
        if (!latestSeenRef.current || q.createdAt > latestSeenRef.current) latestSeenRef.current = q.createdAt;
        changeQuestion('');
        // Too long to read in a few seconds, so it stays until the learner closes it; so does a refusal below. Each
        // Submit's word replaces the last one's.
        showToast(`Topic sent${sentTo ? ` to ${brainLabel(sentTo)}` : ''}. The teacher picks it up on its next pass; `
          + `a droplet takes about ${USUAL_MINUTES} minutes to make.`, 'success', { persistent: true, key: 'ask' });
        readAllowance();
        if (picked && !sentTo) readPicks(); // turned off since the page read it: the picker goes
      });
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to send topic', 'error', { persistent: true, key: 'ask' });
      if (err instanceof ApiError && err.status === 429) readAllowance(); // sent elsewhere since, or the limit changed
    }
  };

  // Stable identity, like the callbacks below: each card is handed it. Every opening starts at Everything.
  const requestDelete = useCallback((id: string) => {
    setDeleteLesson(true);
    setTypedDelete('');
    setPendingDelete(id);
  }, []);
  const handleDelete = async (id: string, everything: boolean) => {
    const hadLesson = questions.find((q) => q.id === id)?.status === 'ANSWERED';
    try {
      await api.deleteQuestion(id, hadLesson && everything, !hadLesson);
      setQuestions((prev) => prev.filter((q) => q.id !== id));
      if (hadLesson) {
        showToast(everything
          ? 'Deleted: your topic, its lesson and its videos.'
          : 'Your request is deleted. The lesson stays for others to learn from.', 'success');
      }
    } catch (err) {
      // An API from before deleting everything (mid-deploy, or rolled back) has no such route, so it deleted nothing.
      if (isMissingRoute(err)) {
        showToast('Deleting everything is not available for a moment, so nothing was deleted. Please try again in a few minutes.', 'error');
        return;
      }
      // The card showed it waiting, and its lesson landed before the next poll: nothing was deleted. Show the lesson's
      // card and ask again, with the choice.
      if (!hadLesson && err instanceof ApiError && err.status === 409) {
        showToast(err.message, 'info');
        try {
          const fresh = await api.getQuestion(id);
          setQuestions((prev) => prev.map((q) => (q.id === id ? fresh : q)));
          requestDelete(id);
        } catch {
          // The next poll brings the card up to date; the learner can delete it from there.
        }
        return;
      }
      showToast(err instanceof Error ? err.message : 'Failed to delete topic', 'error');
    }
  };
  const deleting = questions.find((q) => q.id === pendingDelete);
  const deletingLesson = deleting?.status === 'ANSWERED';
  // Everything waits for the word; only my request, and a topic with no lesson, are one confirm as before.
  const deleteBlocked = deletingLesson && deleteLesson && !typedDeleteWord(typedDelete);

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

  const approve = useCallback(async (questionId: string, answer: Answer) => {
    try {
      await api.setDropletApproved(answer.id, true);
      setQuestions((prev) => prev.map((q) => (q.id === questionId && q.answer ? { ...q, answer: { ...q.answer, awaitingReview: false } } : q)));
      showToast(answer.hidden ? 'Approved; it stays hidden until you put it back.' : 'Approved: it is on the Droplets tab.', 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to approve it', 'error');
    }
  }, [showToast]);

  if (authLoading) return <LoadingMessage />;

  if (unreachable) return <ServerUnavailable onRetry={retry} />;

  const canAsk = newQuestion.trim().length >= QUESTION_MIN && !submitting;

  return (
    <PageContainer className="py-6">
      <ConfirmDialog
        open={!!pendingDelete}
        title="Delete topic"
        message={deletingLesson
          ? <DeleteChoice everything={deleteLesson} onChange={setDeleteLesson} typed={typedDelete} onType={setTypedDelete}
              likes={deleting?.answer?.likeCount} comments={deleting?.answer?.commentCount} />
          : 'Delete this topic request? This cannot be undone.'}
        confirmLabel={!deletingLesson ? 'Delete' : deleteLesson ? 'Delete everything' : 'Delete my request'}
        variant="danger"
        confirmDisabled={deleteBlocked}
        onConfirm={() => {
          if (deleteBlocked) return;
          if (pendingDelete) handleDelete(pendingDelete, deletingLesson && deleteLesson);
          setPendingDelete(null);
        }}
        onCancel={() => setPendingDelete(null)}
      />

      <div className="mb-6">
        <h2 className="text-2xl font-bold tracking-wider mb-1">LEARN</h2>
        {user ? (
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Ask for any topic. The teacher makes each one a droplet, a short lesson; ask follow-ups under it to go deeper.
          </p>
        ) : (
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }} data-testid="learn-pitch">
            {/* A line can break between a pill and the character after it, so the colon and the period are held to
                theirs. */}
            Ask for any topic. An AI teacher makes you a{' '}
            <span className="whitespace-nowrap"><DropletPill />:</span> a short lesson with{' '}
            a <Marked color="var(--accent-green)">narrated video</Marked>{' '}
            and a <span className="whitespace-nowrap"><SongChip />.</span> Ask follow-ups under it to go deeper.
          </p>
        )}
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }} data-testid="privacy-note">
          Only you see your requests and follow-ups. Each lesson is shared with everyone on the{' '}
          <Link href="/droplets" className="underline hover-text">Droplets tab</Link>, without your name or your request.
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
            onChange={(e) => changeQuestion(e.target.value)}
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
              background: canAsk ? 'var(--accent-brand)' : 'var(--bg-tertiary)',
              color: canAsk ? 'var(--accent-brand-text)' : 'var(--text-muted)',
            }}
          >
            {submitting ? '...' : 'SUBMIT'}
          </button>
        </div>
        {picksOpen === false ? isAdmin && (
          <p className="text-[10px] mt-3" style={{ color: 'var(--text-muted)' }} data-testid="brain-picks-off">
            Brain: every topic goes to the default brain{defaultBrain ? `, ${BRAIN_MODELS[defaultBrain] ?? defaultBrain}` : ''}, as
            set on the <Link href="/admin/studio" className="underline">Studio page</Link>. Learners see no picker until it is
            turned off there.
          </p>
        ) : (
          <div className="mt-3" data-testid="brain-picker">
            <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="Brain for this topic">
              <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--text-muted)' }}>Brain:</span>
              {brainOptions.map((option) => {
                const on = option.value === brain;
                return (
                  <button key={option.label} type="button" role="radio" aria-checked={on}
                    title={isAdmin && option.adminNote ? `${option.note} ${option.adminNote}` : option.note}
                    onClick={() => pickBrain(option.value)}
                    className={`px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider transition-colors rounded-full border ${
                      on ? 'text-[var(--btn-filled-text)] border-transparent' : 'text-[var(--accent-purple)] border-[var(--accent-purple)] hover:bg-[var(--accent-purple)]/10'
                    }`}
                    style={on ? { background: 'var(--accent-purple)' } : {}}>
                    {option.label}
                  </button>
                );
              })}
            </div>
            <p className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }} data-testid="brain-note">
              {chosen?.note}
              {isAdmin && chosen?.adminNote && ` ${chosen.adminNote}`}
              {isAdmin && brain === null && defaultBrain && ` Now: ${BRAIN_MODELS[defaultBrain] ?? defaultBrain}.`}
            </p>
          </div>
        )}
        <p className="text-[10px] mt-2 flex justify-between" style={{ color: 'var(--text-muted)' }}>
          <span>No personal info: the lesson will be public. Min {QUESTION_MIN} characters. Enter adds a line; Ctrl+Enter (⌘+Enter) or Submit sends.</span>
          <span>{newQuestion.length}/{QUESTION_MAX}</span>
        </p>
        {user && allowance && <AllowanceNote allowance={allowance} />}
        {!user && !askSignIn && (
          <p className="text-[10px] mt-1" style={{ color: 'var(--text-secondary)' }} data-testid="sign-in-to-send">
            Look around and type a topic first: you sign in with Google only to send it.
          </p>
        )}
        {!user && askSignIn && (
          <div className="mt-3 p-3 rounded-md animate-fade-in" role="region" aria-labelledby="sign-in-to-send-title"
            style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }} data-testid="sign-in-prompt">
            <p id="sign-in-to-send-title" className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
              Sign in to send your topic
            </p>
            <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              We keep what you typed. After you sign in it is back in this box, ready to send.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
              <Link ref={signInRef} href={SIGN_IN_HREF} onClick={topicDraft.markSignIn}
                className="inline-block px-4 py-2 rounded font-bold text-xs uppercase tracking-wider transition-colors"
                style={{ background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' }}>
                Sign in with Google
              </Link>
              <button type="button" onClick={() => { setAskSignIn(false); textareaRef.current?.focus(); }}
                className="text-xs underline hover-text" style={{ color: 'var(--text-muted)' }}>
                Not now
              </button>
            </div>
            <p className="text-[10px] mt-3" style={{ color: 'var(--text-muted)' }} data-testid="consent">
              Before you sign in you&rsquo;ll be asked to agree to the{' '}
              <Link href="/terms" className="underline hover-text">Terms of Use</Link> and the{' '}
              <Link href="/privacy" className="underline hover-text">Privacy Policy</Link>. You must be {MIN_AGE} or older.
            </p>
          </div>
        )}
      </div>

      {/* Filter: an admin's view of everyone's topics, or their own */}
      {isAdmin && <div className="flex items-center gap-3 mb-4">
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
      </div>}

      {user && hasNew && !loading && (
        <button
          onClick={load}
          className="w-full text-center text-xs font-medium py-2 px-3 rounded-md cursor-pointer transition-all duration-200 animate-slide-down hover:brightness-110 mb-4"
          style={{ background: 'rgba(50, 173, 230, 0.12)', border: '1px solid rgba(50, 173, 230, 0.3)', color: 'var(--accent-blue)' }}
        >
          &uarr; New activity &mdash; tap to refresh
        </button>
      )}

      {!user ? (
        <div className="space-y-4">
          <p className="text-xs" style={{ color: 'var(--text-muted)' }} data-testid="topics-after-sign-in">
            Your topics show here once you sign in.
          </p>
          <ExampleDroplet />
        </div>
      ) : loading ? (
        <LoadingMessage />
      ) : questions.length === 0 ? (
        <>
          <div className="text-center py-10" style={{ color: 'var(--text-muted)' }}>
            <p>{ownOnly ? 'You have not asked for a topic yet.' : 'No topics yet. Ask for the first one!'}</p>
            <p className="text-xs mt-2">
              Use the form above. The teacher picks up new topics on its next pass. Or watch what others asked for on the{' '}
              <Link href="/droplets" className="underline hover-text">Droplets tab</Link>.
            </p>
          </div>
          <ExampleDroplet />
        </>
      ) : (
        <div className="space-y-3">
          {questions.map((q, i) => (
            <QuestionCard
              key={q.id}
              q={q}
              index={i}
              isAdmin={isAdmin}
              onAnswerChange={updateAnswer}
              onRequestDelete={requestDelete}
              onSetHidden={setHidden}
              onApprove={approve}
            />
          ))}
        </div>
      )}

      {user && cursor && !loading && (
        <div ref={sentinelRef} className="text-center mt-6" data-testid="load-more">
          <button onClick={loadMore} disabled={loadingMore}
            className="px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-colors"
            style={{ color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)' }}>
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
      {user && !cursor && !loading && questions.length >= PAGE_SIZE && (
        <p className="text-center text-[10px] uppercase tracking-wider mt-6" style={{ color: 'var(--text-muted)' }}>End of list</p>
      )}
    </PageContainer>
  );
}
