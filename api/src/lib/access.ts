/**
 * ADMIN_EMAILS, applied on every path a session is resolved rather than only at
 * sign-in. Anyone with a Google account can sign in and use the site; the one
 * thing the config decides is who is an admin.
 *
 * The sign-in route is not the only place that has to read the list: an
 * operator who adds an address to break "nobody is an admin" expects it to work
 * on a session that is already open, without a new sign-in. needsAdminRole
 * answers "does this row disagree with the current config?" so both the
 * sign-in route and the session resolver can repair it.
 */

export type Role = 'USER' | 'ADMIN';

export interface AccessRow {
  email: string;
  role: Role;
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

/**
 * True when ADMIN_EMAILS makes this account an admin and the row does not say
 * so yet. The config outranks an admin's earlier demotion: it is the documented
 * way out of "nobody can manage anything", so it has to win.
 */
export function needsAdminRole(user: AccessRow, env: NodeJS.ProcessEnv = process.env): boolean {
  return user.role !== 'ADMIN' && isAdminEmail(user.email, env);
}
