import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { seed, signIn, agent, apiUrl, sessionToken } from './helpers';

const S = seed();
// The seed's sample clips, as the absolute URLs the video endpoint takes.
const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000';
const SONG = `${BASE}/samples/song.mp4`;
const LESSON = `${BASE}/samples/lesson.mp4`;
// How far a video has played, and whether it is paused.
const playedTo = (video: Locator) => video.evaluate((v: HTMLVideoElement) => v.currentTime);
const paused = (video: Locator) => video.evaluate((v: HTMLVideoElement) => v.paused);

test.describe('the Droplets tab, signed out', () => {
  test('lists the finished droplets and plays one, with no sign-in', async ({ page }) => {
    await page.goto('/learn');
    await page.getByRole('navigation').getByRole('link', { name: 'DROPLETS', exact: true }).click();
    await expect(page).toHaveURL(/\/droplets$/);
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();

    const card = page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' }).first();
    await expect(card).toBeVisible();
    await expect(card.getByText('AP Biology', { exact: true })).toBeVisible();
    // Never who asked, or their words: the title is the bundle's.
    await expect(page.getByText('Ben')).toHaveCount(0);
    await expect(page.getByText('how do cells actually make ATP')).toHaveCount(0);

    // The song plays on the card itself: watching it takes no trip to the droplet's page.
    const onCard = card.getByLabel('ATP synthesis: song video');
    const play = card.getByRole('button', { name: 'Play the song video: ATP synthesis' });
    // The still is a lazy image until it plays, so a page of cards loads only the ones in view.
    await expect(play.locator('img')).toHaveAttribute('src', '/samples/song.jpg');
    await expect(play.locator('img')).toHaveAttribute('loading', 'lazy');
    await play.click();
    await expect.poll(() => playedTo(onCard)).toBeGreaterThan(0);
    await expect(page).toHaveURL(/\/droplets$/);
    // Its own controls take over, to pause it or seek, and the keyboard with them.
    await expect(play).toHaveCount(0);
    await expect(onCard).toHaveAttribute('controls', '');
    await expect(onCard).toBeFocused();
    await expect(onCard).toHaveAttribute('poster', '/samples/song.jpg');

    // And the card links to the page, with both videos, the lesson and the lyrics.
    await card.getByRole('link', { name: 'Full page: ATP synthesis' }).click();
    await expect(page).toHaveURL(new RegExp(`/droplets/${S.droplet}$`));
    await expect(page.getByRole('heading', { name: 'ATP synthesis' })).toBeVisible();
    await expect(page.locator('.md strong', { hasText: 'gradient' })).toBeVisible();
    await expect(page.getByTestId('lyrics').getByText('Spin the rotor, make the ATP')).toBeVisible();
    // The follow-up thread stays on /learn.
    await expect(page.getByText('water wheel')).toHaveCount(0);
    // Labelled as made by AI, with the license it is shared under.
    await expect(page.getByTestId('ai-notice')).toContainText('Made by AI');
    await expect(page.getByRole('link', { name: 'CC BY-NC-SA 4.0' })).toHaveAttribute('href', 'https://creativecommons.org/licenses/by-nc-sa/4.0/');

    const song = page.getByLabel('song video');
    await expect(song).toHaveAttribute('poster', '/samples/song.jpg');
    await expect(page.getByLabel('lesson video')).toBeVisible();
    // It plays: the CSP lets media through and the file is a browser-playable MP4.
    const played = await song.evaluate(async (v: HTMLVideoElement) => {
      v.muted = true;
      await v.play();
      await new Promise((r) => setTimeout(r, 700));
      return v.currentTime;
    });
    expect(played).toBeGreaterThan(0);
  });

  test('one card plays at a time: starting another pauses the first', async ({ page }) => {
    await page.goto('/droplets');
    const cards = page.getByTestId('droplet-card');
    const [first, second] = [cards.nth(0).locator('video'), cards.nth(1).locator('video')];
    await expect(second).toBeVisible();
    await cards.nth(0).getByTestId('card-play').click();
    await expect.poll(() => playedTo(first)).toBeGreaterThan(0);
    await cards.nth(1).getByTestId('card-play').click();
    await expect.poll(() => playedTo(second)).toBeGreaterThan(0);
    expect(await paused(first)).toBe(true);
    expect(await paused(second)).toBe(false);
  });

  test('tags the droplet, and finds droplets by a tag', async ({ page }) => {
    await page.goto(`/droplets/${S.droplet}`);
    // Both videos' tags, once each, under the pair: the song's #kpop, the lesson's #explained, and the #biology they share.
    const tags = page.getByRole('list', { name: 'Tags' });
    await expect(tags).toHaveCount(1);
    await expect(tags.getByRole('link', { name: '#kpop', exact: true })).toBeVisible();
    await expect(tags.getByRole('link', { name: '#explained', exact: true })).toBeVisible();
    await expect(tags.getByRole('link', { name: '#biology', exact: true })).toHaveCount(1);

    // A tag opens the tab searched by it.
    await tags.getByRole('link', { name: '#kpop', exact: true }).click();
    await expect(page).toHaveURL(/\/droplets\?tag=kpop$/);
    await expect(page.getByTestId('tag-search')).toContainText('Tagged #kpop');
    const cards = page.getByTestId('droplet-card');
    await expect(cards).toHaveCount(1);
    await expect(cards.first()).toContainText('ATP synthesis');

    // Typed in the box, written any way, a tag finds every droplet that carries it.
    const box = page.getByRole('searchbox', { name: 'Search droplets by tag' });
    await expect(box).toHaveValue('#kpop');
    await box.fill('#Bio logy');
    await box.press('Enter');
    await expect(page).toHaveURL(/\/droplets\?tag=biology$/);
    await expect(cards.filter({ hasText: 'ATP synthesis' })).toHaveCount(1);
    // Cal's, which a run before this one may have left on the tab once it deleted Cal's account.
    await expect(cards.filter({ hasText: 'Osmosis' }).first()).toBeVisible();

    // A tag no droplet has, and the way back to all of them.
    await box.fill('#nosuchtag');
    await page.getByRole('button', { name: 'Search' }).click();
    await expect(page.getByText('No droplets are tagged #nosuchtag yet.')).toBeVisible();
    await page.getByRole('link', { name: 'Show all droplets' }).click();
    await expect(page).toHaveURL(/\/droplets$/);
    await expect(box).toHaveValue('');
    // A card shows its first few tags, each a search of its own.
    const card = cards.filter({ hasText: 'ATP synthesis' }).first();
    await expect(card.getByRole('link', { name: '#atpsynthesis', exact: true })).toBeVisible();
    await expect(card.getByTestId('tags').getByRole('link')).toHaveCount(4);
  });

  test('a search that fails shows no cards from the last one, and can be tried again', async ({ page }) => {
    await page.goto('/droplets');
    const cards = page.getByTestId('droplet-card');
    await expect(cards.filter({ hasText: 'ATP synthesis' })).toHaveCount(1);
    const broken = (url: URL) => url.pathname.endsWith('/api/droplets') && url.searchParams.get('tag') === 'broken';
    await page.route(broken, (route) => route.fulfill({
      status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'INTERNAL', message: 'Something broke' } }),
    }));
    const box = page.getByRole('searchbox', { name: 'Search droplets by tag' });
    await box.fill('#broken');
    await box.press('Enter');
    await expect(page.getByTestId('droplets-failed')).toContainText('The droplets tagged #broken did not load.');
    await expect(cards).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Load more' })).toHaveCount(0);

    await page.unroute(broken);
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByText('No droplets are tagged #broken yet.')).toBeVisible();
  });

  test('a droplet not on the tab is a 404 page, and the API needs no cookie', async ({ page, request }) => {
    await page.goto('/droplets/not-a-droplet');
    await expect(page.getByText('This droplet is not on the tab')).toBeVisible();
    const list = await request.get(apiUrl('/api/droplets'));
    expect(list.status()).toBe(200);
    expect(JSON.stringify(await list.json())).not.toMatch(/Ben|ben@example|how do cells/);
  });
});

