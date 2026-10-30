import { describe, it, expect, afterEach } from 'vitest';
import {
  dropletJsonError, videoUrlError, serializeVideo, followUpContext, STALE_VIDEO_ERROR, DROPLET_JSON_MAX_BYTES,
  dropletTitle, excerptOf, serializeDroplet, serializeDropletCard,
} from './droplet.js';

describe('dropletJsonError', () => {
  const passed = { status: 'passed', rounds: 1, lines: [] };
  it('allows a text-only lesson, and a bundle with a passed fact-check', () => {
    expect(dropletJsonError(undefined, undefined)).toBeNull();
    expect(dropletJsonError({ topic: 'ATP' }, passed)).toBeNull();
  });
  it('wants both, as objects', () => {
    expect(dropletJsonError({ topic: 'ATP' }, undefined)).toMatch(/together/);
    expect(dropletJsonError(undefined, passed)).toMatch(/together/);
    expect(dropletJsonError([], passed)).toMatch(/objects/);
    expect(dropletJsonError('{}', passed)).toMatch(/objects/);
  });
  it('refuses a lesson whose fact-check did not pass', () => {
    expect(dropletJsonError({}, { status: 'failed' })).toMatch(/passed/);
    expect(dropletJsonError({}, {})).toMatch(/passed/);
  });
  it('caps the size', () => {
    expect(dropletJsonError({ big: 'x'.repeat(DROPLET_JSON_MAX_BYTES) }, passed)).toMatch(/bundle must be 256 KB or less/);
  });
});

describe('videoUrlError', () => {
  it('takes http(s) URLs only', () => {
    expect(videoUrlError('https://media.popsmosis.com/a/out/song_h.mp4', 'songVideoUrl')).toBeNull();
    expect(videoUrlError('javascript:alert(1)', 'songVideoUrl')).toMatch(/http/);
    expect(videoUrlError('/relative.mp4', 'songVideoUrl')).toMatch(/http/);
    expect(videoUrlError(42, 'songVideoUrl')).toMatch(/URL/);
    expect(videoUrlError(`https://x.co/${'a'.repeat(2000)}`, 'songVideoUrl')).toMatch(/2000/);
  });
});

describe('serializeVideo', () => {
  afterEach(() => { delete process.env.VIDEO_STALE_MINUTES; });
  const row = (status: 'RENDERING' | 'READY' | 'FAILED' | null, minutesAgo: number) => ({
    videoStatus: status, songVideoUrl: null, lessonVideoUrl: null, songPosterUrl: null, lessonPosterUrl: null, videoError: null,
    videoUpdatedAt: new Date(Date.now() - minutesAgo * 60_000),
  });
  it('is null for a text-only droplet', () => {
    expect(serializeVideo(row(null, 0))).toBeNull();
  });
  it('reads a render that stopped reporting as failed', () => {
    expect(serializeVideo(row('RENDERING', 5))).toMatchObject({ status: 'RENDERING', error: null });
    expect(serializeVideo(row('RENDERING', 61))).toMatchObject({ status: 'FAILED', error: STALE_VIDEO_ERROR });
    expect(serializeVideo(row('READY', 600))).toMatchObject({ status: 'READY' }); // only a render goes stale
    process.env.VIDEO_STALE_MINUTES = '5';
    expect(serializeVideo(row('RENDERING', 6))?.status).toBe('FAILED');
  });
});

describe('followUpContext', () => {
  it('keeps what a follow-up needs and drops the rest, an old bundle\'s exam question included', () => {
    const context = followUpContext({
      course: 'AP Biology', topic: 'the light reactions', audience: 'high school', core_idea: 'Light pumps protons.',
      facts: [{ id: 'f1', text: 'Only cytochrome b6f pumps protons.', source_quote: '' }],
      misconception: 'Not every carrier pumps.',
      frq: { prompt: 'Explain.', predict_question: 'Which?', model_answer_steps: ['b6f pumps.'], twist: 'A leak?' },
      scenes: [{ id: 'hook' }], direction: [{ line_id: 'v1' }], narration: [{ id: 'n1' }],
      lyrics: [{ id: 'v1', section: 'verse', text: 'Light lands', names: [], fact_id: 'f1' }],
    });
    expect(context).toEqual({
      course: 'AP Biology', topic: 'the light reactions', audience: 'high school', coreIdea: 'Light pumps protons.',
      facts: [{ id: 'f1', text: 'Only cytochrome b6f pumps protons.' }],
      misconception: 'Not every carrier pumps.',
      lyrics: [{ section: 'verse', text: 'Light lands' }],
    });
  });
  it('is null without a bundle, and tolerant of a partial one', () => {
    expect(followUpContext(null)).toBeNull();
    expect(followUpContext({ topic: 'DNA', facts: 'nope' })).toMatchObject({ topic: 'DNA', facts: [], lyrics: [] });
  });
});

