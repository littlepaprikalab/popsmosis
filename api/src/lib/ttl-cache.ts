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
