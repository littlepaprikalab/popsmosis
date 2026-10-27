/**
 * Thumbs-up and comments on the public Droplets tab (routes/droplets.ts).
 *
 * Anyone can read them; a signed-in person can add their own. A comment is public in
 * the way the droplet is: an anonymous visitor reads it. So it carries as little about
 * its author as still says who wrote it, a first name and an initial, and never their
 * email, their Google picture or their id. The counts live on the droplet's row and are
 * kept by triggers (prisma/migrations/20261031120000_droplet_engagement).
 */

export const COMMENT_MAX_LENGTH = 1000;
export const COMMENTS_PAGE_SIZE = 20;
export const COMMENTS_PAGE_MAX = 50;

export function commentsPerUserPerHour(): number {
  const n = parseInt(process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR ?? '', 10);
  return Number.isNaN(n) || n <= 0 ? 30 : n;
}

// Characters that show nothing: zero-width spaces and joiners, direction marks, the word
// joiner, the BOM, the soft hyphen, and the Hangul fillers and Braille blank people use to
// post "empty" text. trim() keeps them, so a comment of nothing but these would post blank.
const INVISIBLE = /[\s\u00AD\u115F\u1160\u180E\u200B-\u200F\u2028-\u202F\u2060-\u206F\u2800\u3164\uFEFF\uFFA0]/gu;

/** True when the text has at least one character a reader would see. */
export function hasVisibleText(text: string): boolean {
  return text.replace(INVISIBLE, '').length > 0;
}

// A UUID, a ULID, anything a client makes up for one attempt; not a place for a payload.
const IDEMPOTENCY_KEY = /^[A-Za-z0-9_.:-]{8,100}$/;

/**
 * The Idempotency-Key a comment was posted with: undefined when none was sent, null
 * when the header is malformed. A retry with the same key gets the first comment back
 * instead of posting a second, so a POST whose answer was lost can be sent again.
 */
export function idempotencyKeyOf(header: string | string[] | undefined): string | undefined | null {
  if (header === undefined) return undefined;
  if (Array.isArray(header) || !IDEMPOTENCY_KEY.test(header)) return null;
  return header;
}

/**
 * How a comment names its author in public: "Grace H." for "Grace Brewster Hopper",
 * "Grace" for a one-word name. Many of the people here are students, and the page is
 * open to anyone, so never the full name, and never the email when there is no name.
 */
export function publicAuthorName(name: string | null | undefined): string {
  const words = (name ?? '').trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return 'Someone';
  const first = [...words[0]].slice(0, 30).join('');
  if (words.length === 1) return first;
  const initial = [...words[words.length - 1]][0].toLocaleUpperCase();
  return `${first} ${initial}.`;
}

type CommentRow = {
  id: string;
  content: string;
  createdAt: Date;
  userId: string;
  user: { name: string | null };
};

/** A comment as the Droplets tab reads it. `mine` is the viewer's own, so the page offers to delete it. */
export function serializeComment(c: CommentRow, viewerId: string | null) {
  return {
    id: c.id,
    content: c.content,
    createdAt: c.createdAt.toISOString(),
    author: { name: publicAuthorName(c.user.name) },
    mine: !!viewerId && c.userId === viewerId,
  };
}

export const commentSelect = {
  id: true,
  content: true,
  createdAt: true,
  userId: true,
  user: { select: { name: true } },
} as const;

export type SerializedComment = ReturnType<typeof serializeComment>;
