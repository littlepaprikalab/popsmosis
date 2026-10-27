import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { seed, signIn, agent, apiUrl, sessionToken } from './helpers';

const S = seed();
// The seed's sample clips, as the absolute URLs the video endpoint takes.
const BASE = process.env.E2E_BASE_URL || 'http://localhost:3000';
const SONG = `${BASE}/samples/song.mp4`;
const LESSON = `${BASE}/samples/lesson.mp4`;

test.describe('the Droplets tab, signed out', () => {
  test('lists the finished droplets and plays one, with no sign-in', async ({ page }) => {
    await page.goto('/learn');
    await page.getByRole('link', { name: 'DROPLETS', exact: true }).click();
    await expect(page).toHaveURL(/\/droplets$/);
    await expect(page.getByRole('link', { name: 'SIGN IN', exact: true })).toBeVisible();

    const card = page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' }).first();
    await expect(card).toBeVisible();
    await expect(card.getByText('AP Biology', { exact: true })).toBeVisible();
    // Never who asked, or their words: the title is the bundle's.
    await expect(page.getByText('Ben')).toHaveCount(0);
    await expect(page.getByText('how do cells actually make ATP')).toHaveCount(0);

    await card.click();
    await expect(page).toHaveURL(new RegExp(`/droplets/${S.droplet}$`));
    await expect(page.getByRole('heading', { name: 'ATP synthesis' })).toBeVisible();
    await expect(page.locator('.md strong', { hasText: 'gradient' })).toBeVisible();
    await expect(page.getByTestId('lyrics').getByText('Spin the rotor, make the ATP')).toBeVisible();
    // The follow-up thread stays on /learn.
    await expect(page.getByText('water wheel')).toHaveCount(0);

    const song = page.getByLabel('song video');
    await expect(song).toHaveAttribute('poster', '/samples/song.jpg');
    await expect(page.getByLabel('lesson video')).toBeVisible();
    // It plays: the CSP lets media through and the file is a browser-playable MP4.
    const played = await song.evaluate(async (v: HTMLVideoElement) => {
      v.muted = true;
      await v.play();
      await new Promise((r) => setTimeout(r, 700));
      return v.currentTime;
    });
    expect(played).toBeGreaterThan(0);
  });

  test('a droplet not on the tab is a 404 page, and the API needs no cookie', async ({ page, request }) => {
    await page.goto('/droplets/not-a-droplet');
    await expect(page.getByText('This droplet is not on the tab')).toBeVisible();
    const list = await request.get(apiUrl('/api/droplets'));
    expect(list.status()).toBe(200);
    expect(JSON.stringify(await list.json())).not.toMatch(/Ben|ben@example|how do cells/);
  });
});

