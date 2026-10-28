import { describe, it, expect } from 'vitest';
import { artFrom, parseArt, DEFAULT_ART, ART_CHOICES } from './studio-settings.js';

describe('artFrom', () => {
  it('is the defaults with nothing stored: Gemini paints for Claude, Muse Image for Muse', () => {
    expect(DEFAULT_ART).toEqual({ claude: 'gemini', meta: 'muse' });
    for (const stored of [undefined, null, 'gemini', [], 42]) expect(artFrom(stored)).toEqual(DEFAULT_ART);
  });

  it('takes what is stored over the defaults, and drops what is no longer allowed', () => {
    expect(artFrom({ claude: 'muse' })).toEqual({ claude: 'muse', meta: 'muse' });
    expect(artFrom({ claude: 'svg', meta: 'gemini' })).toEqual({ claude: 'svg', meta: 'gemini' });
    expect(artFrom({ meta: 'svg', claude: 'dall-e' })).toEqual(DEFAULT_ART);
  });

  it('offers each default first', () => {
    expect(ART_CHOICES.claude[0]).toBe(DEFAULT_ART.claude);
    expect(ART_CHOICES.meta[0]).toBe(DEFAULT_ART.meta);
  });
});

describe('parseArt', () => {
  it('takes one painter or both', () => {
    expect(parseArt({ claude: 'muse' })).toEqual({ art: { claude: 'muse' } });
    expect(parseArt({ claude: 'svg', meta: 'gemini' })).toEqual({ art: { claude: 'svg', meta: 'gemini' } });
  });

  it('refuses what is not a painter that brain may use', () => {
    expect(parseArt({ meta: 'svg' }).error).toBe('art.meta must be one of muse, gemini');
    expect(parseArt({ claude: 'imagen' }).error).toBe('art.claude must be one of gemini, muse, svg');
    expect(parseArt({ openai: 'gemini' }).error).toBe('art takes claude and meta, not openai');
    expect(parseArt({}).error).toBe('art names no painter to change');
    expect(parseArt('gemini').error).toBe('art must be an object');
  });
});
