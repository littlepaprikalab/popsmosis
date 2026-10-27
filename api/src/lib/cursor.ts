/**
 * Opaque cursor-based pagination on (createdAt, id), copied from sparks.
 * Cursors are base64url-encoded JSON that clients pass back via `?cursor=`.
 */

type CursorPayload = Record<string, unknown>;

export function encodeCursor(payload: CursorPayload): string {
  return Buffer.from(JSON.stringify(payload)).toString('base64url');
}

export function decodeCursor(cursor: string): CursorPayload | null {
  try {
    return JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8'));
  } catch {
    return null;
  }
}

/**
 * Decode a compound (createdAt, id) cursor into a Prisma where-fragment that
 * does not skip rows with identical timestamps. Null when missing/malformed.
 *
 * "Older than the cursor" is `createdAt < c OR (createdAt = c AND id < i)`, written
 * as `createdAt <= c AND (createdAt < c OR id < i)`, which means the same. The
 * first half is a bound Postgres can seek a (…, createdAt, id) index to; the plain
 * OR is not, so it read every row newer than the cursor and threw them away (49,984
 * rows, 8.6 ms, for a deep page of a droplet's 60,000 comments; 0.1 ms this way).
 */
export function decodeCreatedAtIdCursor(cursor: string | undefined): Record<string, unknown> | null {
  if (!cursor) return null;
  const decoded = decodeCursor(cursor);
  if (!decoded?.createdAt) return null;
  const createdAt = new Date(decoded.createdAt as string);
  if (isNaN(createdAt.getTime())) return null;
  if (!decoded.id || typeof decoded.id !== 'string') {
    return { createdAt: { lt: createdAt } };
  }
  return {
    createdAt: { lte: createdAt },
    OR: [
      { createdAt: { lt: createdAt } },
      { id: { lt: decoded.id } },
    ],
  };
}

export function encodeCreatedAtIdCursor(
  items: Array<{ createdAt: Date | string; id: string }>,
  limit: number,
): string | null {
  if (items.length < limit) return null;
  const last = items[items.length - 1];
  const createdAt = last.createdAt instanceof Date ? last.createdAt.toISOString() : last.createdAt;
  return encodeCursor({ createdAt, id: last.id });
}
