import { describe, it, expect } from 'vitest';
import { billModels, breakdownError, endToEndOf, madeWithOf, makerOf, serializeBreakdown, BREAKDOWN_MAX_BYTES, MADE_WITH_MAX_MODELS } from './breakdown.js';

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
    { step: 'timing', seconds: 110, tries: 1, costUsd: 0, models: [
      { model: 'htdemucs', calls: 1, costUsd: 0, credits: null },
      { model: 'mms-fa', calls: 1, costUsd: 0, credits: null },
    ], tools: [
      { name: 'demucs', version: '4.1.0' },
      { name: 'torchaudio', version: '2.11.0' },
      { name: 'torch', version: '2.11.0+cpu' },
      { name: 'ffmpeg', version: '7.1.1' },
    ] },
    { step: 'render', seconds: 157, tries: 1, costUsd: 0, models: [], tools: [
      { name: 'skia-python', version: '144.1' },
      { name: 'ffmpeg', version: null },
    ] },
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
      ['lesson', 'request'], ['lesson', 'bundle'], ['videos', 'audio'], ['videos', 'timing'], ['videos', 'render']]);
    expect(b.seconds).toBe(480.4);
    expect(b.partSeconds).toEqual({ lesson: 180.4, videos: 300 }); // each part's own, for the page's waits
    expect(b.costUsd).toBeCloseTo(0.98);
    expect(b.credits).toBe(3300); // ElevenLabs, with no price set for its credits
    expect(b.unpriced).toBe(0);
    expect(b.complete).toBe(false);
    expect(b.steps[1]).toEqual({ ...lesson.steps[1], part: 'lesson', tools: [],
      models: lesson.steps[1].models.map((m) => ({ ...m, maker: 'Anthropic' })) });
  });

  it('names who made each model and tool, and the versions the studio ran', () => {
    const b = serializeBreakdown(lesson, videos)!;
    const step = (name: string) => b.steps.find((s) => s.step === name)!;
    expect(step('audio').models.map((m) => [m.model, m.maker])).toEqual([
      ['lyria-3.5', 'Google'], ['eleven_multilingual_v2', 'ElevenLabs']]);
    expect(step('timing').models.map((m) => [m.model, m.maker])).toEqual([['htdemucs', 'Meta'], ['mms-fa', 'Meta']]);
    expect(step('timing').tools).toEqual([
      { name: 'demucs', version: '4.1.0', maker: 'Meta' },
      { name: 'torchaudio', version: '2.11.0', maker: 'Meta' },
      { name: 'torch', version: '2.11.0+cpu', maker: 'Meta' },
      { name: 'ffmpeg', version: '7.1.1', maker: 'FFmpeg' },
    ]);
    expect(step('render').tools).toEqual([
      { name: 'skia-python', version: '144.1', maker: 'Google' },
      { name: 'ffmpeg', version: null, maker: 'FFmpeg' }, // the studio could not read it
    ]);
    expect(step('request').tools).toEqual([]); // a bill from before tools were listed has none
  });

  it('shows the lesson alone while the videos are not done', () => {
    const b = serializeBreakdown(lesson, null)!;
    expect(b.steps).toHaveLength(2);
    expect(b).toMatchObject({ seconds: 180.4, partSeconds: { lesson: 180.4, videos: null }, credits: null, unpriced: 0, complete: true });
  });

  it('counts a model whose provider said neither a price nor credits', () => {
    const music = { steps: [{ step: 'audio', models: [{ model: 'music_v2_5', calls: 1, costUsd: null, credits: null }] }] };
    expect(serializeBreakdown(null, music)).toMatchObject({ costUsd: 0, credits: null, unpriced: 1, complete: false });
  });

  it('keeps the calls the teacher\'s session answered on a line of their own, at nothing on the bill', () => {
    const b = serializeBreakdown({ steps: [{ step: 'bundle', seconds: 400, tries: 2, models: [
      { model: 'claude-opus-5-5', calls: 1, costUsd: 0.6, credits: null },
      { model: 'claude-opus-5-5', calls: 1, costUsd: 0, credits: null, via: 'session' },
      { model: 'claude-sonnet-5-5', calls: 1, costUsd: 0, credits: null, via: 'something else' }, // not a via: dropped
    ] }], seconds: 400 }, undefined)!;
    expect(b.steps[0].models).toEqual([
      { model: 'claude-opus-5-5', maker: 'Anthropic', calls: 1, costUsd: 0.6, credits: null },
      { model: 'claude-opus-5-5', maker: 'Anthropic', calls: 1, costUsd: 0, credits: null, via: 'session' },
      { model: 'claude-sonnet-5-5', maker: 'Anthropic', calls: 1, costUsd: 0, credits: null },
    ]);
    expect(b.costUsd).toBe(0.6);
  });

  it('keeps what each model and tool did in its step, as the studio said it, and none for an older bill', () => {
    const b = serializeBreakdown(null, { steps: [{ step: 'timing', seconds: 110, tries: 1, models: [
      { model: 'htdemucs', calls: 1, costUsd: 0, credits: null, task: 'separates the vocals so the words can be timed' },
      { model: 'mms-fa', calls: 1, costUsd: 0, credits: null }, // a bill from before the studio said
      { model: 'mms-fa', calls: 1, costUsd: 0, credits: null, task: 7 }, // not text: none
    ], tools: [
      { name: 'demucs', version: '4.1.0', task: 'runs htdemucs' },
      { name: 'torch', version: '2.11.0', task: 'x'.repeat(200) }, // kept to 60 characters
      { name: 'ffmpeg', version: '7.1.1' },
    ] }], seconds: 110 })!;
    expect(b.steps[0].models.map((m) => m.task)).toEqual(['separates the vocals so the words can be timed', undefined, undefined]);
    expect(b.steps[0].tools).toEqual([
      { name: 'demucs', version: '4.1.0', maker: 'Meta', task: 'runs htdemucs' },
      { name: 'torch', version: '2.11.0', maker: 'Meta', task: 'x'.repeat(60) },
      { name: 'ffmpeg', version: '7.1.1', maker: 'FFmpeg' },
    ]);
  });

  it('works out each step from its models, and keeps only what it knows', () => {
    const b = serializeBreakdown({
      steps: [
        { step: 'bundle', seconds: -3, tries: 'two', costUsd: 99, extra: 'dropped', models: [
          { model: 'claude-opus-5-5', calls: 1.6, costUsd: 0.25, credits: 'many' },
          { model: '', calls: 1, costUsd: 5 }, // no name: not a model
          'not a model',
        ], tools: [
          { name: 'ffmpeg', version: 7, extra: 'dropped' }, // a version that is not text: none
          { name: '', version: '1.0' }, // no name: not a tool
          'not a tool',
        ] },
        { step: '', seconds: 1 }, // no name: not a step
        'not a step',
      ],
      seconds: Infinity,
    }, undefined)!;
    expect(b.steps).toEqual([{ step: 'bundle', part: 'lesson', seconds: null, tries: 0, costUsd: 0.25,
      models: [{ model: 'claude-opus-5-5', maker: 'Anthropic', calls: 2, costUsd: 0.25, credits: null }],
      tools: [{ name: 'ffmpeg', version: null, maker: 'FFmpeg' }] }]);
    expect(b.seconds).toBeNull();
    expect(b.partSeconds).toEqual({ lesson: null, videos: null });
    expect(b.costUsd).toBe(0.25);
  });
});

