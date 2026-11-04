/**
 * The brains a topic may be sent to on /learn (api/src/lib/brains.ts). Null is the default brain set on
 * /admin/studio. Anyone may pick any of them, but Muse Wonder costs the most by far, so a learner first types
 * APOLOGY. An admin pays the bill and is not asked. The brain writes and fact-checks the lesson; who paints the
 * pictures goes by its provider, as set on /admin/studio too. While an admin has the default brain make every topic
 * (BrainSettings.defaultOnly), /learn hides the picker.
 */

export type Brain = 'claude' | 'muse-wonder' | 'muse-spark';

export type BrainOption = {
  value: Brain | null;
  label: string;
  note: string; // shown to everyone
  adminNote?: string; // after it, for admins: whose key pays, and who paints
  apology?: true; // a learner types APOLOGY before it is picked
};

export const BRAIN_OPTIONS: BrainOption[] = [
  { value: null, label: 'Default', note: 'The teacher’s usual brain.', adminNote: 'The default brain set on the Studio page.' },
  { value: 'claude', label: 'Claude', note: 'Claude Opus 5.5 writes and checks the lesson.', adminNote: 'Billed to the Anthropic key. The pictures: the Studio page’s painter for Claude (Gemini 3.1 Flash Image by default).' },
  { value: 'muse-wonder', label: 'Muse Wonder', apology: true, note: 'Muse Wonder 1.0 writes and checks the lesson.', adminNote: 'Billed to the Meta key; a learner types an apology to pick it. The pictures: the Studio page’s painter for Muse (Muse Image by default).' },
  { value: 'muse-spark', label: 'Muse Spark', note: 'Muse Spark 1.3 writes and checks the lesson.', adminNote: 'At about a quarter of Wonder’s price, billed to the Meta key. The pictures: the Studio page’s painter for Muse (Muse Image by default).' },
];

export function brainLabel(value: string | null | undefined): string | null {
  return BRAIN_OPTIONS.find((o) => o.value === value && value)?.label ?? null;
}

// Each brain's model, as the studio runs it (studio/brain.py, CHOICES).
export const BRAIN_MODELS: Record<Brain, string> = {
  claude: 'Claude Opus 5.5',
  'muse-wonder': 'Muse Wonder 1.0',
  'muse-spark': 'Muse Spark 1.3',
};

/**
 * What a droplet costs on each Muse, roughly, from the Nov 3 test rounds (docs/test-rounds.md): Muse Wonder $4.11
 * (the kidneys), Muse Spark $1.08 (natural selection), both with Muse Image painting: about 4x. The lesson alone ran
 * 4 to 5 times Spark's on the same topics ($1.16 to $1.26 against $0.28 to $0.30 for the bundle and its fact-check),
 * near their prices per million tokens ($4 / $20 against $1.25 / $4.25). When a new round says otherwise, change
 * these and APOLOGY's 4x together.
 */
export const DROPLET_USD = { wonder: 4, spark: 1 };
/** What a learner types before Muse Wonder is theirs. */
export const APOLOGY = 'I know this is costing the developer 4x more than Muse Spark, I am sorry.';

// The letters and digits alone: any case, with or without the commas, the period and the spaces ("4 x" too).
const letters = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, '');
export const typedApology = (typed: string) => letters(typed) === letters(APOLOGY);

const SORRY_KEY = 'popsmosis.brain.sorry';
// Said on this page: what holds the apology when sessionStorage is blocked or full and keeps nothing.
let sorryHere = false;

/** Whether this tab has said sorry already: once is enough until the tab is closed, a reload or a sign-in included. */
function saidSorry(): boolean {
  if (sorryHere) return true;
  try {
    return window.sessionStorage.getItem(SORRY_KEY) === '1';
  } catch {
    return false;
  }
}

export function saySorry(): void {
  sorryHere = true;
  try {
    window.sessionStorage.setItem(SORRY_KEY, '1');
  } catch {
    // Blocked or full: the apology holds until the page is left.
  }
}

/** On a logout: the apology was theirs, not the next person's on this tab. */
export function forgetSorry(): void {
  sorryHere = false;
  try {
    window.sessionStorage.removeItem(SORRY_KEY);
  } catch {
    // Blocked storage: nothing was kept.
  }
}

/** Whether this viewer must type APOLOGY before `value` is picked. */
export function needsApology(value: Brain | null, isAdmin: boolean): boolean {
  return !isAdmin && !!BRAIN_OPTIONS.find((o) => o.value === value)?.apology && !saidSorry();
}

const STORAGE_KEY = 'popsmosis.brain';

/**
 * The brain picked last on this browser, so a run of topics stays on it. Muse Wonder comes back only to an admin, or
 * to a learner who has said sorry in this tab: a new tab, or another learner on a shared browser, is asked again.
 */
export function rememberedBrain(isAdmin: boolean): Brain | null {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY) as Brain | null;
    return BRAIN_OPTIONS.some((o) => o.value === saved && saved) && !needsApology(saved, isAdmin) ? saved : null;
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
