import { describe, it, expect } from 'vitest';
import { bundleSongStyle, SONG_STYLE_SHOWN_MAX_LENGTH } from './song-style.js';

describe('bundleSongStyle', () => {
  it('is the style the brain wrote, on one line, without its closing full stop', () => {
    const style = 'K-pop hip-hop at 94 bpm;\n female vocals with crisp rap verses and a bright sung chorus.';
    expect(bundleSongStyle({ music: { positive_styles: [style], negative_styles: [] } }))
      .toBe('K-pop hip-hop at 94 bpm; female vocals with crisp rap verses and a bright sung chorus');
  });
  it('joins an older bundle\'s tags as the studio joins them for the music model', () => {
    expect(bundleSongStyle({ music: { positive_styles: ['bright pop', ' hip-hop verses. ', '', 7, '104 bpm'] } }))
      .toBe('bright pop, hip-hop verses, 104 bpm');
  });
  it('is none for a droplet with no song, or a bundle without a style', () => {
    expect(bundleSongStyle({ music: { positive_styles: [], negative_styles: [] } })).toBeNull(); // a lesson video alone
    expect(bundleSongStyle({ music: { positive_styles: ['  ', '.'] } })).toBeNull();
    expect(bundleSongStyle({ course: 'Biology', topic: 'osmosis' })).toBeNull();
    expect(bundleSongStyle({ music: 'pop' })).toBeNull();
    expect(bundleSongStyle(null)).toBeNull();
    expect(bundleSongStyle(undefined)).toBeNull();
  });
  it('keeps nothing too long to be a style, or that reads as an email address or a phone number', () => {
    expect(bundleSongStyle({ music: { positive_styles: ['p'.repeat(SONG_STYLE_SHOWN_MAX_LENGTH + 1)] } })).toBeNull();
    expect(bundleSongStyle({ music: { positive_styles: ['pop for ada@example.com'] } })).toBeNull();
    expect(bundleSongStyle({ music: { positive_styles: ['pop, call 555 123 4567'] } })).toBeNull();
    expect(bundleSongStyle({ music: { positive_styles: ['hip-hop pop, upbeat 100 bpm groove, punchy 808s'] } }))
      .toBe('hip-hop pop, upbeat 100 bpm groove, punchy 808s');
  });
});
