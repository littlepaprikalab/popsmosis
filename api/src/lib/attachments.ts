/**
 * Attachments: files a human sends with a question or a reply — a screenshot pasted into Zed's Zoey panel, a log, a
 * data table — for the advisor to read (GET /api/attachments/:id) and the pages to list.
 *
 * They travel inside the message's own JSON body as base64, so a message and its files are written together or not at
 * all, and live in Postgres beside it (Attachment in prisma/schema.prisma). What a file is served as is decided from
 * its bytes, never from what the upload claimed: an image as that image, UTF-8 text as plain text, anything else as a
 * download — nothing a browser would run.
 */
import { Prisma } from '../generated/prisma/client.js';

export const ATTACHMENTS_MAX = 5;
export const ATTACHMENT_MAX_BYTES = 4 * 1024 * 1024;
export const ATTACHMENTS_MAX_TOTAL_BYTES = 8 * 1024 * 1024;
export const ATTACHMENT_NAME_MAX_LENGTH = 200;
const MIME_TYPE_MAX_LENGTH = 100;

/** A message's body limit: its files as base64 (four bytes for every three) and room for its text and context. */
export const MESSAGE_BODY_LIMIT = Math.ceil((ATTACHMENTS_MAX_TOTAL_BYTES * 4) / 3) + 256 * 1024;

/** Days a file's bytes are kept; 0 keeps them for good. Past it the row stays, so a thread still lists what was sent. */
export function attachmentRetentionDays(): number {
  const days = parseInt(process.env.ATTACHMENT_RETENTION_DAYS || '30', 10);
  return Number.isFinite(days) && days > 0 ? days : 0;
}

export interface NewAttachment {
  name: string;
  mimeType: string;
  size: number;
  data: Buffer;
}

export const TEXT_PLAIN = 'text/plain';
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;
const MIME_TYPE = /^[A-Za-z0-9][\w.+-]*\/[A-Za-z0-9][\w.+-]*$/;
const NAME_CONTROL_CHARS = /[\u0000-\u001f\u007f]/g;

/** The image types served as themselves, each by the bytes it starts with. */
const IMAGE_SIGNATURES: { mimeType: string; matches: (b: Buffer) => boolean }[] = [
  { mimeType: 'image/png', matches: (b) => b.length >= 8 && b.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) },
  { mimeType: 'image/jpeg', matches: (b) => b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  { mimeType: 'image/gif', matches: (b) => b.length >= 6 && (b.subarray(0, 6).toString('latin1') === 'GIF87a' || b.subarray(0, 6).toString('latin1') === 'GIF89a') },
  { mimeType: 'image/webp', matches: (b) => b.length >= 12 && b.subarray(0, 4).toString('latin1') === 'RIFF' && b.subarray(8, 12).toString('latin1') === 'WEBP' },
];

/** Text a person could read: valid UTF-8 holding no control characters but tabs, line breaks and form feeds. */
function isPlainText(data: Buffer): boolean {
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(data);
  } catch {
    return false;
  }
  return !/[\u0000-\u0008\u000b\u000e-\u001f\u007f]/.test(text);
}

/** What the bytes are, as far as serving them goes: one of the image types, text/plain, or null for anything else. */
export function sniffMimeType(data: Buffer): string | null {
  const image = IMAGE_SIGNATURES.find((s) => s.matches(data));
  if (image) return image.mimeType;
  return isPlainText(data) ? TEXT_PLAIN : null;
}

/** A file name as stored: its last path component, no control characters, cut to the limit with its extension kept. */
export function cleanAttachmentName(raw: string): string {
  const leaf = raw.split(/[\\/]/).pop() ?? '';
  let name = leaf.replace(NAME_CONTROL_CHARS, '').trim();
  if (!name || name === '.' || name === '..') name = 'attachment';
  if (name.length > ATTACHMENT_NAME_MAX_LENGTH) {
    const dot = name.lastIndexOf('.');
    const ext = dot > 0 && name.length - dot <= 16 ? name.slice(dot) : '';
    name = name.slice(0, ATTACHMENT_NAME_MAX_LENGTH - ext.length) + ext;
  }
  return name;
}

/**
 * Read a message's `attachments`: [{ name, data, mimeType? }], data the file as standard base64. Absent or empty is no
 * files; anything malformed or over a limit is an `error` naming the item.
 */
