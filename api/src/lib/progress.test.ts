import { describe, it, expect, afterEach } from 'vitest';
import {
  awaitingVideos, lessonHoldMinutes, lessonProgress, progressError, readProgress, secondsSoFar, storedProgress, videoProgress,
} from './progress.js';

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
  it('keeps when each running step started and when the bill came, by this clock, and reads back only what it knows', () => {
    const stored = storedProgress({ running: [{ step: 'art', seconds: 90 }, { step: 'audio', seconds: 0 }], failed: ['locate'], videos: true, breakdown: BILL }, NOW);
    expect(stored).toEqual({
      running: [{ step: 'art', startedAt: '2026-11-03T11:58:30.000Z' }, { step: 'audio', startedAt: NOW.toISOString() }],
      failed: ['locate'],
      videos: true,
      billAt: NOW.toISOString(),
    });
    expect(storedProgress({ running: [] }, NOW)).toEqual({ running: [], failed: [] }); // no bill: nothing to count on from
    // Read back with how long each has run by the same clock, which a browser counts on from.
    expect(readProgress({ ...stored, breakdown: BILL, extra: 1 }, NOW)).toEqual({
      ...stored,
      running: [{ step: 'art', startedAt: '2026-11-03T11:58:30.000Z', seconds: 90 }, { step: 'audio', startedAt: NOW.toISOString(), seconds: 0 }],
      breakdown: BILL,
    });
    expect(readProgress(null)).toEqual({ running: [], failed: [], videos: null, billAt: null, breakdown: undefined });
    expect(readProgress({ running: [{ step: 'art', startedAt: 'never' }, 'x'], failed: [1, 'art'] }).running).toEqual([]);
    expect(readProgress({ running: [], billAt: 'never' }).billAt).toBeNull();
    // A start a little ahead of this clock (another API instance) never counts below zero.
    expect(readProgress({ running: [{ step: 'art', startedAt: '2026-11-03T12:00:02.000Z' }] }, NOW).running[0].seconds).toBe(0);
  });
});

describe('secondsSoFar', () => {
  const bill = (seconds: number | null) => ({ seconds }) as Parameters<typeof secondsSoFar>[0];
  const billAt = minutesAgo(0.5).toISOString();
  const run = (...seconds: number[]) => seconds.map((s) => ({ step: 'art', startedAt: '', seconds: s }));

  it('counts the time since the bill came on top of it while a step runs', () => {
    expect(secondsSoFar(bill(196), run(29), billAt, 0, NOW)).toBe(226);
    expect(secondsSoFar(bill(null), run(29), billAt, 0, NOW)).toBe(30); // the first step: a bill with no time yet
    // A bill a little ahead of this clock (another API instance) never takes the bill's time down.
    expect(secondsSoFar(bill(196), run(0), minutesAgo(-0.1).toISOString(), 0, NOW)).toBe(196);
  });

  it('is never less than the parts before and the longest step running, nor with no bill to go on', () => {
    // Reported once its claim was renewed: the step has run a little longer than the bill knows.
    expect(secondsSoFar(bill(null), run(31.5), billAt, 0, NOW)).toBe(31.5);
    expect(secondsSoFar(bill(352), run(80, 3), billAt, 276, NOW)).toBe(382); // the lesson's 276, the art's 76 and 30
    expect(secondsSoFar(bill(276), run(80, 3), null, 276, NOW)).toBe(356); // no bill came: the lesson and the 80
    expect(secondsSoFar(null, run(12), null, 0, NOW)).toBe(12);
    // A report stored before bills were timed: never less than its bill, which the page shows above.
    expect(secondsSoFar(bill(196), run(20), null, 0, NOW)).toBe(196);
  });

  it('is the bill\'s time while nothing runs', () => {
    expect(secondsSoFar(bill(196), [], billAt, 0, NOW)).toBe(196);
    expect(secondsSoFar(null, [], billAt, 0, NOW)).toBeNull();
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
  const report = { running: [{ step: 'bundle', startedAt: NOW.toISOString() }], failed: [], videos: false, billAt: NOW.toISOString(), breakdown: BILL };

  it('shows a lesson being written with its bill and its time so far, and nothing running once its lease has lapsed', () => {
    const live = lessonProgress(report, true, minutesAgo(-0.5));
    expect(live.running).toEqual([{ ...report.running[0], seconds: 30 }]);
    expect(live.videos).toBe(false);
    expect(live.seconds).toBe(226); // the bill's 196 seconds, and the 30 since the report
    expect(live.breakdown?.steps.map((s) => [s.step, s.part])).toEqual([['bundle', 'lesson']]);
    const lapsed = lessonProgress(report, false, minutesAgo(-0.5));
    expect(lapsed.running).toEqual([]);
    expect(lapsed.seconds).toBe(196); // nothing runs: the time stops at the bill's
    expect(lessonProgress(null, true)).toEqual({ running: [], failed: [], videos: true, seconds: null, breakdown: null });
  });

  it('shows the videos being made with the whole bill so far, and nothing running once they stop rendering', () => {
    const p = videoProgress({ running: [{ step: 'art', startedAt: NOW.toISOString() }], failed: ['audio'] }, true, BILL,
      { steps: [{ step: 'audio', seconds: 40, tries: 1, models: [] }], seconds: 40 });
    expect(p.running.map((r) => r.step)).toEqual(['art']);
    expect(p.failed).toEqual(['audio']);
    expect(p.breakdown?.steps.map((s) => s.step)).toEqual(['bundle', 'audio']);
    expect(videoProgress({ running: [{ step: 'art', startedAt: NOW.toISOString() }], failed: ['audio'] }, false, BILL, null))
      .toMatchObject({ running: [], failed: [], videos: true, seconds: 196 });
  });

  it('counts the videos\' time so far on from their last report, on top of the lesson\'s whole bill', () => {
    const videos = { steps: [{ step: 'art', seconds: 76, tries: 1, models: [] }], seconds: 76 };
    const raw = { running: [{ step: 'audio', startedAt: minutesAgo(1.5).toISOString() }], failed: [], billAt: minutesAgo(0.25).toISOString() };
    expect(videoProgress(raw, true, BILL, videos, NOW).seconds).toBe(287); // 196 + 76 + 15
    // A report with no bill yet (this attempt's first, cut short): the lesson's time and the audio's 90 seconds.
    expect(videoProgress({ ...raw, billAt: undefined }, true, BILL, videos, NOW).seconds).toBe(286);
  });
});
