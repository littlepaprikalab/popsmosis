/**
 * The studio's settings an admin changes on /admin/studio, kept in the settings table so a change reaches the
 * next run with no redeploy, and the routine's environment holds only keys. The studio reads them with its agent
 * key (GET /api/studio/settings) before it claims anything, and from its claims (POST /api/questions/:id/claim, the
 * video claim and the video queue), as `studio`.
 *
 * `brain`: the brain that writes and fact-checks a topic no admin sent to a brain of its own (lib/brains.ts), and
 * how hard Claude thinks when it is Claude's (studio/brain.py).
 *
 * `song`: who sings the song (studio/steps/audio.py): ElevenLabs' Eleven Music, or Lyria 3.5 on the Gemini API.
 * The narration and the exam question are ElevenLabs' either way.
 *
 * `art`: who paints the droplet's pictures, by the provider of the brain that wrote the lesson. Whoever paints,
 * SAM 3.1 on Meta's API finds each structure in the picture (studio/steps/locate.py), except in the brain's own
 * SVG, which says where each one is.
 *
 *   gemini   Gemini 3.1 Flash Image, on GEMINI_API_KEY
 *   muse     Muse Image, on MODEL_API_KEY
 *   svg      the brain draws it; Claude's only
 *
 * `knobs`: the environment variables an admin may set here instead, by name (lib/knobs.ts). The studio is sent its
 * own (where: 'studio'); the API's own are kept in force by each instance.
 * `spend`: the most one topic may cost in model calls, its lesson and videos and every retry together, in US
 * dollars (`topicUsd`; 0 for no ceiling). The studio adds up the job's calls.jsonl (studio/ledger.py) before each
 * paid call and fails the step once the topic has reached it, so a retry costs nothing and the topic is given up.
 * Calls with no known price (ElevenLabs without ELEVENLABS_USD_PER_1K_CREDITS) count as nothing.
 *
 * `review`: whether a person looks at each droplet before it lists on the public Droplets tab (`required`, off by
 * default: a droplet lists once its videos are READY, after the studio's fact-check). With it on, a droplet whose
 * videos become READY waits off the tab until an admin approves it on /learn; the learner sees their own droplet either
 * way. The API reads it; the studio never does.
 */
import prisma from './prisma.js';
import { BRAINS, type Brain } from './brains.js';
import {
  KNOBS, knobsFrom, putKnobs, reloadKnobsWith, type KnobName, type KnobValue, type Knobs,
} from './knobs.js';

export const PROVIDERS = ['claude', 'meta'] as const;
export type Provider = (typeof PROVIDERS)[number];
export const PAINTERS = ['gemini', 'muse', 'svg'] as const;
export type Painter = (typeof PAINTERS)[number];
export type ArtSettings = Record<Provider, Painter>;
export const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
export type Effort = (typeof EFFORTS)[number];
export type BrainSettings = { default: Brain; effort: Effort };
export const SONG_PROVIDERS = ['elevenlabs', 'gemini'] as const;
export type SongProvider = (typeof SONG_PROVIDERS)[number];
export type SongSettings = { provider: SongProvider };
export type SpendSettings = { topicUsd: number };
export type ReviewSettings = { required: boolean };
// The highest ceiling an admin may set: well above any topic so far ($0.50 to $2 with its videos), low enough that a
// slip of the keyboard is not a blank cheque.
export const MAX_TOPIC_USD = 100;

// What each brain's provider may paint with, its default first.
export const ART_CHOICES: Record<Provider, readonly Painter[]> = {
  claude: ['gemini', 'muse', 'svg'],
  meta: ['muse', 'gemini'],
};
export const DEFAULT_ART: ArtSettings = { claude: 'gemini', meta: 'muse' };
export const DEFAULT_BRAIN: BrainSettings = { default: 'claude', effort: 'high' };
export const DEFAULT_SONG: SongSettings = { provider: 'elevenlabs' };
// Generous for the hackathon: about ten times the dearest topic so far, retries included. Lower it afterwards
// ($5 is about two and a half times), here and in studio/ledger.py.
export const DEFAULT_SPEND: SpendSettings = { topicUsd: 20 };
export const DEFAULT_REVIEW: ReviewSettings = { required: false };

const ART_KEY = 'art';
const BRAIN_KEY = 'brain';
const SONG_KEY = 'song';
const KNOBS_KEY = 'knobs';
const SPEND_KEY = 'spend';
const REVIEW_KEY = 'review';

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const oneOf = <T extends string>(list: readonly T[], value: unknown): value is T =>
  typeof value === 'string' && (list as readonly string[]).includes(value);

