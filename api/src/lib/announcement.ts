/**
 * The announcement an admin sets on /admin/studio, shown to everyone under the nav on every page
 * (web/src/components/AnnouncementBanner.tsx): "Songs are off for today: we are out of quota." Kept in the settings
 * table under `announcement`, so a change needs no redeploy. No row: nothing is shown.
 *
 * Every open page asks for it about once a minute, signed in or not, so each instance keeps what it read for
 * CACHE_MS and reads it once however many ask at the same moment (SharedRead): the instance that takes a change has
 * it at once, another within CACHE_MS, and the database is asked at most once per CACHE_MS per instance, even while
 * it is down (each instance then gives the last one it read).
 *
 * `tone`: how it looks. `info` for news, `warning` for something that does not work as it should.
 */
import prisma from './prisma.js';
import { SharedRead } from './ttl-cache.js';

export const TONES = ['info', 'warning'] as const;
export type Tone = (typeof TONES)[number];
export type AnnouncementChange = { message: string; tone: Tone };
export type Announcement = AnnouncementChange & { updatedAt: string };

// A banner, not a page: about three lines on a phone.
export const MAX_MESSAGE_LENGTH = 500;
export const CACHE_MS = 10_000;

const KEY = 'announcement';

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
const isTone = (value: unknown): value is Tone => typeof value === 'string' && (TONES as readonly string[]).includes(value);

/** The words as the banner shows them: line breaks kept (at most one blank line), other control characters taken out. */
export function cleanMessage(text: string): string {
  return text
    .replace(/\r\n?/g, '\n')
    .replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** An admin's announcement, `{ message, tone? }` (tone `info` when left out), or why it cannot be taken. */
export function parseAnnouncement(value: unknown): { announcement?: AnnouncementChange; error?: string } {
  if (!isObject(value)) return { error: 'the announcement must be an object' };
  const extra = Object.keys(value).find((key) => key !== 'message' && key !== 'tone');
  if (extra) return { error: `the announcement takes message and tone, not ${extra}` };
  if (typeof value.message !== 'string') return { error: 'message must be text' };
  const message = cleanMessage(value.message);
  if (!message) return { error: 'message is required; DELETE /api/announcement takes the announcement down' };
  if (message.length > MAX_MESSAGE_LENGTH) return { error: `message must be ${MAX_MESSAGE_LENGTH} characters or less` };
  const tone = value.tone ?? 'info';
  if (!isTone(tone)) return { error: `tone must be one of ${TONES.join(', ')}` };
  return { announcement: { message, tone } };
}

/** The stored row as the banner gets it; a row with no message is no announcement, and a tone no longer known is info. */
export function announcementFrom(stored: unknown, updatedAt: Date): Announcement | null {
  if (!isObject(stored) || typeof stored.message !== 'string' || !stored.message.trim()) return null;
  return { message: stored.message, tone: isTone(stored.tone) ? stored.tone : 'info', updatedAt: updatedAt.toISOString() };
}

export async function readAnnouncement(db: Pick<typeof prisma, 'setting'> = prisma): Promise<Announcement | null> {
  const row = await db.setting.findUnique({ where: { key: KEY } });
  return row ? announcementFrom(row.value, row.updatedAt) : null;
}

const shared = new SharedRead(() => readAnnouncement(), CACHE_MS);

/** What every page shows, as of at most CACHE_MS ago on this instance. */
export function currentAnnouncement(): Promise<Announcement | null> {
  return shared.get();
}

/** Put an announcement up in place of any before it, or take it down (null). In force on this instance at once. */
export async function writeAnnouncement(change: AnnouncementChange | null): Promise<Announcement | null> {
  const announcement = change
    ? await prisma.setting.upsert({ where: { key: KEY }, create: { key: KEY, value: change }, update: { value: change } })
      .then((row) => announcementFrom(row.value, row.updatedAt))
    : await prisma.setting.deleteMany({ where: { key: KEY } }).then(() => null);
  shared.set(announcement);
  return announcement;
}
