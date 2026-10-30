import { describe, it, expect } from 'vitest';
import {
  parseAttachments, sniffMimeType, cleanAttachmentName, servedAs, contentDisposition,
  ATTACHMENTS_MAX, ATTACHMENT_MAX_BYTES, ATTACHMENTS_MAX_TOTAL_BYTES, ATTACHMENT_NAME_MAX_LENGTH, MESSAGE_BODY_LIMIT,
} from './attachments.js';

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]);
const b64 = (b: Buffer | string) => Buffer.from(b).toString('base64');

describe('parseAttachments', () => {
  it('takes none when there are none', () => {
    expect(parseAttachments(undefined)).toEqual({ attachments: [], error: null });
    expect(parseAttachments(null)).toEqual({ attachments: [], error: null });
    expect(parseAttachments([])).toEqual({ attachments: [], error: null });
  });

  it('decodes each file and judges its type by its bytes, not by what it claims', () => {
    const { attachments, error } = parseAttachments([
      { name: 'C:\\Users\\me\\shot.png', data: b64(PNG), mimeType: 'text/html' },
      { name: 'notes.txt', data: b64('line one\nline two\n') },
      { name: 'page.html', data: b64(Buffer.from([0, 1, 2, 3])), mimeType: 'text/html' },
      { name: 'blob.bin', data: b64(Buffer.from([0, 1, 2, 3])) },
    ]);
    expect(error).toBeNull();
    expect(attachments.map((a) => [a.name, a.mimeType, a.size])).toEqual([
      ['shot.png', 'image/png', PNG.length],
      ['notes.txt', 'text/plain', 18],
      ['page.html', 'text/html', 4], // kept as declared, a download's label: never served as such (servedAs)
      ['blob.bin', 'application/octet-stream', 4],
    ]);
    expect(attachments[0].data.equals(PNG)).toBe(true);

    const claimed = parseAttachments([
      { name: 'fake.png', data: b64(Buffer.from([0, 1, 2, 3])), mimeType: 'image/png' },
      { name: 'fake.txt', data: b64(Buffer.from([0xc3, 0x28])), mimeType: 'text/plain' },
    ]);
    expect(claimed.attachments.map((a) => [a.name, a.mimeType])).toEqual([ // a type served in place is never taken on the upload's word
      ['fake.png', 'application/octet-stream'],
      ['fake.txt', 'application/octet-stream'],
    ]);
  });

  it('refuses what is malformed or over a limit, naming the item', () => {
    expect(parseAttachments('x').error).toMatch(/must be an array/);
    expect(parseAttachments([{ name: 'a', data: b64('a') }, 'x']).error).toMatch(/attachments\[1\] must be an object/);
    expect(parseAttachments([{ data: b64('a') }]).error).toMatch(/attachments\[0\]\.name is required/);
    expect(parseAttachments([{ name: 'a', data: 'not base64!' }]).error).toMatch(/attachments\[0\]\.data is not base64/);
    expect(parseAttachments([{ name: 'a', data: 'abc' }]).error).toMatch(/is not base64/); // not whole groups of four
    expect(parseAttachments([{ name: 'a', data: '' }]).error).toMatch(/\(a\) is empty/);
    expect(parseAttachments([{ name: 'a', data: b64('a'), mimeType: 'not a type' }]).error).toMatch(/mimeType must be a media type/);
    const many = Array.from({ length: ATTACHMENTS_MAX + 1 }, (_, i) => ({ name: `f${i}.txt`, data: b64('x') }));
    expect(parseAttachments(many).error).toMatch(/at most 5 files/);
    expect(parseAttachments([{ name: 'big', data: b64(Buffer.alloc(ATTACHMENT_MAX_BYTES + 1)) }]).error).toMatch(/\(big\) is \d+ bytes/);
    const half = b64(Buffer.alloc(ATTACHMENT_MAX_BYTES));
    expect(parseAttachments([{ name: 'a', data: half }, { name: 'b', data: half }]).error).toBeNull(); // exactly the total
    expect(parseAttachments([{ name: 'a', data: half }, { name: 'b', data: half }, { name: 'c', data: b64('x') }]).error).toMatch(/more than/);
  });

  it('leaves room in a message body for every file at its limit, as base64', () => {
    expect(MESSAGE_BODY_LIMIT).toBeGreaterThan(Math.ceil(ATTACHMENTS_MAX_TOTAL_BYTES / 3) * 4 + 2 * 4000 * 4);
  });
});

