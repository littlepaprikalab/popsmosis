import { describe, it, expect, vi } from 'vitest';
import { SharedRead, TtlCache } from './ttl-cache.js';

describe('TtlCache', () => {
  it('stores, expires, and distinguishes cached null from a miss', () => {
    vi.useFakeTimers();
    const c = new TtlCache<string | null>(10);
    c.set('a', 'x', 1000);
    c.set('b', null, 1000);
    expect(c.get('a')).toBe('x');
    expect(c.get('b')).toBeNull();      // cached negative lookup
    expect(c.get('c')).toBeUndefined(); // miss
    vi.advanceTimersByTime(1001);
    expect(c.get('a')).toBeUndefined();
    vi.useRealTimers();
  });

  it('evicts the oldest entry at capacity', () => {
    const c = new TtlCache<number>(2);
    c.set('a', 1, 10_000);
    c.set('b', 2, 10_000);
    c.set('c', 3, 10_000);
    expect(c.get('a')).toBeUndefined();
    expect(c.get('b')).toBe(2);
    expect(c.get('c')).toBe(3);
    expect(c.size).toBe(2);
  });

  it('delete and clear', () => {
    const c = new TtlCache<number>(5);
    c.set('a', 1, 10_000);
    c.delete('a');
    expect(c.get('a')).toBeUndefined();
    c.set('b', 1, 10_000);
    c.clear();
    expect(c.size).toBe(0);
  });
});

describe('SharedRead', () => {
  const deferred = <T>() => {
    let resolve!: (value: T) => void;
    let reject!: (err: Error) => void;
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };

  it('reads once for every caller that asks while the value is read or kept, and again once it runs out', async () => {
    const read = vi.fn(async () => 'a');
    const shared = new SharedRead(read, 1000);
    const values = await Promise.all(Array.from({ length: 50 }, () => shared.get(0)));
    expect(values.every((v) => v === 'a')).toBe(true);
    expect(await shared.get(999)).toBe('a');
    expect(read).toHaveBeenCalledTimes(1);
    await shared.get(1000);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('puts a value set in force at once, and a read that started before it never brings the old one back', async () => {
    const slow = deferred<string>();
    const shared = new SharedRead(() => slow.promise, 1000);
    const before = shared.get(0);
    shared.set('new', 0);
    slow.resolve('old');
    expect(await before).toBe('old'); // asked before the write
    expect(await shared.get(1)).toBe('new');
    // Nor as the fallback for a read that fails later.
    const failing = new SharedRead<string>(() => Promise.reject(new Error('down')), 1000);
    failing.set('new', 0);
    expect(await failing.get(1000)).toBe('new');
  });

  it('gives the last value when a read fails, keeps the failure for ttlMs, and throws when it has no value yet', async () => {
    let fail = false;
    const read = vi.fn(async () => { if (fail) throw new Error('down'); return 'a'; });
    const shared = new SharedRead(read, 1000);
    await expect(shared.get(0)).resolves.toBe('a');
    fail = true;
    await expect(shared.get(1000)).resolves.toBe('a');
    await expect(shared.get(1500)).resolves.toBe('a');
    expect(read).toHaveBeenCalledTimes(2); // the failure is kept, not read again on each request

    const never = new SharedRead<string>(async () => { throw new Error('down'); }, 1000);
    await expect(never.get(0)).rejects.toThrow('down');
    await expect(never.get(999)).rejects.toThrow('down');
  });
});