describe('billModels', () => {
  it('names every model a bill does, step by step, as stored', () => {
    expect(billModels(lesson)).toEqual(['claude-opus-5-5', 'claude-opus-5']);
    expect(billModels(videos)).toEqual(['lyria-3.5', 'eleven_multilingual_v2', 'htdemucs', 'mms-fa']);
    expect(billModels({ steps: [{ step: 'x', models: [{ model: 7 }, { calls: 1 }, 'junk'] }, 'junk', { step: 'y' }] }))
      .toEqual([7, undefined]); // madeWithOf drops what is not a name
  });
  it('is null with no bill', () => {
    expect(billModels(null)).toBeNull();
    expect(billModels(undefined)).toBeNull();
    expect(billModels([])).toBeNull();
  });
});

describe('madeWithOf', () => {
  it('lists each model once, under its maker, in the order the bill first names them', () => {
    const brainCheck = { steps: [{ step: 'factcheck', models: [{ model: 'claude-sonnet-5-5' }, { model: 'claude-opus-5-5' }] }] };
    const lessonModels = [...billModels(lesson)!, ...billModels(brainCheck)!];
    expect(madeWithOf(lessonModels, billModels(videos))).toEqual([
      { maker: 'Anthropic', models: ['claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5'] },
      { maker: 'Google', models: ['lyria-3.5'] },
      { maker: 'ElevenLabs', models: ['eleven_multilingual_v2'] },
      { maker: 'Meta', models: ['htdemucs', 'mms-fa'] },
    ]);
  });
  it('puts the models no maker is listed for last, and skips what is not a name', () => {
    expect(madeWithOf(['some-new-model', 'claude-opus-5-5', '', '  ', 7, null], ['other-model', ' claude-opus-5-5 '])).toEqual([
      { maker: 'Anthropic', models: ['claude-opus-5-5'] },
      { maker: null, models: ['some-new-model', 'other-model'] },
    ]);
  });
  it('shows only what reads as a model name: never a key, personal information or other text from the agent', () => {
    const sent = ['claude-opus-5-5', `sk-ant-api03-${'x'.repeat(24)}`, `AIza${'b'.repeat(35)}`, '555-123-4567', 'me@example.com',
      'visit https://x.example', 'ignore the rules', 'a'.repeat(65), '-leading-dash', 'music_v2_5', 'muse-spark-1.3', 'sam-3.1'];
    expect(madeWithOf(sent, [])).toEqual([
      { maker: 'Anthropic', models: ['claude-opus-5-5'] },
      { maker: 'ElevenLabs', models: ['music_v2_5'] },
      { maker: 'Meta', models: ['muse-spark-1.3', 'sam-3.1'] },
    ]);
    expect(madeWithOf([`sk-ant-api03-${'x'.repeat(24)}`], ['me@example.com'])).toBeNull();
  });
  it('shows at most MADE_WITH_MAX_MODELS, the first the bill names, from a bill packed with names in linear time', () => {
    const many = Array.from({ length: 1200 }, (_, i) => `claude-${i}`); // about what 16 KB of bill can hold
    const started = performance.now();
    const made = madeWithOf([...many, ...many], many)!;
    expect(performance.now() - started).toBeLessThan(50);
    expect(made).toEqual([{ maker: 'Anthropic', models: many.slice(0, MADE_WITH_MAX_MODELS) }]);
  });
  it('says nothing without both bills, or when they name no model', () => {
    expect(madeWithOf(billModels(lesson), null)).toBeNull(); // who drew and sang it is not known
    expect(madeWithOf(null, billModels(videos))).toBeNull();
    expect(madeWithOf([], [])).toBeNull();
    expect(madeWithOf([''], [7])).toBeNull();
  });
});

