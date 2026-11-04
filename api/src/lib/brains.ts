/**
 * The brains a topic may be sent to on /learn, so it can run on another model, or on another account's credit, than
 * the default. Stored on the question as `brain`; null leaves it to the default brain an admin sets on /admin/studio
 * (lib/studio-settings.ts). Anyone signed in may pick one of LEARNER_BRAINS; the rest are an admin's alone. While an
 * admin has the default make every topic (`defaultOnly` there), no pick is kept and the studio ignores one made before.
 *
 * The studio maps each key to its provider and model (studio/brain.py, CHOICES), and the painter follows the
 * provider, as set on /admin/studio. Keep the two lists the same.
 */

export const BRAINS = ['claude', 'muse-wonder', 'muse-spark'] as const;
export type Brain = (typeof BRAINS)[number];

/** The brains a learner may pick; Muse Wonder is left to admins. Keep web/src/lib/brains.ts the same. */
export const LEARNER_BRAINS: readonly Brain[] = ['claude', 'muse-spark'];

export function isBrain(value: unknown): value is Brain {
  return typeof value === 'string' && (BRAINS as readonly string[]).includes(value);
}

/**
 * The brain a new topic asks for: a key of BRAINS, or null for the default. `error` says why the value cannot be
 * taken; `forbidden` when it is a brain this caller may not pick.
 */
export function parseBrain(value: unknown, isAdmin: boolean): { brain: Brain | null; error?: string; forbidden?: boolean } {
  if (value === undefined || value === null || value === '') return { brain: null };
  if (!isBrain(value)) return { brain: null, error: `brain must be one of ${BRAINS.join(', ')}` };
  if (!isAdmin && !LEARNER_BRAINS.includes(value)) {
    return { brain: null, error: `brain must be one of ${LEARNER_BRAINS.join(', ')}`, forbidden: true };
  }
  return { brain: value };
}
