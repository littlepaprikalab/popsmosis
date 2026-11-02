import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { seed, signIn, apiUrl, sessionToken } from './helpers';

// Play counts (web/src/lib/plays.ts, api/src/routes/plays.ts): what the players send, and the owner's page.
const S = seed();
const ADMIN = { cookie: `popsmosis_session=${sessionToken(S.users.ada)}` };
const STEPS = ['started', 'reached25', 'reached50', 'reached75', 'finished'] as const;
const COUNTS = [...STEPS, 'shared', 'downloaded'] as const;
type Counts = Record<(typeof COUNTS)[number], number>;

/** The seeded droplet's counts for one video, as the owner's ranking has them. */
async function countsOf(request: APIRequestContext, video: 'song' | 'lesson' | 'hook'): Promise<Counts> {
  const res = await request.get(apiUrl(`/api/plays?video=${video}&limit=200`), { headers: ADMIN });
  expect(res.status()).toBe(200);
  const row = (await res.json()).data.find((d: { id: string }) => d.id === S.droplet);
  return Object.fromEntries(COUNTS.map((c) => [c, row?.plays[c] ?? 0])) as Counts;
}
const added = (after: Counts, before: Counts) => Object.fromEntries(COUNTS.map((c) => [c, after[c] - before[c]]));

/** Every count the page sends, in order, and whether any went with a cookie. */
function watchCounts(page: Page) {
  const sent: string[] = [];
  const headers: Promise<Record<string, string>>[] = [];
  page.on('request', (r) => {
    if (r.method() !== 'POST' || !/^\/api\/droplets\/[^/]+\/plays$/.test(new URL(r.url()).pathname)) return;
    const { video, event } = r.postDataJSON();
    sent.push(`${video} ${event}`);
    headers.push(r.allHeaders().catch(() => ({}))); // a request never answered has none by the end of its test
  });
  return { sent, withCookie: async () => (await Promise.all(headers)).filter((h) => 'cookie' in h).length };
}

/** Play to the end, muted and four times as fast: the counts go by how much of it played, not how long it took. */
const playThrough = (video: Locator) => video.evaluate(async (v: HTMLVideoElement) => {
  v.muted = true;
  v.playbackRate = 4;
  const ended = new Promise((r) => v.addEventListener('ended', r, { once: true }));
  await v.play();
  await ended;
});
/** Play until `seconds` in, then pause. */
const playTo = (video: Locator, seconds: number) => video.evaluate(async (v: HTMLVideoElement, to: number) => {
  v.muted = true;
  await v.play();
  await new Promise<void>((r) => { const on = () => { if (v.currentTime >= to) { v.removeEventListener('timeupdate', on); r(); } }; v.addEventListener('timeupdate', on); });
  v.pause();
}, seconds);
const settle = (page: Page) => page.waitForTimeout(500);

