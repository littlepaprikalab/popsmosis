/**
 * The brains a topic may be sent to on /learn (api/src/lib/brains.ts). Null is the default brain set on
 * /admin/studio. Anyone may pick Claude or Muse Spark; Muse Wonder is shown to admins alone. The brain writes and
 * fact-checks the lesson; who paints the pictures goes by its provider, as set on /admin/studio too.
 */

export type Brain = 'claude' | 'muse-wonder' | 'muse-spark';

export type BrainOption = {
  value: Brain | null;
  label: string;
  note: string; // shown to everyone
  adminNote?: string; // after it, for admins: whose key pays, and who paints
  adminOnly?: true;
};

export const BRAIN_OPTIONS: BrainOption[] = [
  { value: null, label: 'Default', note: 'The teacher’s usual brain.', adminNote: 'The default brain set on the Studio page.' },
  { value: 'claude', label: 'Claude', note: 'Claude Opus 5.5 writes and checks the lesson.', adminNote: 'Billed to the Anthropic key. The pictures: the Studio page’s painter for Claude (Gemini 3.1 Flash Image by default).' },
  { value: 'muse-wonder', label: 'Muse Wonder', adminOnly: true, note: 'Muse Wonder 1.0 writes and checks the lesson.', adminNote: 'Admins only. Billed to the Meta key. The pictures: the Studio page’s painter for Muse (Muse Image by default).' },
  { value: 'muse-spark', label: 'Muse Spark', note: 'Muse Spark 1.3 writes and checks the lesson.', adminNote: 'At about a quarter of Wonder’s price, billed to the Meta key. The pictures: the Studio page’s painter for Muse (Muse Image by default).' },
];

/** The options this viewer may pick from: every one for an admin, the rest without Muse Wonder. */
export function brainOptionsFor(isAdmin: boolean): BrainOption[] {
  return isAdmin ? BRAIN_OPTIONS : BRAIN_OPTIONS.filter((o) => !o.adminOnly);
}

export function brainLabel(value: string | null | undefined): string | null {
  return BRAIN_OPTIONS.find((o) => o.value === value && value)?.label ?? null;
}

// Each brain's model, as the studio runs it (studio/brain.py, CHOICES).
export const BRAIN_MODELS: Record<Brain, string> = {
  claude: 'Claude Opus 5.5',
  'muse-wonder': 'Muse Wonder 1.0',
  'muse-spark': 'Muse Spark 1.3',
};

const STORAGE_KEY = 'popsmosis.brain';

/** The brain picked last on this browser, so a run of topics stays on it; one this viewer may not pick is dropped. */
export function rememberedBrain(isAdmin: boolean): Brain | null {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return brainOptionsFor(isAdmin).some((o) => o.value === saved) ? (saved as Brain) : null;
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
