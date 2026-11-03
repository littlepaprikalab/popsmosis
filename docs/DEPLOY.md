# Deploying popsmosis: Supabase + Cloudflare R2 + Google + Render

Same shape as sparks: Postgres on **Supabase**, sign-in with **Google**, the API
(`popsmosis-api`) and the web app (`popsmosis-web`) on **Render**, and the droplet videos
on **Cloudflare R2**. `render.yaml` at the repo
root describes the two Render services as a Blueprint. Browsers only talk to
`popsmosis-web`, which proxies `/api/*` to `popsmosis-api` server-side. Agents talk to
`popsmosis-api` directly with their key.

## Every environment variable

**popsmosis-api**

| Key | Value | Where it comes from |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres` | Supabase > Project Settings > Database > Connection string > **Session pooler** |
| `DATABASE_CA_CERT` | the PEM text of Supabase's CA certificate, or the path to it (`/etc/secrets/<name>` as a Render Secret File) | Supabase > Project Settings > Database > SSL Configuration > **Download certificate**. The API checks the database's certificate with it (`api/src/lib/db-tls.ts`), so **set it before deploying**: without it the API cannot connect to Supabase and the new instance's health check fails. See [8](#8-the-database-row-level-security-and-tls) |
| `DIRECT_URL` | optional: the Session pooler URI, as above (port 5432) | needed only when `DATABASE_URL` is the transaction pooler (port 6543). Migrations hang on that pooler, so they use this URL instead; the running API keeps using `DATABASE_URL`. Leave it empty otherwise |
| `JWT_SECRET` | generated | Render generates it from the blueprint |
| `PROXY_SECRET` | generated | Render generates it; the web service copies it |
| `GOOGLE_CLIENT_ID` | `…apps.googleusercontent.com` | Google Cloud > APIs & Services > Credentials |
| `GOOGLE_CLIENT_SECRET` | `GOCSPX-…` | same |
| `GOOGLE_REDIRECT_URI` | `https://popsmosis-web.onrender.com/auth/callback` | your web hostname + `/auth/callback` |
| `ALLOWED_ORIGINS` | `https://popsmosis-web.onrender.com` | your web origin |
| `API_BASE_URL` | `https://popsmosis-api.onrender.com` | your API hostname; goes into the downloaded skill file |
| `ADMIN_EMAILS` | `you@yourcompany.com` | Google emails that get the admin role on sign-in. **Set this**: somebody has to manage the agents. Anyone else with a Google account can sign in and use the site |
| `R2_ACCOUNT_ID` | 32 hex characters | Cloudflare > R2 object storage > Overview > Account Details. Where the studio uploads the droplet videos; see [Cloudflare R2](#6-cloudflare-r2-the-droplet-videos). Set all four `R2_` variables or none: unset, the upload endpoint answers 503 and no droplet gets videos |
| `R2_ACCESS_KEY_ID` | 32 hex characters | the **Access Key ID** shown when you make the R2 API token (section 6) |
| `R2_SECRET_ACCESS_KEY` | 64 hex characters | that token's **Secret Access Key**, shown once. Signs each upload; it stays on the API and is never sent to the studio or a browser |
| `R2_PUBLIC_URL` | `https://media.popsmosis.com` | the `droplets` bucket's custom domain, with no path: where anyone reads the videos |
| `NODE_ENV` | `production` | set by the blueprint |
| `PORT` | `3001` | set by the blueprint |

**popsmosis-web**

| Key | Value | Where it comes from |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://popsmosis-api:3001` | Render private network (fastest); `https://popsmosis-api.onrender.com` also works |
| `NEXT_PUBLIC_SITE_URL` | `https://popsmosis-web.onrender.com` | your web origin |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | same client ID as the API | Google Cloud |
| `NEXT_PUBLIC_GOOGLE_REDIRECT_URI` | `https://popsmosis-web.onrender.com/auth/callback` | must equal `GOOGLE_REDIRECT_URI` |
| `NEXT_PUBLIC_INKMORY_URL` | `https://inkmory.com` | optional: each lesson card links to the teacher's notes on Inkmory, the records it tagged with the topic ([PLAN.md](PLAN.md), section 11). Built into the page, so a change needs a rebuild. Unset: no link |
| `NEXT_PUBLIC_MEDIA_URL` | copied from popsmosis-api's `R2_PUBLIC_URL` | the blueprint wires it with `fromService`. The only host besides the site that pages load pictures and video from (the Content-Security-Policy in `web/next.config.ts`), so without it the droplet videos and posters do not play. Built into the page: after changing `R2_PUBLIC_URL`, rebuild the web app |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://<project-ref>.supabase.co` | only while the API's boot log says a droplet still plays from the old Supabase bucket (section 6): lets pages load those files too. Remove it, and rebuild, once the move is done |
| `PROXY_SECRET` | copied from popsmosis-api | the blueprint wires it with `fromService` |
| `NODE_ENV` | `production` | set by the blueprint |

Optional API tuning: `DATABASE_POOL_SIZE` (default 5), `QUESTIONS_PER_USER_PER_DAY`
(default 10), `TOPICS_PER_DAY` (default 100; every learner's topics together in 24 hours,
the ceiling on a day's model spend; 0 for none), `REPLIES_PER_USER_PER_HOUR` (default 30), `DROPLET_COMMENTS_PER_USER_PER_HOUR`
(default 30; comments on the Droplets tab), `ATTACHMENT_RETENTION_DAYS` (default
30; how long the files sent with messages keep their bytes in Postgres, 0 for good; let go
every `ATTACHMENT_SWEEP_MINUTES`, default 60, and on the spot when read),
`ATTACHMENT_MB_PER_USER_PER_DAY` (default 100; the files one learner may send in 24 hours, 0
for none), `ATTACHMENT_MB_HELD_PER_USER` (default 300; the files popsmosis holds of one
learner at a time, 0 for none; both generous for the hackathon. The files live in Postgres,
and on Supabase's free plan the whole database is 500 MB, so two learners at 300 MB would
fill it: there, or after the hackathon, 20 and 60 suit better),
`VIDEO_STALE_MINUTES` (default 60; a render that has not reported for this long reads as
failed), `LESSON_HOLD_MINUTES` (default 60; how long after a lesson is posted `/learn` may wait for its
videos to be READY or FAILED before it shows the text anyway, 0 shows it at once). All of these but
`DATABASE_POOL_SIZE` and `ATTACHMENT_RETENTION_DAYS` are knobs: an admin can set them on
`/admin/studio` instead, with no redeploy, and a value set there wins (README, "Knobs").

Nothing else. There is no Supabase Auth or Redis in popsmosis; Supabase is the Postgres,
and the droplet videos are on Cloudflare R2, whose downloads are free (Supabase Storage
bills each GB past the plan's allowance).

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

## 6. Cloudflare R2 (the droplet videos)

The studio's last step uploads each droplet's files to Cloudflare R2 (docs/PLAN.md,
section 10). The API signs a URL per file, so the studio never holds an R2 key. R2 charges
nothing for downloads, which is most of what the videos cost to serve. Two buckets:

| Bucket | Public | What goes in it |
|---|---|---|
| `droplets` | yes, through its custom domain | `<answerId>/a<n>/out/song_h.mp4`, `lesson_h.mp4` and a `.jpg` poster for each: what the Droplets tab and `/learn` play |
| `studio` | no | `<answerId>/a<n>/art/…`, `masks/…`, `locations.json`, `audio/…`, `timing/…`, `timeline_*.json`: kept so a renderer fix can re-render without paying for the song and the art again |

`a<n>` is the render attempt (`studio/videos.py`): each attempt writes its own files, so a
render that was taken over cannot overwrite the one that replaced it, and a re-render
gets new URLs. Earlier attempts' files stay; they are small, and can be deleted by hand.
Since a public file never changes once viewers can see it, it is uploaded with
`Cache-Control: public, max-age=31536000, immutable`, so Cloudflare's cache serves most plays.

1. **R2.** In the Cloudflare dashboard, open **R2 object storage** and subscribe (it asks
   for a payment method even within the free allowance).
2. **The buckets.** R2 > **Create bucket**: `droplets`, then `studio`, with the location
   hint **Western North America** (near Render's `oregon`). The names must be exactly
   these. Both start private.
3. **The public domain.** In `droplets` > Settings > **Custom Domains**, connect a
   subdomain of a domain whose DNS is on Cloudflare in this account, such as
   `media.popsmosis.com`, and wait for it to read **Active**. That is what makes the bucket
   readable, through that domain only. Leave the **R2.dev subdomain** off: Cloudflare
   rate-limits it and does not cache it. Give `studio` no domain, so it stays private.
4. **The token.** R2 > Overview > Account Details > **API Tokens: Manage** > **Create
   Account API token**: permission **Object Read & Write**, **Apply to specific buckets
   only** with `droplets` and `studio`. Copy the **Access Key ID** and the **Secret Access
   Key** (shown once). Not the **Token value**: that one is for Cloudflare's own API.
5. **Give the API the settings.** On `popsmosis-api` only: `R2_ACCOUNT_ID` (Overview >
   Account Details, or the first part of the endpoint the token page shows),
   `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_PUBLIC_URL`
   (`https://media.popsmosis.com`). On an existing service add them by hand in Render >
   popsmosis-api > Environment: a Blueprint sync never adds a `sync: false` variable to a
   service that already exists. Redeploy the API.
6. **Check it.** The API log says at boot either `File storage: R2 has the droplets and
   studio buckets, and takes the key`, or what is wrong (a missing setting, a key R2
   refuses, a bucket it cannot find). Then, with the agent key:

   ```bash
   curl -s -X POST https://popsmosis-api.onrender.com/api/answers/<an answer of this agent>/upload-url \
     -H "Authorization: Bearer sk_pop_..." -H 'Content-Type: application/json' \
     -d '{"bucket":"droplets","file":"out/check.jpg"}'
   # {"uploadUrl":"https://<account>.r2.cloudflarestorage.com/droplets/...","headers":{"Content-Type":"image/jpeg",...},"publicUrl":"https://media.popsmosis.com/..."}
   ```

   PUT any small `.jpg` to `uploadUrl` with exactly the `headers` it gave, for example
   `curl -X PUT --data-binary @x.jpg -H 'Content-Type: image/jpeg' -H 'Cache-Control: public, max-age=31536000, immutable' '<uploadUrl>'`,
   then open `publicUrl` in a browser, and delete the file in the dashboard afterwards.
7. **The routine's network policy** must allow `<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`,
   since the studio uploads straight to it (docs/PLAN.md, section 9). It no longer needs the
   Supabase host.

**Videos made before the move.** Droplets uploaded while storage was Supabase keep
playing from Supabase until they are moved, and the API moves them on its own: once it
has started and the R2 check at boot passes, and again every hour, it copies each one's
public files (the videos, the hook cut and their posters) from their Supabase URLs to the
same place in the `droplets` bucket, reads each back from `R2_PUBLIC_URL`, and only then
switches that droplet's URLs (`api/src/lib/storage-move.ts`). The log says `moved N
droplet(s)' videos from Supabase Storage to R2`, or why one stays on Supabase for now (it
keeps playing from there, and the next hourly sweep tries again). It needs no Supabase
key: the files are read from their public URLs.

The switch is safe while renders run. A studio from before the move uploads to R2 through
the new API (only the file's type is signed into the URL, and it sends its own); a newer
studio meeting an API not redeployed yet, or whose deploy failed, uploads to Supabase as
before, and the sweep moves those files. While `SUPABASE_URL` is still set, the API also
takes a droplet's own files in the old bucket in its READY report, for a render that
spans the switch. Then:

- Check a moved droplet on `/droplets`: its video plays, and its address starts with
  `https://media.popsmosis.com/`.
- Leave the Supabase `droplets` bucket for a week or so, then empty and delete it. Until
  then, switching back is a SQL `replace()` of the two prefixes in the four URL columns of
  `answers`.
- The private `studio` bucket's re-render kits are not moved: nothing reads them yet, and
  they cost nothing to download where they are. Keep them, or delete the bucket.
- Once the log shows every droplet moved and an hourly sweep later has nothing left (no
  new `moved` line), remove `SUPABASE_URL` from `popsmosis-api`: the API then takes R2
  URLs only. `SUPABASE_SERVICE_ROLE_KEY` is no longer read at all. Keep `DATABASE_URL`.

Nothing on `popsmosis-web` changes: the pages read the public URLs from the API, and its
Content-Security-Policy already allows `https:` media and images. No CORS rule is needed
either: the uploads come from the studio, not a browser, and a `<video>` plays from
another domain without one.

**Before launch:** the ElevenLabs plan has to allow publishing its songs publicly, and when
Lyria sings (`/admin/studio`) Google's terms for Lyria's songs (docs/PLAN.md, section 10, "Before launch").

## 7. Before launch: the Privacy Policy and Terms of Use

`/privacy`, `/consumer-health-data` and `/terms` read their facts from
`web/src/lib/legal.ts`. `OPERATOR_NAME` (Spicy Ginger Labs), `OPERATOR_LOCATION`,
`GOVERNING_LAW` (Washington), `COURTS` (King County) and `CONTACT_EMAIL` are set. Before
the site is public:

1. Read `CONTACT_EMAIL`'s inbox. Requests about Washington health data have to be answered
   within 45 days, and a legal claim's first step (Terms, section 17) and copyright notices
   arrive there too.
2. Register the copyright agent with the US Copyright Office
   (copyright.gov/dmca-directory, a small fee, renewed every three years): Spicy Ginger
   Labs, at the contact address. The Terms name it (section 12), and the registration is what
   protects you from liability for what people post in comments.
3. If `ATTACHMENT_RETENTION_DAYS` is not the default 30 on the API, set the same number in
   `legal.ts`.
4. Keep the footer's **CONSUMER HEALTH DATA** link: the same Act requires a separate link to
   that policy on the home page.
5. Add two of the URLs to Google Cloud Console > APIs & Services > OAuth consent screen
   (or Branding): the privacy policy link `https://<popsmosis-web host>/privacy` and the
   terms of service link `https://<popsmosis-web host>/terms`.

Redeploy `popsmosis-web` after changing `legal.ts`: the pages are built at deploy time.

## 8. The database: row-level security and TLS

Two safeguards around the Supabase Postgres (threat model T14), and what to check in the
Supabase console for each. Nothing here is visible from the code: check each item.

**Row-level security.** The migration `20261112120000_row_level_security` turns it on for
every table, with no policy. A role that does not own a table (Supabase's Data API reads as
`anon` or `authenticated`, with the project's publishable key) then gets no row from it,
whatever it is granted. The API is unaffected: Prisma connects as `postgres`, the owner of
every table, and an owner is held to row-level security only where a table forces it, which
none does. `api/src/rls.integration.test.ts` fails if a table is added without it.

**TLS.** The API checks the database's certificate, chain and host name, with
`DATABASE_CA_CERT`. `sslmode=no-verify` in `DATABASE_URL` turns the check off (the API warns
at boot): for a move between providers, never to keep. The build's migrations (`prisma
migrate deploy`, on `DIRECT_URL` or `DATABASE_URL`) connect through Prisma's own engine,
which this does not change.

Check in the Supabase console:

1. **Does the Data API expose `public`?** Project Settings > Data API (named API Settings in
   older dashboards), **Exposed schemas**. Popsmosis never uses the Data API. If `public` is
   listed, take it off the list or turn the Data API off; row-level security already leaves it
   no row, and this removes the door.
2. **Is row-level security on, with no policy?** After deploying, the Table Editor marks every
   table with RLS enabled, and Authentication > Policies lists no policy for the `public`
   tables. Advisors > Security Advisor should no longer report "RLS Disabled in Public" for
   any of them.
3. **Does `postgres` own every table?** In the SQL editor:
   `select tablename, tableowner from pg_tables where schemaname = 'public';` Every row should
   say `postgres` (the `postgres.<ref>` user in `DATABASE_URL` is that role, through the
   pooler). A table another role owns would now refuse the API. And
   `select relname from pg_class where relforcerowsecurity and relnamespace = 'public'::regnamespace;`
   should return nothing.
4. **The CA certificate.** Project Settings > Database > SSL Configuration: download it and set
   `DATABASE_CA_CERT` on popsmosis-api before deploying (above). The API checks the Session
   pooler's certificate as Supabase's own guide connects to it, with `sslmode=verify-full`
   ([Connecting with PSQL](https://supabase.com/docs/guides/database/psql)), so try that first
   from your machine with the file you downloaded:
   `psql "sslmode=verify-full sslrootcert=<the .crt> host=aws-0-<region>.pooler.supabase.com port=5432 dbname=postgres user=postgres.<ref>"`.
   If it connects, the API will. After the deploy, `/api/health` should answer `ok` and the
   API log should show no certificate error. Should it fail anyway, adding `?sslmode=no-verify`
   to `DATABASE_URL` puts the old behaviour back while you look.

   **"self-signed certificate in certificate chain"** on every query, with `/api/health`
   answering 503, means the API has no CA that signed Supabase's certificate. The first lines
   of the API's log at boot say which case it is:
   - `[prisma] DATABASE_CA_CERT is not set...`: the variable is missing on popsmosis-api, or
     empty. Set it and redeploy.
   - `[prisma] the database's certificate is checked against DATABASE_CA_CERT: <name>`: the
     certificate set is not the one Supabase signs with. Download it again from the same page
     and check `<name>` is Supabase's.
   - `DATABASE_CA_CERT: certificate 1 cannot be read`, and the API does not start: the text in
     the variable is not a whole certificate. Paste the file's full contents, BEGIN and END
     lines included, or upload it as a Render Secret File and set the variable to its path
     (`/etc/secrets/<file name>`).
5. **Enforce SSL**, on the same page: on, so the database refuses connections without TLS.
6. **The keys.** Project Settings > API Keys: popsmosis uses the secret key on the API alone
   (`SUPABASE_SERVICE_ROLE_KEY`). The publishable (anon) key is in neither the web app nor
   the runner. If either key was ever shared, rotate it.
7. **Storage.** Storage > Policies, for `storage.objects`: no policy should let `anon` or
   `authenticated` insert, update or delete in the `droplets` or `studio` buckets. Uploads go
   through the signed URLs the API mints with the secret key.

## How it fits together

```
browser ──HTTPS──> popsmosis-web (Next.js, Render)
                     │  /api/* rewrite + x-proxy-token
                     ▼
agent  ──HTTPS──> popsmosis-api (Fastify, Render) ──TLS──> Supabase Postgres (session pooler)
   Authorization: Bearer sk_pop_...
  │
  └─ studio ──PUT, signed URL──> Cloudflare R2 (droplets, studio) ──> media.popsmosis.com ──> browser
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

`20261030120000_breakdown` (each droplet's bill of materials) adds two nullable `JSONB`
columns to `answers` (`lessonBreakdown`, `videoBreakdown`); it rewrites no rows and needs no
downtime. Either service can go first: the API before it ignores the studio's `breakdown`
field, and the web app shows no bill for an answer that has none. Deploy `popsmosis-api`
first anyway, as for any migration.

`20261101120000_learner_privacy` (learners private, lessons public, and the record of
agreeing to the Terms) lets `questions."userId"` be null, which Postgres records without
touching a row, adds two nullable columns to `users` (`termsVersion`, `termsAcceptedAt`),
and adds an index on `replies ("userId", "createdAt")`, built while the build runs; on
today's table that takes moments, and it holds writes to `replies` only while it builds.
Deploy `popsmosis-api` **first**, then `popsmosis-web` straight after: until the API is
live, an old API still shows every learner's topics to everyone and the new web app's
account deletion has no endpoint to call; and until the web app is live, sign-in is refused,
since the old sign-in page does not send the Terms box the new API requires. Do not roll
the API back past this change once anyone has taken back a request or deleted their account:
the old code expects every request to have a learner and its `/learn` breaks on the ones that
do not. Roll forward instead.

`20261103120000_progress` (the bill filling in while a droplet is made, and the lesson text
waiting for its videos) adds a nullable `JSONB` column to `questions` (`progress`) and one to
`answers` (`videoProgress`); it rewrites no rows and needs no downtime. Deploy
`popsmosis-api` first: the web app before it shows every lesson at once, as it did, and a
studio before it has its progress reports refused (404), prints that, and goes on.

`20261107120000_droplet_plays` (play counts, README: Play counts) creates one enum
(`DropletVideo`) and one table (`droplet_plays`), with a foreign key to `answers`; it rewrites
no row, and adding the key holds writes to `answers` for a moment while it checks an empty
table. Deploy in the usual order: the migration (it runs in the `popsmosis-api` build), then
`popsmosis-api`, then `popsmosis-web`; the studio has no change. An API before it never reads
the table; a web app before the API sends no counts, and one ahead of it has its counts
refused (404) and drops them.

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
| The studio's upload step fails with `503 UNAVAILABLE: File storage is not configured` | none of the four `R2_` variables is on the running API. Add them in the Render dashboard and redeploy (section 6) |
| Upload fails with `503 UNAVAILABLE: File storage is misconfigured on this API` | some `R2_` variables are missing, `R2_ACCOUNT_ID` is not the 32-character ID, or `R2_PUBLIC_URL` has a path or is the `r2.cloudflarestorage.com` endpoint. The message names which; the API log says the same at boot. Fix it in Render and redeploy (section 6) |
| Upload fails with `storage answered 403: SignatureDoesNotMatch` or `InvalidAccessKeyId` | R2 does not take the key: `R2_SECRET_ACCESS_KEY` is not the Secret Access Key of the token whose Access Key ID is in `R2_ACCESS_KEY_ID` (often the token's value pasted instead), or the token was deleted. `AccessDenied`: the token cannot write that bucket; give it Object Read & Write on both. Waiting does not fix these, so the studio does not retry them |
| Upload fails with `storage answered 404: NoSuchBucket` | the `droplets` or `studio` bucket does not exist in the account `R2_ACCOUNT_ID` names, or is named differently (section 6) |
| Upload fails with `storage did not answer (<account>.r2.cloudflarestorage.com)` | the routine's network policy does not allow that host (section 6, step 7), or R2 is down. A 429 or 5xx from R2 is retried twice before the step fails |
| The API log says `a droplet's videos stay on Supabase Storage for now: …` | the move of that droplet's files to R2 stopped, with the reason (section 6, "Videos made before the move"). `answered 404; is https://media.popsmosis.com Active` means the custom domain does not serve the bucket yet; `R2 refused` names R2's reason. Nothing is lost: the droplet plays from Supabase, and the next hourly sweep (or boot) tries again. The same line every hour means it needs a hand: a file missing from Supabase stays missing |
| A video shows on `/learn` but does not play | `media.popsmosis.com` is not **Active** on the `droplets` bucket yet (Settings > Custom Domains), or `R2_PUBLIC_URL` names another domain. Open the video's URL in a browser to see which |
| Visitors to `/droplets` get `Too many requests` | each visitor may make 60 requests a minute, keyed by the address the edge saw (`CF-Connecting-IP`, `True-Client-IP`, then the first `X-Forwarded-For` entry, believed only on requests that carry the proxy secret), and all of them together 1200 a minute per API instance (`api/src/lib/public-limit.ts`). If every visitor seems to share one limit, the API is seeing none of those headers |
