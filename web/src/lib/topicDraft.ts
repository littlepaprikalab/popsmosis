/**
 * A topic typed on the Learn page and not sent yet, kept in this tab's sessionStorage: a reload, or a trip to sign in
 * and back (Google included, as it comes back to this tab), finds it in the box. Gone once it is sent, and on a
 * logout, so the next person on a shared computer does not find it. Every call shrugs off blocked storage: the draft
 * then lives only on the page.
 */
const DRAFT_KEY = 'popsmosis:topic-draft';
// Set when Submit, pressed signed out, sent them to sign in: back on the page, it says the topic is waiting.
const SIGN_IN_KEY = 'popsmosis:topic-sign-in';

function read(key: string): string | null {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

function write(key: string, value: string | null) {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch { /* storage blocked */ }
}

export const topicDraft = {
  read: () => read(DRAFT_KEY),
  write: (value: string) => write(DRAFT_KEY, value.trim() ? value : null),
  /** Submit asked for a sign-in: remember it, for the page to say so when they are back. */
  markSignIn: () => write(SIGN_IN_KEY, '1'),
  /** Whether they are back from a sign-in Submit asked for; true only once. */
  takeSignIn: () => {
    const marked = !!read(SIGN_IN_KEY);
    if (marked) write(SIGN_IN_KEY, null);
    return marked;
  },
  clear: () => { write(DRAFT_KEY, null); write(SIGN_IN_KEY, null); },
};
