import { describe, it, expect } from 'vitest';
import { accessFix, accessFixData, isAdminEmail } from './access.js';

const row = (over: Partial<{ email: string; role: 'USER' | 'ADMIN'; status: 'PENDING' | 'APPROVED' | 'DENIED' }> = {}) =>
  ({ email: 'ada@example.com', role: 'USER' as const, status: 'PENDING' as const, ...over });

describe('access invariants', () => {
  it('leaves a row alone when it already agrees with the config', () => {
    expect(accessFix(row({ status: 'APPROVED' }), {})).toBeNull();
    expect(accessFix(row({ role: 'ADMIN', status: 'APPROVED' }), {})).toBeNull();
    expect(accessFix(row(), {})).toBeNull(); // waiting, and nothing says otherwise: keep waiting
    expect(accessFix(row({ status: 'DENIED' }), {})).toBeNull();
  });

  it('approves an admin who was left pending — the case that wedges the waiting page', () => {
    // `UPDATE users SET role = 'ADMIN'` in a SQL console, with no status change.
    expect(accessFix(row({ role: 'ADMIN' }), {})).toEqual({ status: 'APPROVED' });
    expect(accessFix(row({ role: 'ADMIN', status: 'DENIED' }), {})).toEqual({ status: 'APPROVED' });
  });

  it('promotes and approves an ADMIN_EMAILS address without a new sign-in', () => {
    const env = { ADMIN_EMAILS: 'Ada@Example.com, root@example.com' };
    expect(accessFix(row(), env)).toEqual({ role: 'ADMIN', status: 'APPROVED' });
    // Already an admin, still waiting: only the status needs writing.
    expect(accessFix(row({ role: 'ADMIN' }), env)).toEqual({ status: 'APPROVED' });
    // Approved but not yet an admin: only the role.
    expect(accessFix(row({ status: 'APPROVED' }), env)).toEqual({ role: 'ADMIN' });
    expect(accessFix(row({ role: 'ADMIN', status: 'APPROVED' }), env)).toBeNull();
  });

  it('lets ADMIN_EMAILS override a denial: it is the way out of a deadlock', () => {
    expect(accessFix(row({ status: 'DENIED' }), { ADMIN_EMAILS: 'ada@example.com' }))
      .toEqual({ role: 'ADMIN', status: 'APPROVED' });
  });

  it('approves someone waiting whose address joined an auto-approve list', () => {
    expect(accessFix(row(), { ALLOWED_EMAIL_DOMAINS: 'example.com' })).toEqual({ status: 'APPROVED' });
    expect(accessFix(row(), { ALLOWED_EMAILS: 'ada@example.com' })).toEqual({ status: 'APPROVED' });
    expect(accessFix(row({ email: 'ben@other.com' }), { ALLOWED_EMAIL_DOMAINS: 'example.com' })).toBeNull();
  });

  it('keeps a denial that an admin made on purpose', () => {
    // The auto-approve lists let strangers skip the queue; they do not reverse a decision.
    expect(accessFix(row({ status: 'DENIED' }), { ALLOWED_EMAIL_DOMAINS: 'example.com' })).toBeNull();
    expect(accessFix(row({ status: 'DENIED' }), { ALLOWED_EMAILS: 'ada@example.com' })).toBeNull();
  });

  it('reads ADMIN_EMAILS case- and whitespace-insensitively', () => {
    expect(isAdminEmail('ada@example.com', { ADMIN_EMAILS: ' Ada@Example.com , b@x.com' })).toBe(true);
    expect(isAdminEmail('b@x.com', { ADMIN_EMAILS: ' Ada@Example.com , b@x.com' })).toBe(true);
    expect(isAdminEmail('ada@example.com', {})).toBe(false);
    expect(isAdminEmail('ada@example.com', { ADMIN_EMAILS: '' })).toBe(false);
  });

  it('stamps reviewedAt only when the fix changes the access status', () => {
    expect(accessFixData({ status: 'APPROVED' }).reviewedAt).toBeInstanceOf(Date);
    expect(accessFixData({ role: 'ADMIN' })).toEqual({ role: 'ADMIN' });
  });

  it('clears the reviewer on an automatic approval so it does not credit the wrong admin', () => {
    // Approving a row an admin had denied leaves reviewedAt saying "just now";
    // keeping their reviewedById would read as that admin having approved them.
    expect(accessFixData({ status: 'APPROVED' }).reviewedById).toBeNull();
    expect(accessFixData({ role: 'ADMIN' })).not.toHaveProperty('reviewedById'); // a role grant is not a review
  });

  it('is idempotent: applying a fix leaves nothing more to fix', () => {
    // The resolver runs on every cache miss, so a fix that did not converge
    // would rewrite the same row every 30s for the life of the account.
    const envs = [{}, { ADMIN_EMAILS: 'ada@example.com' }, { ALLOWED_EMAIL_DOMAINS: 'example.com' }, { ALLOWED_EMAILS: 'ada@example.com' }];
    const roles = ['USER', 'ADMIN'] as const;
    const statuses = ['PENDING', 'APPROVED', 'DENIED'] as const;
    for (const env of envs) {
      for (const role of roles) {
        for (const status of statuses) {
          const start = row({ role, status });
          const fix = accessFix(start, env);
          if (!fix) continue;
          expect(accessFix({ ...start, ...fix }, env)).toBeNull();
        }
      }
    }
  });
});
