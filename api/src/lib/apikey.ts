import { randomBytes, createHash } from 'crypto';

export const API_KEY_PREFIX = 'sk_zoey_';

/**
 * Personal access tokens: a human authenticating from something with no cookie
 * jar. A different prefix from an agent key on purpose — the two authenticate
 * different kinds of caller and reach different endpoints, so a token pasted
 * where a key belongs must fail on its shape rather than on a hash lookup that
 * cannot say which it was.
 */
export const USER_TOKEN_PREFIX = 'pk_zoey_';

/**
 * Keys minted before the project was renamed carry the old prefix. They stay
 * valid: only the raw key's sha256 is stored, and that hash covers the prefix,
 * so the format check is the only thing that ever has to know the old name.
 * Nothing mints these any more. Drop this once every agent has been rotated
 * onto an API_KEY_PREFIX key.
 */
export const LEGACY_API_KEY_PREFIX = 'sk_bong_';

// Full key = prefix + 64 hex chars. Used to cheaply reject malformed tokens
// before any database lookup. Accepts either prefix; the hash lookup in
// resolveAgentToken is what actually authenticates a key.
export const API_KEY_PATTERN = /^sk_(?:zoey|bong)_[0-9a-f]{64}$/;

// Same shape, user side. No legacy prefix: nothing minted these before now.
export const USER_TOKEN_PATTERN = /^pk_zoey_[0-9a-f]{64}$/;

export function generateApiKey(): { raw: string; hashed: string } {
  const raw = `${API_KEY_PREFIX}${randomBytes(32).toString('hex')}`;
  return { raw, hashed: hashApiKey(raw) };
}

export function generateUserToken(): { raw: string; hashed: string } {
  const raw = `${USER_TOKEN_PREFIX}${randomBytes(32).toString('hex')}`;
  return { raw, hashed: hashApiKey(raw) };
}

export function hashApiKey(key: string): string {
  return createHash('sha256').update(key).digest('hex');
}

export function isAgentToken(token: string | undefined): token is string {
  return (
    typeof token === 'string' &&
    (token.startsWith(API_KEY_PREFIX) || token.startsWith(LEGACY_API_KEY_PREFIX))
  );
}

export function isWellFormedAgentToken(token: string | undefined): token is string {
  return typeof token === 'string' && API_KEY_PATTERN.test(token);
}

export function isUserToken(token: string | undefined): token is string {
  return typeof token === 'string' && token.startsWith(USER_TOKEN_PREFIX);
}

export function isWellFormedUserToken(token: string | undefined): token is string {
  return typeof token === 'string' && USER_TOKEN_PATTERN.test(token);
}

/** Read the bearer token from an Authorization header, or undefined. */
export function bearerToken(authorization: string | undefined): string | undefined {
  if (!authorization) return undefined;
  const [scheme, value] = authorization.split(' ', 2);
  if (scheme?.toLowerCase() !== 'bearer' || !value) return undefined;
  return value.trim();
}