export function parseAttachments(value: unknown): { attachments: NewAttachment[]; error: string | null } {
  if (value === undefined || value === null) return { attachments: [], error: null };
  if (!Array.isArray(value)) return { attachments: [], error: 'attachments must be an array' };
  if (value.length > ATTACHMENTS_MAX) return { attachments: [], error: `attachments may hold at most ${ATTACHMENTS_MAX} files` };

  const attachments: NewAttachment[] = [];
  let totalBytes = 0;
  for (let i = 0; i < value.length; i++) {
    const item = value[i] as Record<string, unknown> | null;
    const where = `attachments[${i}]`;
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      return { attachments: [], error: `${where} must be an object with a name and data` };
    }
    if (typeof item.name !== 'string' || !item.name.trim()) return { attachments: [], error: `${where}.name is required` };
    if (typeof item.data !== 'string') return { attachments: [], error: `${where}.data must be the file as a base64 string` };
    if (item.data.length % 4 !== 0 || !BASE64.test(item.data)) return { attachments: [], error: `${where}.data is not base64` };
    const name = cleanAttachmentName(item.name);
    const data = Buffer.from(item.data, 'base64');
    if (data.length === 0) return { attachments: [], error: `${where} (${name}) is empty` };
    if (data.length > ATTACHMENT_MAX_BYTES) {
      return { attachments: [], error: `${where} (${name}) is ${data.length} bytes; a file may be at most ${ATTACHMENT_MAX_BYTES}` };
    }
    totalBytes += data.length;
    if (totalBytes > ATTACHMENTS_MAX_TOTAL_BYTES) {
      return { attachments: [], error: `the attachments come to more than ${ATTACHMENTS_MAX_TOTAL_BYTES} bytes` };
    }
    let declared: string | null = null;
    if (item.mimeType !== undefined && item.mimeType !== null) {
      if (typeof item.mimeType !== 'string' || item.mimeType.length > MIME_TYPE_MAX_LENGTH || !MIME_TYPE.test(item.mimeType)) {
        return { attachments: [], error: `${where}.mimeType must be a media type such as image/png` };
      }
      declared = item.mimeType.toLowerCase();
    }
    // A declared type is kept only as a download's label: one served in place must come from the bytes themselves.
    const kept = declared !== null && servedAs(declared).disposition === 'attachment' ? declared : 'application/octet-stream';
    attachments.push({ name, mimeType: sniffMimeType(data) ?? kept, size: data.length, data });
  }
  return { attachments, error: null };
}

/** The nested create a question or reply writes its files with. */
export function attachmentsCreate(attachments: NewAttachment[]) {
  if (attachments.length === 0) return undefined;
  return { create: attachments.map((a) => ({ name: a.name, mimeType: a.mimeType, size: a.size, data: new Uint8Array(a.data) })) };
}

/** How a stored file goes out: images and text shown in place, everything else only ever downloaded. */
export function servedAs(mimeType: string): { contentType: string; disposition: 'inline' | 'attachment' } {
  if (IMAGE_SIGNATURES.some((s) => s.mimeType === mimeType)) return { contentType: mimeType, disposition: 'inline' };
  if (mimeType === TEXT_PLAIN) return { contentType: 'text/plain; charset=utf-8', disposition: 'inline' };
  return { contentType: 'application/octet-stream', disposition: 'attachment' };
}

/** A Content-Disposition naming the file, its UTF-8 name for the clients that read filename* and a plain one for the rest. */
export function contentDisposition(disposition: 'inline' | 'attachment', name: string): string {
  const fallback = name.replace(/[^\x20-\x7e]|["\\%]/g, '_');
  return `${disposition}; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(name)}`;
}

/** Let go of the bytes of every file older than the retention, keeping the rows. */
export async function sweepExpiredAttachments(db: { $executeRaw: (query: Prisma.Sql) => Promise<number> }): Promise<number> {
  const days = attachmentRetentionDays();
  if (days === 0) return 0;
  return db.$executeRaw(Prisma.sql`
    UPDATE attachments SET data = NULL, "purgedAt" = now()
    WHERE data IS NOT NULL AND "createdAt" < now() - make_interval(days => ${days})
  `);
}
