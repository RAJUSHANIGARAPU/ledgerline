# Security

## Reporting a vulnerability

Send details privately to the maintainer (see the repository profile) rather than opening a public issue. Include the version, steps to reproduce and the impact. Expect an acknowledgement within a few days; this is a volunteer project and has no bug bounty.

## What the application does

- Passwords: argon2id via argon2-cffi, minimum 12 characters, rehashed on login when parameters change.
- Sessions: signed cookie (`ledgerline_session`), `SameSite=Lax`, `Secure` by default, limited lifetime (`LEDGERLINE_SESSION_MAX_AGE_SECONDS`).
- CSRF: every state-changing route requires a token (form field or `X-CSRF-Token` header for HTMX).
- Login throttling: 5 failed attempts per email in 15 minutes lock that email out for the window. The counter is in memory per process, so it resets on restart and is not shared between workers.
- Tenants: every customer account is a tenant; a user can belong to several. Roles live on the membership: `owner` (everything, including users, phone tokens, business settings with the IBAN, creating administrations and the inbox address), `bookkeeper` (writes the books) and `accountant` (read-only, 403 on every write). Exports of personal data are owner-only. The membership is re-checked on every request.
- Isolation: PostgreSQL row-level security on every table that holds bookkeeping data (`ENABLE` and `FORCE`, one `tenant_isolation` policy per table). The application connects as `ledgerline_app`, which is not a superuser and has no `BYPASSRLS`. There is no setting that switches the policies off. A query without tenant context raises instead of returning rows. See docs/adr/0001-multi-tenancy.md.
- Email intake: each administration has a random inbox token (`mailbox+<token>@domain`). A `SECURITY DEFINER` function maps an exact token to its administration and tenant and returns nothing otherwise. Mail without a valid token is rejected when several tenants exist.
- References between tenant tables are composite `(tenant_id, id)` foreign keys, so the database refuses a row that points into another tenant.
- The phone upload API counts failed token attempts per client address (20 in 10 minutes, then 429, in memory per process).
- Within a tenant every query is also scoped by administration; ids from forms are checked against the current administration.
- Headers: a strict Content-Security-Policy (no inline script or style, same-origin only), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, restrictive `Permissions-Policy`.
- Ledger integrity: journal entries and the audit trail are append-only, enforced by database triggers.
- No outbound network calls at runtime and no third-party assets.
- The container runs as a non-root user.

## What it does not do

- Row-level security limits what a forgotten filter can leak. It does not stop code that is able to run arbitrary SQL as `ledgerline_app` (for example through an SQL injection): that code can set `app.tenant_id` itself. Foreign keys are checked by the database without the policies, so isolation of references between tables relies on the application checks listed above.
- Accepted residual risk, tenant context: the tenant selector is the setting `app.tenant_id`, which the app role can set. RLS protects against a missing filter, not against SQL injection. The code builds no raw SQL from input; `tests/tenancy/test_hardening.py` fails when a `text()` or `execute()` argument is built with an f-string, `.format`, `%` or concatenation.
- Accepted residual risk, shared tables: `users` and `memberships` are not under RLS, so a read primitive in the app role also reads every tenant's password hashes (argon2id) and membership map.
- Known limitation: adding a user answers "already exists" for an email that is registered in another tenant (an account-existence oracle). Invitations are future work.
- Known limitation: row ids come from global sequences, so invoice and document ids reveal overall volume across tenants. Public ids are future work.
- No two-factor authentication.
- No password reset by email (an owner can create a replacement user in Settings or with `ledgerline create-user`).
- No encryption of data at rest: use an encrypted disk or volume on the host.
- No rate limiting beyond the login throttle: put the reverse proxy in front for that.
- No malware scanning of uploads.

## Operator checklist

- Strong random `LEDGERLINE_SECRET_KEY`, `POSTGRES_PASSWORD` and `LEDGERLINE_APP_DB_PASSWORD`; never commit `.env`.
- Keep the owner database credentials out of the app and worker (compose does this); never point `LEDGERLINE_DATABASE_URL` at a superuser outside local development.
- Serve over HTTPS only (see DEPLOY.md); keep `LEDGERLINE_COOKIE_SECURE=true`.
- Do not publish the database port. Compose does not expose it.
- Keep Docker images updated (`docker compose pull && docker compose up -d --build`) and test restores (BACKUP_RESTORE.md).
- Copy backups off the host and treat them as personal data.

## Reporting a vulnerability

Please report security problems privately through GitHub: the repository's **Security** tab, then
**Report a vulnerability**. Do not open a public issue. Include the steps to reproduce and the
version (commit) you tested.
