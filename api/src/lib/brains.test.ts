import { describe, it, expect } from 'vitest';
import { parseBrain, isBrain, BRAINS } from './brains.js';

describe('parseBrain', () => {
  it('leaves the choice to the teacher when none is sent', () => {
    for (const value of [undefined, null, '']) {
      expect(parseBrain(value, true)).toEqual({ brain: null });
      expect(parseBrain(value, false)).toEqual({ brain: null });
    }
  });

  it('takes each brain from an admin', () => {
    for (const brain of BRAINS) expect(parseBrain(brain, true)).toEqual({ brain });
  });

  it('refuses a brain from anyone else, so a learner cannot spend on a costlier model', () => {
    expect(parseBrain('muse-wonder', false)).toMatchObject({ brain: null, forbidden: true });
  });

  it('refuses a brain it does not know, whoever sends it', () => {
    for (const value of ['Claude', 'muse-spark-1.3', 'haiku', 42, {}]) {
      const parsed = parseBrain(value, true);
      expect(parsed.brain).toBeNull();
      expect(parsed.error).toBe('brain must be one of claude, muse-wonder, muse-spark');
      expect(parsed.forbidden).toBeUndefined();
    }
  });
});

describe('isBrain', () => {
  it('knows exactly the three', () => {
    expect(BRAINS).toEqual(['claude', 'muse-wonder', 'muse-spark']);
    expect(isBrain('muse-spark')).toBe(true);
    expect(isBrain('meta')).toBe(false);
  });
});
