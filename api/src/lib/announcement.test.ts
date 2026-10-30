import { describe, it, expect } from 'vitest';
import { announcementFrom, cleanMessage, parseAnnouncement, MAX_MESSAGE_LENGTH } from './announcement.js';

describe('parseAnnouncement', () => {
  it('takes a message, info when no tone is named', () => {
    expect(parseAnnouncement({ message: 'Songs are off today.' })).toEqual({ announcement: { message: 'Songs are off today.', tone: 'info' } });
    expect(parseAnnouncement({ message: 'Out of quota', tone: 'warning' })).toEqual({ announcement: { message: 'Out of quota', tone: 'warning' } });
  });

  it('keeps the message as the banner will show it', () => {
    expect(parseAnnouncement({ message: '  Songs are off.\r\n\r\n\r\nBack tomorrow.\u0007  ' }).announcement?.message)
      .toBe('Songs are off.\n\nBack tomorrow.');
  });

  it('refuses what is not an announcement', () => {
    expect(parseAnnouncement('hi').error).toBe('the announcement must be an object');
    expect(parseAnnouncement({ message: 'hi', until: 'tomorrow' }).error).toBe('the announcement takes message and tone, not until');
    expect(parseAnnouncement({ message: 42 }).error).toBe('message must be text');
    expect(parseAnnouncement({ message: ' \n\u0000 ' }).error).toBe('message is required; DELETE /api/announcement takes the announcement down');
    expect(parseAnnouncement({ message: 'x'.repeat(MAX_MESSAGE_LENGTH + 1) }).error).toBe(`message must be ${MAX_MESSAGE_LENGTH} characters or less`);
    expect(parseAnnouncement({ message: 'hi', tone: 'danger' }).error).toBe('tone must be one of info, warning');
  });

  it('counts the length after cleaning, so trailing spaces do not push a message over', () => {
    expect(parseAnnouncement({ message: `${'x'.repeat(MAX_MESSAGE_LENGTH)}   ` }).announcement?.message).toHaveLength(MAX_MESSAGE_LENGTH);
  });
});

describe('cleanMessage', () => {
  it('keeps tabs and single blank lines', () => {
    expect(cleanMessage('a\tb\n\nc')).toBe('a\tb\n\nc');
  });
});

describe('announcementFrom', () => {
  const at = new Date('2026-10-30T12:00:00Z');

  it('is the stored announcement with when it was set', () => {
    expect(announcementFrom({ message: 'Out of quota', tone: 'warning' }, at))
      .toEqual({ message: 'Out of quota', tone: 'warning', updatedAt: '2026-10-30T12:00:00.000Z' });
  });

  it('is none for a row with no message, and info for a tone no longer known', () => {
    for (const stored of [null, 'hi', [], {}, { message: '  ' }, { message: 3 }]) expect(announcementFrom(stored, at)).toBeNull();
    expect(announcementFrom({ message: 'hi', tone: 'danger' }, at)?.tone).toBe('info');
  });
});
