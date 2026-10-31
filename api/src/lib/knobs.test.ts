import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  KNOBS, KNOB_NAMES, REFRESH_MS, keepKnobsFresh, knob, knobsFrom, parseKnobs, putKnobs, reloadKnobsWith, storedKnobs,
} from './knobs.js';
import { claimMinutes } from './claim.js';
import { videoStaleMinutes } from './droplet.js';
import { commentsPerUserPerHour } from './engagement.js';
import { lessonHoldMinutes } from './progress.js';
import { debounceMs, fireBetaHeader } from './routine.js';
import { usageLimits } from './usage.js';
import { videoMaxAttempts } from './video-claim.js';

afterEach(() => {
  putKnobs({});
  delete process.env.QUESTION_CLAIM_MINUTES;
  vi.useRealTimers();
});

describe('KNOBS', () => {
  it('has a default each knob takes, so the page can offer it', () => {
    expect(knobsFrom(Object.fromEntries(KNOB_NAMES.map((name) => [name, KNOBS[name].default])))).toEqual(
      Object.fromEntries(KNOB_NAMES.filter((name) => KNOBS[name].default !== null).map((name) => [name, KNOBS[name].default])),
    );
  });

  it('has the defaults the code that reads each one has without it', () => {
    expect(usageLimits({})).toEqual({
      topicsPerUserPerDay: KNOBS.QUESTIONS_PER_USER_PER_DAY.default,
      topicsPerDay: KNOBS.TOPICS_PER_DAY.default,
      repliesPerUserPerHour: KNOBS.REPLIES_PER_USER_PER_HOUR.default,
      attachmentMbPerUserPerDay: KNOBS.ATTACHMENT_MB_PER_USER_PER_DAY.default,
      attachmentMbHeldPerUser: KNOBS.ATTACHMENT_MB_HELD_PER_USER.default,
    });
    expect(commentsPerUserPerHour()).toBe(KNOBS.DROPLET_COMMENTS_PER_USER_PER_HOUR.default);
    expect(claimMinutes()).toBe(KNOBS.QUESTION_CLAIM_MINUTES.default);
    expect(videoStaleMinutes()).toBe(KNOBS.VIDEO_STALE_MINUTES.default);
    expect(lessonHoldMinutes()).toBe(KNOBS.LESSON_HOLD_MINUTES.default);
    expect(videoMaxAttempts()).toBe(KNOBS.VIDEO_MAX_ATTEMPTS.default);
    expect(debounceMs()).toBe(KNOBS.POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS.default * 1000);
    expect(fireBetaHeader()).toBe(KNOBS.POPSMOSIS_ROUTINE_BETA.default);
  });
});

