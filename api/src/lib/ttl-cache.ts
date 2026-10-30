/**
 * Tiny in-process TTL cache with a size cap (oldest entry evicted first).
 * Not shared across instances; use it only where a short stale window is fine.
 */
export class TtlCache<V> {
  private map = new Map<string, { value: V; expiresAt: number }>();

  constructor(private maxEntries = 1000) {}

  get(key: string): V | undefined {
    const hit = this.map.get(key);
    if (!hit) return undefined;
    if (hit.expiresAt <= Date.now()) {
      this.map.delete(key);
      return undefined;
    }
    return hit.value;
  }

  set(key: string, value: V, ttlMs: number): void {
    if (this.map.size >= this.maxEntries && !this.map.has(key)) {
      const oldest = this.map.keys().next().value;
      if (oldest !== undefined) this.map.delete(oldest);
    }
    this.map.delete(key); // re-insert so insertion order reflects recency
    this.map.set(key, { value, expiresAt: Date.now() + ttlMs });
  }

  delete(key: string): void {
    this.map.delete(key);
  }

  clear(): void {
    this.map.clear();
  }

  get size(): number {
    return this.map.size;
  }
}

/**
 * One value read from somewhere slower than memory (the database), shared by every caller on this instance and kept
 * for ttlMs. Callers that ask while it is being read wait on that one read, so the value running out under load costs
 * one read, not one per request in flight. set() puts a value the instance just wrote in force at once, and a read
 * that started before it never brings the old value back. A read that fails gives the last value read or set, if
 * there is one, and is kept like any other, so a database that is down is asked once per ttlMs, not on every request.
 */
export class SharedRead<V> {
  private kept: { value: Promise<V>; expiresAt: number } | undefined;
  private last: { value: V } | undefined;

  constructor(private readonly read: () => Promise<V>, private readonly ttlMs: number) {}

  get(now = Date.now()): Promise<V> {
    if (this.kept && now < this.kept.expiresAt) return this.kept.value;
    const value: Promise<V> = this.read().then(
      (read) => {
        if (this.kept?.value === value) this.last = { value: read };
        return read;
      },
      (err) => {
        if (this.last) return this.last.value;
        throw err;
      },
    );
    this.kept = { value, expiresAt: now + this.ttlMs };
    return value;
  }

  set(value: V, now = Date.now()): void {
    this.last = { value };
    this.kept = { value: Promise.resolve(value), expiresAt: now + this.ttlMs };
  }
}
