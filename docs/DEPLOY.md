# Deploying popsmosis: Supabase + Google + Render

Same shape as sparks: Postgres on **Supabase**, sign-in with **Google**, the API
(`popsmosis-api`) and the web app (`popsmosis-web`) on **Render**. `render.yaml` at the repo
root describes the two Render services as a Blueprint. Browsers only talk to
`popsmosis-web`, which proxies `/api/*` to `popsmosis-api` server-side. Agents talk to
`popsmosis-api` directly with their key.

## Every environment variable

**popsmosis-api**

| Key | Value | Where it comes from |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres` | Supabase > Project Settings > Database > Connection string > **Session pooler** |
| `DIRECT_URL` | optional, same as above | only if you want migrations on a different URL |
| `JWT_SECRET` | generated | Render generates it from the blueprint |
| `PROXY_SECRET` | generated | Render generates it; the web service copies it |
| `GOOGLE_CLIENT_ID` | `…apps.googleusercontent.com` | Google Cloud > APIs & Services > Credentials |
| `GOOGLE_CLIENT_SECRET` | `GOCSPX-…` | same |
| `GOOGLE_REDIRECT_URI` | `https://popsmosis-web.onrender.com/auth/callback` | your web hostname + `/auth/callback` |
| `ALLOWED_ORIGINS` | `https://popsmosis-web.onrender.com` | your web origin |
| `API_BASE_URL` | `https://popsmosis-api.onrender.com` | your API hostname; goes into the downloaded skill file |
| `ALLOWED_EMAIL_DOMAINS` | `yourcompany.com` | optional: these domains (and subdomains) are approved on sign-in without waiting; comma-separated |
| `ALLOWED_EMAILS` | optional | exact addresses approved on sign-in without waiting (a contractor on gmail) |
| `ADMIN_EMAILS` | `you@yourcompany.com` | Google emails that get the admin role (and approval) on sign-in. **Set this**: somebody has to approve everyone else |
| `NODE_ENV` | `production` | set by the blueprint |
| `PORT` | `3001` | set by the blueprint |

**popsmosis-web**

| Key | Value | Where it comes from |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://popsmosis-api:3001` | Render private network (fastest); `https://popsmosis-api.onrender.com` also works |
| `NEXT_PUBLIC_SITE_URL` | `https://popsmosis-web.onrender.com` | your web origin |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | same client ID as the API | Google Cloud |
| `NEXT_PUBLIC_GOOGLE_REDIRECT_URI` | `https://popsmosis-web.onrender.com/auth/callback` | must equal `GOOGLE_REDIRECT_URI` |
| `PROXY_SECRET` | copied from popsmosis-api | the blueprint wires it with `fromService` |
| `NODE_ENV` | `production` | set by the blueprint |

Optional API tuning: `DATABASE_POOL_SIZE` (default 5), `QUESTIONS_PER_USER_PER_DAY`
(default 10), `REPLIES_PER_USER_PER_HOUR` (default 30), `ATTACHMENT_RETENTION_DAYS` (default
30; how long the files sent with messages keep their bytes in Postgres, 0 for good).

Nothing else. There is no Supabase Auth, Storage, or Redis in popsmosis; Supabase is
only the Postgres.

## 0. Before you start

- A Supabase project. Copy the **Session pooler** connection string (port 5432 on
  the `pooler.supabase.com` host, user `postgres.<ref>`). Do not use the direct
  `db.<ref>.supabase.co` host: it is IPv6-only and Render cannot reach it. The
  transaction pooler (port 6543) also works, but session mode is the right one for
  a long-running server.
- A Google Cloud project with an OAuth 2.0 **Web application** client. You will add
  the production redirect URI in step 3, once you know the web hostname.
- The repo pushed to GitHub with `main` containing this code.

## 1. Create the Blueprint

1. Render dashboard > **New** > **Blueprint** > connect `littlepaprikalab/popsmosis`.
2. Render reads `render.yaml` and shows the two services. Every env var marked
   `sync: false` is prompted for; fill them from the table above. The hostnames are
   what Render will assign; if you want different ones, change the `name` fields in
   `render.yaml` before you apply — renaming a service afterwards means editing it
   in both places, and a name that no longer matches the live resource makes the
   next sync create a duplicate (see below).
   `JWT_SECRET` and `PROXY_SECRET` are generated, and the web service copies
   `PROXY_SECRET` from the API automatically.

3. **Apply**. Render builds both services, runs `prisma migrate deploy` against
   Supabase at the end of the API build, and starts them. The API's health check is
   `/api/health`.

### If the services already exist (repo moves and renames)