test.describe('play counts', () => {
  test('a play counts each step once a page view, in order, with no cookie, and a new page view counts again', async ({ page, request }) => {
    const before = await countsOf(request, 'song');
    const counts = watchCounts(page);
    await page.goto(`/droplets/${S.droplet}`);
    const song = page.getByLabel('song video');
    await playThrough(song);
    await expect.poll(() => counts.sent).toEqual(STEPS.map((s) => `song ${s}`));

    // Played again on the same page: nothing more.
    await playThrough(song);
    await settle(page);
    expect(counts.sent).toHaveLength(STEPS.length);
    expect(await counts.withCookie()).toBe(0);
    await expect.poll(async () => added(await countsOf(request, 'song'), before))
      .toEqual({ started: 1, reached25: 1, reached50: 1, reached75: 1, finished: 1, shared: 0, downloaded: 0 });

    // A new page view: this play stops just past a third.
    await page.reload();
    await playTo(page.getByLabel('song video'), 3);
    await expect.poll(() => counts.sent.slice(STEPS.length)).toEqual(['song started', 'song reached25']);
    await expect.poll(async () => added(await countsOf(request, 'song'), before))
      .toEqual({ started: 2, reached25: 2, reached50: 1, reached75: 1, finished: 1, shared: 0, downloaded: 0 });
  });

  test("a skip to the end counts as nothing played, on a card on the tab", async ({ page }) => {
    const counts = watchCounts(page);
    await page.goto('/droplets');
    const card = page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' }).first();
    await card.getByTestId('card-play').click();
    const video = card.getByLabel('ATP synthesis: song video');
    await video.evaluate(async (v: HTMLVideoElement) => {
      v.muted = true;
      await new Promise<void>((r) => { const on = () => { if (v.currentTime >= 0.5) { v.removeEventListener('timeupdate', on); r(); } }; v.addEventListener('timeupdate', on); });
      const ended = new Promise((r) => v.addEventListener('ended', r, { once: true }));
      v.currentTime = v.duration - 0.4;
      await ended;
    });
    await settle(page);
    expect(counts.sent).toEqual(['song started']);
  });

  test('a signed-in learner is counted with no account: their plays on /learn go without the cookie', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    const counts = watchCounts(page);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: 'how do cells actually make ATP' }).first();
    await playTo(card.getByLabel('lesson video'), 2.5);
    await expect.poll(() => counts.sent).toEqual(['lesson started', 'lesson reached25']);
    expect(await counts.withCookie()).toBe(0);
  });

  test("an admin's own plays are not counted", async ({ page, context }) => {
    await signIn(context, S.users.ada);
    const counts = watchCounts(page);
    await page.goto(`/droplets/${S.droplet}`);
    await expect(page.getByRole('link', { name: 'PLAYS', exact: true })).toBeVisible(); // signed in as the admin
    await playThrough(page.getByLabel('song video'));
    await settle(page);
    expect(counts.sent).toEqual([]);
  });

  test("the hook tile counts a share that went through and a download, each once a page view", async ({ page, request }) => {
    // A start of the clip, as the Clips feed would send it: the ranking read here lists only droplets with one.
    expect((await request.post(apiUrl(`/api/droplets/${S.droplet}/plays`), { data: { video: 'hook', event: 'started' } })).status()).toBe(204);
    const before = await countsOf(request, 'hook');
    // This browser's share sheet, closed without sharing.
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'share', { configurable: true, value: () => Promise.reject(new DOMException('closed', 'AbortError')) });
    });
    const counts = watchCounts(page);
    await page.goto(`/droplets/${S.droplet}`);
    const tile = page.getByTestId('hook-clip');
    await tile.getByTestId('hook-share').click();
    await settle(page);
    expect(counts.sent).toEqual([]);

    await page.evaluate(() => Object.defineProperty(navigator, 'share', {
      configurable: true,
      value: (data: ShareData) => { (window as unknown as { shared: ShareData }).shared = data; return Promise.resolve(); },
    }));
    await tile.getByTestId('hook-share').click();
    expect(await page.evaluate(() => (window as unknown as { shared: ShareData }).shared.text)).toBe('#popsmosis');
    await tile.getByTestId('hook-share').click();
    for (let i = 0; i < 2; i++) {
      const download = page.waitForEvent('download');
      await tile.getByTestId('hook-download').click();
      await (await download).cancel();
    }
    await expect.poll(() => counts.sent).toEqual(['hook shared', 'hook downloaded']);
    await expect.poll(async () => added(await countsOf(request, 'hook'), before))
      .toEqual({ started: 0, reached25: 0, reached50: 0, reached75: 0, finished: 0, shared: 1, downloaded: 1 });

    // Only the buttons: the clip plays in the Clips feed, not on the droplet's page.
    await expect(tile.locator('video')).toHaveCount(0);
  });

  test('counting never holds a video up: with the API not answering, it plays to the end', async ({ page }) => {
    const counts = watchCounts(page);
    await page.route('**/api/droplets/*/plays', () => undefined); // never answered
    await page.goto(`/droplets/${S.droplet}`);
    await playThrough(page.getByLabel('song video'));
    // Its steps wait on the start, which never came back: nothing more went.
    await settle(page);
    expect(counts.sent).toEqual(['song started']);
    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('a start over a limit stops that play there; an API that has no counts hears from a page load once', async ({ page }) => {
    const counts = watchCounts(page);
    let status = 429;
    await page.route('**/api/droplets/*/plays', (route) => route.fulfill({ status, contentType: 'application/json', body: '{"error":{"code":"x","message":"x"}}' }));
    await page.goto(`/droplets/${S.droplet}`);
    await playThrough(page.getByLabel('song video'));
    await settle(page);
    expect(counts.sent).toEqual(['song started']);

    // Another play is its own: its start goes. An API older than the counts answers it 404...
    status = 404;
    await playTo(page.getByLabel('lesson video'), 1);
    await expect.poll(() => counts.sent).toEqual(['song started', 'lesson started']);
    // ...and after that this page load sends none, for the rest of that play or on the next page.
    await playThrough(page.getByLabel('lesson video'));
    await page.getByRole('link', { name: '← All droplets' }).click();
    await expect(page).toHaveURL(/\/droplets$/);
    const card = page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' }).first();
    await card.getByTestId('card-play').click();
    await expect.poll(() => card.getByLabel('ATP synthesis: song video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.5);
    await settle(page);
    expect(counts.sent).toEqual(['song started', 'lesson started']);
  });

  test('the Clips feed counts the clip that plays, and with reduced motion only once it is pressed', async ({ page }) => {
    const counts = watchCounts(page);
    await page.goto('/droplets?view=clips');
    await expect.poll(() => counts.sent).toContain('hook started');

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.reload();
    const clip = page.getByTestId('clip').first().locator('video');
    await expect(clip).toBeVisible();
    await settle(page);
    expect(counts.sent.filter((c) => c === 'hook started')).toHaveLength(1);
    await clip.click();
    await expect.poll(() => counts.sent.filter((c) => c === 'hook started')).toHaveLength(2);
  });
});

test.describe("the owner's page", () => {
  test('shows where plays drop off, every droplet together, with the counts on hover, on focus and in a table', async ({ page, context, request }) => {
    // A lesson play stopped past halfway, counted as the page would send it.
    for (const event of STEPS.slice(0, 3)) {
      expect((await request.post(apiUrl(`/api/droplets/${S.droplet}/plays`), { data: { video: 'lesson', event } })).status()).toBe(204);
    }
    const res = await request.get(apiUrl('/api/plays?totals=1'), { headers: ADMIN });
    const { totals } = await res.json();
    expect(totals.lesson.started).toBeGreaterThan(0);

    await signIn(context, S.users.ada);
    const asked: string[] = [];
    page.on('request', (r) => { if (new URL(r.url()).pathname === '/api/plays') asked.push(new URL(r.url()).searchParams.get('totals') ?? ''); });
    await page.goto('/admin/plays');
    const chart = page.getByTestId('drop-off-chart');
    await expect(chart.getByRole('list', { name: 'Videos' })).toContainText(`Lesson${totals.lesson.started} ${totals.lesson.started === 1 ? 'play' : 'plays'}`);
    const points = chart.getByTestId('drop-off-point');
    await expect(points).toHaveCount(5);
    const tip = chart.getByTestId('drop-off-tooltip');

    // Hovering the 50% point lists every video there.
    await points.nth(2).hover();
    await expect(tip).toContainText('50% played');
    await expect(tip).toContainText(`Lesson · ${totals.lesson.reached50} of ${totals.lesson.started}`);
    await page.mouse.move(1, 1);
    await expect(tip).toHaveCount(0);

    // So does the keyboard, and each point says it to a screen reader.
    await points.nth(4).focus();
    await expect(tip).toContainText('Finished (95% played)');
    await expect(points.nth(4)).toHaveAttribute('aria-label', new RegExp(`Lesson \\d+%, ${totals.lesson.finished} of ${totals.lesson.started}`));

    // And the numbers are in a table, for anyone who cannot see the chart or hover it.
    await chart.getByText('The numbers').click();
    const lesson = chart.getByTestId('drop-off-table').getByRole('row', { name: /^Lesson/ });
    await expect(lesson.getByRole('cell').nth(0)).toHaveText(String(totals.lesson.started));
    await expect(lesson.getByRole('cell').nth(2)).toHaveText(new RegExp(`^${totals.lesson.reached50} · `));

    // Switching the ranking's video asks for its page only: the totals came once, and the chart stays.
    await page.getByTestId('plays-video').getByRole('button', { name: 'Hook clip' }).click();
    await expect.poll(() => asked.length).toBe(2);
    expect(asked).toEqual(['1', '']);
    await expect(points).toHaveCount(5);
  });

  test('on a phone, a tapped point keeps its counts showing until another is tapped', async ({ browser, request }) => {
    expect((await request.post(apiUrl(`/api/droplets/${S.droplet}/plays`), { data: { video: 'song', event: 'started' } })).status()).toBe(204);
    const context = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
    await signIn(context, S.users.ada);
    const page = await context.newPage();
    await page.goto('/admin/plays');
    const chart = page.getByTestId('drop-off-chart');
    const points = chart.getByTestId('drop-off-point');
    const tip = chart.getByTestId('drop-off-tooltip');
    await points.nth(1).tap();
    await expect(tip).toContainText('25% played');
    await page.waitForTimeout(500);
    await expect(tip).toContainText('25% played'); // still there once the finger has lifted
    await points.nth(3).tap();
    await expect(tip).toContainText('75% played');
    // The chart fits the phone: nothing runs off the side of the page.
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await context.close();
  });

  test('ranks the droplets by how many plays finished, per video, for admins only', async ({ page, context, request }) => {
    // A play of the lesson to the end, and a start of the hook clip, counted as the page would send them.
    for (const [video, event] of [...STEPS.map((e) => ['lesson', e]), ['hook', 'started']]) {
      const res = await request.post(apiUrl(`/api/droplets/${S.droplet}/plays`), { data: { video, event } });
      expect(res.status()).toBe(204);
    }
    const lesson = await countsOf(request, 'lesson');

    await signIn(context, S.users.ben);
    await page.goto('/admin/plays');
    await expect(page.getByText('Admin access required.')).toBeVisible();

    await signIn(context, S.users.ada);
    await page.goto('/learn');
    await page.getByRole('navigation').getByRole('link', { name: 'PLAYS', exact: true }).click();
    await expect(page).toHaveURL(/\/admin\/plays$/);
    await page.getByTestId('plays-min').selectOption('1');
    await page.getByTestId('plays-video').getByRole('button', { name: 'Lesson' }).click();
    const row = page.getByTestId('plays-row').filter({ hasText: 'ATP synthesis' });
    await expect(row).toBeVisible();
    await expect(row.getByRole('cell').nth(2)).toHaveText(String(lesson.started));
    await expect(row.getByRole('cell').nth(6)).toContainText(`${Math.round((lesson.finished / lesson.started) * 100)}%`);
    await row.getByRole('link', { name: 'ATP synthesis' }).click();
    await expect(page).toHaveURL(new RegExp(`/droplets/${S.droplet}$`));

    // The hook clip's ranking has its shares and downloads too.
    await page.goBack();
    await page.getByTestId('plays-min').selectOption('1');
    await page.getByTestId('plays-video').getByRole('button', { name: 'Hook clip' }).click();
    await expect(page.getByTestId('plays-table').getByRole('columnheader', { name: 'Downloads' })).toBeVisible();
  });
});