// A studio's bill as it sends one (studio/ledger.py), small enough to read in a test.
const studioBill = (steps: [string, number, number][]) => ({
  steps: steps.map(([step, seconds, costUsd]) => ({
    step, seconds, tries: 1,
    models: costUsd ? [{ model: step === 'art' ? 'claude-haiku-5-5' : 'claude-opus-5-5', calls: 1, costUsd, credits: null }] : [],
    tools: [],
  })),
  seconds: steps.reduce((sum, [, seconds]) => sum + seconds, 0),
});

test.describe('videos on /learn', () => {
  test('a droplet being made shows its bill filling in, step by step, and its lesson as soon as it is checked', async ({ page, context, request }) => {
    test.setTimeout(150_000); // four rounds of the card's own 15-second poll
    const text = `E2E ${Date.now()}: the light reactions, step by step`;
    const asked = await request.post(apiUrl('/api/questions'), {
      headers: { cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: { content: text },
    });
    expect(asked.status()).toBe(201);
    const questionId = (await asked.json()).id;
    await signIn(context, S.users.ada);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    const bill = card.getByTestId('breakdown');
    const step = (label: string) => bill.locator('table').getByTestId('breakdown-step').filter({ hasText: label });

    // Nobody has picked it up: the whole droplet to make, folded to one line.
    await expect(card.getByTestId('waiting')).toBeVisible();
    await expect(bill.getByTestId('breakdown-progress')).toHaveText('11 steps to go');
    await expect(bill.locator('table')).toBeHidden();

    // The studio claims it and starts the lesson: the bill opens by itself and fills in on the card's poll.
    const bot = agent(request, S.POPSMOSIS_API_KEY);
    expect((await bot.claim(questionId)).status()).toBe(200);
    const lessonBill = studioBill([['request', 0.4, 0], ['bundle', 196, 0.53], ['audit', 40, 0.06]]);
    expect((await bot.progress(questionId, { running: [{ step: 'factcheck', seconds: 20 }], breakdown: lessonBill, videos: true })).status()).toBe(204);
    // With the time so far: the bill's 236.4 seconds, and the time since the report.
    await expect(bill.getByTestId('breakdown-progress')).toHaveText(/^Making it · 3 of 11 steps done · [34]\.\d min · \$0\.59 so far$/, { timeout: 25_000 });
    await expect(step('Read the request')).toHaveAttribute('data-status', 'done');
    await expect(step('Write the lesson')).toHaveAttribute('data-status', 'done');
    await expect(step('Write the lesson')).toContainText('$0.53');
    await expect(step('Audit the facts')).toHaveAttribute('data-status', 'done');
    await expect(step('Fact-check')).toHaveAttribute('data-status', 'running');
    await expect(step('Draw the diagrams')).toHaveAttribute('data-status', 'waiting');
    // The time so far counts up while the fact-check runs, and the bill's last line has it too.
    const elapsed = bill.getByTestId('breakdown-elapsed');
    const before = (await elapsed.textContent())!;
    await expect(elapsed).not.toHaveText(before, { timeout: 10_000 }); // a tenth of a minute is 6 seconds
    await expect(bill.locator('tfoot')).toContainText(/So far\s*[34]\.\d min/);

    // The lesson is posted and the videos started: the text shows at once, with the videos rendering above it
    // and the bill under it filling in as they are made.
    const lesson = 'The light reactions **pump protons** across the thylakoid membrane.';
    const posted = await bot.answer(questionId, lesson, {
      bundle: { course: 'AP Biology', topic: 'the light reactions' },
      factcheck: { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] },
      breakdown: studioBill([['request', 0.4, 0], ['bundle', 196, 0.53], ['audit', 40, 0.06], ['factcheck', 80, 0.18]]),
    });
    expect(posted.status()).toBe(201);
    const answerId = (await posted.json()).id;
    expect((await bot.videoClaim(answerId)).status()).toBe(200);
    expect((await bot.videoProgress(answerId, {
      attempt: 1, running: [{ step: 'art', seconds: 40 }, { step: 'audio', seconds: 40 }], breakdown: studioBill([]),
    })).status()).toBe(204);
    await expect(card.locator('.md strong', { hasText: 'pump protons' })).toBeVisible({ timeout: 25_000 });
    await expect(card.getByTestId('videos-rendering')).toBeVisible();
    await expect(card.getByTestId('awaiting-videos')).toHaveCount(0);
    await expect(card.getByText('READY', { exact: true })).toBeVisible();
    await expect(card.getByPlaceholder(/Ask a follow-up/)).toBeVisible();
    // The lesson's whole 316.4 seconds and the art's and audio's 40 so far, and the time since the videos' report.
    await expect(bill.getByTestId('breakdown-progress')).toHaveText(/^Making it · 4 of 11 steps done · [56]\.\d min · \$0\.77 so far$/);
    await bill.locator('summary').click();
    await expect(step('Fact-check')).toHaveAttribute('data-status', 'done');
    await expect(step('Draw the diagrams')).toHaveAttribute('data-status', 'running');
    await expect(step('Song and voice')).toHaveAttribute('data-status', 'running');

    // Folded, the card reads as the lesson.
    await card.getByRole('button', { name: 'Collapse card' }).click();
    await expect(card.getByTestId('answer-preview')).toContainText('pump protons');
    await card.getByRole('button', { name: 'Expand card' }).click();

    // The art is done and the diagrams' labels found while the audio goes on.
    expect((await bot.videoProgress(answerId, {
      attempt: 1, running: [{ step: 'audio', seconds: 100 }], breakdown: studioBill([['art', 76, 0.008], ['locate', 0.2, 0]]),
    })).status()).toBe(204);
    await expect(step('Draw the diagrams')).toHaveAttribute('data-status', 'done', { timeout: 25_000 });
    await expect(step('Find the labels')).toHaveAttribute('data-status', 'done');
    await expect(step('Song and voice')).toHaveAttribute('data-status', 'running');

    // Done: the videos appear above the lesson, and the bill shows its totals.
    expect((await bot.video(answerId, { status: 'READY', attempt: 1, songVideoUrl: SONG, lessonVideoUrl: LESSON })).status()).toBe(200);
    await expect(card.getByTestId('droplet-players')).toBeVisible({ timeout: 25_000 });
    await expect(card.locator('.md strong', { hasText: 'pump protons' })).toBeVisible();
    await expect(card.getByTestId('videos-rendering')).toHaveCount(0);
    await expect(card.getByText('READY', { exact: true })).toBeVisible();
    const playersBox = await card.getByTestId('droplet-players').boundingBox();
    const lessonBox = await card.locator('.md').boundingBox();
    expect(playersBox!.y).toBeLessThan(lessonBox!.y); // the videos come first
    await expect(bill.getByTestId('breakdown-total')).toBeVisible();
  });

  test('a lesson shows while its videos render, then the players appear above it without a reload; an admin approves it, and can hide it from the tab', async ({ page, context, request }) => {
    // Review on (it is off by default): each droplet waits for an admin before it lists.
    await request.put(apiUrl('/api/studio/settings'), {
      headers: { cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: { review: { required: true } },
    });
    // Ada asks, the teacher answers and starts the render, all through the API.
    const text = `E2E ${Date.now()}: the Calvin cycle`;
    const asked = await request.post(apiUrl('/api/questions'), {
      headers: { cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: { content: text },
    });
    expect(asked.status()).toBe(201);
    const bot = agent(request, S.POPSMOSIS_API_KEY);
    const questionId = (await asked.json()).id;
    expect((await bot.claim(questionId)).status()).toBe(200);
    // The studio says, as it writes the lesson, that videos follow.
    expect((await bot.progress(questionId, { running: [{ step: 'factcheck', seconds: 0 }], videos: true })).status()).toBe(204);
    const answer = await bot.answer(questionId, 'The Calvin cycle **fixes carbon** with the ATP the light reactions made.', {
      bundle: { course: 'AP Biology', topic: 'the Calvin cycle' }, // the videos are made from it
      factcheck: { status: 'passed', reason: '', rounds: 1, lines: [], corrections: [] },
    });
    expect(answer.status()).toBe(201);
    const answerId = (await answer.json()).id;
    expect((await bot.video(answerId, { status: 'RENDERING' })).status()).toBe(200);

    await signIn(context, S.users.ada);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    // While they render, the lesson is there to read, and the card says the videos are coming.
    await expect(card.locator('.md strong', { hasText: 'fixes carbon' })).toBeVisible();
    await expect(card.getByTestId('videos-rendering')).toBeVisible();
    await expect(card.getByTestId('awaiting-videos')).toHaveCount(0);

    // Done: the card puts the players above the lesson on its own poll.
    expect((await bot.video(answerId, { status: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON })).status()).toBe(200);
    await expect(card.getByTestId('droplet-players')).toBeVisible({ timeout: 25_000 });
    await expect(card.getByTestId('videos-rendering')).toHaveCount(0);
    await expect(card.locator('.md strong', { hasText: 'fixes carbon' })).toBeVisible();
    await expect(card.getByLabel('song video')).toHaveAttribute('src', SONG);

    // It waits off the public tab until Ada (an admin) approves it; then it is on it, until she hides it.
    const onTab = async () => (await (await request.get(apiUrl('/api/droplets'))).json()).data.some((d: { id: string }) => d.id === answerId);
    await expect(card.getByTestId('awaiting-review')).toContainText('Waiting for review');
    expect(await onTab()).toBe(false);
    expect((await request.get(apiUrl(`/api/droplets/${answerId}`))).status()).toBe(404);
    await card.getByTestId('approve-droplet').click();
    await expect(card.getByRole('link', { name: 'On the public Droplets tab' })).toBeVisible();
    expect(await onTab()).toBe(true);
    await card.getByTestId('toggle-hidden').click();
    await expect(card.getByText('Hidden from the Droplets tab.')).toBeVisible();
    expect(await onTab()).toBe(false);
    await card.getByTestId('toggle-hidden').click();
    await expect(card.getByRole('link', { name: 'On the public Droplets tab' })).toBeVisible();
    expect(await onTab()).toBe(true);
    await request.put(apiUrl('/api/studio/settings'), { // back to the default
      headers: { cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: { review: { required: false } },
    });
  });

  test("a finished lesson shows both videos' tags under them, each a search of the tab", async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: 'how do cells actually make ATP' }).first();
    const tags = card.getByRole('list', { name: 'Tags' });
    await expect(tags.getByRole('link', { name: '#kpop', exact: true })).toBeVisible();
    await expect(tags.getByRole('link', { name: '#explained', exact: true })).toBeVisible();
    await tags.getByRole('link', { name: '#explained', exact: true }).click();
    await expect(page).toHaveURL(/\/droplets\?tag=explained$/);
    await expect(page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' })).toHaveCount(1);
  });

  test('a finished droplet carries its bill of materials: each step, its models and tools, their makers, time and cost', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: 'how do cells actually make ATP' }).first();
    const bill = card.getByTestId('breakdown');
    await bill.scrollIntoViewIfNeeded();
    // Folded to its totals: the lesson's 4.6 min and the videos' 6.3, and what was spent.
    await expect(bill.getByTestId('breakdown-total')).toHaveText('10.9 min · $0.80 + 3,214 cr');
    await expect(bill.locator('table')).toBeHidden();

    await bill.locator('summary').click();
    await expect(bill.getByTestId('breakdown-step')).toHaveCount(10);
    const audio = bill.getByTestId('breakdown-step').filter({ hasText: 'Song and voice' });
    await expect(audio).toContainText('lyria-3.5');
    await expect(audio).toContainText('$0.08');
    const table = bill.locator('table'); // the phone's copy of the bill is hidden at this width
    await expect(table.getByText('eleven_multilingual_v2')).toBeVisible();
    await expect(table.getByText('3,214 cr', { exact: true })).toBeVisible();
    await expect(audio.getByTestId('maker')).toHaveText('Google'); // Lyria, the first of the step's models

    // Each step's tools follow its models, with the versions the studio ran, and every line names its maker.
    const line = (text: string) => table.locator('tr').filter({ hasText: text });
    for (const [name, maker] of [['claude-haiku-5-5', 'Anthropic'], ['eleven_multilingual_v2', 'ElevenLabs'],
      ['htdemucs', 'Meta'], ['mms-fa', 'Meta'], ['demucs 4.1.0', 'Meta'], ['torchaudio 2.11.0+cpu', 'Meta'],
      ['torch 2.11.0+cpu', 'Meta']]) {
      await expect(line(name).first().getByTestId('maker')).toHaveText(maker);
    }
    const render = bill.getByTestId('breakdown-step').filter({ hasText: 'Render' });
    await expect(render).toContainText('skia-python 144.1');
    await expect(render.getByTestId('maker')).toHaveText('Google');
    await expect(line('ffmpeg 6.1.1-3ubuntu5')).toHaveCount(3); // the audio, the timing and the render each ran it
    await expect(table.getByTestId('maker')).toHaveCount(15); // 7 model lines and 8 tool lines
    await expect(bill.getByText('Versions: as installed on the studio’s machine that made the videos.')).toBeVisible();

    // The public tab shows the droplet, never its bill.
    await page.goto(`/droplets/${S.droplet}`);
    await expect(page.getByRole('heading', { name: 'ATP synthesis' })).toBeVisible();
    await expect(page.getByTestId('breakdown')).toHaveCount(0);
  });

  test('a failed render says why under the lesson', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const failed = page.getByTestId('videos-failed').first();
    await failed.scrollIntoViewIfNeeded();
    await expect(failed).toContainText('render: ffmpeg exited 1 on the song.');
  });
});