describe('the public droplet', () => {
  const row = {
    id: 'a1',
    content: '## ATP synthase\n\nA **rotary motor** in the [inner membrane](https://x.org).\n\n| A | B |\n|---|---|\n| 1 | 2 |',
    createdAt: new Date('2026-10-27T12:00:00Z'),
    bundle: {
      course: 'AP Biology', topic: 'ATP synthase', core_idea: 'x', scenes: [{ id: 's' }],
      lyrics: [{ id: 'l1', section: 'verse', text: 'Protons flow', names: [], fact_id: 'f1' }, { id: 'l2', section: 'chorus' }, 'junk'],
    },
    songVideoUrl: 'https://x/song.mp4', lessonVideoUrl: null, songPosterUrl: 'https://x/song.jpg', lessonPosterUrl: null,
    likeCount: 3, commentCount: 1,
    songTags: ['atpsynthase', 'apbio', 'studysong'], lessonTags: ['atpsynthase', 'apbio'],
  };

  it('is titled from the bundle, never the request', () => {
    expect(dropletTitle(row.bundle)).toEqual({ title: 'AP Biology: ATP synthase', course: 'AP Biology', topic: 'ATP synthase' });
    expect(dropletTitle({ topic: 'DNA' }).title).toBe('DNA');
    expect(dropletTitle(null).title).toBe('A droplet');
  });

  it('lists a card with plain-text lines of the lesson', () => {
    const card = serializeDropletCard(row);
    expect(card.excerpt).toBe('ATP synthase A rotary motor in the inner membrane. A B 1 2');
    expect(card.video).toEqual({ songVideoUrl: 'https://x/song.mp4', lessonVideoUrl: null, songPosterUrl: 'https://x/song.jpg', lessonPosterUrl: null });
    expect(Object.keys(card).sort()).toEqual(['commentCount', 'course', 'createdAt', 'excerpt', 'id', 'likeCount', 'liked', 'tags', 'title', 'topic', 'video']);
    expect(card.tags).toEqual({ song: ['atpsynthase', 'apbio', 'studysong'], lesson: ['atpsynthase', 'apbio'] });
    expect(card).toMatchObject({ likeCount: 3, commentCount: 1, liked: false }); // nobody signed in
    expect(serializeDropletCard(row, true).liked).toBe(true);
    expect(excerptOf('word '.repeat(100), 20)).toBe('word word word word…');
  });

  it('drops table and horizontal rules, keeps link text, and takes linear time on lines made to be slow', () => {
    expect(excerptOf('# Title\n\n| a | b |\n|:---|---:|\n| 1 | 2 |\n\n---\r\nSee [the docs](https://x.example/a) and ![a cell](c.png).'))
      .toBe('Title a b 1 2 See the docs and a cell.');
    expect(excerptOf('Not a rule: a---b and -- two')).toBe('Not a rule: a---b and -- two');
    expect(excerptOf(`Read [the guide](https://example.org/${'a'.repeat(520)}) first.`)).toBe('Read the guide first.');
    // Each of these took about 3 s with the old rule regex (a dash line that ends in something else), or grew with
    // the square of the length for brackets that never close.
    for (const slow of ['-'.repeat(1599) + 'a', '-'.repeat(1598) + ' x', '| ' + '- '.repeat(799) + '#', '['.repeat(1600),
      '!['.repeat(800), '[a](' + '('.repeat(1596), '[a]('.repeat(400)]) {
      const started = performance.now();
      excerptOf(slow);
      expect(performance.now() - started, slow.slice(0, 6)).toBeLessThan(50);
    }
  });

  it('shows one with the lesson and the lyrics, and nothing else from the bundle', () => {
    const droplet = serializeDroplet(row);
    expect(droplet.content).toBe(row.content);
    expect(droplet.lyrics).toEqual([{ section: 'verse', text: 'Protons flow' }]);
    expect(JSON.stringify(droplet)).not.toMatch(/core_idea|scenes|fact_id/);
  });
});
