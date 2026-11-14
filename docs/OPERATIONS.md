# Operations runbook (hosted pilot on one server)

For the person who runs the server. Everything here is configuration, scripts and free software; the only recurring cost is the server and the off-site storage you choose. Check current prices with the provider before ordering.

| Piece | Where |
|---|---|
| Server, firewall, bootstrap | `deploy/hetzner/` (README there) |
| Stack | `docker-compose.yml`: db, migrate (one-shot), app, worker, backup |
| Backups | `ops/backup/` (service `backup`), restore drill `scripts/restore-drill.sh` |
| Health | `GET /healthz` (liveness), `GET /healthz/deep` (readiness for monitors) |
| Update and rollback | `deploy/hetzner/update.sh` |

## Deploy (first time)

1. Read `deploy/hetzner/README.md`, then run `deploy/hetzner/provision.sh` yourself (it creates billable resources; `DRY_RUN=1` shows what it would do).
2. Point the DNS A record at the server. Caddy gets the certificate by itself on the first request.
3. `ssh deploy@<ip>`, read `~/FIRST-LOGIN.txt` once, delete it.
4. Edit `~/ledgerline/.env`: `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, the two ping URLs. Then `docker compose up -d`.
5. Run the restore drill (below) before you put real data in. Until the first off-site backup succeeds, `/healthz/deep` returns 503 on purpose.

## Update and rollback

```sh
cd ~/ledgerline
deploy/hetzner/update.sh v1.2.0     # a tag; without an argument it fast-forwards the current branch
```

The script refuses to run with local changes, takes a backup (the local tier and the off-site copy), remembers the image ids of the running services, checks out the target, runs `docker compose up -d --build` and waits for `/healthz`. If the new version is not healthy within `HEALTH_TIMEOUT` (120 s) it checks out the previous commit, points the image names back at the previous builds and starts them without rebuilding, and exits non-zero.

Limits you should know:
- The migration has already run when the health check fails. The previous code then runs on the newer schema. Keep migrations additive (new columns and tables first, removal in a later release) so the previous version still works against them. If a migration damaged data, restore the pre-update backup instead (see Restore).
- The first update after a fresh install has no earlier images to go back to; the script says so.

Manual rollback to a known tag: `git checkout <tag> && docker compose up -d --build`.

## Backups

The `backup` service runs once at start (when the last success is older than 24 hours) and then daily at `BACKUP_HOUR_UTC` (02:00 UTC default). One run:

1. Exports a database snapshot, counts the rows of every table in that same snapshot and runs `pg_dump -Fc` as the owner role against it. The counts become `manifest.txt` (table, rows, schema revision, SHA-256 of the dump).
2. Keeps `./backups/ledgerline-<time>.dump` plus its manifest locally (`BACKUP_KEEP_DAYS`, default 30): the fast first tier.
3. Copies dump and manifest to the restic repository, then applies retention: `--keep-daily 14 --keep-weekly 8 --keep-monthly 24 --keep-yearly 8`.
4. On the weekday `BACKUP_CHECK_WEEKDAY` (7 = Sunday) runs `restic check --read-data-subset=5%`.
5. Writes `last_backup_ok` (time, whether the off-site copy happened) into the shared ops volume. `/healthz/deep` reads it.
6. Pings `BACKUP_HEALTHCHECK_PING_URL` (`/start`, success, `/fail`). Any failing step stops the run, logs `FAILED` and sends the fail ping; the service retries hourly.

Run one by hand: `docker compose run --rm -e BACKUP_ONCE=1 backup`.

### Off-site repository

restic (BSD-2-Clause) writes an encrypted, deduplicated repository to S3-compatible storage. Any provider works; set the repository URL and credentials in `.env`:

```
RESTIC_REPOSITORY=s3:https://<endpoint>/<bucket>
RESTIC_PASSWORD=<long random passphrase>
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=<region>
```

Hetzner Object Storage is S3-compatible; take the endpoint and region from your bucket's page in the Hetzner console and check the current storage price there. Create the bucket and an access key limited to that bucket yourself. The service creates the repository on first use.

### Key handling

`RESTIC_PASSWORD` encrypts everything. Without it the off-site backups are unreadable noise, and restic has no recovery for a lost password. Store it in your password manager, and in a second place that is not this server (printed and sealed, or a second manager account). The copy in `.env` is only for the running job. Do the same for the storage access key. When the server is lost, these two are all you need next to the repository.

### Retention versus the 7-year duty

Bookkeeping records must be kept 7 years (docs/COMPLIANCE_NL.md). The repository keeps 14 days, 8 weeks, 24 months and 8 years (`keep-yearly 8`: one snapshot per year, the last of that year). The yearly snapshots cover the statutory period for the data as it stood at each year end, but they are disaster recovery, not an archive you hand to an inspector. The statutory copy is the clients' own: every administration owner can export their administration (Settings, Privacy and data), and you should ask pilot clients to keep a yearly export. An erasure request cannot reach old snapshots; see the conflict described in COMPLIANCE_NL.md and say so in your agreement.

## Restore

From the local tier (same server, mistake in the data): `scripts/restore.sh backups/ledgerline-<time>.dump`.

From the off-site repository onto a new server: bootstrap the server (`deploy/hetzner/`), put the repository settings in `.env`, then

```sh
docker compose run --rm --no-deps --entrypoint sh backup -c \
  'restic restore latest --tag ledgerline --host ledgerline --target /backups/restore'
