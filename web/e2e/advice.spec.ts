import { test, expect } from '@playwright/test';
import { seed, signIn, agent, apiUrl, sessionToken } from './helpers';

const S = seed();

test.describe('signed out', () => {
  test('shows the sign-in card and no questions', async ({ page }) => {
    await page.goto('/advice');
    await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible();
    await expect(page.getByText('ASK A QUESTION', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();
  });

  test('/ redirects to /advice', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/advice$/);
  });

  test('admin page refuses anonymous visitors', async ({ page }) => {
    await page.goto('/admin/agents');
    await expect(page.getByText('Admin access required.')).toBeVisible();
  });
});

test.describe('signed in', () => {
  test('sees everyone\'s questions with the sparks look', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    await expect(page.getByText('@Ben')).toBeVisible();
    // Seeded: Ada's pending question and Ben's answered one, both visible to Ben.
    await expect(page.getByText('Is there a way to get notified')).toBeVisible();
    await expect(page.getByText('My export to CSV')).toBeVisible();
    await expect(page.getByText('WAITING', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('ANSWERED', { exact: true }).first()).toBeVisible();
    // Badges and the agent's markdown answer.
    await expect(page.locator('.badge-agent').first()).toBeVisible();
    await expect(page.locator('.md strong', { hasText: 'before' })).toBeVisible();
    // GitHub-flavored markdown: a table renders as a table, not a line of pipes.
    await expect(page.locator('.md table th', { hasText: 'Footer shows' })).toBeVisible();
    // Advisor liveness line comes from the status endpoint.
    await expect(page.getByText(/Advisor (last active|has not connected)/)).toBeVisible();
  });

  test('a card folds down to a preview and opens again', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    const card = page.getByTestId('question-card').filter({ hasText: 'My export to CSV' }).first();
    const answer = card.locator('.md strong', { hasText: 'before' });

    // Cards start open: the answer is on screen and the collapsed preview is not.
    await expect(answer).toBeVisible();
    await expect(card.getByTestId('answer-preview')).toBeHidden();

    // Clicking the header folds the answer and its thread away, leaving a snippet.
    await card.getByRole('button', { name: 'Collapse card' }).click();
    await expect(card.getByTestId('answer-preview')).toBeVisible();
    await expect(answer).toBeHidden();
    // The question stays readable while collapsed; only the body folds.
    await expect(card.getByText('My export to CSV')).toBeVisible();

    // The header opens it again...
    await card.getByRole('button', { name: 'Expand card' }).click();
    await expect(answer).toBeVisible();
    await expect(card.getByTestId('answer-preview')).toBeHidden();

    // ...and the chevron under the answer closes a long card without scrolling up.
    await card.getByRole('button', { name: 'Collapse', exact: true }).click();
    await expect(answer).toBeHidden();
    await expect(card.getByTestId('answer-preview')).toBeVisible();
  });

  test('folding a card keeps an unsent reply, and selecting the question does not fold it', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    const card = page.getByTestId('question-card').filter({ hasText: 'My export to CSV' }).first();
    const replyBox = card.getByPlaceholder(/Reply to the advisor/);

    // A draft survives the fold: the body stays mounted, it is only hidden.
    await replyBox.fill('half-written thought');
    await card.getByRole('button', { name: 'Collapse card' }).click();
    await expect(card.getByTestId('answer-preview')).toBeVisible();
    await card.getByRole('button', { name: 'Expand card' }).click();
    await expect(replyBox).toHaveValue('half-written thought');

    // Dragging across the header to copy the question is not a request to fold
    // it. (A double-click's first click still folds, as accordions generally do.)
    const box = (await card.getByText('My export to CSV').boundingBox())!;
    await page.mouse.move(box.x + 4, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2, { steps: 12 });
    await page.mouse.up();
    expect(await page.evaluate(() => window.getSelection()?.toString().length ?? 0)).toBeGreaterThan(0);
    await expect(card.getByTestId('answer-preview')).toBeHidden();

    // A selection made elsewhere on the page must not swallow the next fold.
    await page.getByRole('heading', { name: 'ADVICE' }).dblclick();
    await card.getByRole('button', { name: 'Collapse card' }).click();
    await expect(card.getByTestId('answer-preview')).toBeVisible();
  });

  test('My Questions filter narrows to the caller', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    await page.getByRole('button', { name: 'My Questions' }).click();
    await expect(page).toHaveURL(/mine=1/);
    await expect(page.getByText('My export to CSV')).toBeVisible();
    await expect(page.getByText('Is there a way to get notified')).toHaveCount(0);
  });

  test('ask, get answered by the advisor, reply, get a reply back; all without reloading', async ({ page, context, request }) => {
    await signIn(context, S.users.ada);
    await page.goto('/advice');

    const text = `E2E ${Date.now()}: how do I export only the filtered rows?`;
    const askBox = page.getByPlaceholder('What do you need help with?');
    await askBox.fill(text);
    await askBox.press('Enter'); // newline, does not submit
    await expect(askBox).toHaveValue(`${text}\n`);
    await expect(page.getByTestId('question-card').filter({ hasText: text })).toHaveCount(0);
    await page.getByRole('button', { name: 'SUBMIT' }).click();
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    await expect(card).toBeVisible();
    await expect(card.getByTestId('waiting')).toBeVisible();
    await expect(card.getByText('WAITING', { exact: true })).toBeVisible();

    // The advisor answers through the agent API, as a runner would.
    const bot = agent(request, S.ZOEY_API_KEY);
    expect((await bot.me()).status()).toBe(200);
    const queue = await (await bot.queue()).json();
    const mine = queue.data.find((q: { content: string }) => q.content === text);
    expect(mine).toBeTruthy();
    expect((await bot.answer(mine.id, 'Apply the filter, then use **Export > Filtered rows**.')).status()).toBe(201);

    // Localized refresh: the waiting card updates itself within its 15s poll, no reload.
    await expect(card.getByText('ANSWERED', { exact: true })).toBeVisible({ timeout: 25_000 });
    await expect(card.locator('.md strong', { hasText: 'Export > Filtered rows' })).toBeVisible();
    await expect(card.getByTestId('waiting')).toHaveCount(0);

    // Human replies under the answer.
    const replyBox = card.getByPlaceholder(/Reply to the advisor/);
    await replyBox.fill('There is no Filtered rows option in my menu.');
    await replyBox.press('Enter'); // Enter is a newline, not send
    await expect(replyBox).toHaveValue(/\n$/);
    // Not posted yet: no rendered reply paragraph (getByText would also match the textarea's value).
    await expect(card.locator('p', { hasText: 'There is no Filtered rows option' })).toHaveCount(0);
    await replyBox.press('Control+Enter');
    await expect(card.locator('p', { hasText: 'There is no Filtered rows option' })).toBeVisible();
    await expect(card.getByTestId('awaiting-agent')).toBeVisible();

    // The advisor sees the whole thread and responds.
    const threads = await (await bot.replyQueue()).json();
    const thread = threads.data.find((t: { question: { content: string } }) => t.question.content === text);
    expect(thread).toBeTruthy();
    expect(thread.answer.content).toContain('Filtered rows');
    expect(thread.thread.map((r: { authorType: string }) => r.authorType)).toEqual(['HUMAN']);
    expect((await bot.reply(thread.answer.id, thread.replyTo.id, 'Then your plan lacks the export add-on. Ask an admin to enable it.')).status()).toBe(201);

    // ...and the reply shows up in place.
    await expect(card.getByText('Then your plan lacks the export add-on')).toBeVisible({ timeout: 25_000 });
    await expect(card.getByTestId('awaiting-agent')).toHaveCount(0);
    await expect(card.locator('.badge-agent')).toHaveCount(2);
  });

  test('a question asked with files shows them: a picture as its thumbnail, a log as a link', async ({ page, context, request }) => {
    // Asked as Zed's Zoey panel asks, the files in the body; Ben, whose day of questions the other tests leave room in.
    const text = `E2E ${Date.now()}: why does this wall flicker?`;
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64'); // 1x1
    const asked = await request.post(apiUrl('/api/questions'), {
      headers: { cookie: `zoey_session=${sessionToken(S.users.ben)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: {
        content: text,
        attachments: [{ name: 'wall.png', data: png.toString('base64') }, { name: 'zed.log', data: Buffer.from('Zed: the wall flickers\n').toString('base64') }],
      },
    });
    expect(asked.status()).toBe(201);

    await signIn(context, S.users.ben);
    await page.goto('/advice');
    const files = page.getByTestId('question-card').filter({ hasText: text }).first().getByTestId('attachments');
    const picture = files.getByRole('img', { name: 'wall.png' });
    await expect(picture).toBeVisible();
    await expect.poll(() => picture.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1); // its bytes came through the proxy
    await expect(files.getByRole('link', { name: /zed\.log/ })).toHaveAttribute('href', /^\/api\/attachments\/\w+$/);
  });

  test('pages through many questions by cursor as the user scrolls', async ({ page, context }) => {
    // seed:dev creates 26 questions (2 recent + 24 older), more than one 20-item page.
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    const cards = page.getByTestId('question-card');
    await expect(cards).toHaveCount(20);
    await expect(page.getByTestId('load-more')).toBeVisible();
    await page.getByTestId('load-more').scrollIntoViewIfNeeded();
    await expect.poll(async () => cards.count(), { timeout: 10_000 }).toBeGreaterThan(20);
    await expect(page.getByText('End of list')).toBeVisible({ timeout: 10_000 });
  });

  test('logout clears the session', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/advice');
    await expect(page.getByText('@Ben')).toBeVisible();
    await page.getByRole('button', { name: 'LOGOUT' }).click();
    await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible();
    expect((await context.cookies()).some((c) => c.name === 'zoey_session')).toBe(false);
    await page.reload();
    await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible();
  });
});

test.describe('admin', () => {
  test('lists agents, creates one with a key shown once, and can disable it', async ({ page, context, request }) => {
    await signIn(context, S.users.ada);
    await page.goto('/admin/agents');
    await expect(page.getByText(S.agent.name, { exact: true })).toBeVisible();

    const name = `e2e-agent-${Date.now()}`;
    await page.getByPlaceholder('agent name, e.g. zoey-helper').fill(name);
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page.getByText('API KEY — SHOWN ONCE')).toBeVisible();
    const key = (await page.locator('code.select-all').textContent())!.trim();
    expect(key).toMatch(/^sk_zoey_[0-9a-f]{64}$/);

    const bot = agent(request, key);
    expect((await bot.me()).status()).toBe(200);

    const row = page.locator('div.rounded-lg', { hasText: name });
    await row.getByRole('button', { name: 'Disable' }).click();
    await expect(row.getByText('disabled', { exact: true })).toBeVisible();
    expect((await bot.me()).status()).toBe(403);
    await row.getByRole('button', { name: 'Enable' }).click();
    await expect(row.getByText('enabled', { exact: true })).toBeVisible();
    expect((await bot.me()).status()).toBe(200);

    // Revoke: the key dies, the agent stays, Mint issues a new one.
    await row.getByRole('button', { name: 'Revoke key' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Revoke' }).click();
    await expect(row.getByText('no key', { exact: true })).toBeVisible();
    expect((await bot.me()).status()).toBe(401);
    await row.getByRole('button', { name: 'Mint key' }).click();
    await expect(page.getByText('API KEY — SHOWN ONCE')).toBeVisible();
    const key2 = (await page.locator('code.select-all').textContent())!.trim();
    expect(key2).not.toBe(key);
    expect((await agent(request, key2).me()).status()).toBe(200);
    await expect(row.getByText('no key', { exact: true })).toHaveCount(0);
  });

  test('admins can make and unmake other admins, never themselves', async ({ page, context }) => {
    await signIn(context, S.users.ada);
    await page.goto('/admin/users');
    const benRow = page.getByTestId('user-row').filter({ hasText: 'ben@example.com' });
    const adaRow = page.getByTestId('user-row').filter({ hasText: 'ada@example.com' });
    await expect(adaRow.getByText('(you)')).toBeVisible();
    await expect(adaRow.getByRole('button')).toBeDisabled();

    await benRow.getByRole('button', { name: 'Make admin' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Make admin' }).click();
    await expect(benRow.getByText('admin', { exact: true })).toBeVisible();

    await benRow.getByRole('button', { name: 'Remove admin' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Remove admin' }).click();
    await expect(benRow.getByText('admin', { exact: true })).toHaveCount(0);
  });

  test('non-admins are refused', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/admin/agents');
    await expect(page.getByText('Admin access required.')).toBeVisible();
  });
});

test.describe('approval', () => {
  test('a pending user waits; the admin approves; the waiting page lets them through', async ({ browser, page, context }) => {
    // Cal signed in but is not on the auto-approve list: every page is the waiting room.
    await signIn(context, S.users.cal);
    await page.goto('/advice');
    await expect(page.getByText('WAITING FOR APPROVAL')).toBeVisible();
    await expect(page.getByText('cal@example.com')).toBeVisible();
    await expect(page.getByText('ASK A QUESTION', { exact: true })).toHaveCount(0);
    await page.goto('/admin/users');
    await expect(page.getByText('WAITING FOR APPROVAL')).toBeVisible();

    // Ada sees him in the queue and on the nav badge, and approves.
    const adaCtx = await browser.newContext();
    await signIn(adaCtx, S.users.ada);
    const ada = await adaCtx.newPage();
    await ada.goto('/admin/users');
    await expect(ada.getByTestId('pending-badge')).toHaveText('1');
    const calRow = ada.getByTestId('pending-row').filter({ hasText: 'cal@example.com' });
    await calRow.getByRole('button', { name: 'Approve' }).click();
    await ada.getByRole('dialog').getByRole('button', { name: 'Approve' }).click();
    await expect(ada.getByTestId('pending-row')).toHaveCount(0);
    await expect(ada.getByTestId('pending-badge')).toHaveCount(0);
    const approvedRow = ada.getByTestId('user-row').filter({ hasText: 'cal@example.com' });
    await expect(approvedRow.getByText(/approved just now/)).toBeVisible();

    // Cal's page polls /auth/me and lets him in without a reload.
    await expect(page.getByText('WAITING FOR APPROVAL')).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByText('USERS', { exact: true })).toHaveCount(0); // approved, not admin
    await page.goto('/advice');
    await expect(page.getByText('@Cal')).toBeVisible();
    await expect(page.getByText('My export to CSV')).toBeVisible();

    // Revoke: Cal's next request 403s, the client re-reads /me, the gate closes.
    await approvedRow.getByRole('button', { name: 'Revoke' }).click();
    await ada.getByRole('dialog').getByRole('button', { name: 'Revoke' }).click();
    await expect(approvedRow.getByText('denied', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('ACCESS DENIED')).toBeVisible();
    await adaCtx.close();
  });
});

// The API is a separate Render service that spins down when idle and goes
// unroutable for a moment mid-deploy. Render answers those windows itself with
// an HTML error page, which must never be read as "this user is signed out".
test.describe('API unavailable', () => {
  const renderErrorPage = { status: 502, contentType: 'text/html; charset=utf-8', body: '<!DOCTYPE html><html><body>502</body></html>' };

  test('a signed-in user sees the outage, not the sign-in card', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.route('**/api/auth/me', (route) => route.fulfill(renderErrorPage));

    await page.goto('/advice');
    await expect(page.getByTestId('server-unavailable')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('link', { name: 'Sign in with Google' })).toHaveCount(0);

    // Once the API is back, the retry button gets the page to its real state.
    await page.unroute('**/api/auth/me');
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByText('@Ben')).toBeVisible();
  });

  test('a single 502 is ridden out without the user noticing', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    let failed = false;
    await page.route('**/api/auth/me', (route) => {
      if (failed) return route.continue();
      failed = true;
      return route.fulfill(renderErrorPage);
    });

    await page.goto('/advice');
    await expect(page.getByText('@Ben')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('server-unavailable')).toHaveCount(0);
  });
});
