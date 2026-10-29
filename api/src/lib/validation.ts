/**
 * Validates a string field. Returns null if valid, or an error message.
 */
export function validateString(
  value: unknown,
  fieldName: string,
  opts: { required?: boolean; minLength?: number; maxLength?: number } = {},
): string | null {
  const { required = false, minLength = 0, maxLength = 10000 } = opts;

  if (value === null || value === undefined || value === '') {
    return required ? `${fieldName} is required` : null;
  }
  if (typeof value !== 'string') {
    return `${fieldName} must be a string`;
  }
  if (value.trim().length < minLength) {
    return `${fieldName} must be at least ${minLength} characters`;
  }
  if (value.length > maxLength) {
    return `${fieldName} must be ${maxLength} characters or less`;
  }
  return null;
}

/**
 * Basic content filter for questions and replies, copied from the sparks
 * advice column: block obvious personal-info patterns (emails, phone numbers).
 */
export function containsPersonalInfo(text: string): boolean {
  if (/\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/.test(text)) return true;
  if (/(?<![\w-])\d{3}[-.\s]?\d{3}[-.\s]?\d{4}(?![\w-])/.test(text)) return true; // not inside a name like SM_Dirt_W_Green120-3232210491
  return false;
}

/**
 * Whether text an agent wants to post looks like it carries a key or an environment dump. The teacher is an LLM that
 * holds the agent key and the providers' keys and reads text any learner can write; if that text talks it into
 * pasting a key, the lesson, the reply or the failure reason is the way out. This refuses the plain forms. It cannot
 * catch a key the agent was talked into encoding, so it backs up the skill file's rules rather than replacing them.
 */
const SECRET_PATTERNS: RegExp[] = [
  /sk_pop_[0-9a-f]{16,}/i, // popsmosis agent keys
  /sk-ant-[A-Za-z0-9_-]{16,}/, // Anthropic
  /AIza[0-9A-Za-z_-]{30,}/, // Google (Gemini)
  /\bsk_[0-9a-f]{40,}\b/i, // ElevenLabs
  /sb_secret_[A-Za-z0-9_-]{10,}/, // Supabase
  /sk_inkmory_[A-Za-z0-9_-]{10,}/, // Inkmory
  /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\./, // a JWT
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  // An environment variable that names a key, token, secret or password, with a value: what `printenv` prints.
  /\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*\S{8,}/,
];

export function containsSecret(text: string): boolean {
  return SECRET_PATTERNS.some((p) => p.test(text));
}

/** The refusal for agent text that containsSecret flags, or null. */
export function secretError(field: string, ...texts: unknown[]): string | null {
  const flagged = texts.some((t) => t !== undefined && containsSecret(typeof t === 'string' ? t : JSON.stringify(t)));
  return flagged ? `${field} looks like it holds a key or a secret, so it was not posted` : null;
}

export function parseLimit(raw: string | undefined, fallback: number, max: number): number {
  const n = parseInt(raw ?? '', 10);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(n, max);
}