describe('parseKnobs', () => {
  it('takes a value each knob allows, and null to give a knob back to the environment', () => {
    expect(parseKnobs({ QUESTION_CLAIM_MINUTES: 30, TOPICS_PER_DAY: 0, STUDIO_VIDEOS: 'off' })).toEqual({
      knobs: { QUESTION_CLAIM_MINUTES: 30, TOPICS_PER_DAY: 0, STUDIO_VIDEOS: 'off' },
    });
    expect(parseKnobs({ ELEVENLABS_USD_PER_1K_CREDITS: 0.3, ELEVENLABS_VOICE_ID: 'Xb7hH8MSUJpSbSDYk0k2' }).knobs)
      .toEqual({ ELEVENLABS_USD_PER_1K_CREDITS: 0.3, ELEVENLABS_VOICE_ID: 'Xb7hH8MSUJpSbSDYk0k2' });
    expect(parseKnobs({ GEMINI_SONG_MODEL: null }).knobs).toEqual({ GEMINI_SONG_MODEL: null });
    for (const draws of ['math', 'charts', 'brain']) {
      expect(parseKnobs({ STUDIO_CLAUDE_DRAWS: draws }).knobs).toEqual({ STUDIO_CLAUDE_DRAWS: draws });
    }
  });

  it('refuses a value out of range, of the wrong kind, or not a choice, and a name that is not a knob', () => {
    expect(parseKnobs({ QUESTION_CLAIM_MINUTES: 0 }).error).toBe('knobs.QUESTION_CLAIM_MINUTES must be a whole number from 1 to 1440');
    expect(parseKnobs({ QUESTION_CLAIM_MINUTES: '30' }).error).toBe('knobs.QUESTION_CLAIM_MINUTES must be a whole number from 1 to 1440');
    expect(parseKnobs({ VIDEO_MAX_ATTEMPTS: 2.5 }).error).toBe('knobs.VIDEO_MAX_ATTEMPTS must be a whole number from 1 to 10');
    expect(parseKnobs({ ELEVENLABS_USD_PER_1K_CREDITS: -1 }).error).toBe('knobs.ELEVENLABS_USD_PER_1K_CREDITS must be a number from 0 to 100');
    expect(parseKnobs({ STUDIO_VIDEOS: 'no' }).error).toBe('knobs.STUDIO_VIDEOS must be one of on, off');
    expect(parseKnobs({ STUDIO_CLAUDE_DRAWS: 'everything' }).error)
      .toBe('knobs.STUDIO_CLAUDE_DRAWS must be one of math, charts, brain');
    expect(parseKnobs({ ELEVENLABS_VOICE_ID: 'Alice the voice' }).error)
      .toBe('knobs.ELEVENLABS_VOICE_ID must be an ElevenLabs voice id: letters and digits');
    expect(parseKnobs({ POPSMOSIS_ROUTINE_BETA: '' }).error).toBe('knobs.POPSMOSIS_ROUTINE_BETA must be lowercase letters, digits and hyphens');
    expect(parseKnobs({ JWT_SECRET: 'x' }).error).toBe('knobs has no JWT_SECRET');
    expect(parseKnobs({ ATTACHMENT_RETENTION_DAYS: 7 }).error).toBe('knobs has no ATTACHMENT_RETENTION_DAYS');
    expect(parseKnobs({ toString: 1 }).error).toBe('knobs has no toString');
    expect(parseKnobs({}).error).toBe('knobs names nothing to change');
    expect(parseKnobs([]).error).toBe('knobs must be an object');
  });
});

describe('knobsFrom', () => {
  it('keeps only knobs at a value still allowed', () => {
    for (const stored of [undefined, null, 'x', [], 4]) expect(knobsFrom(stored)).toEqual({});
    expect(knobsFrom({ QUESTION_CLAIM_MINUTES: 30, VIDEO_MAX_ATTEMPTS: 99, PORT: 80, STUDIO_VIDEOS: null })).toEqual({
      QUESTION_CLAIM_MINUTES: 30,
    });
  });
});

describe('knob', () => {
  it('is the value set on the page over the environment, and the environment\'s once it is cleared', () => {
    process.env.QUESTION_CLAIM_MINUTES = '45';
    expect(knob('QUESTION_CLAIM_MINUTES')).toBe('45');
    expect(claimMinutes()).toBe(45);
    putKnobs({ QUESTION_CLAIM_MINUTES: 30 });
    expect(knob('QUESTION_CLAIM_MINUTES')).toBe('30');
    expect(claimMinutes()).toBe(30);
    putKnobs({});
    expect(claimMinutes()).toBe(45);
    delete process.env.QUESTION_CLAIM_MINUTES;
    expect(knob('QUESTION_CLAIM_MINUTES')).toBeUndefined();
    expect(claimMinutes()).toBe(20);
  });

  it('wins over an environment passed in, as the limits are read', () => {
    putKnobs({ TOPICS_PER_DAY: 0, QUESTIONS_PER_USER_PER_DAY: 3, ATTACHMENT_MB_HELD_PER_USER: 7 });
    expect(usageLimits({ TOPICS_PER_DAY: '50', REPLIES_PER_USER_PER_HOUR: '4', ATTACHMENT_MB_HELD_PER_USER: '500' })).toEqual({
      topicsPerUserPerDay: 3, topicsPerDay: 0, repliesPerUserPerHour: 4,
      attachmentMbPerUserPerDay: KNOBS.ATTACHMENT_MB_PER_USER_PER_DAY.default, attachmentMbHeldPerUser: 7,
    });
  });

  it('reaches the cooldown and the beta header the routine fires with', () => {
    putKnobs({ POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS: 0, POPSMOSIS_ROUTINE_BETA: 'experimental-cc-routine-2026-11-01' });
    expect(debounceMs()).toBe(0);
    expect(fireBetaHeader()).toBe('experimental-cc-routine-2026-11-01');
  });
});