scripts/restore.sh backups/restore/work/current/ledgerline.dump
```

The first command writes the dump and its manifest under `./backups/restore/`; the second restores it as for the local tier. Start the stack (`docker compose up -d`) so the migrations and the app role are set up, and delete `./backups/restore` afterwards.

**After every restore, re-apply offboarding.** A backup taken before a client left still holds that
client's tenant. Run `ledgerline delete-tenant` again (in the `migrate` service, see
`docs/pilot/ONBOARDING_RUNBOOK.md`, Offboarding) for every tenant in your offboarding log before
anyone signs in.

## Restore drill (monthly)

```sh
scripts/restore-drill.sh                 # latest snapshot
scripts/restore-drill.sh --snapshot abc123
```

On any machine with Docker (a laptop is fine). With the repository settings in the environment or in `.env`, it fetches the snapshot with the restic image, starts a throw-away compose project with random passwords, restores the dump, compares the row count of every table with the manifest, runs the migrations, starts the app and requests `/healthz`. It ends with `RESULT: PASS` or `RESULT: FAIL - <reason>` and removes the project. A local repository (absolute path in `RESTIC_REPOSITORY`) is mounted instead of reached over the network. `--keep` leaves the project running for a manual look; `DRILL_SKIP_APP=1` stops after the row counts.

Counts are compared before the migrations run, so a migration cannot hide a restore problem. Log the date, snapshot and result in your own records each month. A failed drill is an incident: the backups are not usable until it passes.

## Pilot measurements

No analytics service and no extra tracking table: uploads and processing times are derived from the audit events the app already writes (`inbox.receive`, `inbox.receive_failed`, `inbox.create_purchase`, `inbox.link_duplicate`). They hold channel, reason code and timestamps, never document contents or amounts.

```
docker compose exec app ledgerline pilot-report --from 2026-11-15 --to 2026-11-30 [--tenant N]
docker compose exec app ledgerline feedback-export --from 2026-11-15 --to 2026-11-30 > feedback.csv
```

- Channels: `web` (inbox upload), `phone` (capture page), `shortcut` (iOS shortcut token), `email` (mailbox).
- Time to processed runs from receipt to the first purchase created or linked from the inbox document. Rejected documents are excluded; documents still waiting are counted separately. Percentiles are nearest-rank, dates are UTC.
- Not counted: requests with an invalid token (no administration to attribute them to), duplicates of a document already received, and events from before this release (no channel recorded; they fall back to the source, so shortcut uploads show as `phone`).
- Feedback comes from the "Feedback" link in the app (owners and bookkeepers). The text is personal data, stored verbatim in `feedback_entries`, included in the administration ZIP export and deleted with the tenant by `delete-tenant`. The form refuses empty text, text over 5000 characters and NUL bytes, and allows 20 messages per user per hour (per process).
- The CSV has a `tenant_id` column. Its text is verbatim except that a cell starting with `=`, `+`, `-`, `@`, tab or carriage return gets a leading `'` so a spreadsheet does not run it as a formula.
- The terminal report escapes control characters (including ANSI and OSC sequences) as `\uXXXX`; newline and tab stay. Storage and CSV keep the original characters.
- Both commands refuse to run when the database role is a superuser, has BYPASSRLS or owns tables without forced row-level security, because every tenant would then see all rows. Use the application role, or pass `--allow-rls-bypass` knowingly. An unknown `--tenant` is an error.
- Known limits: a receive failure is recorded in the polling session, so a poll that fails and re-fetches the message can record the failure twice (receipts are deduplicated by hash, failures are not). Unknown senders, unroutable mail, size refusals made before the app sees the file, unexpected errors and uploads without a filename are not counted as failures.

## Monitoring

`GET /healthz/deep` is unauthenticated, rate limited (30 requests per minute per client address, then 429) and returns JSON with only check names, ages and percentages:

