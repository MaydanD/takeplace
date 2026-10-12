# Takeplace — closed test stand deployment (VPS `94.16.106.2`)

Operational runbook for running Takeplace as a **closed test stand for one bar**
on the existing German Netcup VPS, alongside — and without disturbing — the
already-running `infra` (Caddy + Control Panel), `data` (shared PostgreSQL) and
`smm-planner` compose projects.

> **Status: closed test stand, NOT a production release.**
> Stage 14 items (offsite backup, RPO/RTO commitment, legal/retention gates,
> real-data migration, monitoring) are explicitly **not** complete. See
> [Open items before real data](#open-items-before-real-data).

---

## 1. Topology

Nothing is published directly to the internet. The Takeplace stack joins two
**pre-existing external Docker networks** and never creates or recreates them.

```
Internet ──▶ :80/:443 ──▶ [infra] caddy (edge)
                              │  TLS (Let's Encrypt)  +  optional basic_auth
                              ├── /api/*, /health/*  ──▶ takeplace-api:8000   (edge + data)
                              └── everything else    ──▶ takeplace-frontend:80 (edge)

[data] postgres:5432  (never published)         ──▶ takeplace-api, takeplace-worker, migrate
```

| Service | Networks | Purpose |
|---|---|---|
| `migrate` (one-shot) | `data` | `alembic upgrade head` as `takeplace_migrator` |
| `api` | `edge` (alias `takeplace-api`), `data` | FastAPI/uvicorn, admin + public API, SSE |
| `worker` | `data` only | exactly one replica: VK delivery loop + retention/maintenance loop |
| `frontend` | `edge` (alias `takeplace-frontend`) | nginx serving the built SPA |

* **No `ports:` anywhere** in `docker-compose.prod.yml`. Reachability is only
  through Caddy on `edge` and PostgreSQL on `data`.
* The stack uses a **dedicated logical database** (`takeplace`) with its own two
  roles inside the shared PostgreSQL instance. `smm_planner` and `control` are
  never referenced, queried or migrated by anything in this directory.
* The `api` trusts `X-Forwarded-*` **only** from the single Caddy address on
  `edge` (`TAKEPLACE_FORWARDED_ALLOW_IPS`) — no subnet, no wildcard.

## 2. Files on the server

```
/opt/takeplace/
├── repo/                     # Git checkout — build context and compose files
│   ├── docker-compose.prod.yml
│   ├── .env                  # mode 600, GENERATED, never committed
│   └── deploy/               # this runbook and the scripts below
├── secrets/
│   ├── basic-auth.pass       # mode 600, outer Basic Auth password (UNUSED while
│   │                         # the gate is disabled; kept so it can be re-enabled)
│   └── admin.pass            # mode 600, inner Takeplace admin password (used by smoke-test.sh)
└── backups/
    └── takeplace-<stamp>.sql.gz (+ .sha256)
```

Compose is always run from `/opt/takeplace/repo`; the project name is pinned to
`takeplace` by the compose file, so container names are `takeplace-api-1`,
`takeplace-worker-1`, `takeplace-frontend-1`.

## 3. First-time setup

```bash
cd /opt/takeplace/repo

# 1. generate .env (random secrets + detected Caddy edge address) — mode 600
bash deploy/scripts/bootstrap-prod-env.sh --domain takeplace.94-16-106-2.sslip.io

# 2. create the isolated roles + databases in the EXISTING PostgreSQL
#    (also adds the db+role-scoped pg_hba lines and reloads the config)
bash deploy/scripts/provision-db.sh

# 3. build and start (migrations run first, API/worker wait for them)
docker compose -f docker-compose.prod.yml up -d --build

# 4. publish the HTTPS hostname through the existing Caddy (graceful reload).
#    On THIS stand the outer gate stays off, so add --no-basic-auth (§4).
bash deploy/scripts/install-caddy-site.sh --domain takeplace.94-16-106-2.sslip.io --no-basic-auth

# 5. create the venue + admin with the official CLI
docker compose -f docker-compose.prod.yml exec api \
  takeplace create-venue --slug dragon --name "THE DRAGON BAR" \
    --timezone Europe/Moscow --address "Пушкинская 4" \
    --login admin --password '<test password>'
```

## 4. Day-to-day operations

```bash
cd /opt/takeplace/repo
C="docker compose -f docker-compose.prod.yml"

$C ps                       # status + health
$C logs -f --tail=100 api   # API logs (JSON, PII-redacted)
$C logs -f --tail=100 worker
```

The stack publishes no host port, so the health endpoints are reachable through
Caddy on the site origin (see §7 for the exact commands):

```bash
curl -s https://takeplace.94-16-106-2.sslip.io/health/ops
```

`/health/ops` reports `degraded` (never a container failure) for outbox/worker/
maintenance conditions. It is deliberately **not** used as a liveness probe.

### Restart

```bash
$C restart api worker frontend     # or: $C up -d --force-recreate api
```

Caddy is **not** part of this stack and is never restarted here. To change the
Takeplace routing, edit the managed block and reload:

```bash
bash deploy/scripts/install-caddy-site.sh --domain <host>
```

### Update

The server holds a **plain copy** of the source tree, not a git checkout: there
is no `.git` in `/opt/takeplace/repo`, so `git fetch`/`git checkout` on the host
will not work. Ship the new revision from the developer machine by streaming an
archive of the tracked files **plus** the untracked prod files:

```bash
# on the developer machine, from the repository root (Git Bash on Windows)
cd /c/vibe/takeplace
git ls-files -z | tar --null -T - -czf /tmp/takeplace-src.tgz \
  docker-compose.prod.yml backend/Dockerfile.prod \
  frontend/Dockerfile.prod frontend/nginx.prod.conf deploy
gzip -t /tmp/takeplace-src.tgz                  # fail fast on a truncated archive
scp -q -o BatchMode=yes /tmp/takeplace-src.tgz root@94.16.106.2:/tmp/takeplace-src.tgz
ssh -o BatchMode=yes -n root@94.16.106.2 \
  'set -e; gzip -t /tmp/takeplace-src.tgz; \
   tar xzf /tmp/takeplace-src.tgz -C /opt/takeplace/repo; \
   chown -R root:root /opt/takeplace/repo; rm -f /tmp/takeplace-src.tgz'
```

Use the file + `gzip -t` + `scp` form above rather than `tar | ssh 'tar x'`: a
streamed pipe has been observed to fail mid-transfer (`gzip: stdin: unexpected
end of file`) and leave a half-updated tree. Verify the sync afterwards with
`sha256sum` on a file you just changed, and inspect `git status`-equivalent
parity if in doubt.

**Extraction only adds and overwrites — it never deletes.** A file you deleted or
renamed locally therefore stays on the server and keeps being part of the deployed
tree. After any sync that removes something, delete it explicitly and re-check the
parity, e.g.:

```bash
# on the server: compare the deployed file list with the archive you just sent
cd /opt/takeplace/repo && find . -type f -not -name .env -not -path './node_modules/*' | sort > /tmp/deployed.txt
# and remove anything obsolete by hand, then re-run the fingerprint above
```

`.env` is **not** in `git ls-files` (it is gitignored), which is exactly why this
is safe: the archive can never overwrite the generated prod secrets. **Never**
sync with `rsync --delete` or a plain `tar` of the whole directory — either one
would clobber `/opt/takeplace/repo/.env` (and `.env` cannot be regenerated
byte-identically, because the DB roles it describes already exist).

Then rebuild and restart on the server:

```bash
cd /opt/takeplace/repo
bash deploy/scripts/bootstrap-prod-env.sh --help  # (only if .env keys changed)
TAKEPLACE_IMAGE_TAG=<commit> docker compose -f docker-compose.prod.yml build
TAKEPLACE_IMAGE_TAG=<commit> docker compose -f docker-compose.prod.yml up -d
bash deploy/scripts/smoke-test.sh                 # end-to-end gate
```

Set `TAKEPLACE_IMAGE_TAG=<commit>` inside `.env` instead of on the command line
to make the deployed revision explicit and stable for the next command.

If you would rather have a real git checkout on the host, clone the repository
into a temporary directory and copy its `.git` into `/opt/takeplace/repo` — that
preserves `.env` and re-enables the `git`-based flow above.

### Rollback

```bash
# 1. the application: point back at the previous image tag and recreate
sed -i 's/^TAKEPLACE_IMAGE_TAG=.*/TAKEPLACE_IMAGE_TAG=<previous-tag>/' .env
docker compose -f docker-compose.prod.yml up -d

# 2. the database schema (only if the new revision had a migration)
docker compose -f docker-compose.prod.yml run --rm migrate alembic downgrade -1
docker compose -f docker-compose.prod.yml run --rm migrate alembic current

# 3. the reverse proxy (only if routing changed)
cp -a /opt/infra/Caddyfile.bak-takeplace-<stamp> /opt/infra/Caddyfile
docker exec infra-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Previous image tags stay on disk, so step 1 is instant. A database rollback is
*not* covered by an image rollback: check whether the revision you are leaving
added a migration and downgrade explicitly.

**Never** run `docker compose down -v`, `docker system prune --volumes`, or
`git reset --hard` on this host.

### Backup

```bash
bash deploy/scripts/backup-db.sh              # newest 14 kept, sha256 written
bash deploy/scripts/restore-verify.sh         # restores into *_restore_check
```

`restore-verify.sh` fails loudly unless the restored `alembic_version` matches
the live database, every expected table exists, and the row snapshots print.
It never writes to the live database.

#### Scheduled backup (installed)

A daily job **is** installed on the host, as a single file so it is easy to
review and disable:

```bash
bash deploy/scripts/install-backup-cron.sh            # install / re-install
bash deploy/scripts/install-backup-cron.sh --remove   # stop the schedule
cat /etc/cron.d/takeplace-backup                      # what is actually scheduled
tail -n 20 /var/log/takeplace-backup.log              # last runs
```

| Item | Value |
|---|---|
| Schedule | `20 3 * * *` (daily 03:20 host time; override with `TAKEPLACE_BACKUP_HOUR`/`_MINUTE`) |
| Runs as | `root`, `cd /opt/takeplace/repo && bash deploy/scripts/cron-backup.sh` |
| Dumps | `/opt/takeplace/backups/`, mode 600, `.sql.gz` + `.sha256`, newest 14 kept |
| Log | `/var/log/takeplace-backup.log` (rotated to `.log.1` past 1 MiB) |
| Scope | only the isolated `takeplace` database |

The job only writes inside `/opt/takeplace/backups` and its log file, so two
consecutive runs cannot collide with each other and it cannot touch the
`smm_planner` / `control` databases. The host root crontab is empty and this is
the only Takeplace entry in `/etc/cron.d`.

Verify a schedule change with `run-parts --test /etc/cron.d` and by running the
cron command line by hand — that is what a real run executes.

### Outer gate (optional Basic Auth) — currently DISABLED

The site is served **without** the Caddy Basic Auth gate: opening
`https://takeplace.94-16-106-2.sslip.io/` reaches the app directly and the admin
area redirects unauthenticated visitors to `/admin/login`, so the Takeplace
account is the only gate. This was requested by the operator; the stand is
otherwise unchanged.

```bash
# turn the outer gate OFF (current state; browser goes straight to the login page)
bash deploy/scripts/install-caddy-site.sh --domain takeplace.94-16-106-2.sslip.io --no-basic-auth

# turn it back ON (reuses /opt/takeplace/secrets/basic-auth.pass;
# add --rotate-password to mint a new one)
bash deploy/scripts/install-caddy-site.sh --domain takeplace.94-16-106-2.sslip.io
```

The installer rewrites only the fragment between the `>>> takeplace >>>` /
`<<< takeplace <<<` markers, validates the whole file with `caddy validate`, then
applies it with a graceful `caddy reload`. **The fragment's own comment header
sits inside those markers**: an earlier template kept it outside, which made every
re-run append another copy. Both the template and the installer are fixed and the
behaviour is covered by running the installer twice and checking that exactly one
fragment remains.

> ⚠️ With the gate disabled the hostname is reachable by anyone. The remaining
> protections are the Argon2id admin hash, the login rate limiter (429 after ~20
> attempts/60 s **burst**, or 10 attempts/300 s **sustained**, per client), the
> CSRF Origin check and `noindex` headers. See §8 — do not put real guest data
> behind this until that list is accepted.

### Landing page (root redirect)

`https://takeplace.94-16-106-2.sslip.io/` answers **302 → `/admin/login`**, so
staff land on the login form instead of the public status page. This is one line
in the Caddy fragment:

```caddyfile
	redir / /admin/login 302
```

Only the exact `/` path is redirected — `/api/*`, `/health/*`, `/assets/*` and
the public booking page `/b/<slug>` behave exactly as before (verified). Remove,
change or make it permanent in
[deploy/caddy/takeplace.caddyfile](caddy/takeplace.caddyfile) and re-run
`install-caddy-site.sh`; no application code or image is involved. 302 (not 301)
keeps it reversible without a permanent entry in browser caches.

Measured with the gate temporarily re-enabled: the redirect is evaluated **before**
`basic_auth`, so `/` keeps answering `302` while every other path — including the
redirect target `/admin/login` — answers `401` until the credentials are supplied.
That is safe (the target is behind the gate) and it is why both verification
scripts probe `/health/ready`, never `/`, when detecting the gate.

**Test-ordering caveat:** `smoke-test.sh` and `verify-client-ip.sh` both end by
saturating the login limiter on purpose. Run them one at a time and wait for the
window to clear before the second one (or before logging in by hand), otherwise
the second suite reports a spurious `429` and a `FAIL` in
`a request through Caddy has a different identity than the untrusted peer`.

The window is the **sustained** limiter, not the burst one:
`TAKEPLACE_LOGIN_RATE_WINDOW_SECONDS=300` with
`TAKEPLACE_LOGIN_RATE_MAX_REQUESTS=10` (burst: 20 per 60 s). Measured on this
host: a run started 120 s after the previous suite still saw `429` on its first
request and failed; the same run 310 s later passed 8/8. Budget **~5 minutes**
between the two suites.

## 5. Secrets

| Secret | Location | In Git? |
|---|---|---|
| DB app / migrator passwords | `/opt/takeplace/repo/.env` (600) | no |
| H (idempotency) + abuse HMAC keys | `.env` | no |
| VK token-encryption key ring | `.env` | no |
| Caddy Basic Auth password (plaintext) | `/opt/takeplace/secrets/basic-auth.pass` (600) — only used while the optional outer gate is enabled | no |
| Takeplace admin password (test stand) | `/opt/takeplace/secrets/admin.pass` (600) | no — `smoke-test.sh` reads it from here instead of hard-coding it |
| Caddy Basic Auth password (bcrypt) | `/opt/infra/Caddyfile` managed block | no (this file is not a repo file) |

`.env` is matched by the repository's `.gitignore` (`.env`, `.env.*`) and
`deploy/env.prod.example` is the committed, secret-free template.

### Reading the admin password

No script prints it, nothing writes it to a log, and it is not in Git. Read it on
the host, or copy it to your machine as a local mode-600 file:

```bash
# on the server
cat /opt/takeplace/secrets/admin.pass

# from the developer machine: creates admin.pass locally, prints nothing
ssh -o BatchMode=yes root@94.16.106.2 'cat /opt/takeplace/secrets/admin.pass' \
  > admin.pass && chmod 600 admin.pass
```

Then log in at `https://takeplace.94-16-106-2.sslip.io/admin/login` as `admin`.
Rotating it uses only the app's own CLI — never `psql` and never a literal on the
command line (the value would land in the shell history and in `ps` output);
the CLI generates the password and this captures it straight into the secrets
file:

```bash
cd /opt/takeplace/repo
umask 077
docker compose -f docker-compose.prod.yml exec -T api takeplace reset-password dragon \
  | awk '/^    password: /{print $2}' > /opt/takeplace/secrets/admin.pass
chmod 600 /opt/takeplace/secrets/admin.pass
```

`reset-password` also revokes every session of that venue, so an open admin tab
is logged out. Verify the new value with the login line in §7 — a wrong password
answers `401`, a rate-limited client `429` (wait ~5 minutes and retry).

## 6. Isolation guarantees

* `TAKEPLACE_DB_HOST=postgres` resolves on the `data` network only.
* `provision-db.sh` creates `takeplace`, `takeplace_restore_check`,
  `takeplace_app`, `takeplace_migrator` and touches nothing else. The app role
  has DML only (`REVOKE CREATE ON DATABASE/SCHEMA`), enforced by the same grants
  the repository's CI asserts.
* The shared PostgreSQL has no catch-all `host all all` rule; access is granted
  per project. `provision-pg-hba.sh` (called by `provision-db.sh`) appends
  db+role-scoped lines inside managed markers and applies them with
  `pg_reload_conf()` — a reload, never a restart.
* The API's database pool is deliberately small (3+2 for the API, 3 for the
  worker) because the shared instance runs `max_connections=30` and already
  serves two other applications.
