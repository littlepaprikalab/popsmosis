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
    await expect(page.getByText('Your requests are private.')).toBeVisible();
    // Where people write: a mail link, on every legal page.
    await expect(page.getByRole('link', { name: 'spicy.ginger.labs@gmail.com' }).first()).toHaveAttribute('href', 'mailto:spicy.ginger.labs@gmail.com');
    await expect(page.getByTestId('contact-missing')).toHaveCount(0);

    await page.getByRole('contentinfo').getByRole('link', { name: 'TERMS', exact: true }).click();
    await expect(page).toHaveURL(/\/terms$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Terms of Use' })).toBeVisible();
    await expect(page.locator('#license')).toContainText('CC BY-NC-SA 4.0');
    await expect(page.locator('#law')).toContainText('the State of Washington, USA');
    await expect(page.locator('#disputes')).toContainText('class action');
    await expect(page.locator('#disputes')).toContainText('arbitration opt-out');
  });

  test('the sign-in page will not go on to Google until the Terms are agreed to', async ({ page }) => {
    await page.goto('/auth/callback');
    const signIn = page.getByRole('button', { name: 'Sign in with Google' });
    const box = page.getByTestId('agree-terms');
    await expect(signIn).toBeDisabled();
    await expect(page.getByText('binding individual arbitration and a waiver of class actions')).toBeVisible();
    await expect(page.getByRole('link', { name: 'section 17' })).toHaveAttribute('href', '/terms#disputes');
    await box.check();
    await expect(signIn).toBeEnabled();
    await box.uncheck();
    await expect(signIn).toBeDisabled();
  });

  test('the Consumer Health Data Privacy Policy has its own link on the home page', async ({ page }) => {
    // Washington's My Health My Data Act: a separate link, on the home page, to a page of its own.
    await page.goto('/');
    await page.getByRole('contentinfo').getByRole('link', { name: 'CONSUMER HEALTH DATA', exact: true }).click();
    await expect(page).toHaveURL(/\/consumer-health-data$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Consumer Health Data Privacy Policy' })).toBeVisible();
    for (const id of ['collect', 'sources', 'shared', 'third-parties', 'rights']) await expect(page.locator(`#${id}`)).toBeVisible();
    await expect(page.locator('#rights')).toContainText('45 days');
    await expect(page.getByRole('link', { name: 'www.atg.wa.gov/file-complaint' })).toHaveAttribute('href', 'https://www.atg.wa.gov/file-complaint');
  });
});

test.describe('the account page', () => {
  test('asks a visitor to sign in', async ({ page }) => {
    await page.goto('/account');
    await expect(page.getByText('Sign in to see your account.')).toBeVisible();
  });

  test('asks for the Terms first, then deletes the account: signed out, the request gone, the lesson still public', async ({ page, context, request }) => {
    // Cal's account has no record of agreeing (seed:dev), like one made before the box: asked before anything else.
    await signIn(context, S.users.cal);
    await page.goto('/learn');
    const gate = page.getByTestId('terms-gate');
    await expect(gate).toBeVisible();
    await page.keyboard.press('Escape'); // it does not go away on its own
    await expect(gate).toBeVisible();
    const agreeButton = gate.getByRole('button', { name: 'Agree and continue' });
    await expect(agreeButton).toBeDisabled();
    await gate.getByTestId('agree-terms').check();
    await agreeButton.click();
    await expect(gate).toBeHidden();
    await expect(page.getByText('Osmosis, for my biology test on Monday')).toBeVisible();

    // The name in the nav is the way to the account page.
    await page.getByRole('navigation').getByRole('link', { name: '@Cal' }).click();
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByTestId('account-email')).toHaveText('cal@example.com');
    await expect(page.getByTestId('terms-record')).toContainText('You agreed to the Terms of Use and Privacy Policy on');

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
