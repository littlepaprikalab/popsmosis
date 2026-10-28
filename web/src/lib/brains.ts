/**
 * The brains an admin may pick for a new topic on /learn (api/src/lib/brains.ts). Null is the teacher's own
 * setting (BRAIN_PROVIDER where the studio runs), and the only one a learner's topic gets. The brain writes and
 * fact-checks the lesson; who paints the pictures goes by its provider, as set on /admin/studio.
 */

export type Brain = 'claude' | 'muse-wonder' | 'muse-spark';

export const BRAIN_OPTIONS: { value: Brain | null; label: string; note: string }[] = [
  { value: null, label: 'Default', note: 'Whatever the teacher is set to (BRAIN_PROVIDER where the studio runs).' },
  { value: 'claude', label: 'Claude', note: 'Claude Opus 5.5 writes and checks it, billed to the Anthropic key. The pictures: the Studio page’s painter for Claude (Gemini 3.1 Flash Image by default).' },
  { value: 'muse-wonder', label: 'Muse Wonder', note: 'Muse Wonder 1.0 writes and checks it, billed to the Meta key. The pictures: the Studio page’s painter for Muse (Muse Image by default).' },
  { value: 'muse-spark', label: 'Muse Spark', note: 'Muse Spark 1.3 writes and checks it, at about a quarter of Wonder’s price, billed to the Meta key. The pictures: the Studio page’s painter for Muse (Muse Image by default).' },
];

export function brainLabel(value: string | null | undefined): string | null {
  return BRAIN_OPTIONS.find((o) => o.value === value && value)?.label ?? null;
}

const STORAGE_KEY = 'popsmosis.brain';

/** The brain this admin picked last on this browser, so a run of test topics stays on it. */
export function rememberedBrain(): Brain | null {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return BRAIN_OPTIONS.some((o) => o.value === saved) ? (saved as Brain) : null;
  } catch {
    return null;
  }
}

export function rememberBrain(value: Brain | null): void {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, value);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private mode or blocked storage: the pick just isn't remembered.
  }
}
