/**
 * The knobs: the environment variables that tune the API and the studio, which an admin can also set on
 * /admin/studio, so a change needs no redeploy. A value set there wins over the variable; cleared there, the
 * variable is read again, else the default in the code. Kept in the settings table by the variable's name, under
 * `knobs` (lib/studio-settings.ts reads and writes the row).
 *
 * The API's own are read where they always were, synchronously and often (in SQL, on every question it
 * serializes), so each instance keeps the stored values in memory: read before it listens, replaced by every
 * change it takes, and read again every REFRESH_MS on a timer (keepKnobsFresh), so a change taken by another
 * instance is in force on this one within REFRESH_MS whatever the traffic, and no request waits on a read. The
 * studio's are sent to the studio with its other settings, and it puts them over its own environment
 * (studio/knobs.py) from its next run.
 *
 * Not knobs: the keys and secrets, the URLs, what is read once at start (the database pool, the port), and
 * ATTACHMENT_RETENTION_DAYS, which the Privacy Policy states (web/src/lib/legal.ts), so it changes with the policy.
 * INKMORY_ENABLED is not one either: the teacher's own record reads it in its shell (docs/popsmosis-agent-skill.md).
 */

export type KnobValue = number | string;
type Where = 'api' | 'studio';
type Spec =
  | { where: Where; kind: 'int'; default: number; min: number; max: number }
  | { where: Where; kind: 'number'; default: number | null; min: number; max: number }
  | { where: Where; kind: 'text'; default: string; pattern: RegExp; rule: string }
  | { where: Where; kind: 'choice'; default: string; choices: readonly string[] };

export const KNOBS = {
  // The API: what learners may ask for (lib/usage.ts, lib/engagement.ts).
  QUESTIONS_PER_USER_PER_DAY: { where: 'api', kind: 'int', default: 10, min: 0, max: 1000 },
  TOPICS_PER_DAY: { where: 'api', kind: 'int', default: 100, min: 0, max: 100_000 },
  REPLIES_PER_USER_PER_HOUR: { where: 'api', kind: 'int', default: 30, min: 0, max: 1000 },
  DROPLET_COMMENTS_PER_USER_PER_HOUR: { where: 'api', kind: 'int', default: 30, min: 1, max: 1000 },
  // Generous for the hackathon; lower them afterwards (20 and 60), here and in lib/usage.ts.
  ATTACHMENT_MB_PER_USER_PER_DAY: { where: 'api', kind: 'int', default: 100, min: 0, max: 10_000 },
  ATTACHMENT_MB_HELD_PER_USER: { where: 'api', kind: 'int', default: 300, min: 0, max: 100_000 },
  // The API: the leases and the renders (lib/claim.ts, lib/droplet.ts, lib/progress.ts, lib/video-claim.ts).
  QUESTION_CLAIM_MINUTES: { where: 'api', kind: 'int', default: 20, min: 1, max: 1440 },
  VIDEO_STALE_MINUTES: { where: 'api', kind: 'int', default: 60, min: 1, max: 1440 },
  LESSON_HOLD_MINUTES: { where: 'api', kind: 'int', default: 0, min: 0, max: 1440 },
  VIDEO_MAX_ATTEMPTS: { where: 'api', kind: 'int', default: 2, min: 1, max: 10 },
  // The API: waking the teacher (lib/routine.ts).
  POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS: { where: 'api', kind: 'int', default: 300, min: 0, max: 3600 },
  POPSMOSIS_ROUTINE_BETA: {
    where: 'api', kind: 'text', default: 'experimental-cc-routine-2026-04-01',
    pattern: /^[a-z0-9][a-z0-9-]{0,99}$/, rule: 'lowercase letters, digits and hyphens',
  },
  // The studio (studio/knobs.py). STUDIO_VIDEOS: both videos, the lesson video alone (no song, no hook cut), or none.
  STUDIO_VIDEOS: { where: 'studio', kind: 'choice', default: 'on', choices: ['on', 'lesson', 'off'] },
  ELEVENLABS_VOICE_ID: {
    where: 'studio', kind: 'text', default: 'Xb7hH8MSUJpSbSDYk0k2', pattern: /^[A-Za-z0-9]{1,64}$/,
    rule: 'an ElevenLabs voice id: letters and digits',
  },
  GEMINI_SONG_MODEL: {
    where: 'studio', kind: 'text', default: 'lyria-3.5', pattern: /^[a-z0-9][a-z0-9.-]{0,63}$/,
    rule: 'a Lyria model id: lowercase letters, digits, dots and hyphens',
  },
  ELEVENLABS_USD_PER_1K_CREDITS: { where: 'studio', kind: 'number', default: null, min: 0, max: 100 },
} as const satisfies Record<string, Spec>;

