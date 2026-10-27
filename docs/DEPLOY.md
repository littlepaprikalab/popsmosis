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
| `DIRECT_URL` | optional: the Session pooler URI, as above (port 5432) | needed only when `DATABASE_URL` is the transaction pooler (port 6543). Migrations hang on that pooler, so they use this URL instead; the running API keeps using `DATABASE_URL`. Leave it empty otherwise |
| `JWT_SECRET` | generated | Render generates it from the blueprint |
| `PROXY_SECRET` | generated | Render generates it; the web service copies it |
| `GOOGLE_CLIENT_ID` | `…apps.googleusercontent.com` | Google Cloud > APIs & Services > Credentials |
| `GOOGLE_CLIENT_SECRET` | `GOCSPX-…` | same |
| `GOOGLE_REDIRECT_URI` | `https://popsmosis-web.onrender.com/auth/callback` | your web hostname + `/auth/callback` |
| `ALLOWED_ORIGINS` | `https://popsmosis-web.onrender.com` | your web origin |
| `API_BASE_URL` | `https://popsmosis-api.onrender.com` | your API hostname; goes into the downloaded skill file |
| `ADMIN_EMAILS` | `you@yourcompany.com` | Google emails that get the admin role on sign-in. **Set this**: somebody has to manage the agents. Anyone else with a Google account can sign in and use the site |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` | Supabase > Project Settings > API > Project URL. Where the studio uploads the droplet videos; see [Supabase Storage](#6-supabase-storage-the-droplet-videos). Unset, the upload endpoint answers 503 and no droplet gets videos |
| `SUPABASE_SERVICE_ROLE_KEY` | the `service_role` key | Supabase > Project Settings > API Keys. Signs each upload; it stays on the API and is never sent to the studio or a browser |
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
(default 10), `REPLIES_PER_USER_PER_HOUR` (default 30), `DROPLET_COMMENTS_PER_USER_PER_HOUR`
(default 30; comments on the Droplets tab), `ATTACHMENT_RETENTION_DAYS` (default
30; how long the files sent with messages keep their bytes in Postgres, 0 for good),
`VIDEO_STALE_MINUTES` (default 60; a render that has not reported for this long reads as
failed).

Nothing else. There is no Supabase Auth or Redis in popsmosis; Supabase is the Postgres
and, for the droplet videos, Storage.

## 0. Before you start

- A Supabase project. Copy the **Session pooler** connection string (port 5432 on
  the `pooler.supabase.com` host, user `postgres.<ref>`). Do not use the direct
  `db.<ref>.supabase.co` host: it is IPv6-only and Render cannot reach it. The
  transaction pooler (port 6543) works for the running API but not for migrations:
  `prisma migrate deploy` needs one Postgres session for its whole run, and that
  pooler swaps sessions between statements, so the build hangs. If you use it for
  `DATABASE_URL`, set `DIRECT_URL` to the session pooler string. Session mode is the
  right one for a long-running server anyway.
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
UPDATE users SET role = 'ADMIN' WHERE email = 'them@example.com';
```

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

## 6. Supabase Storage (the droplet videos)

The studio's last step uploads each droplet's files to Supabase Storage in the same
Supabase project as the Postgres (docs/PLAN.md, section 10). The API mints a signed URL
per file, so the studio never holds a Supabase key. Two buckets:

| Bucket | Public | What goes in it |
|---|---|---|
| `droplets` | yes | `<answerId>/a<n>/out/song_h.mp4`, `lesson_h.mp4` and a `.jpg` poster for each: what the Droplets tab and `/learn` play |
| `studio` | no | `<answerId>/a<n>/art/…`, `masks/…`, `locations.json`, `audio/…`, `timing/…`, `timeline_*.json`: kept so a renderer fix can re-render without paying for the song and the art again |

`a<n>` is the render attempt (`studio/videos.py`): each attempt writes its own files, so a
render that was taken over cannot overwrite the one that replaced it, and a re-render
gets new URLs. Earlier attempts' files stay; they are small next to the egress, and can
be deleted by hand.

