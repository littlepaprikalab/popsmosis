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
  // The shapes Prisma 7 gives over adapter-pg, as Render logged them, for a query and for a raw query.
  const refused = (reason: string) => ({
    code: 'P1011',
    message: `\nInvalid \`prisma.setting.findUnique()\` invocation:\n\n\nError opening a TLS connection: ${reason}`,
    meta: { modelName: 'Setting', driverAdapterError: { name: 'DriverAdapterError', cause: { kind: 'TlsConnectionError', reason } } },
  });
  it('never retries a database certificate the pool refuses, which fails the same way every time', () => {
    for (const reason of [
      'self-signed certificate in certificate chain',
      'unable to get local issuer certificate',
      'certificate has expired',
      "Hostname/IP does not match certificate's altnames: Host: db.example.com. is not in the cert's altnames: DNS:other.example.com",
    ]) expect(isTransientDbError(refused(reason))).toBe(false);
    expect(isTransientDbError({
      code: 'P2010',
      message: 'Invalid `prisma.$queryRaw()` invocation:\n\n\nRaw query failed. Code: `N/A`. Message: `Error opening a TLS connection: self-signed certificate in certificate chain`',
    })).toBe(false);
    expect(isTransientDbError({ message: 'Invalid invocation', cause: refused('self-signed certificate in certificate chain') })).toBe(false);
  });
  it('still retries a TLS handshake the network cut short', () => {
    expect(isTransientDbError(refused('Client network socket disconnected before secure TLS connection was established'))).toBe(true);
    expect(isTransientDbError({ code: 'P1011', message: 'Error opening a TLS connection: read ECONNRESET' })).toBe(true);
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
  const refusedCertificate = {
    code: 'P1011',
    message: 'Error opening a TLS connection: self-signed certificate in certificate chain',
    meta: { driverAdapterError: { cause: { kind: 'TlsConnectionError', reason: 'self-signed certificate in certificate chain' } } },
  };

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

  it('tries a refused database certificate once', async () => {
    const op = vi.fn().mockRejectedValue(refusedCertificate);
    await expect(withDbRetry(op, { sleep: noSleep })).rejects.toBe(refusedCertificate);
    expect(op).toHaveBeenCalledTimes(1);
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
