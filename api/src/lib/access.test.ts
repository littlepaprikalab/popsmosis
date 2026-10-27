import { describe, it, expect } from 'vitest';
import { needsAdminRole, isAdminEmail } from './access.js';

const row = (over: Partial<{ email: string; role: 'USER' | 'ADMIN' }> = {}) =>
  ({ email: 'ada@example.com', role: 'USER' as const, ...over });

describe('ADMIN_EMAILS', () => {
  it('leaves a row alone when it already agrees with the config', () => {
    expect(needsAdminRole(row(), {})).toBe(false);
    expect(needsAdminRole(row({ role: 'ADMIN' }), {})).toBe(false); // granted on the USERS page; the config does not take it away
    expect(needsAdminRole(row({ role: 'ADMIN' }), { ADMIN_EMAILS: 'ada@example.com' })).toBe(false);
  });

  it('promotes an ADMIN_EMAILS address without a new sign-in', () => {
    expect(needsAdminRole(row(), { ADMIN_EMAILS: 'Ada@Example.com, root@example.com' })).toBe(true);
    expect(needsAdminRole(row({ email: 'ben@example.com' }), { ADMIN_EMAILS: 'ada@example.com' })).toBe(false);
  });

  it('reads ADMIN_EMAILS case- and whitespace-insensitively', () => {
    expect(isAdminEmail('ada@example.com', { ADMIN_EMAILS: ' Ada@Example.com , b@x.com' })).toBe(true);
    expect(isAdminEmail('b@x.com', { ADMIN_EMAILS: ' Ada@Example.com , b@x.com' })).toBe(true);
    expect(isAdminEmail(' Ada@Example.com ', { ADMIN_EMAILS: 'ada@example.com' })).toBe(true);
    expect(isAdminEmail('ada@example.com', {})).toBe(false);
    expect(isAdminEmail('ada@example.com', { ADMIN_EMAILS: '' })).toBe(false);
  });
});
