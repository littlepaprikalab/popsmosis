import { loadSignInPolicy, isAutoApproved } from './signin-policy.js';

/**
 * The access invariants, in one place, applied on every path a session is
 * resolved rather than only at sign-in.
 *
 * Roles and access status are written from several places — the USERS page, the
 * user:promote script, a SQL console, ADMIN_EMAILS at sign-in — and only some of
 * them keep the two in step. A row that says role ADMIN + status PENDING wedges
 * that account on the waiting page forever: every route except /auth/me answers
 * 403 PENDING_APPROVAL, /admin/users included, so the person who is supposed to
 * do the approving cannot reach the page that would approve them. Same shape
 * when an operator adds an address to ADMIN_EMAILS to break a deadlock: the
 * sign-in route is the only thing that reads that list, so an already-signed-in
 * session keeps polling a page that will never change.
 *
 * accessFix answers "what is wrong with this row, given the current config?" so
 * both the sign-in route and the session resolver can repair it.
 */

export type Role = 'USER' | 'ADMIN';
export type Status = 'PENDING' | 'APPROVED' | 'DENIED';

export interface AccessRow {
  email: string;
  role: Role;
  status: Status;
}

/** The columns to write, or null when the row already agrees with the config. */
export interface AccessFix {
  role?: 'ADMIN';
  status?: 'APPROVED';
}

// isAdminEmail runs on every auth-cache miss, so the split does not belong on
// that path. The environment is fixed for the life of the process, so key the
// parse on the raw string: unchanged config costs one map lookup. (Tests change
// env between cases, hence a map rather than a single memo; it is cleared long
// before it could grow.)
const listCache = new Map<string, string[]>();

function parseList(raw: string | undefined): string[] {
  const key = raw || '';
  let list = listCache.get(key);
  if (!list) {
    if (listCache.size > 8) listCache.clear();
    list = key.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
    listCache.set(key, list);
  }
  return list;
}

/** Addresses the operator has made admins through the environment. */
export function isAdminEmail(email: string, env: NodeJS.ProcessEnv = process.env): boolean {
  return parseList(env.ADMIN_EMAILS).includes(email.trim().toLowerCase());
}

export function accessFix(user: AccessRow, env: NodeJS.ProcessEnv = process.env): AccessFix | null {
  const email = user.email.trim().toLowerCase();

  // 1. ADMIN_EMAILS is the operator's config and outranks an admin's earlier
  //    decision, including DENIED: it is the documented way out of "nobody can
  //    approve anyone", so it has to work on a session that is already open.
  if (isAdminEmail(email, env)) {
    const fix: AccessFix = {};
    if (user.role !== 'ADMIN') fix.role = 'ADMIN';
    if (user.status !== 'APPROVED') fix.status = 'APPROVED';
    return fix.role || fix.status ? fix : null;
  }

  // 2. An admin is never gated. Whatever granted the role — the USERS page, the
  //    script, an UPDATE in a SQL console — grants the access that makes it
  //    usable; a pending or denied admin is a contradiction, not a state.
  if (user.role === 'ADMIN' && user.status !== 'APPROVED') return { status: 'APPROVED' };

  // 3. Someone waiting whose address is on an auto-approve list added since they
  //    signed in stops waiting. DENIED stays denied: an admin said no on purpose.
  if (user.status === 'PENDING' && isAutoApproved(email, loadSignInPolicy(env))) return { status: 'APPROVED' };

  return null;
}

/**
 * The update payload for a fix. reviewedAt tracks when status last changed, and
 * reviewedById the admin behind it — so an automatic approval has to clear it,
 * or a row approved by config keeps crediting whichever admin denied them, with
 * a reviewedAt that says it happened just now. Null is what the schema means by
 * "nobody reviewed this", the same as an auto-approval at sign-in. A fix that
 * only grants the role is not a review and touches neither column.
 */
export function accessFixData(fix: AccessFix) {
  return { ...fix, ...(fix.status ? { reviewedAt: new Date(), reviewedById: null } : {}) };
}
