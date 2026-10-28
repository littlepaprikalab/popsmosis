/**
 * The studio's settings an admin changes on /admin/studio, kept in the settings table so a change reaches the
 * next run with no redeploy: the studio reads them from its claims (POST /api/questions/:id/claim, the video
 * claim and the video queue), as `studio`.
 *
 * `art`: who paints the droplet's pictures, by the provider of the brain that wrote the lesson (studio/brain.py).
 * Whoever paints, SAM 3.1 on Meta's API finds each structure in the picture (studio/steps/locate.py), except in
 * the brain's own SVG, which says where each one is.
 *
 *   gemini   Gemini 3.1 Flash Image, on GEMINI_API_KEY
 *   muse     Muse Image, on MODEL_API_KEY
 *   svg      the brain draws it; Claude's only
 */
import prisma from './prisma.js';
import { Prisma } from '../generated/prisma/client.js';

export const PROVIDERS = ['claude', 'meta'] as const;
export type Provider = (typeof PROVIDERS)[number];
export const PAINTERS = ['gemini', 'muse', 'svg'] as const;
export type Painter = (typeof PAINTERS)[number];
export type ArtSettings = Record<Provider, Painter>;

// What each brain's provider may paint with, its default first.
export const ART_CHOICES: Record<Provider, readonly Painter[]> = {
  claude: ['gemini', 'muse', 'svg'],
  meta: ['muse', 'gemini'],
};
export const DEFAULT_ART: ArtSettings = { claude: 'gemini', meta: 'muse' };

const ART_KEY = 'art';

/** The stored art settings over the defaults; a stored value that is no longer allowed falls back to its default. */
export function artFrom(stored: unknown): ArtSettings {
  const art = { ...DEFAULT_ART };
  if (stored && typeof stored === 'object' && !Array.isArray(stored)) {
    for (const provider of PROVIDERS) {
      const painter = (stored as Record<string, unknown>)[provider];
      if (typeof painter === 'string' && (ART_CHOICES[provider] as readonly string[]).includes(painter)) {
        art[provider] = painter as Painter;
      }
    }
  }
  return art;
}

/** An admin's change, `{ claude?, meta? }`: the painters to set, or why it cannot be taken. */
export function parseArt(value: unknown): { art?: Partial<ArtSettings>; error?: string } {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { error: 'art must be an object' };
  const art: Partial<ArtSettings> = {};
  for (const [provider, painter] of Object.entries(value)) {
    if (!(PROVIDERS as readonly string[]).includes(provider)) {
      return { error: `art takes ${PROVIDERS.join(' and ')}, not ${provider}` };
    }
    const allowed = ART_CHOICES[provider as Provider];
    if (typeof painter !== 'string' || !(allowed as readonly string[]).includes(painter)) {
      return { error: `art.${provider} must be one of ${allowed.join(', ')}` };
    }
    art[provider as Provider] = painter as Painter;
  }
  if (Object.keys(art).length === 0) return { error: 'art names no painter to change' };
  return { art };
}

export async function readStudioSettings(db: Pick<typeof prisma, 'setting'> = prisma) {
  const row = await db.setting.findUnique({ where: { key: ART_KEY } });
  return { art: artFrom(row?.value), updatedAt: row?.updatedAt.toISOString() ?? null };
}

/** What a studio claim carries: the settings in force, nothing about who changed them or when. */
export async function studioForClaim() {
  return { art: (await readStudioSettings()).art };
}

export async function writeArt(change: Partial<ArtSettings>) {
  const art = { ...(await readStudioSettings()).art, ...change } as Prisma.InputJsonObject;
  await prisma.setting.upsert({ where: { key: ART_KEY }, create: { key: ART_KEY, value: art }, update: { value: art } });
  return readStudioSettings();
}