test.describe('videos on /learn', () => {
  test('a lesson shows its videos rendering, then the players, without a reload; an admin can hide it from the tab', async ({ page, context, request }) => {
    // Ada asks, the teacher answers and starts the render, all through the API.
    const text = `E2E ${Date.now()}: the Calvin cycle`;
    const asked = await request.post(apiUrl('/api/questions'), {
      headers: { cookie: `popsmosis_session=${sessionToken(S.users.ada)}`, 'x-requested-with': 'XMLHttpRequest' },
      data: { content: text },
    });
    expect(asked.status()).toBe(201);
    const bot = agent(request, S.POPSMOSIS_API_KEY);
    const questionId = (await asked.json()).id;
    expect((await bot.claim(questionId)).status()).toBe(200);
    const answer = await bot.answer(questionId, 'The Calvin cycle **fixes carbon** with the ATP the light reactions made.');
    expect(answer.status()).toBe(201);
    const answerId = (await answer.json()).id;
    expect((await bot.video(answerId, { status: 'RENDERING' })).status()).toBe(200);

    await signIn(context, S.users.ada);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: text }).first();
    await expect(card.getByTestId('videos-rendering')).toBeVisible();

    // Done: the card swaps in the players on its own poll.
    expect((await bot.video(answerId, { status: 'READY', songVideoUrl: SONG, lessonVideoUrl: LESSON })).status()).toBe(200);
    await expect(card.getByTestId('droplet-players')).toBeVisible({ timeout: 25_000 });
    await expect(card.getByTestId('videos-rendering')).toHaveCount(0);
    await expect(card.getByLabel('song video')).toHaveAttribute('src', SONG);

    // It is on the public tab, until Ada (an admin) hides it.
    const onTab = async () => (await (await request.get(apiUrl('/api/droplets'))).json()).data.some((d: { id: string }) => d.id === answerId);
    expect(await onTab()).toBe(true);
    await card.getByTestId('toggle-hidden').click();
    await expect(card.getByText('Hidden from the Droplets tab.')).toBeVisible();
    expect(await onTab()).toBe(false);
    await card.getByTestId('toggle-hidden').click();
    await expect(card.getByRole('link', { name: 'On the public Droplets tab' })).toBeVisible();
    expect(await onTab()).toBe(true);
  });

  test('a finished droplet carries its bill of materials: each step, its models, time and cost', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const card = page.getByTestId('question-card').filter({ hasText: 'how do cells actually make ATP' }).first();
    const bill = card.getByTestId('breakdown');
    await bill.scrollIntoViewIfNeeded();
    // Folded to its totals: the lesson's 4.6 min and the videos' 6.3, and what was spent.
    await expect(bill.getByTestId('breakdown-total')).toHaveText('10.9 min · $0.80 + 3,214 cr');
    await expect(bill.locator('table')).toBeHidden();

    await bill.locator('summary').click();
    await expect(bill.getByTestId('breakdown-step')).toHaveCount(10);
    const audio = bill.getByTestId('breakdown-step').filter({ hasText: 'Song and voice' });
    await expect(audio).toContainText('lyria-3.5');
    await expect(audio).toContainText('$0.08');
    const table = bill.locator('table'); // the phone's copy of the bill is hidden at this width
    await expect(table.getByText('eleven_multilingual_v2')).toBeVisible();
    await expect(table.getByText('3,214 cr', { exact: true })).toBeVisible();

    // The public tab shows the droplet, never its bill.
    await page.goto(`/droplets/${S.droplet}`);
    await expect(page.getByRole('heading', { name: 'ATP synthesis' })).toBeVisible();
    await expect(page.getByTestId('breakdown')).toHaveCount(0);
  });

  test('a failed render says why under the lesson', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto('/learn');
    const failed = page.getByTestId('videos-failed').first();
    await failed.scrollIntoViewIfNeeded();
    await expect(failed).toContainText('render: ffmpeg exited 1 on the song.');
  });
});

