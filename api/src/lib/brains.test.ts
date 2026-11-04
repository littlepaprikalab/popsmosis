import { describe, it, expect } from 'vitest';
import { parseBrain, isBrain, BRAINS } from './brains.js';

describe('parseBrain', () => {
  it('leaves the choice to the teacher when none is sent', () => {
    for (const value of [undefined, null, '']) expect(parseBrain(value)).toEqual({ brain: null });
  });

  it('takes each brain, Muse Wonder too', () => {
    for (const brain of BRAINS) expect(parseBrain(brain)).toEqual({ brain });
  });

  it('refuses a brain it does not know', () => {
    for (const value of ['Claude', 'muse-spark-1.3', 'haiku', 42, {}]) {
      expect(parseBrain(value)).toEqual({ brain: null, error: 'brain must be one of claude, muse-wonder, muse-spark' });
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
