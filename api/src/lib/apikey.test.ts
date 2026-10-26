import { describe, it, expect } from 'vitest';
import {
  generateApiKey,
  hashApiKey,
  isAgentToken,
  isWellFormedAgentToken,
  bearerToken,
  generateUserToken,
  isUserToken,
  isWellFormedUserToken,
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

  it('does not take a key or a token minted by zoey, which popsmosis was copied from', () => {
    expect(isAgentToken(`sk_zoey_${'a'.repeat(64)}`)).toBe(false);
    expect(isWellFormedAgentToken(`sk_zoey_${'a'.repeat(64)}`)).toBe(false);
    expect(isUserToken(`pk_zoey_${'a'.repeat(64)}`)).toBe(false);
  });

  it('generates a prefixed 64-hex personal access token and its sha256 hash', () => {
    const { raw, hashed } = generateUserToken();
    expect(raw).toMatch(/^pk_pop_[0-9a-f]{64}$/);
    expect(hashed).toBe(hashApiKey(raw));
    expect(generateUserToken().raw).not.toBe(generateUserToken().raw);
  });

  it('keeps agent keys and personal access tokens from being mistaken for each other', () => {
    const agent = generateApiKey().raw;
    const user = generateUserToken().raw;
    expect(isAgentToken(user)).toBe(false);
    expect(isWellFormedAgentToken(user)).toBe(false);
    expect(isUserToken(agent)).toBe(false);
    expect(isWellFormedUserToken(agent)).toBe(false);
    expect(isUserToken(user)).toBe(true);
    expect(isWellFormedUserToken(user)).toBe(true);
  });

  it('rejects malformed personal access tokens on shape alone', () => {
    expect(isUserToken('pk_pop_short')).toBe(true); // the prefix says which kind it is...
    expect(isWellFormedUserToken('pk_pop_short')).toBe(false); // ...the pattern says whether it could be real
    expect(isWellFormedUserToken(`pk_pop_${'A'.repeat(64)}`)).toBe(false); // hex is lowercase
    expect(isWellFormedUserToken(`pk_pop_${'a'.repeat(63)}`)).toBe(false);
    expect(isWellFormedUserToken(`pk_pop_${'a'.repeat(65)}`)).toBe(false);
    expect(isUserToken(undefined)).toBe(false);
  });

  it('parses bearer headers', () => {
    expect(bearerToken('Bearer abc')).toBe('abc');
    expect(bearerToken('bearer abc')).toBe('abc');
    expect(bearerToken('Basic abc')).toBeUndefined();
    expect(bearerToken(undefined)).toBeUndefined();
    expect(bearerToken('Bearer')).toBeUndefined();
  });
});
