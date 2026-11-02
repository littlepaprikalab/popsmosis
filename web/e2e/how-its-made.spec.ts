import { test, expect } from '@playwright/test';
import { seed } from './helpers';

const S = seed();

test.describe('the How it’s made page', () => {
  test('is linked from every page\'s footer and from a droplet\'s page, and reads without signing in', async ({ page }) => {
    await page.goto('/droplets');
    await page.getByRole('contentinfo').getByRole('link', { name: 'HOW IT’S MADE', exact: true }).click();
    await expect(page).toHaveURL(/\/how-its-made$/);
    await expect(page.getByRole('heading', { level: 1, name: 'How a droplet is made' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();

    // Beside what made the droplet, where a viewer wonders how it was made.
    await page.goto(`/droplets/${S.droplet}`);
    await page.getByRole('link', { name: 'How it’s made →' }).click();
    await expect(page).toHaveURL(/\/how-its-made$/);
  });

  test('says what runs, what the video says about a wrong picture, and what is not done yet', async ({ page }) => {
    await page.goto('/how-its-made');
    for (const name of ['How it works, step by step', 'When a picture is wrong, the video tells you', 'Why this is hard',
      'The AI models we use', 'What we don’t do yet']) {
      await expect(page.getByRole('heading', { level: 2, name })).toBeVisible();
    }
    // The lesson's steps in order. The fact check and the line check hold it back, and each says so.
    const steps = page.getByRole('list', { name: 'Making the lesson' }).getByRole('listitem');
    await expect(steps).toHaveCount(5);
    await expect(steps).toContainText(['Plan', 'Fact check', 'Rule check', 'Student test', 'Line check']);
    for (const gate of [1, 4]) await expect(steps.nth(gate)).toContainText('Fails? Nothing is posted.');
    await expect(page.getByText('Fails? Nothing is posted.')).toHaveCount(2);
    // Then the pictures and the sound, made side by side.
    const pictures = page.getByRole('list', { name: 'Pictures' });
    await expect(pictures.getByRole('listitem')).toHaveCount(3);
    // What the picture check cannot fix, the video says: the step points down to the card.
    await pictures.getByRole('link', { name: /^Still wrong in a way that could mislead you\?/ }).click();
    await expect(page).toHaveURL(/#notice-title$/);
    await expect(page.getByRole('heading', { level: 2, name: 'When a picture is wrong, the video tells you' })).toBeInViewport();
    await expect(page.getByRole('list', { name: 'Sound' }).getByRole('listitem')).toHaveCount(1);
    // Real frames of videos: the sticky note on a mistake the check found, and the tag on a picture it found none in.
    const frames = page.locator('section[aria-labelledby="notice-title"] img');
    await expect(frames).toHaveCount(2);
    await expect(frames.first()).toHaveAttribute('alt', /sticks a yellow note beside it.*Spindle fibers are the green threads/);
    await expect(frames.last()).toHaveAttribute('alt', /AI-generated picture tag/);
    await frames.last().scrollIntoViewIfNeeded();
    await page.waitForFunction(() => [...document.querySelectorAll('section[aria-labelledby="notice-title"] img')]
      .every((i) => (i as HTMLImageElement).complete && (i as HTMLImageElement).naturalWidth > 0));
    await expect(page.getByRole('article')).toHaveCount(6);
    await expect(page.getByRole('list', { name: 'The models' }).getByRole('listitem')).toHaveCount(8);
  });

  test('sends people on to POPsmosis itself: a droplet to watch, or a topic to ask for', async ({ page }) => {
    await page.goto('/how-its-made');
    await expect(page.getByRole('link', { name: 'Ask for a topic' })).toHaveAttribute('href', '/learn');
    await page.getByRole('link', { name: 'Watch a droplet' }).click();
    await expect(page).toHaveURL(/\/droplets$/);
  });

  test('fits a phone', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/how-its-made');
    await expect(page.getByRole('heading', { level: 1, name: 'How a droplet is made' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});
