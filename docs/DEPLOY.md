# Deploying Ledgerline

Ledgerline runs as the app, the background worker, PostgreSQL 16 and a nightly backup job (local dump plus an encrypted off-site copy), plus a one-shot `migrate` container that runs migrations at every start and exits. Everything is free software; no paid service is needed.

## Local, one command

Requires Docker with the Compose plugin.

```sh
cp .env.example .env
# edit .env: set LEDGERLINE_SECRET_KEY, POSTGRES_PASSWORD and LEDGERLINE_APP_DB_PASSWORD
# for plain http://localhost also set LEDGERLINE_COOKIE_SECURE=false
docker compose up -d --build
```

Open http://127.0.0.1:8000. The app refuses to start without `LEDGERLINE_SECRET_KEY`, and compose refuses to start without the three secrets. Generate each with `openssl rand -hex 24` (hex keeps the passwords safe inside database URLs).

Create the first owner, either by setting `LEDGERLINE_INITIAL_OWNER_EMAIL` and `LEDGERLINE_INITIAL_PASSWORD` in `.env` before the first start, or afterwards:

```sh
docker compose exec -e LEDGERLINE_INITIAL_PASSWORD='a-long-passphrase' app \
  ledgerline create-user --email you@example.com --role owner
docker compose exec app ledgerline create-administration --name "My Company" --legal-form eenmanszaak
```

A fresh installation has one tenant (id 1), so `--tenant` can be left out. With several tenants, create one with `ledgerline create-tenant --name "Client BV"` and pass `--tenant <id>` to `create-user` and `create-administration`. `ledgerline add-membership --email a@b.example --tenant 2 --role bookkeeper` gives an existing login access to another tenant (roles: `owner`, `bookkeeper`, `accountant`, `uploader`). `ledgerline seed-demo --email you@example.com` loads demo data into its own tenant and makes that user its owner.

Remove `LEDGERLINE_INITIAL_PASSWORD` from `.env` once the owner exists.

`LEDGERLINE_COOKIE_SECURE=false` is for local http only. With it on (the default), browsers drop the session cookie over plain http and you cannot sign in.

Migrations run automatically on every `docker compose up`, in the one-shot `migrate` service; app and worker start only after it completed successfully.

## Database roles

Tenant isolation relies on PostgreSQL row-level security (docs/adr/0001-multi-tenancy.md), and superusers and `BYPASSRLS` roles ignore it. So there are two roles:

| Role | Used by | Notes |
|---|---|---|
| `ledgerline` (`POSTGRES_USER`, the owner) | migrations, `ensure-app-role`, the backup service | superuser in the compose database; never used by the running app |
| `ledgerline_app` | app and worker | `NOSUPERUSER NOBYPASSRLS`, data privileges only; password from `LEDGERLINE_APP_DB_PASSWORD` |

On every `docker compose up` the `migrate` service runs `ledgerline migrate` and `ledgerline ensure-app-role` with the owner URL (`LEDGERLINE_MIGRATION_DATABASE_URL`) and exits. App and worker depend on it (`service_completed_successfully`) and receive only the app role URL (`LEDGERLINE_DATABASE_URL`); the owner credentials are not in their environment at all.

`ensure-app-role` grants data access only on tables that have row-level security enabled and forced and a `tenant_isolation` policy, plus `users`, `tenants` and `memberships`. A table without that policy stays inaccessible to the app role (fail closed), and no default privileges exist for future tables. Developers adding a table must give it `tenant_id` and a forced policy, otherwise the app gets "permission denied". Changing `LEDGERLINE_APP_DB_PASSWORD` and restarting rotates the password.

Without compose: set `LEDGERLINE_MIGRATION_DATABASE_URL` to the owner and `LEDGERLINE_DATABASE_URL` to `ledgerline_app`, run `ledgerline migrate` then `ledgerline ensure-app-role` (with `LEDGERLINE_APP_DB_PASSWORD`) as the owner. If `LEDGERLINE_MIGRATION_DATABASE_URL` is unset, migrations use `LEDGERLINE_DATABASE_URL`, which only makes sense for local development where a single superuser does everything (the isolation tests then do not apply to that setup).

The owner role must be a superuser or hold `BYPASSRLS`; the migration refuses to run otherwise. The backup (`pg_dump` and the per-table row counts of the manifest) and the two `SECURITY DEFINER` lookup functions (inbox address routing, phone tokens) read tables that have forced policies.