test.describe('likes and comments on the Droplets tab', () => {
  const cookieOf = (userId: string) => ({ cookie: `popsmosis_session=${sessionToken(userId)}` });
  const asWriter = (userId: string) => ({ ...cookieOf(userId), 'x-requested-with': 'XMLHttpRequest' });
  // The droplet as the API tells this person, so a test checks what landed, not only what the page shows.
  const seenBy = async (request: APIRequestContext, userId: string) =>
    (await request.get(apiUrl(`/api/droplets/${S.droplet}`), { headers: cookieOf(userId) })).json() as Promise<{ liked: boolean; likeCount: number }>;
  // Start from no like by this person, whatever an earlier try of the test left.
  const unlikeFirst = async (request: APIRequestContext, userId: string) => {
    expect((await request.delete(apiUrl(`/api/droplets/${S.droplet}/like`), { headers: asWriter(userId) })).status()).toBe(200);
    return (await seenBy(request, userId)).likeCount;
  };
  const atpCard = (page: Page) => page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' }).first();

  test('signed out: the counts and comments show, and adding to them asks for a sign-in', async ({ page }) => {
    await page.goto('/droplets');
    const card = atpCard(page);
    await expect(card.getByTestId('like-signin')).toHaveText(/\b1\b/); // Ada's
    await expect(card.getByTestId('like-button')).toHaveCount(0);
    await expect(card.getByTestId('card-comments')).toHaveText(/\b1\b/);
    // The card shows its newest comment, all of them there are.
    const preview = card.getByTestId('card-comment-preview');
    await expect(preview.getByTestId('comment')).toHaveCount(1);
    await expect(preview.getByTestId('comment')).toContainText('Ada The chorus is the only way');
    await expect(preview.getByRole('button', { name: /View all/ })).toHaveCount(0);

    // The count opens them on the card, without leaving the list, and asks for a sign-in to add one.
    await card.getByTestId('card-comments').click();
    await expect(card.getByTestId('card-comments')).toHaveAttribute('aria-expanded', 'true');
    const panel = card.getByTestId('card-comments-panel');
    await expect(panel.getByTestId('comment')).toHaveCount(1);
    await expect(panel.getByText('Ada', { exact: true })).toBeVisible();
    await expect(panel.getByTestId('comment-signin')).toHaveAttribute('href', `/auth/callback?returnTo=${encodeURIComponent('/droplets')}`);
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toHaveCount(0);
    await expect(page).toHaveURL(/\/droplets$/);
    await card.getByTestId('card-comments').click();
    await expect(panel).toHaveCount(0);
    await expect(preview.getByTestId('comment')).toHaveCount(1);

    // A link to a droplet's comments lands on them, on its page.
    await page.goto(`/droplets/${S.droplet}#comments`);
    const comments = page.getByTestId('droplet-comments');
    await expect(comments).toBeInViewport();
    await expect(comments.getByTestId('comment')).toHaveCount(1);
    await expect(comments.getByText('The chorus is the only way')).toBeVisible();
    await expect(comments.getByText('Ada', { exact: true })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toHaveCount(0);
    await expect(page.getByTestId('comment-signin')).toHaveAttribute('href', `/auth/callback?returnTo=${encodeURIComponent(`/droplets/${S.droplet}`)}`);
    // Ben asked for this droplet; nothing here says so.
    await expect(page.getByText('Ben')).toHaveCount(0);
  });

  test('signed in: a thumbs-up lands, holds across a reload and across pages, and comes back off', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    await page.goto('/droplets');
    const like = atpCard(page).getByTestId('like-button');
    await expect(like).toHaveAttribute('aria-pressed', 'false');
    await expect(like.getByTestId('like-count')).toHaveText(String(before));

    await like.click();
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await expect(like.getByTestId('like-count')).toHaveText(String(before + 1));
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: true, likeCount: before + 1 }));

    await page.reload();
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await atpCard(page).getByRole('heading', { name: 'ATP synthesis' }).click();
    await expect(page).toHaveURL(new RegExp(`/droplets/${S.droplet}$`));
    const onPage = page.getByTestId('droplet-engagement').getByTestId('like-button');
    await expect(onPage).toHaveAttribute('aria-pressed', 'true');
    await expect(onPage.getByTestId('like-count')).toHaveText(String(before + 1));

    await onPage.click();
    await expect(onPage).toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));

    // Back on the list, the card says what the droplet page just did, not what the list said before.
    await page.goBack();
    await expect(page).toHaveURL(/\/droplets$/);
    await expect(like).toHaveAttribute('aria-pressed', 'false');
    await expect(like.getByTestId('like-count')).toHaveText(String(before));
  });

  test('a card shows its two newest comments and opens to all of them in place, where one can be added', async ({ page, context, request }) => {
    const stamp = `E2E ${Date.now()}`;
    try {
      for (const words of ['the proton part', 'the rotor part']) {
        const res = await request.post(apiUrl(`/api/droplets/${S.droplet}/comments`), { headers: asWriter(S.users.ben), data: { content: `${stamp}: ${words}` } });
        expect(res.status()).toBe(201);
      }
      const total: number = (await (await request.get(apiUrl(`/api/droplets/${S.droplet}/comments`))).json()).commentCount;

      await signIn(context, S.users.ben);
      const calls: string[] = [];
      page.on('request', (r) => { const url = new URL(r.url()); if (url.pathname.startsWith('/api/droplets')) calls.push(`${r.method()} ${url.pathname}`); });
      await page.goto('/droplets');
      const card = atpCard(page);
      const preview = card.getByTestId('card-comment-preview');
      await expect(preview.getByTestId('comment')).toHaveCount(2);
      await expect(preview.getByTestId('comment').nth(0)).toContainText(`${stamp}: the rotor part`);
      await expect(preview.getByTestId('comment').nth(1)).toContainText(`${stamp}: the proton part`);
      expect(calls).toEqual(['GET /api/droplets']); // the list brought them

      // All of them, in place: the two it showed, and the older ones after them.
      await preview.getByRole('button', { name: `View all ${total} comments` }).click();
      const panel = card.getByTestId('card-comments-panel');
      await expect(panel).toBeFocused();
      await expect(panel.getByTestId('comment')).toHaveCount(total);
      await expect(panel.getByText('The chorus is the only way', { exact: false })).toBeVisible();
      expect(calls).toEqual(['GET /api/droplets', `GET /api/droplets/${S.droplet}/comments`]);
      await expect(page).toHaveURL(/\/droplets$/);

      // Added on the card, it is the newest there, folded or not.
      const mine = `${stamp}: written on the card`;
      await panel.getByRole('textbox', { name: 'Add a comment' }).fill(mine);
      await panel.getByRole('button', { name: 'Comment', exact: true }).click();
      await expect(panel.getByTestId('comment').first()).toContainText(mine);
      await expect(card.getByTestId('card-comments')).toHaveText(new RegExp(`\\b${total + 1}\\b`));
      await panel.getByRole('button', { name: 'Hide comments' }).click();
      await expect(panel).toHaveCount(0);
      await expect(preview.getByTestId('comment').first()).toContainText(mine);
      await expect(preview.getByRole('button', { name: `View all ${total + 1} comments` })).toBeVisible();
    } finally {
      // Ben's from this run, whatever step it stopped at: the other tests count Ada's alone.
      const listed = await (await request.get(apiUrl(`/api/droplets/${S.droplet}/comments?limit=50`), { headers: cookieOf(S.users.ben) })).json();
      for (const c of listed.data as { id: string; content: string; mine: boolean }[]) {
        if (c.mine && c.content.startsWith(stamp)) await request.delete(apiUrl(`/api/droplets/${S.droplet}/comments/${c.id}`), { headers: asWriter(S.users.ben) });
      }
    }
  });

  test('from an API that sends no comments with the cards, a card reads its own when it opens', async ({ page }) => {
    // What a new page meets while the API it talks to is still the one before this change, or
    // when the API could not read the cards' comments: the cards come without them.
    await page.route((url) => url.pathname === '/api/droplets', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      for (const d of body.data) delete d.comments;
      return route.fulfill({ response: res, json: body });
    });
    const calls: string[] = [];
    page.on('request', (r) => { const url = new URL(r.url()); if (url.pathname.startsWith('/api/droplets')) calls.push(`${r.method()} ${url.pathname}`); });
    await page.goto('/droplets');
    const card = atpCard(page);
    await expect(card.getByTestId('card-comments')).toHaveText(/\b1\b/);
    await expect(card.getByTestId('card-comment-preview')).toHaveCount(0);

    await card.getByTestId('card-comments').click();
    const panel = card.getByTestId('card-comments-panel');
    await expect(panel.getByTestId('comment')).toHaveCount(1);
    await expect(panel.getByText('The chorus is the only way', { exact: false })).toBeVisible();
    expect(calls).toEqual(['GET /api/droplets', `GET /api/droplets/${S.droplet}/comments`]);
    // Folded again, it shows the newest, now that it has them, and reads nothing more.
    await panel.getByRole('button', { name: 'Hide comments' }).click();
    await expect(card.getByTestId('card-comment-preview').getByTestId('comment')).toHaveCount(1);
    await card.getByTestId('card-comments').click();
    await expect(panel.getByTestId('comment')).toHaveCount(1);
    expect(calls).toHaveLength(2);
  });

  test('a droplet page is one request, comments and all', async ({ page }) => {
    const calls: string[] = [];
    page.on('request', (r) => { const path = new URL(r.url()).pathname; if (path.startsWith('/api/droplets')) calls.push(`${r.method()} ${path}`); });
    await page.goto(`/droplets/${S.droplet}`);
    await expect(page.getByTestId('comment').first()).toBeVisible();
    expect(calls).toEqual([`GET /api/droplets/${S.droplet}`]);
  });

  test('a like sent into a gateway blip is sent again and lands', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    let sent = 0;
    // Render answers a deploy's swap window itself, with an HTML 502.
    await page.route('**/api/droplets/*/like', (route) => {
      sent += 1;
      return sent === 1 ? route.fulfill({ status: 502, contentType: 'text/html', body: '<html>502</html>' }) : route.continue();
    });
    await page.goto(`/droplets/${S.droplet}`);
    const like = page.getByTestId('droplet-engagement').getByTestId('like-button');
    await like.click();
    await expect.poll(() => seenBy(request, S.users.ben), { timeout: 15_000 }).toEqual(expect.objectContaining({ liked: true, likeCount: before + 1 }));
    expect(sent).toBe(2);
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await page.unroute('**/api/droplets/*/like');
    await like.click();
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));
  });

  test('signed out in another tab: a click goes to sign in, records nothing, and an unsent comment waits for the return', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    await page.goto(`/droplets/${S.droplet}`);
    const like = page.getByTestId('droplet-engagement').getByTestId('like-button');
    await expect(like).toBeVisible(); // signed in, for now
    const draft = `half-written ${Date.now()}: the gradient part`;
    await page.getByRole('textbox', { name: 'Add a comment' }).fill(draft);

    // Sessions slide (each request renews one past its halfway mark), so an open page does not
    // time out; what ends one under it is signing out somewhere else, which clears the cookie.
    await context.clearCookies();
    await like.click();
    await expect(page).toHaveURL(new RegExp(`/auth/callback\\?reason=session_expired&returnTo=${encodeURIComponent(`/droplets/${S.droplet}`)}`));
    expect(await seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));

    // Signed back in, the page they were on still has what they were writing.
    await signIn(context, S.users.ben);
    await page.goto(`/droplets/${S.droplet}`);
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toHaveValue(draft);
    await page.getByRole('textbox', { name: 'Add a comment' }).fill('');
  });

  test('clicking fast on a slow network ends where the last click left it, on the page and in the API', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    // Each like takes a while to answer, so the clicks pile up behind one in flight.
    let sent = 0;
    await page.route('**/api/droplets/*/like', async (route) => { sent += 1; await new Promise((r) => setTimeout(r, 400)); await route.continue(); });
    await page.goto(`/droplets/${S.droplet}`);
    const like = page.getByTestId('droplet-engagement').getByTestId('like-button');
    for (let i = 0; i < 5; i++) await like.click({ delay: 0 });
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await expect(like.getByTestId('like-count')).toHaveText(String(before + 1));
    await expect.poll(() => seenBy(request, S.users.ben), { timeout: 15_000 }).toEqual(expect.objectContaining({ liked: true, likeCount: before + 1 }));
    expect(sent).toBeLessThan(5); // the clicks in between were never sent: only where they ended up

    await page.unroute('**/api/droplets/*/like');
    await page.reload();
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await like.click();
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));
  });

  test('a comment posts once even when its first answer is lost, holds across a reload, and its author deletes it', async ({ page, context, request }) => {
    await signIn(context, S.users.ada);
    await page.goto(`/droplets/${S.droplet}`);
    const section = page.getByTestId('droplet-comments');
    await expect(section.getByTestId('comment').first()).toBeVisible();
    const before = Number(await section.getByTestId('comment-count').textContent());
    const text = `E2E ${Date.now()}: the rap verse is where the gradient finally clicked`;

    // The first send reaches the API and its answer never comes back. The page sends it
    // again with the same Idempotency-Key, and gets the same comment rather than a second.
    let lost = 0;
    await page.route('**/api/droplets/*/comments', async (route) => {
      if (route.request().method() !== 'POST' || lost > 0) return route.continue();
      lost += 1;
      await route.fetch();
      return route.abort('connectionreset');
    });
    await page.getByRole('textbox', { name: 'Add a comment' }).fill(text);
    await section.getByRole('button', { name: 'Comment', exact: true }).click();
    const mine = section.getByTestId('comment').filter({ hasText: text });
    await expect(mine).toHaveCount(1);
    expect(lost).toBe(1);
    await expect(section.getByTestId('comment-count')).toHaveText(String(before + 1));
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toHaveValue('');
    const listed = await (await request.get(apiUrl(`/api/droplets/${S.droplet}/comments?limit=50`))).json();
    expect(listed.data.filter((c: { content: string }) => c.content === text)).toHaveLength(1);
    expect(listed.commentCount).toBe(before + 1);
    await page.unroute('**/api/droplets/*/comments');

    await page.reload();
    await expect(mine).toHaveCount(1);
    await expect(mine.getByText('you', { exact: true })).toBeVisible();
    // Ben may not delete it, and is not offered to.
    expect((await request.delete(apiUrl(`/api/droplets/${S.droplet}/comments/${listed.data.find((c: { content: string }) => c.content === text).id}`), { headers: asWriter(S.users.ben) })).status()).toBe(403);

    await mine.getByTestId('delete-comment').click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(mine).toHaveCount(0);
    await expect(section.getByTestId('comment-count')).toHaveText(String(before));
    await page.reload();
    await expect(section.getByTestId('comment')).toHaveCount(before);
  });

  test('markup in a comment is shown as text, and nothing in it runs', async ({ page, context }) => {
    await signIn(context, S.users.ada);
    await page.goto(`/droplets/${S.droplet}`);
    const payload = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script> **not bold** ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Add a comment' }).fill(payload);
    await page.getByTestId('droplet-comments').getByRole('button', { name: 'Comment', exact: true }).click();
    const row = page.getByTestId('comment').filter({ hasText: payload });
    await expect(row).toHaveCount(1);
    await page.reload();
    await expect(row).toHaveCount(1);
    await expect(row.locator('img, script, strong')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
    await row.getByTestId('delete-comment').click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(row).toHaveCount(0);
  });

  test('someone else’s comment offers no delete to a member who is not an admin', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto(`/droplets/${S.droplet}`);
    const adas = page.getByTestId('comment').filter({ hasText: 'The chorus is the only way' });
    await expect(adas).toBeVisible();
    await expect(adas.getByTestId('delete-comment')).toHaveCount(0);
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toBeVisible();
  });
});
