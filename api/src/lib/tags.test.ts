import { describe, it, expect } from 'vitest';
import { bundleTags, cleanTags, serializeTags, tagOf, tagQueryOf, TAGS_PER_VIDEO } from './tags.js';

describe('tagOf', () => {
  it('writes a tag the one way a search is written', () => {
    expect(tagOf('#AP Bio')).toBe('apbio');
    expect(tagOf('#Cell-Respiration!')).toBe('cellrespiration');
    expect(tagOf('ATP_synthase')).toBe('atp_synthase');
    expect(tagOf('#Révolution Française')).toBe('révolutionfrançaise');
    expect(tagOf('#हिन्दी व्याकरण')).toBe('हिन्दीव्याकरण'); // the vowel signs stay, as the studio keeps them
    expect(tagOf('Ｋｐｏｐ')).toBe('kpop');
    expect(tagOf('#')).toBe('');
  });
});

describe('cleanTags', () => {
  it('keeps the first of each, drops what does not fit, and keeps at most ten', () => {
    expect(cleanTags(['#APBio', 'apbio', '#', 7, null, `#${'x'.repeat(41)}`, '#ok'])).toEqual(['apbio', 'ok']);
    const many = Array.from({ length: 15 }, (_, n) => `#t${n}`);
    expect(cleanTags(many)).toHaveLength(TAGS_PER_VIDEO);
    expect(cleanTags(many)[9]).toBe('t9');
  });
  it('takes only a list', () => {
    expect(cleanTags('apbio')).toEqual([]);
    expect(cleanTags(undefined)).toEqual([]);
  });
});

describe('bundleTags', () => {
  it("reads each video's tags from the bundle", () => {
    expect(bundleTags({ tags: { song: ['#Photosynthesis', '#kpop'], lesson: ['#photosynthesis', '#explained'] } }))
      .toEqual({ songTags: ['photosynthesis', 'kpop'], lessonTags: ['photosynthesis', 'explained'] });
  });
  it('gives none for a bundle without tags, or with tags of the wrong shape', () => {
    expect(bundleTags({ topic: 'ATP' })).toEqual({ songTags: [], lessonTags: [] });
    expect(bundleTags({ tags: ['#atp'] })).toEqual({ songTags: [], lessonTags: [] });
    expect(bundleTags(undefined)).toEqual({ songTags: [], lessonTags: [] });
  });
  it('reads back as each video\'s list', () => {
    expect(serializeTags({ songTags: ['a'], lessonTags: ['b'] })).toEqual({ song: ['a'], lesson: ['b'] });
  });
});

describe('tagQueryOf', () => {
  it('reads a search as the tag it names', () => {
    expect(tagQueryOf('#APBio')).toBe('apbio');
    expect(tagQueryOf(' ap bio ')).toBe('apbio');
  });
  it('names no tag when nothing is left of it', () => {
    expect(tagQueryOf('')).toBeNull();
    expect(tagQueryOf('#  ')).toBeNull();
    expect(tagQueryOf(undefined)).toBeNull();
    expect(tagQueryOf(['apbio'])).toBeNull();
  });
  it('still searches for a tag too long to exist, so it finds nothing rather than everything', () => {
    expect(tagQueryOf('x'.repeat(5000))).toBe('x'.repeat(200));
  });
});
