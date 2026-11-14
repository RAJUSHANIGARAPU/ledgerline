# Security

## Reporting a vulnerability

Send details privately to the maintainer (see the repository profile) rather than opening a public issue. Include the version, steps to reproduce and the impact. Expect an acknowledgement within a few days; this is a volunteer project and has no bug bounty.

## What the application does

- Passwords: argon2id via argon2-cffi, minimum 12 characters, rehashed on login when parameters change.
- Sessions: signed cookie (`ledgerline_session`), `SameSite=Lax`, `Secure` by default, limited lifetime (`LEDGERLINE_SESSION_MAX_AGE_SECONDS`).
- CSRF: every state-changing route requires a token (form field or `X-CSRF-Token` header for HTMX).
- Login throttling: 5 failed attempts per email in 15 minutes lock that email out for the window. The counter is in memory per process, so it resets on restart and is not shared between workers.
- Tenants: every customer account is a tenant; a user can belong to several. Roles live on the membership: `owner` (everything, including users, phone tokens, business settings with the IBAN, creating administrations and the inbox address), `bookkeeper` (writes the books), `accountant` (read-only, 403 on every write) and `uploader` (phone capture only, nothing else). Exports of personal data are owner-only. The membership is re-checked on every request.
- Isolation: PostgreSQL row-level security on every table that holds bookkeeping data (`ENABLE` and `FORCE`, one `tenant_isolation` policy per table). The application connects as `ledgerline_app`, which is not a superuser and has no `BYPASSRLS`. There is no setting that switches the policies off. A query without tenant context raises instead of returning rows. See docs/adr/0001-multi-tenancy.md.
- Email intake: each administration has a random inbox token (`mailbox+<token>@domain`). A `SECURITY DEFINER` function maps an exact token to its administration and tenant and returns nothing otherwise. Mail without a valid token is rejected when several tenants exist.
- References between tenant tables are composite `(tenant_id, id)` foreign keys, so the database refuses a row that points into another tenant.
- The phone upload API counts failed token attempts per client address (20 in 10 minutes, then 429, in memory per process).
- Within a tenant every query is also scoped by administration; ids from forms are checked against the current administration.
- Invitations and password reset: tokens are `secrets.token_urlsafe(32)`; only their sha256 is stored (`auth_tokens`), with purpose (`invite`/`reset`), expiry (7 days / 1 hour) and `used_at`. They are single use (a conditional UPDATE, so two concurrent requests cannot both succeed) and are looked up by hash with a constant-time comparison. An invite names one pending membership, so it can only activate that tenant. A reset invalidates the user's other open reset tokens and raises `users.session_version`, which every request checks, so existing sessions end (also on password change and "sign out everywhere").
- No account-existence oracle: inviting an address, and "Forgot password?", answer identically whether or not the address has a login (mail is sent after the response). Reset requests are limited to 3 per hour per address and 10 per hour per client address; invitations to 30 per hour per owner; in memory per process like the login throttle.
- Two-factor login (TOTP, RFC 6238, 30 s steps, +-1 step drift): the secret is Fernet-encrypted at rest with a key derived (HKDF-SHA256) from `LEDGERLINE_SECRET_KEY`. A code is accepted once: the last used step is stored and updated with a conditional UPDATE. After the password the session holds only a short-lived (5 min) "password ok" marker, never the user id; 5 wrong codes end that login, and 10 wrong codes in 15 minutes lock the account's second step (counted server side, so replaying an old cookie does not reset it). 10 recovery codes (80 bits each) are shown once and stored as HMAC-SHA256 with a key derived from the secret key: they are high-entropy random values, so a keyed fast hash is enough and avoids an argon2 cost per guess. A tenant owner can require two-factor for the whole tenant; turning it off needs the password and a current code and is refused while a workspace requires it.
- Audit: invitation sent and accepted, reset requested and completed, password changed, 2FA enabled, disabled and recovery code used, login failures. User-level events are written into every tenant the person belongs to. No tokens, codes, secrets or passwords are logged or audited.
- Headers: a strict Content-Security-Policy (no inline script or style, same-origin only), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, restrictive `Permissions-Policy`.
- Ledger integrity: journal entries and the audit trail are append-only, enforced by database triggers.
- No outbound network calls at runtime and no third-party assets.
- The container runs as a non-root user.

## What it does not do

- Row-level security limits what a forgotten filter can leak. It does not stop code that is able to run arbitrary SQL as `ledgerline_app` (for example through an SQL injection): that code can set `app.tenant_id` itself. Foreign keys are checked by the database without the policies, so isolation of references between tables relies on the application checks listed above.
- Accepted residual risk, tenant context: the tenant selector is the setting `app.tenant_id`, which the app role can set. RLS protects against a missing filter, not against SQL injection. The code builds no raw SQL from input; `tests/tenancy/test_hardening.py` fails when a `text()` or `execute()` argument is built with an f-string, `.format`, `%` or concatenation.
- Accepted residual risk, shared tables: `users` and `memberships` are not under RLS, so a read primitive in the app role also reads every tenant's password hashes (argon2id) and membership map.
- Known limitation: row ids come from global sequences, so invoice and document ids reveal overall volume across tenants. Public ids are future work.
- No encryption of data at rest: use an encrypted disk or volume on the host.
- Rate limiting is in memory per process (login, reset, invitations, second step): it resets on restart and is not shared between workers. Put the reverse proxy in front for real limits.
- A user who loses both the authenticator and the recovery codes needs an operator: delete the row in `user_totp` as the database owner after verifying the person.
- Email is not signed or encrypted end to end: a reset link is as safe as the mailbox. Use mailbox 2FA.
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
