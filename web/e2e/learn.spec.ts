import { test, expect } from '@playwright/test';
import { seed, signIn, agent, apiUrl, sessionToken } from './helpers';

const S = seed();
// The web app's Inkmory link (lib/inkmory.ts) is built in from this; CI sets it, a local run may not.
const INKMORY = (process.env.NEXT_PUBLIC_INKMORY_URL || '').replace(/\/+$/, '');
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

test.describe('signed out', () => {
  test('shows the sign-in card and no topics', async ({ page }) => {
    await page.goto('/learn');
    await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible();
    await expect(page.getByText('WHAT DO YOU WANT TO LEARN?', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();
    // What signing in agrees to, linked, before anyone signs in.
    const consent = page.getByTestId('consent');
    await expect(consent).toContainText('13 or older');
    await expect(consent.getByRole('link', { name: 'Terms of Use' })).toHaveAttribute('href', '/terms');
    await expect(consent.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute('href', '/privacy');
  });

  test('/ redirects to /learn', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/learn$/);
  });

  test('sign-in keeps a page of this site to come back to, and never another site', async ({ page }) => {
    const kept = async (returnTo: string) => {
      await page.goto(`/auth/callback?returnTo=${encodeURIComponent(returnTo)}`);
      await expect(page.getByRole('button', { name: 'Sign in with Google' })).toBeVisible();
      const saved = await page.evaluate(() => sessionStorage.getItem('auth_return_to'));
      await page.evaluate(() => sessionStorage.removeItem('auth_return_to'));
      return saved;
    };
    expect(await kept('/droplets/abc?tab=song#comments')).toBe('/droplets/abc?tab=song#comments');
    // A backslash reads as a slash, and tabs and newlines are dropped: each of these resolves off-site.
    for (const offSite of ['/\\evil.example', '//evil.example', '/\t/evil.example', '/\n/evil.example', 'https://evil.example/',
      '/\\/evil.example/learn']) {
      expect(await kept(offSite), offSite).toBeNull();
    }
  });

  test('admin page refuses anonymous visitors', async ({ page }) => {
    await page.goto('/admin/agents');
    await expect(page.getByText('Admin access required.')).toBeVisible();
  });
});

test.describe('signed in', () => {
  test('sees only their own topics, with the sparks look', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    await expect(page.getByText('@Ben')).toBeVisible();
    // Seeded: Ben's finished topic is his to see; Ada's waiting one is not.
    await expect(page.getByText('ATP synthesis: how do cells')).toBeVisible();
    await expect(page.getByText('Photosynthesis: how do the light')).toHaveCount(0);
    await expect(page.getByTestId('privacy-note')).toContainText('Only you see your requests');
    // No filter: there is only one list a learner can see.
    await expect(page.getByRole('button', { name: 'My Topics' })).toHaveCount(0);
    await expect(page.getByText('BREWING', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('READY', { exact: true }).first()).toBeVisible();
    // Badges and the agent's markdown lesson.
    await expect(page.locator('.badge-agent').first()).toBeVisible();
    await expect(page.locator('.md strong', { hasText: 'gradient' })).toBeVisible();
    // GitHub-flavored markdown: a table renders as a table, not a line of pipes.
    await expect(page.locator('.md table th', { hasText: 'Stage' })).toBeVisible();
    // Teacher liveness line comes from the status endpoint.
    await expect(page.getByText(/Teacher (last active|has not connected)/)).toBeVisible();
  });

  test('a card folds down to a preview and opens again', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: 'ATP synthesis: how do cells' }).first();
    const answer = card.locator('.md strong', { hasText: 'gradient' });

    // Cards start open: the answer is on screen and the collapsed preview is not.
    await expect(answer).toBeVisible();
    await expect(card.getByTestId('answer-preview')).toBeHidden();

    // Clicking the header folds the answer and its thread away, leaving a snippet.
    await card.getByRole('button', { name: 'Collapse card' }).click();
    await expect(card.getByTestId('answer-preview')).toBeVisible();
    await expect(answer).toBeHidden();
    // The question stays readable while collapsed; only the body folds.
    await expect(card.getByText('ATP synthesis: how do cells')).toBeVisible();

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
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: 'ATP synthesis: how do cells' }).first();
    const replyBox = card.getByPlaceholder(/Ask a follow-up/);

    // A draft survives the fold: the body stays mounted, it is only hidden.
    await replyBox.fill('half-written thought');
    await card.getByRole('button', { name: 'Collapse card' }).click();
    await expect(card.getByTestId('answer-preview')).toBeVisible();
    await card.getByRole('button', { name: 'Expand card' }).click();
    await expect(replyBox).toHaveValue('half-written thought');

    // Dragging across the header to copy the question is not a request to fold
    // it. (A double-click's first click still folds, as accordions generally do.)
    const box = (await card.getByText('ATP synthesis: how do cells').boundingBox())!;
    await page.mouse.move(box.x + 4, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2, { steps: 12 });
    await page.mouse.up();
    expect(await page.evaluate(() => window.getSelection()?.toString().length ?? 0)).toBeGreaterThan(0);
    await expect(card.getByTestId('answer-preview')).toBeHidden();

    // A selection made elsewhere on the page must not swallow the next fold.
    await page.getByRole('heading', { name: 'LEARN', exact: true }).dblclick();
    await card.getByRole('button', { name: 'Collapse card' }).click();
    await expect(card.getByTestId('answer-preview')).toBeVisible();
  });

  test('a lesson links to the teacher\'s notes about it on Inkmory, when Inkmory is set', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: 'ATP synthesis: how do cells' }).first();
    await expect(card.locator('.md strong', { hasText: 'gradient' })).toBeVisible();
    const link = card.getByTestId('teacher-notes');
    if (!INKMORY) {
      await expect(link).toHaveCount(0);
      return;
    }
    // Inkmory's search for the tag the teacher puts on its records about this topic.
    await expect(link).toHaveAttribute('href', new RegExp(`^${escape(INKMORY)}/search\\?ref=popsmosis%3A[a-z0-9]+$`));
    await expect(link).toHaveAttribute('target', '_blank');
    await expect(link).toHaveAttribute('rel', /noopener/);
  });

  test('an admin sees everyone\'s topics, and My Topics narrows to their own', async ({ page, context }) => {
    await signIn(context, S.users.ada);
    await page.goto('/learn');
    await expect(page.getByText('ATP synthesis: how do cells')).toBeVisible();
    await expect(page.getByText('Photosynthesis: how do the light')).toBeVisible();
    // Someone else's thread is readable, but not the admin's to post in.
    const bens = page.getByTestId('question-card').filter({ hasText: 'ATP synthesis: how do cells' }).first();
    await expect(bens.getByText('Think of a water wheel')).toBeVisible();
    await expect(bens.getByPlaceholder(/Ask a follow-up/)).toHaveCount(0);

    await page.getByRole('button', { name: 'My Topics' }).click();
    await expect(page).toHaveURL(/mine=1/);
    await expect(page.getByText('Photosynthesis: how do the light')).toBeVisible();
    await expect(page.getByText('ATP synthesis: how do cells')).toHaveCount(0);
  });

  test('a topic with a lesson can be deleted: the request goes, the lesson stays on the Droplets tab', async ({ page, context, request }) => {
    const text = `E2E ${Date.now()}: diffusion, for my quiz`;
    const asked = await request.post(apiUrl('/api/questions'), {
      headers: { cookie: `popsmosis_session=${sessionToken(S.users.ben)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: { content: text },
    });
    expect(asked.status()).toBe(201);
    const bot = agent(request, S.POPSMOSIS_API_KEY);
    const questionId = (await asked.json()).id;
    expect((await bot.claim(questionId)).status()).toBe(200);
    const answer = await bot.answer(questionId, 'Particles spread from **high to low** concentration.');
    const answerId = (await answer.json()).id;
    const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000';
    expect((await bot.video(answerId, { status: 'RENDERING' })).status()).toBe(200);
    expect((await bot.video(answerId, { status: 'READY', songVideoUrl: `${BASE}/samples/song.mp4`, lessonVideoUrl: `${BASE}/samples/lesson.mp4` })).status()).toBe(200);

    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    await card.getByRole('button', { name: 'delete' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('The lesson stays on the public Droplets tab');
    await dialog.getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByTestId('question-card').filter({ hasText: text })).toHaveCount(0);
    await expect(page.getByText('The lesson stays for others to learn from')).toBeVisible();

    // The lesson is still public, and the request no longer says who asked or what they wrote.
    const tab = await request.get(apiUrl(`/api/droplets/${answerId}`));
    expect(tab.status()).toBe(200);
    expect((await tab.json()).content).toContain('high to low');
    await page.reload();
    await expect(page.getByText(text)).toHaveCount(0);
  });

  test('a topic the teacher gives up says why, in place, and can be deleted', async ({ page, context, request }) => {
    await signIn(context, S.users.ada);
    await page.goto('/learn');

    const text = `E2E ${Date.now()}: a topic that cannot pass its fact-check`;
    await page.getByPlaceholder('A topic, e.g. ATP synthesis').fill(text);
    await page.getByRole('button', { name: 'SUBMIT' }).click();
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    await expect(card.getByTestId('waiting')).toBeVisible();

    // The studio claims the topic, fails twice, and the operator gives it up.
    const bot = agent(request, S.POPSMOSIS_API_KEY);
    const mine = (await (await bot.queue()).json()).data.find((q: { content: string }) => q.content === text);
    expect((await bot.claim(mine.id)).status()).toBe(200);
    expect((await bot.fail(mine.id, 'factcheck failed: 1 line still fails')).status()).toBe(200);

    await expect(card.getByText('FAILED', { exact: true })).toBeVisible({ timeout: 25_000 });
    await expect(card.getByTestId('failed')).toContainText('factcheck failed: 1 line still fails');
    await expect(card.getByTestId('waiting')).toHaveCount(0);
    // A failed topic is where the teacher's notes help most: they say what broke.
    if (INKMORY) {
      await expect(card.getByTestId('teacher-notes')).toHaveAttribute('href', `${INKMORY}/search?ref=popsmosis%3A${mine.id}`);
    } else {
      await expect(card.getByTestId('teacher-notes')).toHaveCount(0);
    }

    await card.getByRole('button', { name: 'delete' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByTestId('question-card').filter({ hasText: text })).toHaveCount(0);
  });

  test('ask for a topic, get a lesson from the teacher, follow up, get a reply back; all without reloading', async ({ page, context, request }) => {
    await signIn(context, S.users.ada);
    await page.goto('/learn');

    const text = `E2E ${Date.now()}: the Krebs cycle, step by step`;
    const askBox = page.getByPlaceholder('A topic, e.g. ATP synthesis');
    await askBox.fill(text);
    await askBox.press('Enter'); // newline, does not submit
    await expect(askBox).toHaveValue(`${text}\n`);
    await expect(page.getByTestId('question-card').filter({ hasText: text })).toHaveCount(0);
    await page.getByRole('button', { name: 'SUBMIT' }).click();
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    await expect(card).toBeVisible();
    await expect(card.getByTestId('waiting')).toBeVisible();
    await expect(card.getByText('BREWING', { exact: true })).toBeVisible();

    // The teacher answers through the agent API, as a runner would.
    const bot = agent(request, S.POPSMOSIS_API_KEY);
    expect((await bot.me()).status()).toBe(200);
    const queue = await (await bot.queue()).json();
    const mine = queue.data.find((q: { content: string }) => q.content === text);
    expect(mine).toBeTruthy();
    expect((await bot.answer(mine.id, 'The Krebs cycle turns acetyl-CoA into **CO2 and energy carriers**.')).status()).toBe(201);

    // Localized refresh: the waiting card updates itself within its 15s poll, no reload.
    await expect(card.getByText('READY', { exact: true })).toBeVisible({ timeout: 25_000 });
    await expect(card.locator('.md strong', { hasText: 'CO2 and energy carriers' })).toBeVisible();
    await expect(card.getByTestId('waiting')).toHaveCount(0);

    // Human follows up under the lesson.
    const replyBox = card.getByPlaceholder(/Ask a follow-up/);
    await replyBox.fill('Where does the acetyl-CoA come from?');
    await replyBox.press('Enter'); // Enter is a newline, not send
    await expect(replyBox).toHaveValue(/\n$/);
    // Not posted yet: no rendered reply paragraph (getByText would also match the textarea's value).
    await expect(card.locator('p', { hasText: 'Where does the acetyl-CoA come from' })).toHaveCount(0);
    await replyBox.press('Control+Enter');
    await expect(card.locator('p', { hasText: 'Where does the acetyl-CoA come from' })).toBeVisible();
    await expect(card.getByTestId('awaiting-agent')).toBeVisible();

    // The teacher sees the whole thread and responds.
    const threads = await (await bot.replyQueue()).json();
    const thread = threads.data.find((t: { question: { content: string } }) => t.question.content === text);
    expect(thread).toBeTruthy();
    expect(thread.answer.content).toContain('energy carriers');
    expect(thread.thread.map((r: { authorType: string }) => r.authorType)).toEqual(['HUMAN']);
    expect((await bot.reply(thread.answer.id, thread.replyTo.id, 'From pyruvate, made by glycolysis, once it enters the mitochondrion.')).status()).toBe(201);

    // ...and the reply shows up in place.
    await expect(card.getByText('From pyruvate, made by glycolysis')).toBeVisible({ timeout: 25_000 });
    await expect(card.getByTestId('awaiting-agent')).toHaveCount(0);
    await expect(card.locator('.badge-agent')).toHaveCount(2);
  });

  test('an admin sends a topic to a brain of their own: the card and the teacher\'s claim say which, and the pick is remembered', async ({ page, context, request }) => {
    await signIn(context, S.users.ada);
    await page.goto('/learn');
    const picker = page.getByTestId('brain-picker');
    await expect(picker.getByRole('radio', { name: 'Default' })).toHaveAttribute('aria-checked', 'true');
    await picker.getByRole('radio', { name: 'Muse Spark' }).click();
    await expect(picker.getByRole('radio', { name: 'Muse Spark' })).toHaveAttribute('aria-checked', 'true');
    await expect(picker.getByRole('radio', { name: 'Default' })).toHaveAttribute('aria-checked', 'false');
    await expect(page.getByTestId('brain-note')).toContainText('Muse Spark 1.3');

    const text = `E2E ${Date.now()}: osmosis, on Muse Spark`;
    await page.getByPlaceholder('A topic, e.g. ATP synthesis').fill(text);
    await page.getByRole('button', { name: 'SUBMIT' }).click();
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    await expect(card.getByTestId('topic-brain')).toHaveText('Muse Spark');

    // The studio learns the topic's brain from its claim (studio/run.py).
    const asAda = { cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' };
    const own = await (await request.get(apiUrl('/api/questions?mine=true&limit=5'), { headers: asAda })).json();
    const sent = own.data.find((q: { content: string }) => q.content === text);
    expect(sent.brain).toBe('muse-spark');
    const claimed = await agent(request, S.POPSMOSIS_API_KEY).claim(sent.id);
    expect((await claimed.json()).brain).toBe('muse-spark');

    // Remembered on this browser, so a run of test topics stays on it.
    await page.reload();
    await expect(page.getByTestId('brain-picker').getByRole('radio', { name: 'Muse Spark' })).toHaveAttribute('aria-checked', 'true');
    await page.getByTestId('brain-picker').getByRole('radio', { name: 'Default' }).click();
    await page.reload();
    await expect(page.getByTestId('brain-picker').getByRole('radio', { name: 'Default' })).toHaveAttribute('aria-checked', 'true');
    expect((await request.delete(apiUrl(`/api/questions/${sent.id}`), { headers: asAda })).status()).toBe(204);
  });

  test('a learner gets no brain picker: their topics are made by the teacher\'s own', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    await expect(page.getByPlaceholder('A topic, e.g. ATP synthesis')).toBeVisible();
    await expect(page.getByTestId('brain-picker')).toHaveCount(0);
  });

  test('a topic asked with files shows them: a picture as its thumbnail, a text file as a link', async ({ page, context, request }) => {
    // Asked straight to the API with the files in the body; Ben, whose day of questions the other tests leave room in.
    const text = `E2E ${Date.now()}: explain this diagram of a neuron`;
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64'); // 1x1
    const asked = await request.post(apiUrl('/api/questions'), {
      headers: { cookie: `popsmosis_session=${sessionToken(S.users.ben)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: {
        content: text,
        attachments: [{ name: 'neuron.png', data: png.toString('base64') }, { name: 'notes.txt', data: Buffer.from('Dendrites in, axon out.\n').toString('base64') }],
      },
    });
    expect(asked.status()).toBe(201);

    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const files = page.getByTestId('question-card').filter({ hasText: text }).first().getByTestId('attachments');
    const picture = files.getByRole('img', { name: 'neuron.png' });
    await expect(picture).toBeVisible();
    await expect.poll(() => picture.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1); // its bytes came through the proxy
    await expect(files.getByRole('link', { name: /notes\.txt/ })).toHaveAttribute('href', /^\/api\/attachments\/\w+$/);
  });

  test('pages through many topics by cursor as the user scrolls', async ({ page, context }) => {
    // seed:dev creates 27 topics (3 recent + 24 older), more than one 20-item page, and an admin sees them all.
    await signIn(context, S.users.ada);
    await page.goto('/learn');
    const cards = page.getByTestId('question-card');
    await expect(cards).toHaveCount(20);
    await expect(page.getByTestId('load-more')).toBeVisible();
    await page.getByTestId('load-more').scrollIntoViewIfNeeded();
    await expect.poll(async () => cards.count(), { timeout: 10_000 }).toBeGreaterThan(20);
    await expect(page.getByText('End of list')).toBeVisible({ timeout: 10_000 });
  });

  test('logout clears the session', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    await expect(page.getByText('@Ben')).toBeVisible();
    await page.getByRole('button', { name: 'LOGOUT' }).click();
    await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible();
    expect((await context.cookies()).some((c) => c.name === 'popsmosis_session')).toBe(false);
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
    await page.getByPlaceholder('agent name, e.g. popsmosis-teacher').fill(name);
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page.getByText('API KEY — SHOWN ONCE')).toBeVisible();
    const key = (await page.locator('code.select-all').textContent())!.trim();
    expect(key).toMatch(/^sk_pop_[0-9a-f]{64}$/);

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

// The API is a separate Render service that spins down when idle and goes
// unroutable for a moment mid-deploy. Render answers those windows itself with
// an HTML error page, which must never be read as "this user is signed out".
test.describe('studio settings', () => {
  const asAda = () => ({ cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' });

  test('an admin picks who paints each brain\'s pictures, and the teacher\'s next claim carries it', async ({ page, context, request }) => {
    // From the defaults, whatever an earlier run left.
    await request.put(apiUrl('/api/studio/settings'), {
      headers: asAda(),
      data: {
        brain: { default: 'claude', effort: 'high' }, song: { provider: 'elevenlabs' }, art: { claude: 'gemini', meta: 'muse' },
        spend: { topicUsd: 5 },
      },
    });
    await signIn(context, S.users.ada);
    await page.goto('/admin/studio');
    const claude = page.getByTestId('art-claude');
    const meta = page.getByTestId('art-meta');
    await expect(claude.getByRole('radio', { name: /Gemini 3.1 Flash Image/ })).toHaveAttribute('aria-checked', 'true');
    await expect(meta.getByRole('radio', { name: /Muse Image/ })).toHaveAttribute('aria-checked', 'true');
    await expect(meta.getByRole('radio', { name: /Claude draws it/ })).toHaveCount(0); // the SVG art is Claude's only

    await claude.getByRole('radio', { name: /Muse Image/ }).click();
    await expect(claude.getByRole('radio', { name: /Muse Image/ })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByTestId('studio-updated')).toContainText('Last changed');
    await page.reload();
    await expect(page.getByTestId('art-claude').getByRole('radio', { name: /Muse Image/ })).toHaveAttribute('aria-checked', 'true');

    // The studio reads it from its claim of a topic.
    const text = `E2E ${Date.now()}: studio settings`;
    const asked = await request.post(apiUrl('/api/questions'), { headers: asAda(), data: { content: text } });
    const claimed = await agent(request, S.POPSMOSIS_API_KEY).claim((await asked.json()).id);
    expect((await claimed.json()).studio).toEqual({
      brain: { default: 'claude', effort: 'high' }, song: { provider: 'elevenlabs' }, art: { claude: 'muse', meta: 'muse' }, knobs: {},
      spend: { topicUsd: 5 },
    });
    expect((await request.delete(apiUrl(`/api/questions/${(await asked.json()).id}`), { headers: asAda() })).status()).toBe(204);
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { art: { claude: 'gemini' } } });
  });

  test('an admin sets the default brain and Claude\'s effort, and the teacher reads them with its key', async ({ page, context, request }) => {
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { brain: { default: 'claude', effort: 'high' } } });
    await signIn(context, S.users.ada);
    await page.goto('/admin/studio');
    const brains = page.getByTestId('brain-default');
    await expect(brains.getByRole('radio', { name: /Claude Opus 5.5/ })).toHaveAttribute('aria-checked', 'true');
    await brains.getByRole('radio', { name: /Muse Spark 1.3/ }).click();
    await expect(brains.getByRole('radio', { name: /Muse Spark 1.3/ })).toHaveAttribute('aria-checked', 'true');
    await page.getByTestId('brain-effort').getByRole('radio', { name: 'medium' }).click();
    await expect(page.getByTestId('brain-effort').getByRole('radio', { name: 'medium' })).toHaveAttribute('aria-checked', 'true');

    const teacher = await request.get(apiUrl('/api/studio/settings'), { headers: { Authorization: `Bearer ${S.POPSMOSIS_API_KEY}` } });
    expect((await teacher.json()).brain).toEqual({ default: 'muse-spark', effort: 'medium' });

    // The LEARN page's Default button says which brain it is now.
    await page.goto('/learn');
    await page.getByTestId('brain-picker').getByRole('radio', { name: 'Default' }).click();
    await expect(page.getByTestId('brain-note')).toContainText('Now: Muse Spark 1.3.');
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { brain: { default: 'claude', effort: 'high' } } });
  });

  test('an admin picks who sings the song, and the teacher reads it with its key', async ({ page, context, request }) => {
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { song: { provider: 'elevenlabs' } } });
    await signIn(context, S.users.ada);
    await page.goto('/admin/studio');
    const song = page.getByTestId('song');
    await expect(song.getByRole('radio', { name: /ElevenLabs Eleven Music/ })).toHaveAttribute('aria-checked', 'true');
    await song.getByRole('radio', { name: /Gemini Lyria 3.5/ }).click();
    await expect(song.getByRole('radio', { name: /Gemini Lyria 3.5/ })).toHaveAttribute('aria-checked', 'true');
    const teacher = await request.get(apiUrl('/api/studio/settings'), { headers: { Authorization: `Bearer ${S.POPSMOSIS_API_KEY}` } });
    expect((await teacher.json()).song).toEqual({ provider: 'gemini' });
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { song: { provider: 'elevenlabs' } } });
  });

  test('an admin sets a knob instead of its environment variable: the API\'s at once, the studio\'s for the teacher', async ({ page, context, request }) => {
    const knobs = { POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS: null, STUDIO_VIDEOS: null };
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { knobs } }); // none set, whatever an earlier run left
    await signIn(context, S.users.ada);
    await page.goto('/admin/studio');
    const cooldown = page.getByTestId('knob-POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS');
    await expect(cooldown.getByTestId('knob-source')).toHaveText('Default');
    await expect(cooldown.getByTestId('knob-facts')).toContainText('in force: 300 seconds');

    // A value it does not take is never sent.
    await cooldown.getByRole('textbox').fill('9000');
    await expect(cooldown.getByRole('alert')).toHaveText('Takes a whole number from 0 to 3600.');
    await expect(cooldown.getByRole('button', { name: 'Save' })).toBeDisabled();
    await cooldown.getByRole('textbox').fill('120');
    await cooldown.getByRole('textbox').press('Enter');
    await expect(page.getByText('Cooldown between wake-ups: 120 seconds from now on.')).toBeVisible();
    await expect(cooldown.getByTestId('knob-source')).toHaveText('Set here');
    await expect(cooldown.getByTestId('knob-facts')).toContainText('in force: 120 seconds');
    await page.reload();
    await expect(page.getByTestId('knob-POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS').getByRole('textbox')).toHaveValue('120');

    // The studio's: a choice saves on a click, and the teacher reads it with its key; never the API's own.
    const videos = page.getByTestId('knob-STUDIO_VIDEOS');
    await expect(videos.getByTestId('knob-source')).toHaveText('Not set here');
    await expect(videos.getByRole('radio', { checked: true })).toHaveCount(0);
    await videos.getByRole('radio', { name: 'off' }).click();
    await expect(videos.getByRole('radio', { name: 'off' })).toHaveAttribute('aria-checked', 'true');
    const teacher = await request.get(apiUrl('/api/studio/settings'), { headers: { Authorization: `Bearer ${S.POPSMOSIS_API_KEY}` } });
    expect((await teacher.json()).knobs).toEqual({ STUDIO_VIDEOS: 'off' });

    // Cleared, each is the environment's again.
    await page.getByTestId('knob-POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS').getByRole('button', { name: 'Clear' }).click();
    await expect(page.getByTestId('knob-POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS').getByTestId('knob-source')).toHaveText('Default');
    await expect(page.getByTestId('knob-POPSMOSIS_ROUTINE_DEBOUNCE_SECONDS').getByRole('textbox')).toHaveValue('');
    await videos.getByRole('button', { name: 'Clear' }).click();
    await expect(videos.getByTestId('knob-source')).toHaveText('Not set here');
    const after = await request.get(apiUrl('/api/studio/settings'), { headers: { Authorization: `Bearer ${S.POPSMOSIS_API_KEY}` } });
    expect((await after.json()).knobs).toEqual({});
  });

  test('an admin sets the per-topic spend ceiling, and the teacher reads it with its key', async ({ page, context, request }) => {
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { spend: { topicUsd: 5 } } });
    await signIn(context, S.users.ada);
    await page.goto('/admin/studio');
    const spend = page.getByTestId('spend');
    await expect(spend.getByTestId('spend-now')).toContainText('Now: $5.00.');
    await spend.getByLabel('Dollars a topic:').fill('2.5');
    await spend.getByRole('button', { name: 'Save' }).click();
    await expect(spend.getByTestId('spend-now')).toContainText('Now: $2.50.');
    const teacher = await request.get(apiUrl('/api/studio/settings'), { headers: { Authorization: `Bearer ${S.POPSMOSIS_API_KEY}` } });
    expect((await teacher.json()).spend).toEqual({ topicUsd: 2.5 });
    // Out of range is refused on the page, and nothing is saved.
    await spend.getByLabel('Dollars a topic:').fill('500');
    await spend.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText(/from 0 \(no ceiling\) to 100/)).toBeVisible();
    await page.reload();
    await expect(page.getByTestId('spend').getByTestId('spend-now')).toContainText('Now: $2.50.');
    await request.put(apiUrl('/api/studio/settings'), { headers: asAda(), data: { spend: { topicUsd: 5 } } });
  });

  test('is for admins only', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/admin/studio');
    await expect(page.getByText('Admin access required.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'STUDIO' })).toHaveCount(0);
  });
});

test.describe('API unavailable', () => {
  const renderErrorPage = { status: 502, contentType: 'text/html; charset=utf-8', body: '<!DOCTYPE html><html><body>502</body></html>' };

  test('a signed-in user sees the outage, not the sign-in card', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.route('**/api/auth/me', (route) => route.fulfill(renderErrorPage));

    await page.goto('/learn');
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

    await page.goto('/learn');
    await expect(page.getByText('@Ben')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('server-unavailable')).toHaveCount(0);
  });
});