/** The stored art settings over the defaults; a stored value that is no longer allowed falls back to its default. */
export function artFrom(stored: unknown): ArtSettings {
  const art = { ...DEFAULT_ART };
  if (isObject(stored)) {
    for (const provider of PROVIDERS) {
      if (oneOf(ART_CHOICES[provider], stored[provider])) art[provider] = stored[provider] as Painter;
    }
  }
  return art;
}

/** The stored brain settings over the defaults, as artFrom(). */
export function brainFrom(stored: unknown): BrainSettings {
  const brain = { ...DEFAULT_BRAIN };
  if (isObject(stored)) {
    if (oneOf(BRAINS, stored.default)) brain.default = stored.default;
    if (oneOf(EFFORTS, stored.effort)) brain.effort = stored.effort;
  }
  return brain;
}

/** The stored song settings over the defaults, as artFrom(). */
export function songFrom(stored: unknown): SongSettings {
  return isObject(stored) && oneOf(SONG_PROVIDERS, stored.provider) ? { provider: stored.provider } : { ...DEFAULT_SONG };
}

const isCeiling = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= MAX_TOPIC_USD;

/** The stored spend settings over the defaults, as artFrom(). */
export function spendFrom(stored: unknown): SpendSettings {
  return isObject(stored) && isCeiling(stored.topicUsd) ? { topicUsd: stored.topicUsd } : { ...DEFAULT_SPEND };
}

/** An admin's change to the spend, `{ topicUsd }` in dollars (0 for no ceiling), or why it cannot be taken. */
export function parseSpendSettings(value: unknown): { spend?: SpendSettings; error?: string } {
  if (!isObject(value)) return { error: 'spend must be an object' };
  const extra = Object.keys(value).find((key) => key !== 'topicUsd');
  if (extra) return { error: `spend takes topicUsd, not ${extra}` };
  if (!isCeiling(value.topicUsd)) {
    return { error: `spend.topicUsd must be a number of dollars from 0 (no ceiling) to ${MAX_TOPIC_USD}` };
  }
  return { spend: { topicUsd: Math.round(value.topicUsd * 100) / 100 } };
}

/** The stored review setting over the default, as artFrom(). */
export function reviewFrom(stored: unknown): ReviewSettings {
  return isObject(stored) && typeof stored.required === 'boolean' ? { required: stored.required } : { ...DEFAULT_REVIEW };
}

/** An admin's change to the review, `{ required }`, or why it cannot be taken. */
export function parseReviewSettings(value: unknown): { review?: ReviewSettings; error?: string } {
  if (!isObject(value)) return { error: 'review must be an object' };
  const extra = Object.keys(value).find((key) => key !== 'required');
  if (extra) return { error: `review takes required, not ${extra}` };
  if (typeof value.required !== 'boolean') return { error: 'review.required must be true or false' };
  return { review: { required: value.required } };
}

/** Whether a droplet whose videos become READY now waits for an admin before it lists: one row, read at READY. */
export async function reviewRequired(db: Pick<typeof prisma, 'setting'> = prisma): Promise<boolean> {
  return reviewFrom((await db.setting.findUnique({ where: { key: REVIEW_KEY } }))?.value).required;
}

/** An admin's change to the song, `{ provider }`, or why it cannot be taken. */
export function parseSongSettings(value: unknown): { song?: SongSettings; error?: string } {
  if (!isObject(value)) return { error: 'song must be an object' };
  const extra = Object.keys(value).find((key) => key !== 'provider');
  if (extra) return { error: `song takes provider, not ${extra}` };
  if (!oneOf(SONG_PROVIDERS, value.provider)) return { error: `song.provider must be one of ${SONG_PROVIDERS.join(', ')}` };
  return { song: { provider: value.provider } };
}

/** An admin's change to the art, `{ claude?, meta? }`: the painters to set, or why it cannot be taken. */
export function parseArt(value: unknown): { art?: Partial<ArtSettings>; error?: string } {
  if (!isObject(value)) return { error: 'art must be an object' };
  const art: Partial<ArtSettings> = {};
  for (const [provider, painter] of Object.entries(value)) {
    if (!oneOf(PROVIDERS, provider)) return { error: `art takes ${PROVIDERS.join(' and ')}, not ${provider}` };
    const allowed = ART_CHOICES[provider];
    if (!oneOf(allowed, painter)) return { error: `art.${provider} must be one of ${allowed.join(', ')}` };
    art[provider] = painter;
  }
  if (Object.keys(art).length === 0) return { error: 'art names no painter to change' };
  return { art };
}