export type KnobName = keyof typeof KNOBS;
export type ApiKnob = { [K in KnobName]: (typeof KNOBS)[K]['where'] extends 'api' ? K : never }[KnobName];
export type Knobs = Partial<Record<KnobName, KnobValue>>;
export const KNOB_NAMES = Object.keys(KNOBS) as KnobName[];

// How often each instance reads the stored knobs again: the most a change on another instance takes to reach it.
export const REFRESH_MS = 10_000;

let stored: Knobs = {};
let changes = 0; // counts every change put in force, so a read begun before one does not undo it

const isKnob = (name: string): name is KnobName => Object.prototype.hasOwnProperty.call(KNOBS, name);

/** Why a value cannot be the knob's, or null. */
function refusal(name: KnobName, value: unknown): string | null {
  const spec: Spec = KNOBS[name];
  switch (spec.kind) {
    case 'int':
      return Number.isInteger(value) && (value as number) >= spec.min && (value as number) <= spec.max
        ? null : `must be a whole number from ${spec.min} to ${spec.max}`;
    case 'number':
      return typeof value === 'number' && Number.isFinite(value) && value >= spec.min && value <= spec.max
        ? null : `must be a number from ${spec.min} to ${spec.max}`;
    case 'text':
      return typeof value === 'string' && spec.pattern.test(value) ? null : `must be ${spec.rule}`;
    case 'choice':
      return typeof value === 'string' && spec.choices.includes(value) ? null : `must be one of ${spec.choices.join(', ')}`;
  }
}

/** The stored knobs: those still allowed, at a value still allowed; anything else falls back as if never set. */
export function knobsFrom(value: unknown): Knobs {
  const knobs: Knobs = {};
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [name, setting] of Object.entries(value)) {
      if (isKnob(name) && refusal(name, setting) === null) knobs[name] = setting as KnobValue;
    }
  }
  return knobs;
}

/** An admin's change to the knobs, `{ NAME: value | null }` (null: back to the environment), or why it cannot be taken. */
export function parseKnobs(value: unknown): { knobs?: Partial<Record<KnobName, KnobValue | null>>; error?: string } {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { error: 'knobs must be an object' };
  const knobs: Partial<Record<KnobName, KnobValue | null>> = {};
  for (const [name, setting] of Object.entries(value)) {
    if (!isKnob(name)) return { error: `knobs has no ${name}` };
    const why = setting === null ? null : refusal(name, setting);
    if (why) return { error: `knobs.${name} ${why}` };
    knobs[name] = setting as KnobValue | null;
  }
  if (Object.keys(knobs).length === 0) return { error: 'knobs names nothing to change' };
  return { knobs };
}

/** The knob as its variable's text: the value an admin set, else the environment's (undefined when neither). */
export function knob(name: KnobName, env: NodeJS.ProcessEnv = process.env): string | undefined {
  const value = stored[name];
  return value === undefined ? env[name] : String(value);
}

/** The knobs set on /admin/studio as this instance has them. */
export function storedKnobs(): Knobs {
  return { ...stored };
}

/** Put the stored knobs in force on this instance: what a change just wrote, or what a read found. */
export function putKnobs(value: unknown): void {
  stored = knobsFrom(value);
  changes += 1;
}

let reading: Promise<void> | null = null;

/**
 * Read the stored knobs again with load() and put them in force: one read at a time, and a change put in force
 * while it reads wins over what it read. A read that fails leaves what is in force.
 */
export function reloadKnobsWith(load: () => Promise<unknown>): Promise<void> {
  if (reading) return reading;
  const before = changes;
  reading = load()
    .then((value) => { if (changes === before) putKnobs(value); })
    .finally(() => { reading = null; });
  return reading;
}

/**
 * Read the stored knobs again every REFRESH_MS until the timer it returns is cleared. A read the database is slow
 * to answer is not started again until it ends, and one that fails is a warning: what is in force stays. The timer
 * never holds the process open.
 */
export function keepKnobsFresh(load: () => Promise<unknown>, log: { warn(obj: object, msg: string): void }): NodeJS.Timeout {
  let waiting = false;
  const timer = setInterval(() => {
    if (waiting) return;
    waiting = true;
    reloadKnobsWith(load)
      .catch((err) => log.warn({ err }, 'could not read the knobs; keeping those in force'))
      .finally(() => { waiting = false; });
  }, REFRESH_MS);
  timer.unref();
  return timer;
}
