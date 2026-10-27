import { describe, it, expect } from 'vitest';
import { breakdownError, serializeBreakdown, BREAKDOWN_MAX_BYTES } from './breakdown.js';

const lesson = {
  steps: [
    { step: 'request', seconds: 0.4, tries: 1, costUsd: 0, models: [] },
    { step: 'bundle', seconds: 180, tries: 2, costUsd: 0.9, models: [
      { model: 'claude-opus-5-5', calls: 1, costUsd: 0.4, credits: null },
      { model: 'claude-opus-5', calls: 1, costUsd: 0.5, credits: null },
    ] },
  ],
  seconds: 180.4,
};
const videos = {
  steps: [
    { step: 'audio', seconds: 95, tries: 1, costUsd: 0.08, models: [
      { model: 'lyria-3.5', calls: 1, costUsd: 0.08, credits: null },
      { model: 'eleven_multilingual_v2', calls: 2, costUsd: null, credits: 3300 },
    ] },
    { step: 'render', seconds: 157, tries: 1, costUsd: 0, models: [] },
  ],
  seconds: 300,
};

describe('breakdownError', () => {
  it('takes none, or an object with its steps', () => {
    expect(breakdownError(undefined)).toBeNull();
    expect(breakdownError(lesson)).toBeNull();
    expect(breakdownError({ steps: [] })).toBeNull();
  });
  it('refuses anything else', () => {
    expect(breakdownError(null)).toMatch(/steps array/);
    expect(breakdownError([])).toMatch(/steps array/);
    expect(breakdownError({ steps: 'bundle' })).toMatch(/steps array/);
  });
  it('caps the size', () => {
    expect(breakdownError({ steps: [], note: 'x'.repeat(BREAKDOWN_MAX_BYTES) })).toMatch(/16 KB or less/);
  });
});

describe('serializeBreakdown', () => {
  it('is null when the studio sent none', () => {
    expect(serializeBreakdown(null, null)).toBeNull();
    expect(serializeBreakdown(undefined, 'nonsense')).toBeNull();
  });

  it('lists the lesson, then the videos, and adds them up', () => {
    const b = serializeBreakdown(lesson, videos)!;
    expect(b.steps.map((s) => [s.part, s.step])).toEqual([
      ['lesson', 'request'], ['lesson', 'bundle'], ['videos', 'audio'], ['videos', 'render']]);
    expect(b.seconds).toBe(480.4);
    expect(b.costUsd).toBeCloseTo(0.98);
    expect(b.credits).toBe(3300); // ElevenLabs, with no price set for its credits
    expect(b.unpriced).toBe(0);
    expect(b.complete).toBe(false);
    expect(b.steps[1]).toEqual({ ...lesson.steps[1], part: 'lesson' });
  });

  it('shows the lesson alone while the videos are not done', () => {
    const b = serializeBreakdown(lesson, null)!;
    expect(b.steps).toHaveLength(2);
    expect(b).toMatchObject({ seconds: 180.4, credits: null, unpriced: 0, complete: true });
  });

  it('counts a model whose provider said neither a price nor credits', () => {
    const music = { steps: [{ step: 'audio', models: [{ model: 'music_v2_5', calls: 1, costUsd: null, credits: null }] }] };
    expect(serializeBreakdown(null, music)).toMatchObject({ costUsd: 0, credits: null, unpriced: 1, complete: false });
  });

  it('works out each step from its models, and keeps only what it knows', () => {
    const b = serializeBreakdown({
      steps: [
        { step: 'bundle', seconds: -3, tries: 'two', costUsd: 99, extra: 'dropped', models: [
          { model: 'claude-opus-5-5', calls: 1.6, costUsd: 0.25, credits: 'many' },
          { model: '', calls: 1, costUsd: 5 }, // no name: not a model
          'not a model',
        ] },
        { step: '', seconds: 1 }, // no name: not a step
        'not a step',
      ],
      seconds: Infinity,
    }, undefined)!;
    expect(b.steps).toEqual([{ step: 'bundle', part: 'lesson', seconds: null, tries: 0, costUsd: 0.25,
      models: [{ model: 'claude-opus-5-5', calls: 2, costUsd: 0.25, credits: null }] }]);
    expect(b.seconds).toBeNull();
    expect(b.costUsd).toBe(0.25);
  });
});
