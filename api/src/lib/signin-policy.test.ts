import { describe, it, expect } from 'vitest';
import { loadSignInPolicy, isAutoApproved, isEmptyPolicy } from './signin-policy.js';

describe('sign-in policy (auto-approve list)', () => {
  it('auto-approves nobody when nothing is configured: everyone waits for an admin', () => {
    const p = loadSignInPolicy({});
    expect(isEmptyPolicy(p)).toBe(true);
    expect(isAutoApproved('anyone@gmail.com', p)).toBe(false);
  });

  it('auto-approves listed domains, including subdomains, case-insensitively', () => {
    const p = loadSignInPolicy({ ALLOWED_EMAIL_DOMAINS: 'Example.com, @partner.org' });
    expect(isEmptyPolicy(p)).toBe(false);
    expect(isAutoApproved('Ada@example.com', p)).toBe(true);
    expect(isAutoApproved('ben@mail.example.com', p)).toBe(true);
    expect(isAutoApproved('x@partner.org', p)).toBe(true);
    expect(isAutoApproved('x@notexample.com', p)).toBe(false);
    expect(isAutoApproved('x@example.com.evil.net', p)).toBe(false);
    expect(isAutoApproved('x@gmail.com', p)).toBe(false);
  });

  it('auto-approves exact emails alongside domains', () => {
    const p = loadSignInPolicy({ ALLOWED_EMAIL_DOMAINS: 'example.com', ALLOWED_EMAILS: 'Contractor@Gmail.com' });
    expect(isAutoApproved('contractor@gmail.com', p)).toBe(true);
    expect(isAutoApproved('other@gmail.com', p)).toBe(false);
    expect(isAutoApproved('ada@example.com', p)).toBe(true);
  });

  it('emails-only policy leaves everyone else pending', () => {
    const p = loadSignInPolicy({ ALLOWED_EMAILS: 'a@x.com' });
    expect(isEmptyPolicy(p)).toBe(false);
    expect(isAutoApproved('a@x.com', p)).toBe(true);
    expect(isAutoApproved('b@x.com', p)).toBe(false);
  });
});