1. **The project.** If there is none yet, create one at supabase.com > New project (pick a
   region near Render's `oregon`, such as West US) and keep the database password: the
   same project is the Postgres of section 0 and holds the buckets. One project is enough.
2. **Create the buckets.** Supabase > Storage > **New bucket**: `droplets` with **Public
   bucket** on, then `studio` with it off. The names must be exactly these. Or in the SQL
   editor:

   ```sql
   insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types) values
     ('droplets', 'droplets', true,  52428800, array['video/mp4', 'image/jpeg']),
     ('studio',   'studio',   false, 52428800, array['image/png', 'audio/mpeg', 'application/json']);
   ```

   50 MB is the most a file may be on Supabase's free plan, which a few minutes of 1080p at
   a modest bitrate stays under; on a paid plan raise the limit (Storage > Settings, then
   each bucket's own) if a video is refused. Leave the bucket policies alone: the signed
   URLs need none, and a public bucket is readable without one.
3. **Give the API the keys.** Supabase > Project Settings > API: copy the **Project URL** to
   `SUPABASE_URL`, and the **`service_role`** key (under API Keys; on newer projects, the
   legacy keys tab) to `SUPABASE_SERVICE_ROLE_KEY`, both on `popsmosis-api` only. The API
   sends the key as both `apikey` and a bearer token, which is what the `service_role` JWT
   takes; a newer `sb_secret_…` key has not been tried. On an existing service add the two
   by hand in Render > popsmosis-api > Environment: a Blueprint sync never adds a
   `sync: false` variable to a service that already exists. Redeploy the API.
4. **Check it.** With the agent key:

   ```bash
   curl -s -X POST https://popsmosis-api.onrender.com/api/answers/<an answer of this agent>/upload-url \
     -H "Authorization: Bearer sk_pop_..." -H 'Content-Type: application/json' \
     -d '{"bucket":"droplets","file":"out/check.jpg"}'
   # {"uploadUrl":"https://<ref>.supabase.co/storage/v1/object/upload/sign/droplets/...?token=...","publicUrl":"..."}
   ```

   A 503 means the two variables are not set on the running API; a 503 that says storage
   did not give an upload URL means Supabase refused: check the key and the bucket name.
5. **The routine's network policy** must allow the Supabase host (`<ref>.supabase.co`), since
   the studio uploads straight to it (docs/PLAN.md, section 9).

Nothing on `popsmosis-web` changes: the pages read the public URLs from the API, and its
Content-Security-Policy already allows `https:` media and images.

**Before launch:** the ElevenLabs plan has to allow publishing its songs publicly, and with
`SONG_PROVIDER=gemini` Google's terms for Lyria's songs (docs/PLAN.md, section 10, "Before launch").

## How it fits together

```
browser ──HTTPS──> popsmosis-web (Next.js, Render)
                     │  /api/* rewrite + x-proxy-token
                     ▼
agent  ──HTTPS──> popsmosis-api (Fastify, Render) ──TLS──> Supabase Postgres (session pooler)
   Authorization: Bearer sk_pop_...
```

- The session cookie is set on the web origin because the browser never leaves it.
- Agent keys (`sk_pop_...`) are exempt from the proxy gate: an agent has no
  browser in front of it, so it reaches the API directly. The key's shape is
  what exempts it; the route guard is what authenticates it.
- `PROXY_SECRET` makes the API refuse browser-style requests that did not come
  through the web app. Agent keys bypass that gate; `/api/health` is open.
- Migrations run at the end of the API build. On a paid instance, move
  `npm --workspace api run db:deploy` from `buildCommand` to a `preDeployCommand`
  in `render.yaml`; Render then runs it against the live database right before the
  swap, and a failed migration blocks the deploy instead of the build.

## Redeploys and migrations

Auto-deploy is off for both services (`autoDeployTrigger: "off"` in `render.yaml`), so a
push to `main` does not ship anything by itself. Deploy each service by hand: open it in
the Render dashboard and pick **Manual Deploy > Deploy latest commit**, or call its
deploy hook (Settings > Deploy Hook) from a script. Deploy `popsmosis-api` first when a
change needs a migration, so the schema is in place before the web app relies on it.
To turn auto-deploy back on, set `autoDeployTrigger: commit` (every push) or
`checksPass` (only once CI passes) and sync the Blueprint.

Schema changes ship as new files in `prisma/migrations/` (create them locally with
`npm --workspace api run db:migrate`) and are applied during the API build, through
`DIRECT_URL` when it is set and `DATABASE_URL` otherwise (`api/prisma.config.ts`). The
running API always uses `DATABASE_URL`. If that is the transaction pooler (port 6543), set
`DIRECT_URL` to the Session pooler string before deploying a migration, or the build hangs
(see Troubleshooting).

`20261029120000_droplets_tab` (the Droplets tab) adds three nullable columns to `answers`
(`hiddenAt` and the two poster URLs) and one index; it rewrites no rows and needs no
downtime. Deploy `popsmosis-api` before `popsmosis-web`, since the web app's new pages call
its new endpoints.

## Troubleshooting

| Symptom | Cause |
|---|---|
| A 502 on `/api/auth/me` right after signing in, ~160KB of HTML in the response | the API service was unroutable for that moment — spun down on the free plan, or mid-deploy — so Render answered with its own error page (`x-render-routing: no-deploy`) instead of the API's JSON. The web app rides out and reports these windows rather than treating them as a signed-out user; moving `popsmosis-api` off the free plan is what removes them, since free services have no zero-downtime deploy and spin down when idle. `render.yaml` declares no `plan`, so upgrading in the dashboard is enough and a sync will not undo it |
| `403 Direct API access not allowed` in the browser | `PROXY_SECRET` differs between the two services, or `NEXT_PUBLIC_API_URL` points somewhere else |
| Sign-in bounces with `redirect_uri_mismatch` | Google Cloud redirect URI does not exactly equal `GOOGLE_REDIRECT_URI` |
| Nobody is an admin | put your address in `ADMIN_EMAILS` and redeploy, or run `npm --workspace api run user:promote -- --email you@…`. An open session picks the change up on its own within 30s; no new sign-in needed. If it does not, check the API log for `Could not persist the admin role from ADMIN_EMAILS` |
| `… is listed in ADMIN_EMAILS` when removing an admin | that address is an admin by configuration and the API reapplies it on every request, so the demotion is refused rather than reverted a moment later. Take the address out of `ADMIN_EMAILS`, redeploy, then demote on the USERS page |
| Sign-in works but CORS errors appear | `ALLOWED_ORIGINS` does not include the web origin (only matters if the browser hits the API directly) |
| Agent gets `401` | wrong key; `403` means the agent is disabled on `/admin/agents` |
| Build fails in `prisma generate` | the `postinstall` step needs the schema at `prisma/schema.prisma`; do not move it |
| `Cannot find module 'typescript'` / `Failed to load next.config.ts` in the build | the install dropped devDependencies. `NODE_ENV=production` makes `npm ci` skip them, and both builds need them (`tsc`, `typescript`, `tailwindcss`). The blueprint uses `npm ci --include=dev`; if you overrode the build command in the Render dashboard, add that flag back |
| `Cannot find module '<package>'` in the API build or at API start, though it works locally | the API build installs only the `api` workspace and the root (`npm ci --workspace api --include-workspace-root`), so a package listed only in `web/package.json` is not there. Add it to `api/package.json` |
| The API build stops after `Datasource "db": … pooler.supabase.com:6543` and never finishes | the migration step is going through Supabase's **transaction** pooler (port 6543), which cannot keep the one session a migration needs. Set `DIRECT_URL` to the **Session pooler** string (same host, user and password, port `5432`, no `?pgbouncer=true`), cancel the stuck deploy and deploy again. The `Datasource` line should then end in `:5432` |
| `Timed out trying to acquire a postgres advisory lock (SELECT pg_advisory_lock(72707369))` in the API build | an earlier migration run that hung still holds Prisma's migration lock on a pooled connection. In the Supabase SQL editor run `SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' AND objid = 72707369;`, then deploy again |
| `P1001: Can't reach database server at db.<ref>.supabase.co:5432` | `DATABASE_URL` is the **direct** connection string, which is IPv6-only and unreachable from Render. Replace it with the **Session pooler** string: host `aws-0-<region>.pooler.supabase.com`, port `5432`, user `postgres.<project-ref>`. Supabase > Project Settings > Database > Connection string > Session pooler. Update it in the Render dashboard (Environment) and redeploy — it is a `sync: false` var, so editing `render.yaml` will not change it |
| `password authentication failed` | the pooler user is `postgres.<project-ref>`, not `postgres` |
| The studio's upload step fails with `503 UNAVAILABLE: File storage is not configured` | `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are not on the running API. Add both in the Render dashboard and redeploy (section 6) |
| Upload fails with `503 UNAVAILABLE: File storage did not give an upload URL` | the API reached Supabase and was refused; the API log has `signed upload URL failed` with Supabase's status. Usually the `droplets` or `studio` bucket does not exist or is named differently, or the key is not the `service_role` one (section 6) |
| Upload fails with `storage answered 4xx` on one file | Supabase refused the bytes: most often the file is over the bucket's size limit or its type is not in the bucket's allowed MIME types. Raise or extend them on the bucket |
| A video shows on `/learn` but does not play | the `droplets` bucket is not public, so its public URL answers 400. Make it public (section 6) |
| Visitors to `/droplets` get `Too many requests` | each visitor may make 60 requests a minute, keyed by the address the edge saw (`CF-Connecting-IP`, `True-Client-IP`, then the first `X-Forwarded-For` entry, believed only on requests that carry the proxy secret), and all of them together 1200 a minute per API instance (`api/src/lib/public-limit.ts`). If every visitor seems to share one limit, the API is seeing none of those headers |