* `online_booking_enabled=false` on the venue: the public booking UI answers but
  guest self-service creation stays off until the owner enables it.
* Only synthetic test names/numbers are allowed on this host. Do not import a
  real client base and do not enable public booking for real guests.

## 7. Verifying the deployment

Three levels, cheapest first.

```bash
# 1. one-liners (the outer gate is currently DISABLED — see §4 "Outer gate")
curl -so /dev/null -w '%{http_code}\n' https://takeplace.94-16-106-2.sslip.io/  # 302 -> /admin/login
curl -so /dev/null -w '%{http_code}\n' \
  https://takeplace.94-16-106-2.sslip.io/admin/login                     # 200 = login page
curl -s https://takeplace.94-16-106-2.sslip.io/health/ready          # {"status":"ok","database":"ok"}
curl -so /dev/null -w '%{http_code}\n' \
  https://takeplace.94-16-106-2.sslip.io/api/admin/v1/me             # 401 without a session
# With the gate ENABLED, prefix Basic Auth: -u "takeplace:$(cat /opt/takeplace/secrets/basic-auth.pass)"

# 2. backend-executed checks
docker compose -f docker-compose.prod.yml exec api takeplace list-venues
docker compose -f docker-compose.prod.yml exec api takeplace audit-invariants   # "invariants: clean"

# 3. full end-to-end suites (both exit non-zero on any failure)
bash deploy/scripts/smoke-test.sh          # ~70 assertions; may poll up to --max-wait for WAITING
bash deploy/scripts/verify-client-ip.sh    # trusted-proxy / X-Forwarded-For properties
```

