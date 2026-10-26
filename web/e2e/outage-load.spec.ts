import { test, expect } from '@playwright/test';
import { seed, signIn } from './helpers';

const S = seed();

const RENDER_502 = { status: 502, contentType: 'text/html; charset=utf-8', body: '<!DOCTYPE html><html><body>502</body></html>' };

// What one open tab aims at an API that has stopped answering. Retrying is
// right for a blip and wrong for an outage: without a shared view of "the API
// is down" every poll retries its own way through the same failure, on the API
// least able to take it. These bound that. Both were measured on the code that
// first added the retries: 24 requests and 18 hung sockets.
test.describe('outage load', () => {
  // Each test watches a fixed 45s window, which leaves no room under the 60s
  // default once a CI runner adds sign-in and page load on top.
  test.describe.configure({ timeout: 90_000 });

  test('an outage that starts mid-session does not multiply the polling', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    await expect(page.getByText('@Ben')).toBeVisible();

    let count = 0;
    await page.route('**/api/**', (route) => { count += 1; return route.fulfill(RENDER_502); });
    await page.waitForTimeout(45_000);

    // The polls themselves are ~8 in this window; the budget leaves room for the
    // first burst of retries, before anything had established the API was gone.
    expect(count).toBeLessThan(18);
  });

  test('a hanging API does not pile up in-flight requests', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    await expect(page.getByText('@Ben')).toBeVisible();

    // Never answering is the worst case: every attempt costs the full client
    // timeout, so a tab that keeps starting polls runs out of sockets.
    let started = 0;
    await page.route('**/api/**', () => { started += 1; });
    await page.waitForTimeout(45_000);

    expect(started).toBeLessThan(8);
  });
});
