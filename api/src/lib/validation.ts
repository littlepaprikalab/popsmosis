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

export function parseLimit(raw: string | undefined, fallback: number, max: number): number {
  const n = parseInt(raw ?? '', 10);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(n, max);
}
