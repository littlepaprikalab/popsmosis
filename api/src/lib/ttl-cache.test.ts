import { describe, it, expect, vi } from 'vitest';
import { TtlCache } from './ttl-cache.js';

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
