/**
 * Response shapes shared by the question routes. The web app and the agent
 * both read these, so the shape is fixed here rather than leaking Prisma rows.
 */
import { serializeVideo, type VideoStatus } from './droplet.js';

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
  videoError: string | null;
  videoUpdatedAt: Date | null;
  agent: { id: string; name: string };
  replies: ReplyRow[];
};

type QuestionRow = {
  id: string;
  userId: string;
  content: string;
  status: 'PENDING' | 'ANSWERED' | 'FAILED';
  failedReason: string | null;
  createdAt: Date;
  user: { id: string; name: string | null; avatarUrl: string | null };
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

export function serializeAnswer(a: AnswerRow, viewerId?: string) {
  const replies = a.replies.map((r) => serializeReply(r, viewerId));
  const last = a.replies[a.replies.length - 1];
  return {
    id: a.id,
    questionId: a.questionId,
    agent: a.agent,
    content: a.content,
    model: a.model,
    createdAt: a.createdAt.toISOString(),
    video: serializeVideo(a), // null: a text-only droplet
    replies,
    // True while the latest reply is from a human: the agent owes a response.
    awaitingAgent: !!last && !last.agentId,
  };
}

export function serializeQuestion(q: QuestionRow, viewerId?: string) {
  const isOwner = !!viewerId && q.userId === viewerId;
  return {
    id: q.id,
    content: q.content,
    status: q.status,
    failedReason: q.status === 'FAILED' ? q.failedReason : null,
    attachments: q.attachments.map(serializeAttachment),
    createdAt: q.createdAt.toISOString(),
    user: q.user,
    answer: q.answers[0] ? serializeAnswer(q.answers[0], viewerId) : null,
    isOwner,
    canDelete: isOwner && q.status !== 'ANSWERED', // a failed topic has no lesson to keep either
  };
}

export type SerializedQuestion = ReturnType<typeof serializeQuestion>;
export type SerializedAnswer = ReturnType<typeof serializeAnswer>;
export type SerializedReply = ReturnType<typeof serializeReply>;
export type SerializedAttachment = ReturnType<typeof serializeAttachment>;

/** The signed-in user's own record, and the rows on /admin/users. */
export function publicUser(u: {
  id: string; email: string; name: string | null; avatarUrl: string | null;
  role: string; status: string; reviewedAt: Date | null; createdAt: Date;
}) {
  return {
    id: u.id, email: u.email, name: u.name, avatarUrl: u.avatarUrl, role: u.role, status: u.status,
    reviewedAt: u.reviewedAt ? u.reviewedAt.toISOString() : null,
    createdAt: u.createdAt.toISOString(),
  };
}
