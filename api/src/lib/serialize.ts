/**
 * Response shapes shared by the question routes. The web app and the agent
 * both read these, so the shape is fixed here rather than leaking Prisma rows.
 */
import { serializeVideo, type VideoStatus } from './droplet.js';
import { serializeBreakdown } from './breakdown.js';
import { serializeTags } from './tags.js';
import { termsCurrent } from './terms.js';
import { awaitingVideos, lessonProgress, readProgress, videoProgress } from './progress.js';
import { claimMinutes } from './claim.js';

export const userSelect = { id: true, name: true, avatarUrl: true } as const;
export const agentSelect = { id: true, name: true } as const;
// Never the bytes: a list of cards must not drag every file out of the database. GET /api/attachments/:id serves them.
export const attachmentSelect = { id: true, name: true, mimeType: true, size: true, purgedAt: true } as const;
export const attachmentsInclude = { select: attachmentSelect, orderBy: { createdAt: 'asc' as const } } as const;

export const replyInclude = {
  user: { select: userSelect },
  agent: { select: agentSelect },
  attachments: attachmentsInclude,
} as const;

export const answerInclude = {
  agent: { select: agentSelect },
  replies: { include: replyInclude, orderBy: { createdAt: 'asc' as const } },
} as const;

// Tens of KB each, and a page of cards needs neither: the reply queue reads the bundle on its own.
export const answerOmit = { bundle: true, factcheck: true } as const;

export const questionInclude = {
  user: { select: userSelect },
  answers: { omit: answerOmit, include: answerInclude, orderBy: { createdAt: 'asc' as const } },
  attachments: attachmentsInclude,
} as const;

type AttachmentRow = {
  id: string;
  name: string;
  mimeType: string;
  size: number;
  purgedAt: Date | null;
};

type ReplyRow = {
  id: string;
  answerId: string;
  userId: string | null;
  agentId: string | null;
  replyToId: string | null;
  content: string;
  createdAt: Date;
  user: { id: string; name: string | null; avatarUrl: string | null } | null;
  agent: { id: string; name: string } | null;
  attachments: AttachmentRow[];
};

type AnswerRow = {
  id: string;
  questionId: string;
  agentId: string;
  content: string;
  model: string | null;
  createdAt: Date;
  videoStatus: VideoStatus | null;
  songVideoUrl: string | null;
  lessonVideoUrl: string | null;
  songPosterUrl: string | null;
  lessonPosterUrl: string | null;
  hookVideoUrl: string | null;
  hookPosterUrl: string | null;
  videoError: string | null;
  videoUpdatedAt: Date | null;
  videoAttempts: number;
  hiddenAt: Date | null;
  lessonBreakdown: unknown;
  videoBreakdown: unknown;
  videoProgress: unknown;
  songTags: string[];
  lessonTags: string[];
  agent: { id: string; name: string };
  replies: ReplyRow[];
};

type QuestionRow = {
  id: string;
  userId: string | null; // null once the learner took it back (lib/privacy.ts)
  content: string;
  status: 'PENDING' | 'ANSWERED' | 'FAILED';
  failedReason: string | null;
  createdAt: Date;
  claimedAt: Date | null;
  progress: unknown;
  brain: string | null;
  user: { id: string; name: string | null; avatarUrl: string | null } | null;
  answers: AnswerRow[];
  attachments: AttachmentRow[];
};

/** A file as a message lists it: what it is, and where its bytes are while they are kept. */
export function serializeAttachment(a: AttachmentRow) {
  return {
    id: a.id,
    name: a.name,
    mimeType: a.mimeType,
    size: a.size,
    url: `/api/attachments/${a.id}`,
    expired: a.purgedAt !== null, // past the retention: the row says what was sent, the bytes are gone
  };
}

export function serializeReply(r: ReplyRow, viewerId?: string) {
  return {
    id: r.id,
    answerId: r.answerId,
    authorType: r.agentId ? ('AGENT' as const) : ('HUMAN' as const),
    replyToId: r.replyToId,
    content: r.content,
    attachments: r.attachments.map(serializeAttachment), // a human reply's files
    createdAt: r.createdAt.toISOString(),
    user: r.user,
    agent: r.agent,
    canDelete: !!viewerId && r.userId === viewerId,
  };
}

/**
 * `videosPlanned`: the studio said, while it wrote the lesson, that the videos come after it (lib/progress.ts);
 * it holds the text back in the moment between the post and the videos' first claim.
 */
