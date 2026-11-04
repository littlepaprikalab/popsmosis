import { describe, it, expect } from 'vitest';
import {
  artFrom, brainFrom, songFrom, spendFrom, parseArt, parseBrainSettings, parseSongSettings, parseSpendSettings, DEFAULT_ART,
  DEFAULT_BRAIN, DEFAULT_SONG, DEFAULT_SPEND, ART_CHOICES,
} from './studio-settings.js';

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

describe('brainFrom', () => {
  it('is Claude at high effort with nothing stored, and topics sent to a brain of their own go to it', () => {
    expect(DEFAULT_BRAIN).toEqual({ default: 'claude', effort: 'high', defaultOnly: false });
    for (const stored of [undefined, null, 'claude', []]) expect(brainFrom(stored)).toEqual(DEFAULT_BRAIN);
  });

  it('takes what is stored, and drops what is no longer allowed', () => {
    expect(brainFrom({ default: 'muse-spark' })).toEqual({ default: 'muse-spark', effort: 'high', defaultOnly: false });
    expect(brainFrom({ default: 'muse-spark-1.3', effort: 'medium' })).toEqual({ default: 'claude', effort: 'medium', defaultOnly: false });
    expect(brainFrom({ effort: 'hihg' })).toEqual(DEFAULT_BRAIN);
    expect(brainFrom({ default: 'muse-spark', defaultOnly: true })).toEqual({ default: 'muse-spark', effort: 'high', defaultOnly: true });
    expect(brainFrom({ defaultOnly: 'yes' })).toEqual(DEFAULT_BRAIN);
  });
});

describe('parseBrainSettings', () => {
  it('takes the default brain, the effort, whether the default makes every topic, or any of them', () => {
    expect(parseBrainSettings({ default: 'muse-wonder' })).toEqual({ brain: { default: 'muse-wonder' } });
    expect(parseBrainSettings({ default: 'claude', effort: 'max' })).toEqual({ brain: { default: 'claude', effort: 'max' } });
    expect(parseBrainSettings({ defaultOnly: true })).toEqual({ brain: { defaultOnly: true } });
    expect(parseBrainSettings({ default: 'claude', defaultOnly: false })).toEqual({ brain: { default: 'claude', defaultOnly: false } });
  });

  it('refuses what is not a brain or an effort', () => {
    expect(parseBrainSettings({ default: 'claude-haiku-5-5' }).error).toBe('brain.default must be one of claude, muse-wonder, muse-spark');
    expect(parseBrainSettings({ effort: 'hihg' }).error).toBe('brain.effort must be one of low, medium, high, xhigh, max');
    expect(parseBrainSettings({ model: 'x' }).error).toBe('brain takes default, effort and defaultOnly, not model');
    expect(parseBrainSettings({ defaultOnly: 'on' }).error).toBe('brain.defaultOnly must be true or false');
    expect(parseBrainSettings({}).error).toBe('brain names nothing to change');
    expect(parseBrainSettings('claude').error).toBe('brain must be an object');
  });
});

describe('songFrom and parseSongSettings', () => {
  it('is ElevenLabs with nothing stored, and takes Lyria when stored', () => {
    expect(DEFAULT_SONG).toEqual({ provider: 'gemini' });
    for (const stored of [undefined, null, 'gemini', { provider: 'suno' }]) expect(songFrom(stored)).toEqual(DEFAULT_SONG);
    expect(songFrom({ provider: 'gemini' })).toEqual({ provider: 'gemini' });
  });

  it('takes a provider it has, and refuses anything else', () => {
    expect(parseSongSettings({ provider: 'gemini' })).toEqual({ song: { provider: 'gemini' } });
    expect(parseSongSettings({ provider: 'suno' }).error).toBe('song.provider must be one of elevenlabs, gemini');
    expect(parseSongSettings({ provider: 'gemini', model: 'lyria-3.5' }).error).toBe('song takes provider, not model');
    expect(parseSongSettings('gemini').error).toBe('song must be an object');
  });
});

describe('spendFrom and parseSpendSettings', () => {
  it('is $20 a topic with nothing stored, takes a stored ceiling, and 0 for none', () => {
    expect(DEFAULT_SPEND).toEqual({ topicUsd: 20 });
    for (const stored of [undefined, null, 5, { topicUsd: '5' }, { topicUsd: -1 }, { topicUsd: 1e9 }, { topicUsd: NaN }]) {
      expect(spendFrom(stored)).toEqual(DEFAULT_SPEND);
    }
    expect(spendFrom({ topicUsd: 2.5 })).toEqual({ topicUsd: 2.5 });
    expect(spendFrom({ topicUsd: 0 })).toEqual({ topicUsd: 0 });
  });

  it('takes a number of dollars to the cent, and refuses anything else', () => {
    expect(parseSpendSettings({ topicUsd: 3 })).toEqual({ spend: { topicUsd: 3 } });
    expect(parseSpendSettings({ topicUsd: 0 })).toEqual({ spend: { topicUsd: 0 } });
    expect(parseSpendSettings({ topicUsd: 1.234 })).toEqual({ spend: { topicUsd: 1.23 } });
    const range = 'spend.topicUsd must be a number of dollars from 0 (no ceiling) to 100';
    for (const topicUsd of [-0.01, 100.01, '5', null, Infinity]) expect(parseSpendSettings({ topicUsd }).error).toBe(range);
    expect(parseSpendSettings({}).error).toBe(range);
    expect(parseSpendSettings({ topicUsd: 5, daily: 50 }).error).toBe('spend takes topicUsd, not daily');
    expect(parseSpendSettings(5).error).toBe('spend must be an object');
  });
});
