import { describe, it, expect } from 'vitest';
import {
  generateApiKey,
  hashApiKey,
  isAgentToken,
  isWellFormedAgentToken,
  bearerToken,
} from './apikey.js';

describe('apikey', () => {
  it('generates a prefixed 64-hex key and its sha256 hash', () => {
    const { raw, hashed } = generateApiKey();
    expect(raw).toMatch(/^sk_pop_[0-9a-f]{64}$/);
    expect(hashed).toBe(hashApiKey(raw));
    expect(hashed).toMatch(/^[0-9a-f]{64}$/);
    expect(hashed).not.toContain(raw);
  });

  it('never generates the same key twice', () => {
    expect(generateApiKey().raw).not.toBe(generateApiKey().raw);
  });

  it('classifies tokens', () => {
    const { raw } = generateApiKey();
    expect(isAgentToken(raw)).toBe(true);
    expect(isWellFormedAgentToken(raw)).toBe(true);
    expect(isAgentToken('sk_pop_short')).toBe(true);
    expect(isWellFormedAgentToken('sk_pop_short')).toBe(false);
    expect(isAgentToken('sk_sparks_' + 'a'.repeat(64))).toBe(false);
    expect(isAgentToken(undefined)).toBe(false);
  });

  it('does not take a key minted by zoey, which popsmosis was copied from', () => {
    expect(isAgentToken(`sk_zoey_${'a'.repeat(64)}`)).toBe(false);
    expect(isWellFormedAgentToken(`sk_zoey_${'a'.repeat(64)}`)).toBe(false);
  });

  it('parses bearer headers', () => {
    expect(bearerToken('Bearer abc')).toBe('abc');
    expect(bearerToken('bearer abc')).toBe('abc');
    expect(bearerToken('Basic abc')).toBeUndefined();
    expect(bearerToken(undefined)).toBeUndefined();
    expect(bearerToken('Bearer')).toBeUndefined();
  });
});