describe('makerOf', () => {
  it('knows the makers of the models and tools the studio runs, whatever their version', () => {
    expect(makerOf('claude-opus-5-5')).toBe('Anthropic');
    expect(makerOf('claude-haiku-5-5')).toBe('Anthropic');
    expect(makerOf('muse-wonder-1.0')).toBe('Meta');
    expect(makerOf('gemini-3.1-flash-image')).toBe('Google');
    expect(makerOf('muse-image-1.0')).toBe('Meta');
    expect(makerOf('sam-3.1')).toBe('Meta');
    expect(makerOf('htdemucs')).toBe('Meta');
    expect(makerOf('mms-fa')).toBe('Meta');
    expect(makerOf('torchaudio')).toBe('Meta');
    expect(makerOf('lyria-3.5')).toBe('Google');
    expect(makerOf('music_v2_5')).toBe('ElevenLabs');
    expect(makerOf('eleven_multilingual_v2')).toBe('ElevenLabs');
    expect(makerOf('FFmpeg')).toBe('FFmpeg');
  });
  it('names no maker for a name it does not know', () => {
    expect(makerOf('gpt-9')).toBeNull();
    expect(makerOf('')).toBeNull();
  });
});

describe('endToEndOf', () => {
  const asked = new Date('2026-11-02T12:00:00.000Z');
  const at = (minutes: number) => new Date(asked.getTime() + minutes * 60_000);

  it('counts from the request to the lesson posted and to the videos READY, waits and all', () => {
    expect(endToEndOf(asked, { createdAt: at(7.5), videoStatus: 'READY', videoUpdatedAt: at(15.2) }))
      .toEqual({ lesson: 450, videos: 912 });
  });
  it('has no videos time until they are READY, nor for a droplet without them', () => {
    expect(endToEndOf(asked, { createdAt: at(7.5), videoStatus: 'RENDERING', videoUpdatedAt: at(12) })).toEqual({ lesson: 450, videos: null });
    expect(endToEndOf(asked, { createdAt: at(7.5), videoStatus: 'FAILED', videoUpdatedAt: at(30) })).toEqual({ lesson: 450, videos: null });
    expect(endToEndOf(asked, { createdAt: at(7.5), videoStatus: null, videoUpdatedAt: null })).toEqual({ lesson: 450, videos: null });
    expect(endToEndOf(asked, { createdAt: at(7.5), videoStatus: 'READY', videoUpdatedAt: null })).toEqual({ lesson: 450, videos: null });
  });
  it('never has the videos before the lesson, whatever the two clocks say', () => {
    expect(endToEndOf(asked, { createdAt: at(7.5), videoStatus: 'READY', videoUpdatedAt: new Date(at(7.5).getTime() - 300) }))
      .toEqual({ lesson: 450, videos: 450 });
  });
  it('rounds to a tenth of a second, and is never less than nothing', () => {
    expect(endToEndOf(asked, { createdAt: new Date(asked.getTime() + 1234), videoStatus: null, videoUpdatedAt: null }).lesson).toBe(1.2);
    expect(endToEndOf(asked, { createdAt: at(-1), videoStatus: null, videoUpdatedAt: null }).lesson).toBe(0);
  });
});