`smoke-test.sh` authenticates as the stand admin using the password from
`/opt/takeplace/secrets/admin.pass` (override with `--admin-password-file` or
`TAKEPLACE_ADMIN_PASSWORD_FILE`); it aborts with a clear error if that file is
missing, and it never contains the password itself. The dev-side
`deploy/env.prod.example` and this runbook document keys, never secret values.

`smoke-test.sh` covers TLS (plus the outer Basic Auth gate when it is enabled —
otherwise that one assertion is reported as SKIP, never as a pass), the SPA
document/assets/deep-link
fallback, the CSRF Origin check, login/session/logout, schedule, halls/tables,
the full booking lifecycle (`NEW -> WAITING -> OPEN -> UNDO_OPEN -> OPEN ->
CLOSED`), idempotent replay, change-time, cancel, the atomic `WALK_IN`
create+OPEN path, two simultaneous admin sessions, the SSE stream with a live
mutation and a reconnect, the health endpoints and login rate limiting.

Two rules make the lifecycle time-dependent — the suite reports `SKIP` (never a
false PASS) when it cannot satisfy them:

* **WAITING** (§10) requires `starts_at <= now < ends_at`, so the suite creates a
  booking at the next 5-minute boundary and polls until the interval begins;
* **OPEN** (§10) requires `shift_starts_at <= now < ends_at`, so a booking for a
  later business date cannot be opened before that shift starts.

