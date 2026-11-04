import { test, expect } from '@playwright/test';
import { apiUrl, seed, sessionToken, signIn } from './helpers';

const S = seed();
const asAda = () => ({ cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' });
const setRole = (request: import('@playwright/test').APIRequestContext, id: string, role: 'ADMIN' | 'USER') =>
  request.patch(apiUrl(`/api/users/${id}`), { headers: asAda(), data: { role } });

test.describe('the admin Users page', () => {
  test.beforeEach(async ({ context }) => { await signIn(context, S.users.ada); });

  test('lists admins first, then loads the rest a page at a time until there are no more', async ({ page, request }) => {
    // Everyone, in the API's order, in one page.
    const all = (await (await request.get(apiUrl('/api/users?limit=200'), { headers: asAda() })).json()).data as { email: string }[];
    expect(all.length).toBeGreaterThanOrEqual(2);
    // Pages of 1 instead of 50, so the few users here (privacy.spec.ts deletes Cal) run to several pages: the real API
    // and its real cursors.
    await page.route(/\/api\/users\?/, async (route) => {
      const url = new URL(route.request().url());
      url.searchParams.set('limit', '1');
      await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
    });

    await page.goto('/admin/users');
    const rows = page.getByTestId('user-row');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('ada@example.com'); // the admin, before the newer users

    const more = page.getByRole('button', { name: 'Load more' });
    for (let shown = 1; shown < all.length; shown++) {
      await more.click();
      await expect(rows).toHaveCount(shown + 1);
    }
    await expect(more).toHaveCount(0); // the last page says it is the last
    // Everyone once, in the API's order.
    for (const [i, u] of all.entries()) await expect(rows.nth(i)).toContainText(u.email);
  });

  test('a search that finds one of two admins leaves their Remove admin button on', async ({ page, request }) => {
    expect((await setRole(request, S.users.ben, 'ADMIN')).status()).toBe(200);
    try {
      await page.goto('/admin/users');
      await page.getByPlaceholder('search by name or email').fill('ben@example.com');
      await page.getByRole('button', { name: 'Search' }).click();
      const rows = page.getByTestId('user-row');
      await expect(rows).toHaveCount(1);
      // Only Ben is on the page, but Ada is an admin too: he is not the last one.
      await expect(rows.getByRole('button', { name: 'Remove admin' })).toBeEnabled();
    } finally {
      await setRole(request, S.users.ben, 'USER');
    }
  });
});
