import { randomBytes, createHash } from 'crypto';

export const API_KEY_PREFIX = 'sk_pop_';

// Full key = prefix + 64 hex chars. Used to cheaply reject malformed tokens
// before any database lookup; the hash lookup in resolveAgentToken is what
// actually authenticates a key.
export const API_KEY_PATTERN = /^sk_pop_[0-9a-f]{64}$/;

export function generateApiKey(): { raw: string; hashed: string } {
  const raw = `${API_KEY_PREFIX}${randomBytes(32).toString('hex')}`;
  return { raw, hashed: hashApiKey(raw) };
}

export function hashApiKey(key: string): string {
  return createHash('sha256').update(key).digest('hex');
}

export function isAgentToken(token: string | undefined): token is string {
  return typeof token === 'string' && token.startsWith(API_KEY_PREFIX);
}

export function isWellFormedAgentToken(token: string | undefined): token is string {
  return typeof token === 'string' && API_KEY_PATTERN.test(token);
}

/** Read the bearer token from an Authorization header, or undefined. */
export function bearerToken(authorization: string | undefined): string | undefined {
  if (!authorization) return undefined;
  const [scheme, value] = authorization.split(' ', 2);
  if (scheme?.toLowerCase() !== 'bearer' || !value) return undefined;
  return value.trim();
}
