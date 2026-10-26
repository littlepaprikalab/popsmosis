/**
 * Who gets in without waiting. popsmosis is an internal tool: any Google account can
 * sign in, but a new account starts PENDING and sees only a waiting page until
 * an admin approves it on /admin/users. These lists skip the queue:
 *
 *   ALLOWED_EMAIL_DOMAINS=example.com,partner.org   auto-approve these domains (and subdomains)
 *   ALLOWED_EMAILS=a@gmail.com,b@gmail.com          auto-approve these exact addresses
 *   ADMIN_EMAILS (read in routes/auth.ts)           auto-approve and make admin
 *
 * With both lists empty every new sign-in waits for an admin. That is the safe
 * default: a blank config can no longer expose every question to the internet.
 */

function parseList(raw: string | undefined): string[] {
  return (raw || '')
    .split(',')
    .map((s) => s.trim().toLowerCase().replace(/^@/, ''))
    .filter(Boolean);
}

export interface SignInPolicy {
  domains: string[];
  emails: string[];
}

export function loadSignInPolicy(env: NodeJS.ProcessEnv = process.env): SignInPolicy {
  return { domains: parseList(env.ALLOWED_EMAIL_DOMAINS), emails: parseList(env.ALLOWED_EMAILS) };
}

/** No auto-approve list at all: every new account waits for an admin. */
export function isEmptyPolicy(policy: SignInPolicy): boolean {
  return policy.domains.length === 0 && policy.emails.length === 0;
}

/** True when the address may skip the approval queue. */
export function isAutoApproved(email: string, policy: SignInPolicy): boolean {
  const normalized = email.trim().toLowerCase();
  if (policy.emails.includes(normalized)) return true;
  const domain = normalized.split('@')[1] ?? '';
  return policy.domains.some((d) => domain === d || domain.endsWith(`.${d}`));
}
