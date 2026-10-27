import { test, expect } from '@playwright/test';
import { seed, signIn, apiUrl, sessionToken } from './helpers';

const S = seed();

test.describe('the Privacy Policy and Terms of Use', () => {
  test('are linked from every page\'s footer and read without signing in', async ({ page }) => {
    await page.goto('/droplets');
    await page.getByRole('contentinfo').getByRole('link', { name: 'PRIVACY', exact: true }).click();
    await expect(page).toHaveURL(/\/privacy$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Privacy Policy' })).toBeVisible();
    await expect(page.getByText('The lessons are public.')).toBeVisible();
    await expect(page.getByText('You are private.')).toBeVisible();

    await page.getByRole('contentinfo').getByRole('link', { name: 'TERMS', exact: true }).click();
    await expect(page).toHaveURL(/\/terms$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Terms of Use' })).toBeVisible();
    await expect(page.locator('#license')).toContainText('CC BY-NC-SA 4.0');
  });
});

test.describe('the account page', () => {
  test('asks a visitor to sign in', async ({ page }) => {
    await page.goto('/account');
    await expect(page.getByText('Sign in to see your account.')).toBeVisible();
  });

  test('deletes the account: signed out, the request gone, the lesson still public', async ({ page, context, request }) => {
    await signIn(context, S.users.cal);
    await page.goto('/learn');
    await expect(page.getByText('Osmosis, for my biology test on Monday')).toBeVisible();

    // The name in the nav is the way to the account page.
    await page.getByRole('navigation').getByRole('link', { name: '@Cal' }).click();
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByTestId('account-email')).toHaveText('cal@example.com');

    // Cancel first: nothing happens.
    await page.getByRole('button', { name: 'Delete my account' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
    const me = () => request.get(apiUrl('/api/auth/me'), { headers: { cookie: `popsmosis_session=${sessionToken(S.users.cal)}` } });
    expect((await me()).status()).toBe(200);

    await page.getByRole('button', { name: 'Delete my account' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('stay on the public Droplets tab');
    await dialog.getByRole('button', { name: 'Delete my account' }).click();

    // Signed out on the Droplets tab, where Cal's lesson still is.
    await expect(page).toHaveURL(/\/droplets$/);
    await expect(page.getByText('Your account is deleted.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();
    expect((await me()).status()).toBe(401);
    const tab = await request.get(apiUrl(`/api/droplets/${S.calDroplet}`));
    expect(tab.status()).toBe(200);
    expect(JSON.stringify(await tab.json())).not.toMatch(/Cal|cal@example|Monday/);
  });
});
