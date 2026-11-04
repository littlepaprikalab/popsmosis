/**
 * The brains a topic may be sent to on /learn, so it can run on another model, or on another account's credit, than
 * the default. Stored on the question as `brain`; null leaves it to the default brain an admin sets on /admin/studio
 * (lib/studio-settings.ts). Anyone signed in may pick any of them. A learner types an apology before picking Muse
 * Wonder (web/src/lib/brains.ts); that is the page's, so the API takes it from anyone. While an admin has the default
 * make every topic (`defaultOnly` there), no pick is kept and the studio ignores one made before.
 *
 * The studio maps each key to its provider and model (studio/brain.py, CHOICES), and the painter follows the
 * provider, as set on /admin/studio. Keep the two lists the same.
 */

export const BRAINS = ['claude', 'muse-wonder', 'muse-spark'] as const;
export type Brain = (typeof BRAINS)[number];

export function isBrain(value: unknown): value is Brain {
  return typeof value === 'string' && (BRAINS as readonly string[]).includes(value);
}

/** The brain a new topic asks for: a key of BRAINS, or null for the default. `error` says why the value cannot be taken. */
export function parseBrain(value: unknown): { brain: Brain | null; error?: string } {
  if (value === undefined || value === null || value === '') return { brain: null };
  if (!isBrain(value)) return { brain: null, error: `brain must be one of ${BRAINS.join(', ')}` };
  return { brain: value };
}