test.describe('likes and comments on the Droplets tab', () => {
  const cookieOf = (userId: string) => ({ cookie: `popsmosis_session=${sessionToken(userId)}` });
  const asWriter = (userId: string) => ({ ...cookieOf(userId), 'x-requested-with': 'XMLHttpRequest' });
  // The droplet as the API tells this person, so a test checks what landed, not only what the page shows.
  const seenBy = async (request: APIRequestContext, userId: string) =>
    (await request.get(apiUrl(`/api/droplets/${S.droplet}`), { headers: cookieOf(userId) })).json() as Promise<{ liked: boolean; likeCount: number }>;
  // Start from no like by this person, whatever an earlier try of the test left.
  const unlikeFirst = async (request: APIRequestContext, userId: string) => {
    expect((await request.delete(apiUrl(`/api/droplets/${S.droplet}/like`), { headers: asWriter(userId) })).status()).toBe(200);
    return (await seenBy(request, userId)).likeCount;
  };
  const atpCard = (page: Page) => page.getByTestId('droplet-card').filter({ hasText: 'ATP synthesis' }).first();

  test('signed out: the counts and comments show, and adding to them asks for a sign-in', async ({ page }) => {
    await page.goto('/droplets');
    const card = atpCard(page);
    await expect(card.getByTestId('like-signin')).toHaveText(/\b1\b/); // Ada's
    await expect(card.getByTestId('like-button')).toHaveCount(0);
    await expect(card.getByTestId('card-comments')).toHaveText(/\b1\b/);

    await card.getByTestId('card-comments').click();
    await expect(page).toHaveURL(new RegExp(`/droplets/${S.droplet}#comments$`));
    const comments = page.getByTestId('droplet-comments');
    await expect(comments).toBeInViewport();
    await expect(comments.getByTestId('comment')).toHaveCount(1);
    await expect(comments.getByText('The chorus is the only way')).toBeVisible();
    await expect(comments.getByText('Ada', { exact: true })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toHaveCount(0);
    await expect(page.getByTestId('comment-signin')).toHaveAttribute('href', `/auth/callback?returnTo=${encodeURIComponent(`/droplets/${S.droplet}`)}`);
    // Ben asked for this droplet; nothing here says so.
    await expect(page.getByText('Ben')).toHaveCount(0);
  });

  test('signed in: a thumbs-up lands, holds across a reload and across pages, and comes back off', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    await page.goto('/droplets');
    const like = atpCard(page).getByTestId('like-button');
    await expect(like).toHaveAttribute('aria-pressed', 'false');
    await expect(like.getByTestId('like-count')).toHaveText(String(before));

    await like.click();
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await expect(like.getByTestId('like-count')).toHaveText(String(before + 1));
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: true, likeCount: before + 1 }));

    await page.reload();
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await atpCard(page).getByRole('heading', { name: 'ATP synthesis' }).click();
    await expect(page).toHaveURL(new RegExp(`/droplets/${S.droplet}$`));
    const onPage = page.getByTestId('droplet-engagement').getByTestId('like-button');
    await expect(onPage).toHaveAttribute('aria-pressed', 'true');
    await expect(onPage.getByTestId('like-count')).toHaveText(String(before + 1));

    await onPage.click();
    await expect(onPage).toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));

    // Back on the list, the card says what the droplet page just did, not what the list said before.
    await page.goBack();
    await expect(page).toHaveURL(/\/droplets$/);
    await expect(like).toHaveAttribute('aria-pressed', 'false');
    await expect(like.getByTestId('like-count')).toHaveText(String(before));
  });

  test('a droplet page is one request, comments and all', async ({ page }) => {
    const calls: string[] = [];
    page.on('request', (r) => { const path = new URL(r.url()).pathname; if (path.startsWith('/api/droplets')) calls.push(`${r.method()} ${path}`); });
    await page.goto(`/droplets/${S.droplet}`);
    await expect(page.getByTestId('comment').first()).toBeVisible();
    expect(calls).toEqual([`GET /api/droplets/${S.droplet}`]);
  });

  test('a like sent into a gateway blip is sent again and lands', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    let sent = 0;
    // Render answers a deploy's swap window itself, with an HTML 502.
    await page.route('**/api/droplets/*/like', (route) => {
      sent += 1;
      return sent === 1 ? route.fulfill({ status: 502, contentType: 'text/html', body: '<html>502</html>' }) : route.continue();
    });
    await page.goto(`/droplets/${S.droplet}`);
    const like = page.getByTestId('droplet-engagement').getByTestId('like-button');
    await like.click();
    await expect.poll(() => seenBy(request, S.users.ben), { timeout: 15_000 }).toEqual(expect.objectContaining({ liked: true, likeCount: before + 1 }));
    expect(sent).toBe(2);
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await page.unroute('**/api/droplets/*/like');
    await like.click();
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));
  });

  test('signed out in another tab: a click goes to sign in, records nothing, and an unsent comment waits for the return', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    await page.goto(`/droplets/${S.droplet}`);
    const like = page.getByTestId('droplet-engagement').getByTestId('like-button');
    await expect(like).toBeVisible(); // signed in, for now
    const draft = `half-written ${Date.now()}: the gradient part`;
    await page.getByRole('textbox', { name: 'Add a comment' }).fill(draft);

    // Sessions slide (each request renews one past its halfway mark), so an open page does not
    // time out; what ends one under it is signing out somewhere else, which clears the cookie.
    await context.clearCookies();
    await like.click();
    await expect(page).toHaveURL(new RegExp(`/auth/callback\\?reason=session_expired&returnTo=${encodeURIComponent(`/droplets/${S.droplet}`)}`));
    expect(await seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));

    // Signed back in, the page they were on still has what they were writing.
    await signIn(context, S.users.ben);
    await page.goto(`/droplets/${S.droplet}`);
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toHaveValue(draft);
    await page.getByRole('textbox', { name: 'Add a comment' }).fill('');
  });

  test('clicking fast on a slow network ends where the last click left it, on the page and in the API', async ({ page, context, request }) => {
    const before = await unlikeFirst(request, S.users.ben);
    await signIn(context, S.users.ben);
    // Each like takes a while to answer, so the clicks pile up behind one in flight.
    let sent = 0;
    await page.route('**/api/droplets/*/like', async (route) => { sent += 1; await new Promise((r) => setTimeout(r, 400)); await route.continue(); });
    await page.goto(`/droplets/${S.droplet}`);
    const like = page.getByTestId('droplet-engagement').getByTestId('like-button');
    for (let i = 0; i < 5; i++) await like.click({ delay: 0 });
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await expect(like.getByTestId('like-count')).toHaveText(String(before + 1));
    await expect.poll(() => seenBy(request, S.users.ben), { timeout: 15_000 }).toEqual(expect.objectContaining({ liked: true, likeCount: before + 1 }));
    expect(sent).toBeLessThan(5); // the clicks in between were never sent: only where they ended up

    await page.unroute('**/api/droplets/*/like');
    await page.reload();
    await expect(like).toHaveAttribute('aria-pressed', 'true');
    await like.click();
    await expect.poll(() => seenBy(request, S.users.ben)).toEqual(expect.objectContaining({ liked: false, likeCount: before }));
  });

  test('a comment posts once even when its first answer is lost, holds across a reload, and its author deletes it', async ({ page, context, request }) => {
    await signIn(context, S.users.ada);
    await page.goto(`/droplets/${S.droplet}`);
    const section = page.getByTestId('droplet-comments');
    await expect(section.getByTestId('comment').first()).toBeVisible();
    const before = Number(await section.getByTestId('comment-count').textContent());
    const text = `E2E ${Date.now()}: the rap verse is where the gradient finally clicked`;

    // The first send reaches the API and its answer never comes back. The page sends it
    // again with the same Idempotency-Key, and gets the same comment rather than a second.
    let lost = 0;
    await page.route('**/api/droplets/*/comments', async (route) => {
      if (route.request().method() !== 'POST' || lost > 0) return route.continue();
      lost += 1;
      await route.fetch();
      return route.abort('connectionreset');
    });
    await page.getByRole('textbox', { name: 'Add a comment' }).fill(text);
    await section.getByRole('button', { name: 'Comment', exact: true }).click();
    const mine = section.getByTestId('comment').filter({ hasText: text });
    await expect(mine).toHaveCount(1);
    expect(lost).toBe(1);
    await expect(section.getByTestId('comment-count')).toHaveText(String(before + 1));
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toHaveValue('');
    const listed = await (await request.get(apiUrl(`/api/droplets/${S.droplet}/comments?limit=50`))).json();
    expect(listed.data.filter((c: { content: string }) => c.content === text)).toHaveLength(1);
    expect(listed.commentCount).toBe(before + 1);
    await page.unroute('**/api/droplets/*/comments');

    await page.reload();
    await expect(mine).toHaveCount(1);
    await expect(mine.getByText('you', { exact: true })).toBeVisible();
    // Ben may not delete it, and is not offered to.
    expect((await request.delete(apiUrl(`/api/droplets/${S.droplet}/comments/${listed.data.find((c: { content: string }) => c.content === text).id}`), { headers: asWriter(S.users.ben) })).status()).toBe(403);

    await mine.getByTestId('delete-comment').click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(mine).toHaveCount(0);
    await expect(section.getByTestId('comment-count')).toHaveText(String(before));
    await page.reload();
    await expect(section.getByTestId('comment')).toHaveCount(before);
  });

  test('markup in a comment is shown as text, and nothing in it runs', async ({ page, context }) => {
    await signIn(context, S.users.ada);
    await page.goto(`/droplets/${S.droplet}`);
    const payload = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script> **not bold** ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Add a comment' }).fill(payload);
    await page.getByTestId('droplet-comments').getByRole('button', { name: 'Comment', exact: true }).click();
    const row = page.getByTestId('comment').filter({ hasText: payload });
    await expect(row).toHaveCount(1);
    await page.reload();
    await expect(row).toHaveCount(1);
    await expect(row.locator('img, script, strong')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
    await row.getByTestId('delete-comment').click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(row).toHaveCount(0);
  });

  test('someone else’s comment offers no delete to a member who is not an admin', async ({ page, context }) => {
    await signIn(context, S.users.ben);
    await page.goto(`/droplets/${S.droplet}`);
    const adas = page.getByTestId('comment').filter({ hasText: 'The chorus is the only way' });
    await expect(adas).toBeVisible();
    await expect(adas.getByTestId('delete-comment')).toHaveCount(0);
    await expect(page.getByRole('textbox', { name: 'Add a comment' })).toBeVisible();
  });
});
