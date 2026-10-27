/**
 * Which version of the Terms of Use and Privacy Policy people have agreed to (web/src/app/terms, web/src/app/privacy).
 *
 * Signing in needs the box ticked (POST /api/auth/google, acceptTerms), and the account keeps the version and the time,
 * as the record that this person agreed. Change TERMS_VERSION when the pages change in a way people must agree to again
 * (what is collected or published, how disputes are settled, the limits on liability): every account then reads as not
 * current, and the web app asks once, at the next visit, before anything else (components/TermsGate.tsx). A typo fix
 * moves LAST_UPDATED in web/src/lib/legal.ts and leaves this alone.
 */
export const TERMS_VERSION = '2026-10-27';

export function termsCurrent(user: { termsVersion: string | null }): boolean {
  return user.termsVersion === TERMS_VERSION;
}

/** What an account stores when its person agrees: the version they saw, and when. */
export function termsAccepted(now: Date = new Date()) {
  return { termsVersion: TERMS_VERSION, termsAcceptedAt: now };
}
