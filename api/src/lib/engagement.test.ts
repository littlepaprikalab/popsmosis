import { describe, it, expect, afterEach } from 'vitest';
import { commentsPerUserPerHour, idempotencyKeyOf, publicAuthorName, serializeComment } from './engagement.js';
import { isForeignKeyViolation } from './errors.js';

describe('a comment in public', () => {
  it('names its author by first name and initial, never in full and never by email', () => {
    expect(publicAuthorName('Grace Brewster Hopper')).toBe('Grace H.');
    expect(publicAuthorName('  ada   lovelace ')).toBe('ada L.');
    expect(publicAuthorName('Cher')).toBe('Cher');
    expect(publicAuthorName('')).toBe('Someone');
    expect(publicAuthorName(null)).toBe('Someone');
    expect(publicAuthorName('Łukasz Żółw')).toBe('Łukasz Ż.');
    expect(publicAuthorName(`${'x'.repeat(80)} Y`)).toBe(`${'x'.repeat(30)} Y.`);
  });

  it('says whose it is only to its author', () => {
    const row = { id: 'c1', content: 'Catchy!', createdAt: new Date('2026-10-30T10:00:00Z'), userId: 'u1', user: { name: 'Grace Hopper' } };
    expect(serializeComment(row, 'u1')).toEqual({ id: 'c1', content: 'Catchy!', createdAt: '2026-10-30T10:00:00.000Z', author: { name: 'Grace H.' }, mine: true });
    expect(serializeComment(row, 'u2').mine).toBe(false);
    expect(serializeComment(row, null).mine).toBe(false);
    expect(JSON.stringify(serializeComment(row, null))).not.toContain('u1');
  });
});

describe('the Idempotency-Key header', () => {
  it('takes a UUID or the like, and refuses anything else', () => {
    expect(idempotencyKeyOf(undefined)).toBeUndefined();
    expect(idempotencyKeyOf('7c9e6679-7425-40de-944b-e07fc1f90ae7')).toBe('7c9e6679-7425-40de-944b-e07fc1f90ae7');
    expect(idempotencyKeyOf('short')).toBeNull();
    expect(idempotencyKeyOf('has spaces in it')).toBeNull();
    expect(idempotencyKeyOf('k'.repeat(101))).toBeNull();
    expect(idempotencyKeyOf(['a'.repeat(10), 'b'.repeat(10)])).toBeNull();
  });
});

describe('the hourly comment limit', () => {
  const saved = process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR;
  afterEach(() => {
    if (saved === undefined) delete process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR;
    else process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR = saved;
  });

  it('is 30 unless set to a positive number', () => {
    delete process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR;
    expect(commentsPerUserPerHour()).toBe(30);
    process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR = '5';
    expect(commentsPerUserPerHour()).toBe(5);
    process.env.DROPLET_COMMENTS_PER_USER_PER_HOUR = '0';
    expect(commentsPerUserPerHour()).toBe(30);
  });
});

describe('a foreign key that points at nothing', () => {
  it('is recognised from a Prisma query and from a raw one', () => {
    expect(isForeignKeyViolation({ code: 'P2003' })).toBe(true);
    // What adapter-pg throws for $executeRaw (checked against Postgres 16).
    expect(isForeignKeyViolation({
      code: 'P2010',
      meta: { driverAdapterError: { cause: { originalCode: '23503', kind: 'ForeignKeyConstraintViolation' } } },
    })).toBe(true);
    expect(isForeignKeyViolation({ code: 'P2010', cause: { code: '23503' } })).toBe(true);
    expect(isForeignKeyViolation({ code: 'P2002' })).toBe(false);
    expect(isForeignKeyViolation(null)).toBe(false);
  });
});
