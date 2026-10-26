import { describe, it, expect, vi } from 'vitest';
import { isTransientDbError, withDbRetry, MAX_ATTEMPTS } from './db-retry.js';

describe('isTransientDbError', () => {
  it('recognises Prisma and Postgres transient codes', () => {
    expect(isTransientDbError({ code: 'P1001', message: 'x' })).toBe(true);
    expect(isTransientDbError({ code: 'P2024', message: 'x' })).toBe(true);
    expect(isTransientDbError({ code: 'P2034', message: 'x' })).toBe(true);
    expect(isTransientDbError({ code: '57P01', message: 'x' })).toBe(true);
    expect(isTransientDbError({ code: '08006', message: 'x' })).toBe(true);
    expect(isTransientDbError({ code: '40001', message: 'x' })).toBe(true);
  });
  it('recognises connection-drop messages and wrapped causes', () => {
    expect(isTransientDbError(new Error('server closed the connection unexpectedly'))).toBe(true);
    expect(isTransientDbError(new Error('read ECONNRESET'))).toBe(true);
    expect(isTransientDbError({ message: 'Invalid invocation', cause: { code: 'P1017' } })).toBe(true);
  });
  it('never retries logical errors', () => {
    expect(isTransientDbError({ code: 'P2002', message: 'Unique constraint failed' })).toBe(false);
    expect(isTransientDbError({ code: 'P2025', message: 'Record not found' })).toBe(false);
    expect(isTransientDbError(new Error('content is required'))).toBe(false);
    expect(isTransientDbError(null)).toBe(false);
    expect(isTransientDbError('nope')).toBe(false);
  });
});

describe('withDbRetry', () => {
  const noSleep = async () => {};

  it('returns on first success without retrying', async () => {
    const op = vi.fn().mockResolvedValue(42);
    await expect(withDbRetry(op, { sleep: noSleep })).resolves.toBe(42);
    expect(op).toHaveBeenCalledTimes(1);
  });

  it('retries a transient failure then succeeds', async () => {
    const op = vi.fn()
      .mockRejectedValueOnce({ code: 'P1001', message: "Can't reach database server" })
      .mockResolvedValue('ok');
    const onRetry = vi.fn();
    await expect(withDbRetry(op, { sleep: noSleep, onRetry })).resolves.toBe('ok');
    expect(op).toHaveBeenCalledTimes(2);
    expect(onRetry).toHaveBeenCalledWith(expect.objectContaining({ code: 'P1001' }), 1);
  });

  it('gives up after MAX_ATTEMPTS and rethrows the last error', async () => {
    const op = vi.fn().mockRejectedValue({ code: 'P2024', message: 'pool timeout' });
    await expect(withDbRetry(op, { sleep: noSleep })).rejects.toMatchObject({ code: 'P2024' });
    expect(op).toHaveBeenCalledTimes(MAX_ATTEMPTS);
  });

  it('does not retry logical errors', async () => {
    const op = vi.fn().mockRejectedValue({ code: 'P2002', message: 'Unique constraint failed' });
    await expect(withDbRetry(op, { sleep: noSleep })).rejects.toMatchObject({ code: 'P2002' });
    expect(op).toHaveBeenCalledTimes(1);
  });

  it('backs off between attempts', async () => {
    const sleeps: number[] = [];
    const op = vi.fn()
      .mockRejectedValueOnce(new Error('connection reset by peer'))
      .mockRejectedValueOnce(new Error('connection reset by peer'))
      .mockResolvedValue(1);
    await withDbRetry(op, { sleep: async (ms) => { sleeps.push(ms); } });
    expect(sleeps).toEqual([100, 200]);
  });
});