The suite is idempotent: it reuses existing tables instead of re-saving a layout
(which would correctly fail with `409 TABLE_ARCHIVE_BLOCKED` while a future
booking still references a table), releases leftovers from earlier runs, and
cancels the future bookings it created. Its rate-limit step leaves the login
limiter saturated, so give it ~5 minutes before logging in as a human.

### Caddy config caveat

`/opt/infra/Caddyfile` is a **single-file bind mount**. Never replace it with
`cp`/`install`/`mv`: the container would keep the old inode while the path points
at a new one, so Caddy would silently keep serving the previous config and every
later `caddy reload` would revert the Takeplace block. `install-caddy-site.sh`
writes in place and aborts if the container's view of the file differs from the
host's (in that case, `docker restart infra-caddy-1` re-establishes the mount).

## 8. Open items before real data

These are **not** done and must be closed before this stand holds real guest
data (Stage 14 / §65):

1. **Offsite backups** — the current dump lives on the same disk as the
   database. No off-host copy, no restore from a remote copy.
2. **RPO/RTO** — no agreed or measured recovery point/objective, and the daily
   job (§4 Backup) is **not monitored**: a failing run only shows up in
   `/var/log/takeplace-backup.log`, in nobody's inbox.
3. **Backup encryption + retention policy** — dumps contain personal data once
   real bookings exist; no encryption and no retention policy is defined.