describe('sniffMimeType', () => {
  it('knows the images it serves by their first bytes', () => {
    expect(sniffMimeType(PNG)).toBe('image/png');
    expect(sniffMimeType(Buffer.from([0xff, 0xd8, 0xff, 0xe0]))).toBe('image/jpeg');
    expect(sniffMimeType(Buffer.from('GIF89a....'))).toBe('image/gif');
    expect(sniffMimeType(Buffer.concat([Buffer.from('RIFF'), Buffer.alloc(4), Buffer.from('WEBPVP8 ')]))).toBe('image/webp');
  });

  it('reads UTF-8 without control characters as text, and nothing else', () => {
    expect(sniffMimeType(Buffer.from('Grüße\tand\r\n日本語\n'))).toBe('text/plain');
    expect(sniffMimeType(Buffer.from([0x61, 0x00, 0x62]))).toBeNull(); // a NUL
    expect(sniffMimeType(Buffer.from([0xc3, 0x28]))).toBeNull(); // not UTF-8
    expect(sniffMimeType(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>'))).toBe('text/plain'); // shown as its text, never drawn
  });
});

describe('cleanAttachmentName', () => {
  it('keeps the last path component, without control characters', () => {
    expect(cleanAttachmentName('a/b/c.png')).toBe('c.png');
    expect(cleanAttachmentName('C:\\x\\y.log')).toBe('y.log');
    expect(cleanAttachmentName('bad\u0000\nname.txt')).toBe('badname.txt');
    expect(cleanAttachmentName('../')).toBe('attachment');
    expect(cleanAttachmentName('..')).toBe('attachment');
  });

  it('cuts a long name to the limit, its extension kept', () => {
    const name = cleanAttachmentName('x'.repeat(500) + '.png');
    expect(name.length).toBe(ATTACHMENT_NAME_MAX_LENGTH);
    expect(name.endsWith('.png')).toBe(true);
  });
});

describe('servedAs and contentDisposition', () => {
  it('shows images and text in place and hands everything else over as a download', () => {
    expect(servedAs('image/png')).toEqual({ contentType: 'image/png', disposition: 'inline' });
    expect(servedAs('text/plain')).toEqual({ contentType: 'text/plain; charset=utf-8', disposition: 'inline' });
    expect(servedAs('text/html')).toEqual({ contentType: 'application/octet-stream', disposition: 'attachment' });
    expect(servedAs('image/svg+xml')).toEqual({ contentType: 'application/octet-stream', disposition: 'attachment' });
  });

  it('names the file both ways', () => {
    expect(contentDisposition('inline', 'shot.png')).toBe(`inline; filename="shot.png"; filename*=UTF-8''shot.png`);
    expect(contentDisposition('attachment', 'Grüße "a".txt')).toBe(`attachment; filename="Gr__e _a_.txt"; filename*=UTF-8''Gr%C3%BC%C3%9Fe%20%22a%22.txt`);
  });
});

describe('scheduleAttachmentSweep', () => {
  it('runs a minute or so after start, then on its interval, never two at once, and stops', async () => {
    const { vi } = await import('vitest');
    const { scheduleAttachmentSweep } = await import('./attachments.js');
    vi.useFakeTimers();
    try {
      let calls = 0;
      let release: () => void = () => {};
      const db = { $executeRaw: () => { calls += 1; return new Promise<number>((resolve) => { release = () => resolve(0); }); } };
      const log = { info: () => {}, warn: () => {} };
      const stop = scheduleAttachmentSweep(db, log, { ATTACHMENT_SWEEP_MINUTES: '10' });
      await vi.advanceTimersByTimeAsync(59_000);
      expect(calls).toBe(0);
      await vi.advanceTimersByTimeAsync(31_000); // the first run, within 90 s
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(10 * 60_000); // still running: the next is skipped
      expect(calls).toBe(1);
      release();
      await vi.advanceTimersByTimeAsync(10 * 60_000);
      expect(calls).toBe(2);
      release();
      stop();
      await vi.advanceTimersByTimeAsync(60 * 60_000);
      expect(calls).toBe(2);
      expect(scheduleAttachmentSweep(db, log, { ATTACHMENT_RETENTION_DAYS: '0' })).toBeTypeOf('function'); // kept for good: no timers
    } finally {
      vi.useRealTimers();
    }
  });

  it('reads the fewest file bytes a body can carry from its length', async () => {
    const { minimumFileBytes, MESSAGE_TEXT_ROOM } = await import('./attachments.js');
    expect(minimumFileBytes(1000)).toBe(0);
    expect(minimumFileBytes(MESSAGE_TEXT_ROOM + 4000)).toBe(3000);
  });
});
