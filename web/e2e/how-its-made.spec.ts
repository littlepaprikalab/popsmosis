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
    for (const name of ['The steps', 'When a picture is wrong, the video says so', 'Why this is hard', 'The models', 'What we don’t do yet']) {
      await expect(page.getByRole('heading', { level: 2, name })).toBeVisible();
    }
    // Both checks that hold the lesson back, in the order they run.
    const steps = page.locator('section[aria-labelledby="steps-title"] ol').first().getByRole('listitem');
    await expect(steps).toHaveCount(5);
    await expect(steps.nth(3)).toContainText('Fact audit');
    await expect(steps.nth(4)).toContainText('Fact-check');
    // The card as the video draws it, with the note's lead in the renderer's words.
    await expect(page.getByText('A check found these mistakes:')).toBeVisible();
    await expect(page.getByRole('article')).toHaveCount(6);
    await expect(page.getByRole('list', { name: 'The models' }).getByRole('listitem')).toHaveCount(7);
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
