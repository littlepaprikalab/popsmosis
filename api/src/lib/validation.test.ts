import { describe, it, expect } from 'vitest';
import { validateString, containsPersonalInfo, containsSecret, secretError, parseLimit } from './validation.js';

describe('validateString', () => {
  it('requires when asked', () => {
    expect(validateString(undefined, 'content', { required: true })).toBe('content is required');
    expect(validateString('', 'content', { required: true })).toBe('content is required');
    expect(validateString(undefined, 'content')).toBeNull();
  });
  it('checks type and bounds', () => {
    expect(validateString(42, 'content')).toBe('content must be a string');
    expect(validateString('   hi', 'content', { minLength: 10 })).toBe('content must be at least 10 characters');
    expect(validateString('x'.repeat(11), 'content', { maxLength: 10 })).toBe('content must be 10 characters or less');
    expect(validateString('hello world', 'content', { minLength: 5, maxLength: 20 })).toBeNull();
  });
});

describe('containsPersonalInfo', () => {
  it('flags emails and phone numbers', () => {
    expect(containsPersonalInfo('mail me at a.b@example.com')).toBe(true);
    expect(containsPersonalInfo('call 555-123-4567')).toBe(true);
    expect(containsPersonalInfo('call 555 123 4567')).toBe(true);
    expect(containsPersonalInfo('how do I reset my password?')).toBe(false);
  });
  it('lets digits inside actor and asset names through', () => {
    expect(containsPersonalInfo('call me on 555-123-4567.')).toBe(true);
    expect(containsPersonalInfo('move SM_Village_Dirt_W_Green120-323221049 up')).toBe(false);
    expect(containsPersonalInfo('move SM_Village_Dirt_W_Green120-3232210491 up')).toBe(false);
    expect(containsPersonalInfo('select BP_Torch_5551234567 and Rock-555-123-4567')).toBe(false);
  });
});

describe('parseLimit', () => {
  it('falls back and caps', () => {
    expect(parseLimit(undefined, 20, 50)).toBe(20);
    expect(parseLimit('abc', 20, 50)).toBe(20);
    expect(parseLimit('0', 20, 50)).toBe(20);
    expect(parseLimit('10', 20, 50)).toBe(10);
    expect(parseLimit('999', 20, 50)).toBe(50);
  });
});

describe('containsSecret', () => {
  it('flags the keys the teacher holds, and an environment dump', () => {
    for (const leak of [
      `here: sk_pop_${'ab12'.repeat(16)}`,
      'sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv',
      'AIzaSyD-1234567890abcdefghijklmnopqrstu',
      `sk_${'0f'.repeat(24)}`,
      'sb_secret_AbCdEf123456',
      'sk_inkmory_abcdef1234567890',
      'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig',
      '-----BEGIN RSA PRIVATE KEY-----',
      'POPSMOSIS_ANTHROPIC_API_KEY=whatever-it-is',
      'GEMINI_API_KEY = abcdefgh12345',
    ]) {
      expect(containsSecret(leak), leak).toBe(true);
    }
  });
  it('lets lessons, lyrics and replies through, including ones about keys', () => {
    for (const text of [
      'ATP synthase turns as protons flow back across the membrane.',
      'A public key encrypts; only the private key decrypts.',
      'In RSA, KEY = (n, e) is the public half.',
      'The sk_ prefix in this lesson is just an example.',
      'Set API_KEY=<yours> in your own .env file.',
    ]) {
      expect(containsSecret(text), text).toBe(false);
    }
  });
  it('reads every text and JSON it is given', () => {
    expect(secretError('content', 'fine', { lyrics: ['fine', 'GEMINI_API_KEY=abcdefgh12345'] })).toMatch(/content looks like it holds a key/);
    expect(secretError('content', 'fine', undefined, { lyrics: ['fine'] })).toBeNull();
  });
});