export function serializeAnswer(a: AnswerRow, viewerId?: string, videosPlanned = false, now = new Date()) {
  const replies = a.replies.map((r) => serializeReply(r, viewerId));
  const last = a.replies[a.replies.length - 1];
  const video = serializeVideo(a, now);
  return {
    id: a.id,
    questionId: a.questionId,
    agent: a.agent,
    content: a.content,
    model: a.model,
    createdAt: a.createdAt.toISOString(),
    video, // null: a text-only droplet
    tags: serializeTags(a), // each video's hashtags (lib/tags.ts); none for a lesson posted without a bundle
    // Each step's time, cost and models (lib/breakdown.ts); null when the studio sent none.
    breakdown: serializeBreakdown(a.lessonBreakdown, a.videoBreakdown),
    hidden: a.hiddenAt !== null, // an admin took it off the public Droplets tab
    replies,
    // True while the latest reply is from a human: the agent owes a response.
    awaitingAgent: !!last && !last.agentId,
    // True while the page holds the lesson text back until its videos are done, or LESSON_HOLD_MINUTES pass.
    awaitingVideos: awaitingVideos(a, video, videosPlanned, now),
  };
}

/**
 * Where the studio is with this droplet, while it is being made (lib/progress.ts): the topic is waiting for its
 * lesson, or its videos are to come or rendering. Null once there is nothing left to make.
 */
function progressOf(q: QuestionRow, answer: ReturnType<typeof serializeAnswer> | null, now: Date) {
  if (q.status === 'PENDING') {
    const leaseLive = !!q.claimedAt && now.getTime() - q.claimedAt.getTime() < claimMinutes() * 60_000;
    return lessonProgress(q.progress, leaseLive, now);
  }
  const row = q.answers[0];
  const rendering = answer?.video?.status === 'RENDERING';
  if (!row || !answer || !(rendering || answer.awaitingVideos)) return null;
  return videoProgress(row.videoProgress, rendering, row.lessonBreakdown, row.videoBreakdown, now);
}

export function serializeQuestion(q: QuestionRow, viewerId?: string, now = new Date()) {
  const isOwner = !!viewerId && q.userId === viewerId;
  const videosPlanned = readProgress(q.progress).videos === true;
  const answer = q.answers[0] ? serializeAnswer(q.answers[0], viewerId, videosPlanned, now) : null;
  return {
    id: q.id,
    content: q.content,
    status: q.status,
    failedReason: q.status === 'FAILED' ? q.failedReason : null,
    // The brain an admin picked for it (lib/brains.ts); null: the teacher's own. The studio reads it from the claim.
    brain: q.brain,
    attachments: q.attachments.map(serializeAttachment),
    createdAt: q.createdAt.toISOString(),
    user: q.user,
    answer,
    // While the droplet is being made: the steps running now and the bill so far, shown as it fills in.
    progress: progressOf(q, answer, now),
    isOwner,
    // Always the owner's to take back. One with a lesson keeps the lesson for everyone else (lib/privacy.ts).
    canDelete: isOwner,
  };
}

/**
 * A follow-up as the teacher reads it: never who wrote it. The agent's key is held by an LLM that reads
 * learner-written text, so it is given what it needs to teach and nothing that names a learner (lib/privacy.ts).
 */
export function serializeReplyForAgent(r: ReplyRow) {
  const { user: _user, canDelete: _canDelete, ...rest } = serializeReply(r);
  return rest;
}

/** A question as the teacher reads it (the queues, the claim): the request and its files, never who asked. */
export function serializeQuestionForAgent(q: QuestionRow, now = new Date()) {
  const { user: _user, isOwner: _isOwner, canDelete: _canDelete, answer, ...rest } = serializeQuestion(q, undefined, now);
  const replies = q.answers[0]?.replies.map(serializeReplyForAgent) ?? [];
  return { ...rest, answer: answer && { ...answer, replies } };
}

export type SerializedQuestion = ReturnType<typeof serializeQuestion>;
export type SerializedAnswer = ReturnType<typeof serializeAnswer>;
export type SerializedReply = ReturnType<typeof serializeReply>;
export type SerializedAttachment = ReturnType<typeof serializeAttachment>;

/** The signed-in user's own record, and the rows on /admin/users. */
export function publicUser(u: {
  id: string; email: string; name: string | null; avatarUrl: string | null; role: string; createdAt: Date;
  termsVersion: string | null; termsAcceptedAt: Date | null;
}) {
  return {
    id: u.id, email: u.email, name: u.name, avatarUrl: u.avatarUrl, role: u.role, createdAt: u.createdAt.toISOString(),
    termsAcceptedAt: u.termsAcceptedAt?.toISOString() ?? null, // when they last agreed to the Terms (lib/terms.ts)
    termsCurrent: termsCurrent(u), // false: the web app asks them to agree to the current version first
  };
}
