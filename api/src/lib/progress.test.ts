import { describe, it, expect, afterEach } from 'vitest';
import { awaitingVideos, lessonHoldMinutes, lessonProgress, progressError, readProgress, storedProgress, videoProgress } from './progress.js';

const NOW = new Date('2026-11-03T12:00:00Z');
const minutesAgo = (m: number) => new Date(NOW.getTime() - m * 60_000);
const BILL = { steps: [{ step: 'bundle', seconds: 196, tries: 1, models: [{ model: 'claude-opus-5-5', calls: 1, costUsd: 0.53, credits: null }] }], seconds: 196 };

describe('progressError', () => {
  it('takes a report of the steps running, the failed ones, the bill so far and, for the lesson, whether videos follow', () => {
    expect(progressError({ running: [{ step: 'bundle', seconds: 0 }] }, 'lesson')).toBeNull();
    expect(progressError({ running: [], failed: ['art'], breakdown: BILL }, 'videos')).toBeNull();
    expect(progressError({ running: [{ step: 'factcheck', seconds: 12.5 }], videos: false, breakdown: BILL }, 'lesson')).toBeNull();
  });

  it('refuses anything else, saying why', () => {
    expect(progressError(undefined, 'lesson')).toMatch(/object/);
    expect(progressError({}, 'lesson')).toMatch(/running/);
    expect(progressError({ running: [{ step: '', seconds: 1 }] }, 'lesson')).toMatch(/step name/);
    expect(progressError({ running: [{ step: 'x'.repeat(41), seconds: 1 }] }, 'lesson')).toMatch(/step name/);
    expect(progressError({ running: [{ step: 'art', seconds: -1 }] }, 'videos')).toMatch(/seconds/);
    expect(progressError({ running: [{ step: 'art' }] }, 'videos')).toMatch(/seconds/);
    expect(progressError({ running: Array.from({ length: 13 }, () => ({ step: 'art', seconds: 1 })) }, 'videos')).toMatch(/at most 12/);
    expect(progressError({ running: [], failed: [3] }, 'videos')).toMatch(/failed/);
    expect(progressError({ running: [], videos: 'yes' }, 'lesson')).toMatch(/true or false/);
    expect(progressError({ running: [], videos: true }, 'videos')).toMatch(/only for the lesson/);
    expect(progressError({ running: [], breakdown: { steps: 'no' } }, 'lesson')).toMatch(/steps array/);
  });
});

describe('storedProgress and readProgress', () => {
  it('keeps when each running step started, by this clock, and reads back only what it knows', () => {
    const stored = storedProgress({ running: [{ step: 'art', seconds: 90 }, { step: 'audio', seconds: 0 }], failed: ['locate'], videos: true }, NOW);
    expect(stored).toEqual({
      running: [{ step: 'art', startedAt: '2026-11-03T11:58:30.000Z' }, { step: 'audio', startedAt: NOW.toISOString() }],
      failed: ['locate'],
      videos: true,
    });
    expect(readProgress({ ...stored, breakdown: BILL, extra: 1 })).toEqual({ ...stored, breakdown: BILL });
    expect(readProgress(null)).toEqual({ running: [], failed: [], videos: null, breakdown: undefined });
    expect(readProgress({ running: [{ step: 'art', startedAt: 'never' }, 'x'], failed: [1, 'art'] }).running).toEqual([]);
  });
});

describe('awaitingVideos', () => {
  afterEach(() => { delete process.env.LESSON_HOLD_MINUTES; });
  const lesson = (minutes: number, attempts = 1) => ({ createdAt: minutesAgo(minutes), videoAttempts: attempts });

  it('holds the text back while the first attempt renders, for 20 minutes after the lesson was posted', () => {
    expect(lessonHoldMinutes()).toBe(20);
    expect(awaitingVideos(lesson(5), { status: 'RENDERING' }, false, NOW)).toBe(true);
    expect(awaitingVideos(lesson(19.9), { status: 'RENDERING' }, false, NOW)).toBe(true);
    expect(awaitingVideos(lesson(20), { status: 'RENDERING' }, false, NOW)).toBe(false);
  });

  it('shows it once the videos are READY or FAILED, or a second attempt starts, so it is never hidden again', () => {
    expect(awaitingVideos(lesson(5), { status: 'READY' }, true, NOW)).toBe(false);
    expect(awaitingVideos(lesson(5), { status: 'FAILED' }, true, NOW)).toBe(false);
    expect(awaitingVideos(lesson(5, 2), { status: 'RENDERING' }, true, NOW)).toBe(false);
    expect(awaitingVideos(lesson(5, 0), { status: 'RENDERING' }, false, NOW)).toBe(true); // marked by hand, never claimed
  });

  it('holds a lesson with no videos yet only when the studio said they are coming', () => {
    expect(awaitingVideos(lesson(1, 0), null, true, NOW)).toBe(true);
    expect(awaitingVideos(lesson(1, 0), null, false, NOW)).toBe(false); // a text-only droplet: shown at once
    expect(awaitingVideos(lesson(25, 0), null, true, NOW)).toBe(false);
  });

  it('takes LESSON_HOLD_MINUTES, where 0 never holds and a bad value keeps the default', () => {
    process.env.LESSON_HOLD_MINUTES = '5';
    expect(awaitingVideos(lesson(6), { status: 'RENDERING' }, false, NOW)).toBe(false);
    process.env.LESSON_HOLD_MINUTES = '0';
    expect(awaitingVideos(lesson(0), { status: 'RENDERING' }, false, NOW)).toBe(false);
    process.env.LESSON_HOLD_MINUTES = 'soon';
    expect(lessonHoldMinutes()).toBe(20);
  });
});

describe('the page\'s view', () => {
  const report = { running: [{ step: 'bundle', startedAt: NOW.toISOString() }], failed: [], videos: false, breakdown: BILL };

  it('shows a lesson being written with its bill, and nothing running once its lease has lapsed', () => {
    const live = lessonProgress(report, true);
    expect(live.running).toEqual(report.running);
    expect(live.videos).toBe(false);
    expect(live.breakdown?.steps.map((s) => [s.step, s.part])).toEqual([['bundle', 'lesson']]);
    expect(lessonProgress(report, false).running).toEqual([]);
    expect(lessonProgress(null, true)).toEqual({ running: [], failed: [], videos: true, breakdown: null });
  });

  it('shows the videos being made with the whole bill so far, and nothing running once they stop rendering', () => {
    const p = videoProgress({ running: [{ step: 'art', startedAt: NOW.toISOString() }], failed: ['audio'] }, true, BILL,
      { steps: [{ step: 'audio', seconds: 40, tries: 1, models: [] }], seconds: 40 });
    expect(p.running.map((r) => r.step)).toEqual(['art']);
    expect(p.failed).toEqual(['audio']);
    expect(p.breakdown?.steps.map((s) => s.step)).toEqual(['bundle', 'audio']);
    expect(videoProgress({ running: [{ step: 'art', startedAt: NOW.toISOString() }], failed: ['audio'] }, false, BILL, null))
      .toMatchObject({ running: [], failed: [], videos: true });
  });
});
