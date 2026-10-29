import { test, expect, type Page } from '@playwright/test';
import { seed, signIn } from './helpers';

const S = seed();

// Every product on the page and where its links go: the pages these must keep matching are the stores' and the sites'.
const STORES = {
  'Wisteria Reader': ['https://apps.apple.com/app/wisteria-reader/id6753122777',
    'https://play.google.com/store/apps/details?id=com.spicygingerlabs.echoreader'],
  'Writeline': ['https://apps.apple.com/app/writeline-one-line-life-log/id6814636848',
    'https://play.google.com/store/apps/details?id=com.spicygingerlabs.logline'],
  'Hummingbird Biofeedback': ['https://apps.apple.com/app/hummingbird-biofeedback/id6762854409',
    'https://play.google.com/store/apps/details?id=com.spicygingerlabs.hummingbird'],
};
const SITES = { 'SAT Sprints': 'https://www.satsprints.com', 'Total Sparks AI': 'https://totalsparksai.com' };

/** Scroll the page through, so every lazy image has loaded, and return the ones that did not. */
async function brokenImages(page: Page) {
  await page.evaluate(async () => {
    for (let y = 0; y < document.body.scrollHeight; y += 400) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 50));
    }
  });
  await page.waitForFunction(() => [...document.images].every((i) => i.complete));
  return page.evaluate(() => [...document.images].filter((i) => i.naturalWidth === 0).map((i) => i.src));
}

test.describe('the About page', () => {
  test('is linked from the nav and every page\'s footer, and reads without signing in', async ({ page }) => {
    await page.goto('/droplets');
    await page.getByRole('contentinfo').getByRole('link', { name: 'ABOUT', exact: true }).click();
    await expect(page).toHaveURL(/\/about$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Hi, I’m Samantha' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();

    await page.goto('/learn');
    await page.getByRole('navigation').getByRole('link', { name: 'ABOUT', exact: true }).click();
    await expect(page).toHaveURL(/\/about$/);
  });

  test('links every product to its stores or site, in a new tab, and every picture loads', async ({ page }) => {
    await page.goto('/about');
    for (const [name, [appStore, googlePlay]] of Object.entries(STORES)) {
      const card = page.getByRole('article', { name });
      await expect(card.getByRole('link', { name: `${name} on the App Store` })).toHaveAttribute('href', appStore);
      await expect(card.getByRole('link', { name: `${name} on Google Play` })).toHaveAttribute('href', googlePlay);
      await expect(card.getByRole('list', { name: `${name} screenshots` }).getByRole('img')).toHaveCount(3);
    }
    for (const [name, url] of Object.entries(SITES)) {
      await expect(page.getByRole('article', { name }).getByRole('link', { name: new RegExp(`^Visit ${name}`) }))
        .toHaveAttribute('href', url);
    }
    // A store page opens beside POPsmosis, and learns nothing about where the visitor came from.
    for (const link of await page.locator('a[target="_blank"]').all()) {
      await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    }
    // The pictures are POPsmosis's own files: reading the page asks nothing of Apple or Google.
    const srcs = await page.locator('img').evaluateAll((imgs) => imgs.map((i) => (i as HTMLImageElement).src));
    expect(srcs.length).toBe(14);
    for (const src of srcs) expect(new URL(src).origin).toBe(new URL(page.url()).origin);
    expect(await brokenImages(page)).toEqual([]);
  });

  test('sends people on to POPsmosis itself: a droplet to watch, or a topic to ask for', async ({ page }) => {
    await page.goto('/about');
    await expect(page.getByRole('link', { name: 'Ask for a topic' })).toHaveAttribute('href', '/learn');
    await page.getByRole('link', { name: 'Watch a droplet' }).click();
    await expect(page).toHaveURL(/\/droplets$/);

    await page.goto('/about');
    await page.getByRole('link', { name: 'SAT Sprints', exact: true }).click();
    await expect(page).toHaveURL(/\/about#sat-sprints$/);
    await expect(page.getByRole('article', { name: 'SAT Sprints' })).toBeInViewport();
  });

  test('fits a phone, menu included', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/learn');
    await page.getByRole('button', { name: 'Toggle menu' }).click();
    await page.getByRole('navigation').getByRole('link', { name: 'ABOUT', exact: true }).click();
    await expect(page).toHaveURL(/\/about$/);
    expect(await brokenImages(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test('reads the same signed in', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/about');
    await expect(page.getByTestId('terms-gate')).toHaveCount(0);
    await expect(page.getByRole('heading', { level: 1, name: 'Hi, I’m Samantha' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'LOGOUT' })).toBeVisible();
  });
});