/** An admin's change to the brain, `{ default?, effort? }`, or why it cannot be taken. */
export function parseBrainSettings(value: unknown): { brain?: Partial<BrainSettings>; error?: string } {
  if (!isObject(value)) return { error: 'brain must be an object' };
  const brain: Partial<BrainSettings> = {};
  for (const [key, setting] of Object.entries(value)) {
    if (key === 'default') {
      if (!oneOf(BRAINS, setting)) return { error: `brain.default must be one of ${BRAINS.join(', ')}` };
      brain.default = setting;
    } else if (key === 'effort') {
      if (!oneOf(EFFORTS, setting)) return { error: `brain.effort must be one of ${EFFORTS.join(', ')}` };
      brain.effort = setting;
    } else {
      return { error: `brain takes default and effort, not ${key}` };
    }
  }
  if (Object.keys(brain).length === 0) return { error: 'brain names nothing to change' };
  return { brain };
}

export async function readStudioSettings(db: Pick<typeof prisma, 'setting'> = prisma) {
  const rows = await db.setting.findMany({
    where: { key: { in: [ART_KEY, BRAIN_KEY, SONG_KEY, KNOBS_KEY, SPEND_KEY, REVIEW_KEY] } },
  });
  const row = (key: string) => rows.find((r) => r.key === key);
  const times = rows.map((r) => r.updatedAt.getTime());
  return {
    brain: brainFrom(row(BRAIN_KEY)?.value),
    song: songFrom(row(SONG_KEY)?.value),
    art: artFrom(row(ART_KEY)?.value),
    knobs: knobsFrom(row(KNOBS_KEY)?.value),
    spend: spendFrom(row(SPEND_KEY)?.value),
    review: reviewFrom(row(REVIEW_KEY)?.value),
    updatedAt: times.length ? new Date(Math.max(...times)).toISOString() : null,
  };
}

/** The knobs set here that the studio reads (studio/knobs.py), over its own environment. */
function studioKnobs(knobs: Knobs): Knobs {
  return Object.fromEntries(Object.entries(knobs).filter(([name]) => KNOBS[name as KnobName].where === 'studio'));
}

/** What the studio reads: the settings in force, nothing about when they changed (nor the review, which is the API's). */
export async function studioForAgent() {
  const { brain, song, art, knobs, spend } = await readStudioSettings();
  return { brain, song, art, knobs: studioKnobs(knobs), spend };
}

/** The knobs as stored, for lib/knobs.ts to put in force. */
export const loadKnobs = async () => (await prisma.setting.findUnique({ where: { key: KNOBS_KEY } }))?.value;

/** Read the stored knobs again and put them in force on this instance (lib/knobs.ts). */
export function reloadKnobs(): Promise<void> {
  return reloadKnobsWith(loadKnobs);
}

/**
 * Apply an admin's change: only the fields it names, merged into what is stored by Postgres itself (jsonb ||), so
 * two admins saving different fields at once both keep their change. Every key in one transaction, so a change to
 * the brain, the song, the art, the knobs, the spend and the review lands whole or not at all. A knob set to null is taken out
 * (jsonb_strip_nulls), so the environment's value is read again. The knobs are put in force here at once.
 */
export async function writeStudioSettings(change: {
  brain?: Partial<BrainSettings>; song?: SongSettings; art?: Partial<ArtSettings>;
  knobs?: Partial<Record<KnobName, KnobValue | null>>; spend?: SpendSettings; review?: ReviewSettings;
}) {
  const writes = [
    ...(change.brain ? [[BRAIN_KEY, change.brain] as const] : []),
    ...(change.song ? [[SONG_KEY, change.song] as const] : []),
    ...(change.art ? [[ART_KEY, change.art] as const] : []),
    ...(change.knobs ? [[KNOBS_KEY, change.knobs] as const] : []),
    ...(change.spend ? [[SPEND_KEY, change.spend] as const] : []),
    ...(change.review ? [[REVIEW_KEY, change.review] as const] : []),
  ].map(([key, value]) => {
    const json = JSON.stringify(value);
    return prisma.$executeRaw`
      INSERT INTO settings (key, value, "updatedAt") VALUES (${key}, jsonb_strip_nulls(${json}::jsonb), now())
      ON CONFLICT (key) DO UPDATE SET "updatedAt" = now(), value = jsonb_strip_nulls(
        CASE WHEN jsonb_typeof(settings.value) = 'object' THEN settings.value || ${json}::jsonb ELSE ${json}::jsonb END)
    `;
  });
  await prisma.$transaction(writes);
  const saved = await readStudioSettings();
  if (change.knobs) putKnobs(saved.knobs);
  return saved;
}
