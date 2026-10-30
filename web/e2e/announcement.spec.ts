import { test, expect, type Page } from '@playwright/test';
import { apiUrl, seed, sessionToken, signIn } from './helpers';

const S = seed();
const asAda = () => ({ cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' });

// An open page asks for the announcement once a minute, and at once when it comes back into view: this is the second.
const comeBack = (page: Page) => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));

test.describe('the announcement', () => {
  // Never left up for the other files' pages.
  test.afterEach(async ({ request }) => {
    await request.delete(apiUrl('/api/announcement'), { headers: asAda() });
  });

  test('an admin puts one up on STUDIO, and everyone sees it under the nav, signed in or not', async ({ page, context, browser }) => {
    await signIn(context, S.users.ada);
    await page.goto('/admin/studio');
    const editor = page.getByTestId('announcement-editor');
    await expect(editor.getByTestId('announcement-now')).toContainText('Nothing is showing.');
    await expect(page.getByTestId('announcement')).toHaveCount(0);

    await editor.getByRole('textbox', { name: 'Announcement' }).fill('Songs are off until tomorrow: we are out of quota.');
    await editor.getByRole('radio', { name: 'warning' }).click();
    await editor.getByRole('button', { name: 'Show to everyone' }).click();
    // The admin's own page has it at once.
    await expect(page.getByTestId('announcement')).toContainText('Songs are off until tomorrow: we are out of quota.');
    await expect(editor.getByTestId('announcement-now')).toContainText('Showing to everyone');
    await expect(editor.getByRole('button', { name: 'Change' })).toBeDisabled(); // nothing changed since

    const visitor = await browser.newPage(); // no session
    await visitor.goto('/droplets');
    await expect(visitor.getByTestId('announcement')).toContainText('Songs are off until tomorrow: we are out of quota.');
    await visitor.close();

    await editor.getByRole('button', { name: 'Take down' }).click();
    await expect(page.getByTestId('announcement')).toHaveCount(0);
    await expect(editor.getByTestId('announcement-now')).toContainText('Nothing is showing.');
  });

  test('an open page picks up a change; closing one hides it until the next goes up', async ({ page, request }) => {
    await request.put(apiUrl('/api/announcement'), { headers: asAda(), data: { message: 'Songs are off.', tone: 'warning' } });
    await page.goto('/droplets');
    const banner = page.getByTestId('announcement');
    await expect(banner).toContainText('Songs are off.');

    await banner.getByRole('button', { name: 'Close the announcement' }).click();
    await expect(banner).toHaveCount(0);
    const read = page.waitForResponse((res) => new URL(res.url()).pathname === '/api/announcement');
    await page.reload();
    await read;
    await expect(banner).toHaveCount(0); // closed on this browser

    await request.put(apiUrl('/api/announcement'), { headers: asAda(), data: { message: 'Songs are back.' } });
    await comeBack(page);
    await expect(banner).toContainText('Songs are back.');

    await request.delete(apiUrl('/api/announcement'), { headers: asAda() });
    await comeBack(page);
    await expect(banner).toHaveCount(0);
  });
});