```json
{"status":"ok","checks":{"database":{"ok":true},"worker":{"ok":true,"age_seconds":1},
 "backup":{"ok":true,"age_hours":3.2,"offsite":true},"disk":{"ok":true,"free_percent":32.8}}}
```

HTTP 503 with `"status":"fail"` when any check fails; the failing check has `"ok":false` and, where useful, an `error`.

| Check | Fails when |
|---|---|
| database | `SELECT 1` as the app role fails |
| worker | the worker heartbeat file in the ops volume is older than 120 s or missing |
| backup | no successful backup in the last 26 h, or (`LEDGERLINE_HEALTH_REQUIRE_OFFSITE_BACKUP=true`, set by the bootstrap) the last one was not copied off-site |
| disk | free space of the volume under 10 % |

The heartbeat and the backup marker are files in a shared Docker volume (`ops`), so no table or migration is involved.

### External uptime monitor

Use any uptime service that can request a URL, check the status code and alert by email or chat. Point it at `https://<your domain>/healthz/deep`, interval 1 to 5 minutes, alert on any status other than 200, require two consecutive failures to avoid noise. The monitor must run outside this server and outside this provider, otherwise one outage silences both.

### Dead-man's-switch pings

A monitor that polls cannot notice a job that quietly stopped. Create two checks at a service such as healthchecks.io (free tier) or on a self-hosted instance and put their ping URLs in `.env`:

- `LEDGERLINE_HEALTHCHECK_PING_URL`: pinged by the worker every `LEDGERLINE_HEALTHCHECK_PING_SECONDS` (300) after a pass that reached the database. Set the check's period to 5 minutes with a grace of 10 to 15 minutes.
- `BACKUP_HEALTHCHECK_PING_URL`: pinged by the backup job at start, on success and on failure (`/fail`). Period 1 day, grace 3 hours.

Both pings are best effort and never stop the worker or the backup. Both are outgoing requests from the server to the service you chose.

## Incident checklist

1. Note the time you became aware of the problem. Write down what you see, not what you assume.
2. Contain: stop the exposure (`docker compose stop app`, revoke the credential, close the port). Do not delete logs or the evidence.
3. Decide whether personal data was or may have been exposed (access by someone unauthorised, loss of a device or a backup, a leaked key, data sent to the wrong party).
4. If yes, the 72-hour clock matters. Under Article 33 of the GDPR (AVG) the controller notifies the supervisory authority without undue delay and, where feasible, within 72 hours of becoming aware, unless the breach is unlikely to result in a risk to people; a later notification must say why it is late. A processor notifies the controller without undue delay. For the pilot: your clients are the controllers of their administrations' data and you are their processor, so tell every affected client at once, in writing, with what happened, which data, which period, what you did and what they should do. Clients then notify the Autoriteit Persoonsgegevens (AP) through its data breach form on autoriteitpersoonsgegevens.nl and, when the risk to people is high, inform the people concerned. For data you hold as controller (your own sign-in accounts, billing contacts) you notify the AP yourself. Source for the text of the duty: GDPR Article 33, gdpr-info.eu/art-33-gdpr, read 2026-11-08; have a lawyer confirm your role split in the processing agreement.
5. Record every breach in an incident log (facts, effects, measures), also the ones you do not report.
6. Fix the cause, rotate secrets (below), and restore from backup if data was altered. Run the restore drill afterwards.
7. Write the post-mortem: timeline, cause, what detected it, what would have detected it sooner.

Quick diagnostics: `docker compose ps`, `docker compose logs --tail=200 app worker backup`, `curl -s https://<domain>/healthz/deep`, `df -h`, `docker system df`.

## Rotating secrets

| Secret | How |
|---|---|
| `LEDGERLINE_APP_DB_PASSWORD` | change in `.env`, `docker compose up -d` (migrate sets the role password, app and worker restart) |
| `POSTGRES_PASSWORD` | the owner password is stored in the database; change it with `ALTER ROLE ledgerline PASSWORD '...'` through `docker compose exec db psql -U ledgerline`, then change `.env` and `docker compose up -d` |
| `LEDGERLINE_SECRET_KEY` | change in `.env`, `docker compose up -d`; every user is signed out |
| `RESTIC_PASSWORD` | `restic key add` with the new password and `restic key remove` for the old one (run restic through the backup image with the repository environment), update `.env` and the password manager before removing the old key |
| storage access key | create a new key at the provider, update `.env`, `docker compose up -d backup`, run a backup, delete the old key |
| ping URLs | regenerate at the monitoring service, update `.env`, `docker compose up -d` |
| SSH | add the new key to `~deploy/.ssh/authorized_keys` before removing the old one |

After any rotation caused by a suspected leak, treat it as an incident (checklist above).
