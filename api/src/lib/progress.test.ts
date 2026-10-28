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
    // Read back with how long each has run by the same clock, which a browser counts on from.
    expect(readProgress({ ...stored, breakdown: BILL, extra: 1 }, NOW)).toEqual({
      ...stored,
      running: [{ step: 'art', startedAt: '2026-11-03T11:58:30.000Z', seconds: 90 }, { step: 'audio', startedAt: NOW.toISOString(), seconds: 0 }],
      breakdown: BILL,
    });
    expect(readProgress(null)).toEqual({ running: [], failed: [], videos: null, breakdown: undefined });
    expect(readProgress({ running: [{ step: 'art', startedAt: 'never' }, 'x'], failed: [1, 'art'] }).running).toEqual([]);
    // A start a little ahead of this clock (another API instance) never counts below zero.
    expect(readProgress({ running: [{ step: 'art', startedAt: '2026-11-03T12:00:02.000Z' }] }, NOW).running[0].seconds).toBe(0);
  });
});

describe('awaitingVideos', () => {
  afterEach(() => { delete process.env.LESSON_HOLD_MINUTES; });
  const lesson = (minutes: number, attempts = 1) => ({ createdAt: minutesAgo(minutes), videoAttempts: attempts });

  it('holds the text back while the first attempt renders, for 20 minutes after the lesson was posted', () => {
    expect(lessonHoldMinutes()).toBe(20);
    expect(awaitingVideos(lesson(5), { status: 'RENDERING' }, true, NOW)).toBe(true);
    expect(awaitingVideos(lesson(19.9), { status: 'RENDERING' }, true, NOW)).toBe(true);
    expect(awaitingVideos(lesson(20), { status: 'RENDERING' }, true, NOW)).toBe(false);
  });

  it('never holds a lesson whose studio did not say videos follow, so a claim just after its post cannot hide it', () => {
    expect(awaitingVideos(lesson(0.1, 0), null, false, NOW)).toBe(false); // shown at its post...
    expect(awaitingVideos(lesson(0.2), { status: 'RENDERING' }, false, NOW)).toBe(false); // ...and still, once claimed
  });

  it('shows it once the videos are READY or FAILED, or a second attempt starts, so it is never hidden again', () => {
    expect(awaitingVideos(lesson(5), { status: 'READY' }, true, NOW)).toBe(false);
    expect(awaitingVideos(lesson(5), { status: 'FAILED' }, true, NOW)).toBe(false);
    expect(awaitingVideos(lesson(5, 2), { status: 'RENDERING' }, true, NOW)).toBe(false);
    expect(awaitingVideos(lesson(5, 0), { status: 'RENDERING' }, true, NOW)).toBe(true); // marked by hand, never claimed
  });

  it('holds a lesson with no videos yet only when the studio said they are coming', () => {
    expect(awaitingVideos(lesson(1, 0), null, true, NOW)).toBe(true);
    expect(awaitingVideos(lesson(1, 0), null, false, NOW)).toBe(false); // a text-only droplet: shown at once
    expect(awaitingVideos(lesson(25, 0), null, true, NOW)).toBe(false);
  });

  it('never holds past the stale limit, so a late heartbeat cannot hide a lesson the stale render showed', () => {
    process.env.VIDEO_STALE_MINUTES = '10';
    expect(awaitingVideos(lesson(9), { status: 'RENDERING' }, true, NOW)).toBe(true);
    expect(awaitingVideos(lesson(11), { status: 'RENDERING' }, true, NOW)).toBe(false);
    delete process.env.VIDEO_STALE_MINUTES;
  });

  it('takes LESSON_HOLD_MINUTES, where 0 never holds and a bad value keeps the default', () => {
    process.env.LESSON_HOLD_MINUTES = '5';
    expect(awaitingVideos(lesson(4), { status: 'RENDERING' }, true, NOW)).toBe(true);
    expect(awaitingVideos(lesson(6), { status: 'RENDERING' }, true, NOW)).toBe(false);
    process.env.LESSON_HOLD_MINUTES = '0';
    expect(awaitingVideos(lesson(0), { status: 'RENDERING' }, true, NOW)).toBe(false);
    process.env.LESSON_HOLD_MINUTES = 'soon';
    expect(lessonHoldMinutes()).toBe(20);
  });
});

describe('the page\'s view', () => {
  const report = { running: [{ step: 'bundle', startedAt: NOW.toISOString() }], failed: [], videos: false, breakdown: BILL };

  it('shows a lesson being written with its bill, and nothing running once its lease has lapsed', () => {
    const live = lessonProgress(report, true, minutesAgo(-0.5));
    expect(live.running).toEqual([{ ...report.running[0], seconds: 30 }]);
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
