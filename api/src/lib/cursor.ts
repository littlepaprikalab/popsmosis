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
    OR: [
      { createdAt: { lt: createdAt } },
      { createdAt, id: { lt: decoded.id } },
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