## A VPS with HTTPS (Caddy)

1. Any Linux VPS with Docker. Point a DNS A record (for example `books.example.com`) at it.
2. Clone the repository, create `.env` as above and keep `LEDGERLINE_COOKIE_SECURE=true`.
3. Leave `LEDGERLINE_BIND` at its default `127.0.0.1:8000` so the app is reachable only through the proxy.
4. `docker compose up -d --build`.
5. Install Caddy from its official packages and use this `/etc/caddy/Caddyfile`:

```
books.example.com {
    encode zstd gzip
    request_body {
        max_size 12MB
    }
    reverse_proxy 127.0.0.1:8000
}
```

The body limit stops oversized uploads at the proxy, before the app reads them. Caddy obtains and renews a Let's Encrypt certificate by itself. The app starts uvicorn with `--proxy-headers`, so the original scheme and client address are taken from the proxy. Open only ports 80 and 443 in the firewall (plus SSH).

## A Hetzner Cloud server, scripted

`deploy/hetzner/` holds a provisioning script (firewall, SSH key, server through the `hcloud` CLI), a cloud-init file that installs Docker, Caddy, fail2ban and unattended upgrades and starts the stack, and `update.sh`. Start with deploy/hetzner/README.md; the day-to-day runbook is OPERATIONS.md. The Caddyfile above is `deploy/hetzner/Caddyfile.template`.

## Updating

```sh
deploy/hetzner/update.sh v1.2.0
```

It backs up, deploys, waits for `/healthz` and rolls back to the previous commit and images when the new version does not become healthy (see OPERATIONS.md for its limits). The manual form is `git pull && docker compose up -d --build` after a backup (`scripts/backup.sh`). Migrations are applied on start. Upgrading an existing single-owner installation to multi-tenancy needs one new variable, `LEDGERLINE_APP_DB_PASSWORD`, in `.env`; the migration moves all data into tenant 1 and gives every existing user a membership there with their old role.

## Outbound email (invitations and password reset)

Set these in `.env` (compose passes them to the app):

| Variable | Meaning |
|---|---|
| `LEDGERLINE_SMTP_HOST` | SMTP server. Empty = email off. |
| `LEDGERLINE_SMTP_PORT` | 587 (default, STARTTLS) or 465 (SSL). |
| `LEDGERLINE_SMTP_TLS` | `starttls` (default), `ssl` or `none` (trusted local relay only). |
| `LEDGERLINE_SMTP_USER`, `LEDGERLINE_SMTP_PASSWORD` | Login, if the server needs one. |
| `LEDGERLINE_SMTP_FROM` | Sender address, for example `Ledgerline <noreply@books.example.com>`. Required. |
| `LEDGERLINE_ENVIRONMENT` | `production` (default) or `development`. |

Links in the emails use `LEDGERLINE_PUBLIC_BASE_URL`, so set it to your HTTPS address. With SMTP off the app logs a warning and sends nothing; owners see an error when they try to invite someone. In `development` the invitation and reset links are also written to the server log (`docker compose logs app`) so you can test without a mail server. Never use `development` on a server with real users.

Two-factor secrets are encrypted with a key derived from `LEDGERLINE_SECRET_KEY`. Changing that key (it also signs sessions) makes enrolled authenticator secrets and recovery codes unreadable, so users with two-factor login are locked out at the second step until an operator deletes their `user_totp` row. Rotate it deliberately.

## Operations notes

- Logs: `docker compose logs -f app`.
- Health: `GET /healthz` (also the container HEALTHCHECK) and `GET /healthz/deep` for an external monitor (database, worker heartbeat, last backup, free disk; 503 when any fails; rate limited).
- Data lives in the `pgdata` volume. Attachments are stored in the database, so one dump contains everything.
- Backups: see BACKUP_RESTORE.md and OPERATIONS.md. They are taken as the owner role and contain every tenant.
- Background work: the worker visits every tenant on each pass, one transaction per tenant.
- A backup stored on the same machine is not a disaster backup: set the `RESTIC_*` variables so the backup service also writes an encrypted copy to S3-compatible storage, and keep `RESTIC_PASSWORD` outside the server.
- Hosting provider: if a third party hosts the server, a data processing agreement is needed (see COMPLIANCE_NL.md).