`render.yaml` declares no `plan:`, so a sync retains whatever compute plan each
service is already on and never downgrades one you upgraded in the dashboard. A
resource created fresh from this file gets Render's default instead — `0.5c-512mb`,
the paid Starter compute, not free.

Two rules matter when the repo moves to a new owner or name:

- **Never rename a service in `render.yaml`.** The `name` is how Render finds the
  live resource. Change it and the sync leaves the paid service behind and creates a
  new one (on a fresh Blueprint, Render even appends a suffix to avoid the collision).
- **Point the existing Blueprint at the new repo** rather than creating a second one.
  A resource must belong to exactly one Blueprint; two is unpredictable. If you
  already created the second Blueprint, remove the resources from its file (or
  disconnect it) and then delete the stray services — deleting a Blueprint never
  deletes resources, so an undeleted service is recreated on the next sync.

Adopting an existing service this way does not disturb its secrets: `generateValue`
only fills a variable that does not exist yet, and `sync: false` variables are
ignored entirely on an update. `JWT_SECRET` and `PROXY_SECRET` survive, so nobody is
signed out and no agent key stops working.

> Free plan note: free web services spin down after 15 minutes idle and take ~30s to
> wake, and they redeploy with a gap rather than swapping in a new instance first.
> While `popsmosis-api` is in either window Render answers `/api/*` itself, with a 502 and
> an HTML error page; the web app retries those and shows "server unavailable" rather
> than signing anyone out, but the requests still fail. Move it off free before you
> rely on it — since `render.yaml` sets no `plan`, upgrading in the dashboard sticks.
> Supabase's free tier pauses projects after a week of inactivity; a running API keeps
> it awake.

## 2. Verify the API

```
curl https://popsmosis-api.onrender.com/api/health
# {"status":"ok",...}

curl -i https://popsmosis-api.onrender.com/api/questions
# 403 {"error":{"code":"FORBIDDEN","message":"Direct API access not allowed"}}  <- the proxy gate is on
```

## 3. Finish Google OAuth

In Google Cloud > APIs & Services > Credentials > your OAuth client, add:

- Authorized JavaScript origin: `https://popsmosis-web.onrender.com`
- Authorized redirect URI: `https://popsmosis-web.onrender.com/auth/callback`

Then open `https://popsmosis-web.onrender.com`, sign in. If your email is in
`ADMIN_EMAILS`, **AGENTS** and **USERS** appear in the nav.

To make someone an admin without `ADMIN_EMAILS`, after they have signed in once,
either use the USERS page as an existing admin, run
`npm --workspace api run user:promote -- --email them@example.com`, or in the
Supabase SQL editor:

```sql
UPDATE users SET role = 'ADMIN', status = 'APPROVED' WHERE email = 'them@example.com';
```

Set both columns: the role says what they may do, `status` says whether they get
past the waiting page at all. (Granting only the role no longer strands them —
the API approves an admin it finds waiting — but writing both keeps the USERS
page honest immediately.)

The API caches user lookups for 30 seconds, so the change lands within half a
minute without a new sign-in.

## 4. Create the agent and get its skill file

1. `/admin/agents` > create `popsmosis-teacher`. Copy the key; it is shown once.
2. Click **Skill file**. The download has `API_BASE_URL` substituted, so it points at
   `https://popsmosis-api.onrender.com`.
3. Hand the key and the file to your runner. `docs/agent-runbook.md` covers the
   options; the simplest is a Claude Code routine on this repo with the prompt
   `Run one popsmosis heartbeat following docs/popsmosis-agent-skill.md; the API base URL is https://popsmosis-api.onrender.com`
   and `POPSMOSIS_API_KEY` in the routine environment's variables. On the free plan the API
   instance sleeps, so the first request of a heartbeat may take ~30s; the skill file
   tolerates that.
