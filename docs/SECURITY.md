# Security

## Reporting a vulnerability

Send details privately to the maintainer (see the repository profile) rather than opening a public issue. Include the version, steps to reproduce and the impact. Expect an acknowledgement within a few days; this is a volunteer project and has no bug bounty.

## What the application does

- Passwords: argon2id via argon2-cffi, minimum 12 characters, rehashed on login when parameters change.
- Sessions: signed cookie (`ledgerline_session`), `SameSite=Lax`, `Secure` by default, limited lifetime (`LEDGERLINE_SESSION_MAX_AGE_SECONDS`).
- CSRF: every state-changing route requires a token (form field or `X-CSRF-Token` header for HTMX).
- Login throttling: 5 failed attempts per email in 15 minutes lock that email out for the window. The counter is in memory per process, so it resets on restart and is not shared between workers.
- Roles: `owner` can write, `accountant` is read-only (403 on every write). Exports of personal data are owner-only.
- Isolation: every query is scoped by administration; ids from forms are checked against the current administration.
- Headers: a strict Content-Security-Policy (no inline script or style, same-origin only), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, restrictive `Permissions-Policy`.
- Ledger integrity: journal entries and the audit trail are append-only, enforced by database triggers.
- No outbound network calls at runtime and no third-party assets.
- The container runs as a non-root user.

## What it does not do

- No two-factor authentication.
- No password reset by email (an owner can create a replacement user in Settings or with `ledgerline create-user`).
- No encryption of data at rest: use an encrypted disk or volume on the host.
- No rate limiting beyond the login throttle: put the reverse proxy in front for that.
- No malware scanning of uploads.

## Operator checklist

- Strong random `LEDGERLINE_SECRET_KEY` and `POSTGRES_PASSWORD`; never commit `.env`.
- Serve over HTTPS only (see DEPLOY.md); keep `LEDGERLINE_COOKIE_SECURE=true`.
- Do not publish the database port. Compose does not expose it.
- Keep Docker images updated (`docker compose pull && docker compose up -d --build`) and test restores (BACKUP_RESTORE.md).
- Copy backups off the host and treat them as personal data.

## Reporting a vulnerability

Please report security problems privately through GitHub: the repository's **Security** tab, then
**Report a vulnerability**. Do not open a public issue. Include the steps to reproduce and the
version (commit) you tested.
