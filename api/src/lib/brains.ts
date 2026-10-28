/**
 * The brains an admin may pick for a topic on /learn, so a test topic can run on a cheaper model, or on
 * another account's credit, than the default. Stored on the question as `brain`; null, as every learner's
 * topic is, leaves it to the default brain an admin sets on /admin/studio (lib/studio-settings.ts).
 *
 * The studio maps each key to its provider and model (studio/brain.py, CHOICES), and the painter follows the
 * provider, as set on /admin/studio. Keep the two lists the same.
 */

export const BRAINS = ['claude', 'muse-wonder', 'muse-spark'] as const;
export type Brain = (typeof BRAINS)[number];

export function isBrain(value: unknown): value is Brain {
  return typeof value === 'string' && (BRAINS as readonly string[]).includes(value);
}

/**
 * The brain a new topic asks for: a key of BRAINS, or null for the teacher's own. `error` says why the
 * value cannot be taken; `forbidden` when the caller may not pick one at all.
 */
export function parseBrain(value: unknown, isAdmin: boolean): { brain: Brain | null; error?: string; forbidden?: boolean } {
  if (value === undefined || value === null || value === '') return { brain: null };
  if (!isBrain(value)) return { brain: null, error: `brain must be one of ${BRAINS.join(', ')}` };
  if (!isAdmin) return { brain: null, error: 'Only an admin can pick the brain for a topic', forbidden: true };
  return { brain: value };
}