4. Optional, and worth doing: give that routine an **API trigger** and set
   `POPSMOSIS_ROUTINE_FIRE_URL` and `POPSMOSIS_ROUTINE_FIRE_TOKEN` on `popsmosis-api`. The API then
   wakes the routine the moment someone asks a question or replies, so the cron is the
   sweep rather than the latency budget. See
   [Wake the routine on write](agent-runbook.md#wake-the-routine-on-write).

## 5. Custom domain (optional)

Add the domain to `popsmosis-web` under Settings > Custom Domains, then update
`ALLOWED_ORIGINS`, `GOOGLE_REDIRECT_URI`, `NEXT_PUBLIC_SITE_URL`,
`NEXT_PUBLIC_GOOGLE_REDIRECT_URI`, and the Google Cloud redirect URI to the new host.
If you also put the API on a custom domain, update `API_BASE_URL` and re-download the
skill file.

## How it fits together

```
browser ──HTTPS──> popsmosis-web (Next.js, Render)
                     │  /api/* rewrite + x-proxy-token
                     ▼
agent  ──HTTPS──> popsmosis-api (Fastify, Render) ──TLS──> Supabase Postgres (session pooler)
   Authorization: Bearer sk_pop_...
```

- The session cookie is set on the web origin because the browser never leaves it.
- Personal access tokens (`pk_pop_...`) and agent keys (`sk_pop_...`) are
  exempt from the proxy gate: both are for callers with no browser in front of
  them, so they reach the API directly. Their shape is what exempts them; the
  route guard is what authenticates them.
- `PROXY_SECRET` makes the API refuse browser-style requests that did not come
  through the web app. Agent keys bypass that gate; `/api/health` is open.
- Migrations run at the end of the API build. On a paid instance, move
  `npm --workspace api run db:deploy` from `buildCommand` to a `preDeployCommand`
  in `render.yaml`; Render then runs it against the live database right before the
  swap, and a failed migration blocks the deploy instead of the build.

## Redeploys and migrations

Push to `main`; Render auto-deploys both services. Schema changes ship as new files in
`prisma/migrations/` (create them locally with `npm --workspace api run db:migrate`)
and are applied during the build.

## Troubleshooting

| Symptom | Cause |
|---|---|
| A 502 on `/api/auth/me` right after signing in, ~160KB of HTML in the response | the API service was unroutable for that moment — spun down on the free plan, or mid-deploy — so Render answered with its own error page (`x-render-routing: no-deploy`) instead of the API's JSON. The web app rides out and reports these windows rather than treating them as a signed-out user; moving `popsmosis-api` off the free plan is what removes them, since free services have no zero-downtime deploy and spin down when idle. `render.yaml` declares no `plan`, so upgrading in the dashboard is enough and a sync will not undo it |
| `403 Direct API access not allowed` in the browser | `PROXY_SECRET` differs between the two services, or `NEXT_PUBLIC_API_URL` points somewhere else |
| Sign-in bounces with `redirect_uri_mismatch` | Google Cloud redirect URI does not exactly equal `GOOGLE_REDIRECT_URI` |
| Sign-in works but shows "Waiting for approval" | expected for anyone outside `ALLOWED_EMAIL_DOMAINS` / `ALLOWED_EMAILS` / `ADMIN_EMAILS`: an admin approves them on `/admin/users` (the USERS link shows a count). Approval lands within 30s on other instances, immediately on the one that handled the click |
| Nobody can approve anyone | put your address in `ADMIN_EMAILS` and redeploy, or run `npm --workspace api run user:promote -- --email you@…`, which approves as well. An open waiting page picks the change up on its own within 30s; no new sign-in needed |
| `… is listed in ADMIN_EMAILS` when removing an admin | that address is an admin by configuration and the API reapplies it on every request, so the demotion is refused rather than reverted a moment later. Take the address out of `ADMIN_EMAILS`, redeploy, then demote on the USERS page |
| Someone made an admin is still on "Waiting for approval" | their `status` is still `PENDING`. The API repairs this the next time they load a page (within 30s); if it persists, the API did not restart after the config change, or the write is failing — check the API log for `Could not persist the access fix` |
| Sign-in works but CORS errors appear | `ALLOWED_ORIGINS` does not include the web origin (only matters if the browser hits the API directly) |
| Agent gets `401` | wrong key; `403` means the agent is disabled on `/admin/agents` |
| Build fails in `prisma generate` | the `postinstall` step needs the schema at `prisma/schema.prisma`; do not move it |
| `Cannot find module 'typescript'` / `Failed to load next.config.ts` in the build | the install dropped devDependencies. `NODE_ENV=production` makes `npm ci` skip them, and both builds need them (`tsc`, `typescript`, `tailwindcss`). The blueprint uses `npm ci --include=dev`; if you overrode the build command in the Render dashboard, add that flag back |
| `P1001: Can't reach database server at db.<ref>.supabase.co:5432` | `DATABASE_URL` is the **direct** connection string, which is IPv6-only and unreachable from Render. Replace it with the **Session pooler** string: host `aws-0-<region>.pooler.supabase.com`, port `5432`, user `postgres.<project-ref>`. Supabase > Project Settings > Database > Connection string > Session pooler. Update it in the Render dashboard (Environment) and redeploy — it is a `sync: false` var, so editing `render.yaml` will not change it |
| `password authentication failed` | the pooler user is `postgres.<project-ref>`, not `postgres` |
