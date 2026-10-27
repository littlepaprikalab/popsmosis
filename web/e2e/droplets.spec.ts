import { test, expect } from '@playwright/test';
import { seed, signIn, agent, apiUrl, sessionToken } from './helpers';

const S = seed();
// The seed's sample clips, as the absolute URLs the video endpoint takes.
const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000';
const SONG = `${BASE}/samples/song.mp4`;
const LESSON = `${BASE}/samples/lesson.mp4`;

test.describe('the Droplets tab, signed out', () => {
  test('lists the finished droplets and plays one, with no sign-in', async ({ page }) => {
    await page.goto('/learn');
    await page.getByRole('link', { name: 'DROPLETS', exact: true }).click();
    await expect(page).toHaveURL(/\/droplets$/);
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();

    const card = page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' }).first();
    await expect(card).toBeVisible();
    await expect(card.getByText('AP Biology', { exact: true })).toBeVisible();
    // Never who asked, or their words: the title is the bundle's.
    await expect(page.getByText('Ben')).toHaveCount(0);
    await expect(page.getByText('how do cells actually make ATP')).toHaveCount(0);

    await card.click();
    await expect(page).toHaveURL(new RegExp(`/droplets/${S.droplet}$`));
    await expect(page.getByRole('heading', { name: 'ATP synthesis' })).toBeVisible();
    await expect(page.locator('.md strong', { hasText: 'gradient' })).toBeVisible();
    await expect(page.getByTestId('lyrics').getByText('Spin the rotor, make the ATP')).toBeVisible();
    // The follow-up thread stays on /learn.
    await expect(page.getByText('water wheel')).toHaveCount(0);

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

  test('a droplet not on the tab is a 404 page, and the API needs no cookie', async ({ page, request }) => {
    await page.goto('/droplets/not-a-droplet');
    await expect(page.getByText('This droplet is not on the tab')).toBeVisible();
    const list = await request.get(apiUrl('/api/droplets'));
    expect(list.status()).toBe(200);
    expect(JSON.stringify(await list.json())).not.toMatch(/Ben|ben@example|how do cells/);
  });
});

test.describe('videos on /learn', () => {
  test('a lesson shows its videos rendering, then the players, without a reload; an admin can hide it from the tab', async ({ page, context, request }) => {
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
    const answer = await bot.answer(questionId, 'The Calvin cycle **fixes carbon** with the ATP the light reactions made.');
    expect(answer.status()).toBe(201);
    const answerId = (await answer.json()).id;
    expect((await bot.video(answerId, { status: 'RENDERING' })).status()).toBe(200);

    await signIn(context, S.users.ada);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    await expect(card.getByTestId('videos-rendering')).toBeVisible();

    // Done: the card swaps in the players on its own poll.
    expect((await bot.video(answerId, { status: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON })).status()).toBe(200);
    await expect(card.getByTestId('droplet-players')).toBeVisible({ timeout: 25_000 });
    await expect(card.getByTestId('videos-rendering')).toHaveCount(0);
    await expect(card.getByLabel('song video')).toHaveAttribute('src', SONG);

    // It is on the public tab, until Ada (an admin) hides it.
    const onTab = async () => (await (await request.get(apiUrl('/api/droplets'))).json()).data.some((d: { id: string }) => d.id === answerId);
    expect(await onTab()).toBe(true);
    await card.getByTestId('toggle-hidden').click();
    await expect(card.getByText('Hidden from the Droplets tab.')).toBeVisible();
    expect(await onTab()).toBe(false);
    await card.getByTestId('toggle-hidden').click();
    await expect(card.getByRole('link', { name: 'On the public Droplets tab' })).toBeVisible();
    expect(await onTab()).toBe(true);
  });

  test('a failed render says why under the lesson', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const failed = page.getByTestId('videos-failed').first();
    await failed.scrollIntoViewIfNeeded();
    await expect(failed).toContainText('render: ffmpeg exited 1 on the song.');
  });
});