4. **Legal/retention gate** — `TAKEPLACE_BOOKING_PII_RETENTION_DAYS` is an
   operational default, not a legally reviewed value.
5. **Monitoring/alerting** — nothing polls `/health/ops`; `degraded` is only
   visible if a human looks.
6. **VK delivery** — disabled: no valid community token/destination exists. Do
   not invent one; configure it through the admin UI when real credentials exist.
7. **Reverse-proxy address** — `TAKEPLACE_FORWARDED_ALLOW_IPS` pins the Caddy
   container's current `edge` address. Re-run
   `deploy/scripts/check-proxy-ip.sh` after any recreation of that container.
8. **Hostname** — `*.sslip.io` is a temporary testing hostname with a public
   wildcard DNS zone. Replace it with a real domain before any wider rollout.
9. **No outer gate** — at the operator's request the site is served without
   Caddy Basic Auth, so the URL is reachable by anyone who knows it. The
   Takeplace login (Argon2id + rate limiter) is the only gate. Accept this
   consciously, or restore the gate with the one-liner in §4 before real data.

## 9. Deployment record

| Item | Value |
|---|---|
| Date | 2026-10-11 |
| Source | repository `main` @ `ad4e22a` **plus** the deployment files listed below |
| Deployment files | `docker-compose.prod.yml`, `backend/Dockerfile.prod`, `frontend/Dockerfile.prod`, `frontend/nginx.prod.conf`, `deploy/**` — committed as `chore(deploy): add Takeplace staging deployment` (the server itself is a plain tree copy with no `.git`, so it is not a checkout of that commit) |
| Deployed tree | `/opt/takeplace/repo` — plain copy, **no `.git`**; every source file byte-identical to the local tree (`.env` excluded, `.env.example` intentionally not shipped) |
| Admin credential | rotated with the official CLI (`takeplace reset-password dragon`); the 20-char app-generated password lives only in `/opt/takeplace/secrets/admin.pass` (600) and is never committed or logged. A temporary password used for one browser walk-through was retired immediately (it now returns 401) |
| Outer gate | **disabled** (Caddy Basic Auth removed for this site only; SMM Planner / Control Panel blocks byte-identical to before) |
| Landing page | `/` → 302 → `/admin/login` |
| Verification (2026-10-12) | `smoke-test.sh` over the public origin: **70 PASS / 0 FAIL / 1 SKIP** (the SKIP is the Basic-Auth assertion, not applicable while the gate is off). Browser walk-through of the real UI as `admin`: login page renders, a wrong password shows the app's own “Неверный логин или пароль”, `/admin` without a session lands on `/admin/login`, login → admin data → logout → `401`. No HTTP Basic Auth prompt anywhere |
| Tree fingerprint | see the command below (run on the server, `cd /opt/takeplace/repo`): |

```bash
# Fingerprint of everything that runs — excludes .env (secret), node_modules
# (build cache) and this README, which would otherwise change its own value.
find . -type f -not -name .env -not -path './node_modules/*' \
     -not -path './deploy/README.md' -print0 \
  | sort -z | xargs -0 sha256sum | sha256sum
# at deployment: 633e4666896d0e520e6e95e87d33df91e51a3b1f8df96edbf3ec500268f539cf
```
| Backup schedule | `/etc/cron.d/takeplace-backup`, daily 03:20, dumps in `/opt/takeplace/backups` |
| Images | `takeplace-backend:local` 354 MB, `takeplace-frontend:local` 74.4 MB |
| Schema revision | `20261007_0009` (head) |
| Database | `takeplace` 9.5 MB (shared PostgreSQL: `smm_planner` 7.9 MB, `control` 7.7 MB — unchanged) |
| Site | `https://takeplace.94-16-106-2.sslip.io` (Let's Encrypt, HTTP-01) |
| Runtime footprint | api ≈ 69 MiB, worker ≈ 53 MiB, frontend ≈ 3 MiB (limits 224/192/48 MiB) |
| Host after deploy | 1.1 GiB RAM available, 47 GB disk free, no OOM events, PostgreSQL 17/30 connections |
