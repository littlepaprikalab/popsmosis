/**
 * The studio's settings an admin changes on /admin/studio, kept in the settings table so a change reaches the
 * next run with no redeploy, and the routine's environment holds only keys. The studio reads them with its agent
 * key (GET /api/studio/settings) before it claims anything, and from its claims (POST /api/questions/:id/claim, the
 * video claim and the video queue), as `studio`.
 *
 * `brain`: the brain that writes and fact-checks a topic no admin sent to a brain of its own (lib/brains.ts), and
 * how hard Claude thinks when it is Claude's (studio/brain.py).
 *
 * `art`: who paints the droplet's pictures, by the provider of the brain that wrote the lesson. Whoever paints,
 * SAM 3.1 on Meta's API finds each structure in the picture (studio/steps/locate.py), except in the brain's own
 * SVG, which says where each one is.
 *
 *   gemini   Gemini 3.1 Flash Image, on GEMINI_API_KEY
 *   muse     Muse Image, on MODEL_API_KEY
 *   svg      the brain draws it; Claude's only
 */
import prisma from './prisma.js';
import { Prisma } from '../generated/prisma/client.js';
import { BRAINS, type Brain } from './brains.js';

export const PROVIDERS = ['claude', 'meta'] as const;
export type Provider = (typeof PROVIDERS)[number];
export const PAINTERS = ['gemini', 'muse', 'svg'] as const;
export type Painter = (typeof PAINTERS)[number];
export type ArtSettings = Record<Provider, Painter>;
export const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
export type Effort = (typeof EFFORTS)[number];
export type BrainSettings = { default: Brain; effort: Effort };

// What each brain's provider may paint with, its default first.
export const ART_CHOICES: Record<Provider, readonly Painter[]> = {
  claude: ['gemini', 'muse', 'svg'],
  meta: ['muse', 'gemini'],
};
export const DEFAULT_ART: ArtSettings = { claude: 'gemini', meta: 'muse' };
export const DEFAULT_BRAIN: BrainSettings = { default: 'claude', effort: 'high' };

const ART_KEY = 'art';
const BRAIN_KEY = 'brain';

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
  const rows = await db.setting.findMany({ where: { key: { in: [ART_KEY, BRAIN_KEY] } } });
  const row = (key: string) => rows.find((r) => r.key === key);
  const times = rows.map((r) => r.updatedAt.getTime());
  return {
    brain: brainFrom(row(BRAIN_KEY)?.value),
    art: artFrom(row(ART_KEY)?.value),
    updatedAt: times.length ? new Date(Math.max(...times)).toISOString() : null,
  };
}

/** What the studio reads: the settings in force, nothing about when they changed. */
export async function studioForAgent() {
  const { brain, art } = await readStudioSettings();
  return { brain, art };
}

export async function writeStudioSettings(change: { brain?: Partial<BrainSettings>; art?: Partial<ArtSettings> }) {
  const current = await readStudioSettings();
  await prisma.$transaction([
    ...(change.brain ? [[BRAIN_KEY, { ...current.brain, ...change.brain }] as const] : []),
    ...(change.art ? [[ART_KEY, { ...current.art, ...change.art }] as const] : []),
  ].map(([key, value]) => prisma.setting.upsert({
    where: { key },
    create: { key, value: value as Prisma.InputJsonObject },
    update: { value: value as Prisma.InputJsonObject },
  })));
  return readStudioSettings();
}
