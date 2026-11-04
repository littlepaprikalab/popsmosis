import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FIRE_RETRY_MS, RoutineTrigger, parseDebounceMs, routineTrigger } from './routine.js';

const log = { info: () => {}, warn: () => {} };
// Most cases only exercise the fire path, so the queues always hold work.
const WORK = async () => true;
const IDLE = async () => false;

function stubFetch(response: Partial<Response> = { ok: true, status: 200 }) {
  const fn = vi.fn(async () => response as Response);
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('parseDebounceMs', () => {
  it('defaults to 60s, accepts 0, and rejects junk and negatives', () => {
    expect(parseDebounceMs(undefined)).toBe(300_000);
    expect(parseDebounceMs('')).toBe(300_000);
    expect(parseDebounceMs('soon')).toBe(300_000);
    expect(parseDebounceMs('-5')).toBe(300_000);
    expect(parseDebounceMs('0')).toBe(0);
    expect(parseDebounceMs('90')).toBe(90_000);
  });

  it('clamps to a delay setTimeout can hold', () => {
    // Past 2^31-1 ms setTimeout coerces the delay to 1, which would turn an
    // over-large debounce into a fire on every single write.
    for (const raw of ['999999999', '86400', '2147484']) {
      expect(parseDebounceMs(raw)).toBeLessThanOrEqual(2_147_483_647);
    }
    expect(parseDebounceMs('999999999')).toBe(3_600_000);
  });
});

describe('routineTrigger', () => {
  it('is null when the endpoint is not configured', () => {
    // Neither variable is set in the test env, which is the deployment with no
    // API-triggered routine wired to it.
    expect(routineTrigger()).toBeNull();
  });
});

describe('RoutineTrigger', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('fires immediately with the token, beta header, and the reason as text', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, WORK);
    trigger.notify('a new question', log);
    await trigger.settled;

    expect(fetchFn).toHaveBeenCalledTimes(1);
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.example/fire');
    expect(init.method).toBe('POST');
    const headers = init.headers as Record<string, string>;
    expect(headers.authorization).toBe('Bearer tok');
    expect(headers['anthropic-beta']).toBe('experimental-cc-routine-2026-04-01');
    expect(headers['anthropic-version']).toBe('2023-06-01');
    expect(JSON.parse(init.body as string)).toEqual({ text: 'a new question' });
  });

  it('collapses a burst inside the window into one leading and one trailing fire', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, WORK);

    trigger.notify('first', log);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(1);

    // Three more writes during the cooldown queue exactly one trailing fire.
    trigger.notify('second', log);
    trigger.notify('third', log);
    trigger.notify('fourth', log);
    expect(fetchFn).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(60_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2);

    // The trailing fire opened its own window, so the next write waits again.
    trigger.notify('fifth', log);
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('fires a new topic at once, every time, inside the cooldown too, and replies still wait for it', async () => {
    // A run makes one topic at a time: a burst of topics debounced into one or two runs waited hours (Nov 3).
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, WORK);
    for (let i = 0; i < 5; i += 1) {
      trigger.fireNow('a new question', log);
      await trigger.settled;
    }
    expect(fetchFn).toHaveBeenCalledTimes(5);
    // A reply right after waits for the cooldown the last topic's fire opened: one trailing fire.
    trigger.notify('a reply', log);
    trigger.notify('another reply', log);
    expect(fetchFn).toHaveBeenCalledTimes(5);
    await vi.advanceTimersByTimeAsync(60_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(6);
  });

  it('fires again once the window has elapsed', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, WORK);

    trigger.notify('first', log);
    await trigger.settled;
    await vi.advanceTimersByTimeAsync(60_001);

    trigger.notify('second', log);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('fires on every event when the window is zero', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 0, WORK);
    trigger.notify('a', log);
    await trigger.settled;
    trigger.notify('b', log);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('does not double-fire when a write lands in the same tick as the trailing timer', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 100, WORK);

    trigger.notify('a', log);            // leading fire, opens the window
    await trigger.settled;

    await vi.advanceTimersByTimeAsync(10);
    // Queue our timer first so it runs ahead of the trailing one at the same
    // expiry — the boundary case where a write reopens the window.
    setTimeout(() => trigger.notify('c', log), 90);
    trigger.notify('b', log);            // schedules the trailing fire for the same instant

    await vi.advanceTimersByTimeAsync(200);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2); // leading + one at the boundary, never three
  });

  it('collapses a same-tick burst of writes into one fire', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, WORK);
    // lastFiredAt is set before fire() awaits, so concurrent writes in one tick
    // all see the open cooldown.
    for (let i = 0; i < 50; i++) trigger.notify(`q${i}`, log);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('never rejects, even when the logger itself throws', async () => {
    // Nothing awaits the fire promise, so a rejection would be an unhandled
    // rejection — which Node answers by killing the API process.
    stubFetch();
    const exploding = { info: () => { throw new Error('logger exploded'); }, warn: () => { throw new Error('logger exploded'); } };
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 0, WORK);

    trigger.notify('a', exploding);
    await expect(trigger.settled).resolves.toBeUndefined();
  });

  it('skips the trailing fire when the queues were already drained', async () => {
    const fetchFn = stubFetch();
    const info = vi.fn();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, IDLE);

    trigger.notify('first', { info, warn: () => {} });   // leading fire: never asks, the work is new
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(1);

    trigger.notify('second', { info, warn: () => {} });  // queued for the window's end
    await vi.advanceTimersByTimeAsync(60_000);
    await trigger.settled;

    // The run woken by the leading fire drained both queues, so no session is spent.
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(info).toHaveBeenCalledWith({ reason: 'second' }, 'routine fire skipped, queues already empty');
  });

  it('fires the trailing wake when work is still pending', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, WORK);
    trigger.notify('first', log);
    await trigger.settled;
    trigger.notify('second', log);
    await vi.advanceTimersByTimeAsync(60_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('fires anyway when the work check itself fails', async () => {
    const fetchFn = stubFetch();
    const warn = vi.fn();
    const broken = async () => { throw new Error('db down'); };
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, broken);

    trigger.notify('first', log);
    await trigger.settled;
    trigger.notify('second', { info: () => {}, warn });
    await vi.advanceTimersByTimeAsync(60_000);
    await trigger.settled;

    // Fail open: a missed question is worse than a wasted heartbeat.
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(warn.mock.calls[0][1]).toBe('routine work check failed, firing anyway');
  });

  it('swallows a rejected fire and keeps serving later ones', async () => {
    const fetchFn = stubFetch({ ok: false, status: 401 } as Partial<Response>);
    const warn = vi.fn();
    const trigger = new RoutineTrigger('https://api.example/fire', 'bad', 0, WORK);

    trigger.notify('a', log);
    await expect(trigger.settled).resolves.toBeUndefined();

    trigger.notify('b', { info: () => {}, warn });
    await trigger.settled;
    expect(warn).toHaveBeenCalledWith({ status: 401 }, 'routine fire rejected');
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('fires once more, 30s on, when the trigger was busy or failing, and never a third time', async () => {
    const fetchFn = stubFetch({ ok: false, status: 503 } as Partial<Response>);
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 0, WORK);

    trigger.fireNow('a', log);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(FIRE_RETRY_MS);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10 * FIRE_RETRY_MS);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('wakes a reply that came after a retry moved the cooldown (review on #151)', async () => {
    // A fire fails at 0s, a reply queues the trailing wake at 10s, the retry fires at 30s, another reply at 40s:
    // the retry served the first reply, and the second waits for the cooldown the retry opened.
    let status = 503;
    const fetchFn = vi.fn(async () => ({ ok: status < 400, status }) as Response);
    vi.stubGlobal('fetch', fetchFn);
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 300_000, WORK);
    trigger.notify('first', log);
    await trigger.settled;
    status = 200;
    await vi.advanceTimersByTimeAsync(10_000);
    trigger.notify('a reply', log);
    await vi.advanceTimersByTimeAsync(FIRE_RETRY_MS - 10_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2); // the failed fire, then the retry
    await vi.advanceTimersByTimeAsync(10_000);
    trigger.notify('a later reply', log);
    await vi.advanceTimersByTimeAsync(300_000 - 40_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2); // the first window's end: held for the retry's
    await vi.advanceTimersByTimeAsync(30_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(3); // the retry's window's end: the later reply's wake
  });

  it('wakes a reply that came after a new topic fired inside the cooldown', async () => {
    const fetchFn = stubFetch();
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 60_000, WORK);
    trigger.fireNow('a topic', log);
    await trigger.settled;
    await vi.advanceTimersByTimeAsync(10_000);
    trigger.notify('a reply', log);                 // queues the trailing wake for 60s
    await vi.advanceTimersByTimeAsync(20_000);
    trigger.fireNow('another topic', log);          // 30s: serves that reply
    await trigger.settled;
    await vi.advanceTimersByTimeAsync(10_000);
    trigger.notify('a later reply', log);           // 40s: waits on the timer queued at 10s
    await vi.advanceTimersByTimeAsync(20_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(2);       // 60s: nothing yet
    await vi.advanceTimersByTimeAsync(30_000);
    await trigger.settled;
    expect(fetchFn).toHaveBeenCalledTimes(3);       // 90s: the later reply's wake
  });

  it('fires once more after an unreachable endpoint, and not after a refused token', async () => {
    const thrown = vi.fn(async () => { throw new Error('ECONNREFUSED'); });
    vi.stubGlobal('fetch', thrown);
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 0, WORK);
    trigger.fireNow('a', log);
    await trigger.settled;
    await vi.advanceTimersByTimeAsync(FIRE_RETRY_MS);
    await trigger.settled;
    expect(thrown).toHaveBeenCalledTimes(2);

    const refused = stubFetch({ ok: false, status: 401 } as Partial<Response>);
    const other = new RoutineTrigger('https://api.example/fire', 'bad', 0, WORK);
    other.fireNow('b', log);
    await other.settled;
    await vi.advanceTimersByTimeAsync(10 * FIRE_RETRY_MS);
    await other.settled;
    expect(refused).toHaveBeenCalledTimes(1);
  });

  it('swallows a thrown fire — an unreachable endpoint never reaches the caller', async () => {
    const warn = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED'); }));
    const trigger = new RoutineTrigger('https://api.example/fire', 'tok', 0, WORK);

    trigger.notify('a', { info: () => {}, warn });
    await expect(trigger.settled).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0][1]).toBe('routine fire failed');
  });
});