describe('reloadKnobsWith', () => {
  it('puts what it read in force', async () => {
    await reloadKnobsWith(async () => ({ VIDEO_STALE_MINUTES: 15 }));
    expect(storedKnobs()).toEqual({ VIDEO_STALE_MINUTES: 15 });
  });

  it('reads once for callers that ask at the same time', async () => {
    const load = vi.fn(async () => ({ VIDEO_MAX_ATTEMPTS: 3 }));
    await Promise.all([reloadKnobsWith(load), reloadKnobsWith(load), reloadKnobsWith(load)]);
    expect(load).toHaveBeenCalledTimes(1);
    expect(storedKnobs()).toEqual({ VIDEO_MAX_ATTEMPTS: 3 });
  });

  it('never undoes a change put in force while it read', async () => {
    let answer: (value: unknown) => void = () => {};
    const read = reloadKnobsWith(() => new Promise((resolve) => { answer = resolve; }));
    putKnobs({ VIDEO_MAX_ATTEMPTS: 4 }); // an admin's change lands on this instance mid-read
    answer({ VIDEO_MAX_ATTEMPTS: 2 });
    await read;
    expect(storedKnobs()).toEqual({ VIDEO_MAX_ATTEMPTS: 4 });
  });

  it('keeps what is in force when the read fails', async () => {
    putKnobs({ LESSON_HOLD_MINUTES: 0 });
    await expect(reloadKnobsWith(async () => { throw new Error('the database did not answer'); })).rejects.toThrow();
    expect(storedKnobs()).toEqual({ LESSON_HOLD_MINUTES: 0 });
  });
});

describe('keepKnobsFresh', () => {
  const log = { warn: vi.fn() };
  afterEach(() => log.warn.mockClear());

  it('has another instance\'s change in force within REFRESH_MS, with no request to wait on it', async () => {
    vi.useFakeTimers();
    let row: unknown = { QUESTION_CLAIM_MINUTES: 30 };
    const timer = keepKnobsFresh(async () => row, log);
    expect(claimMinutes()).toBe(20); // nothing read yet: the environment's, else the default
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
    expect(claimMinutes()).toBe(30);
    row = {}; // cleared through another instance
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
    expect(claimMinutes()).toBe(20);
    clearInterval(timer);
  });

  it('reads one at a time while the database is slow, warns once for a read that fails, and keeps what is in force', async () => {
    vi.useFakeTimers();
    putKnobs({ VIDEO_MAX_ATTEMPTS: 3 });
    let fail: (err: Error) => void = () => {};
    const load = vi.fn(() => new Promise((_, reject) => { fail = reject; }));
    const timer = keepKnobsFresh(load, log);
    await vi.advanceTimersByTimeAsync(REFRESH_MS * 3); // three ticks while the first read hangs
    expect(load).toHaveBeenCalledTimes(1);
    fail(new Error('the database did not answer'));
    await vi.advanceTimersByTimeAsync(0);
    expect(log.warn).toHaveBeenCalledTimes(1);
    expect(videoMaxAttempts()).toBe(3);
    clearInterval(timer);
  });
});
