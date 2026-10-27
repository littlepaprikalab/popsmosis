import { describe, it, expect } from 'vitest';
import { encodeCreatedAtIdCursor, decodeCreatedAtIdCursor, decodeCursor } from './cursor.js';

describe('cursor', () => {
  it('round-trips createdAt/id', () => {
    const items = [{ id: 'a', createdAt: new Date('2026-01-01T00:00:00Z') }, { id: 'b', createdAt: new Date('2026-01-02T00:00:00Z') }];
    const c = encodeCreatedAtIdCursor(items, 2)!;
    expect(c).toBeTruthy();
    expect(decodeCursor(c)).toEqual({ createdAt: '2026-01-02T00:00:00.000Z', id: 'b' });
    const where = decodeCreatedAtIdCursor(c)!;
    // A bound an index can seek to, and the tie-break on id among rows at the same instant.
    expect(where).toEqual({
      createdAt: { lte: new Date('2026-01-02T00:00:00Z') },
      OR: [{ createdAt: { lt: new Date('2026-01-02T00:00:00Z') } }, { id: { lt: 'b' } }],
    });
  });
  it('returns null when the page is short', () => {
    expect(encodeCreatedAtIdCursor([{ id: 'a', createdAt: new Date() }], 2)).toBeNull();
  });
  it('rejects garbage', () => {
    expect(decodeCreatedAtIdCursor('not-base64-json')).toBeNull();
    expect(decodeCreatedAtIdCursor(Buffer.from('{"createdAt":"nope"}').toString('base64url'))).toBeNull();
    expect(decodeCreatedAtIdCursor(undefined)).toBeNull();
  });
});
