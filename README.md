# Ledgerline

Self-hosted double-entry bookkeeping for Dutch sole traders and small businesses: sales and
purchase invoices, bank statement import and matching, a ledger that can't be edited after
posting, the BTW (VAT) return and ICP overview, and the standard financial reports.

> **Not financial or tax advice.** Ledgerline prepares figures; it does not file anything with
> the Belastingdienst and is not certified software. Check every figure before you file.
> See [docs/COMPLIANCE_NL.md](docs/COMPLIANCE_NL.md) for what has and hasn't been verified.

## What it does

| Area | Features |
|---|---|
| Sales | Draft → issue with gap-free numbering, Dutch invoice-requirement checks, credit notes, PDF, UBL 2.1 (Peppol BIS Billing 3.0 file) |
| Inbox | Supplier invoices by email (IMAP), upload or phone; e-invoices (UBL) read exactly, PDF text read with sanity checks, photos and scans read by an optional, opt-in extraction service; review before anything is booked |
| Phone | Installable web app that opens on the camera, plus an iOS share-sheet shortcut with revocable upload keys ([docs/IPHONE.md](docs/IPHONE.md)) |
| Purchases | Entry with expense account and VAT as stated by the supplier, reverse charge (domestic, EU, non-EU), PDF/image attachment, duplicate guard |
| Bank | CAMT.053 (.001.02/.08) and MT940 import, duplicate/overlap detection, ranked match suggestions, book-to-account, undo, SEPA credit-transfer file for paying suppliers |
| Ledger | Journal with manual entries, reversals, chart of accounts, per-account ledger, period lock |
| VAT | Return per month/quarter/year mapped to the rubrieken with drill-down to the bookings, ICP overview, optional settlement entry |
| Reports | Dashboard, profit & loss, balance sheet, trial balance, aged receivables/payables, CSV export |
| Platform | Setup wizard, in-app user guide, multiple administrations, read-only accountant role, contacts, GDPR export and anonymisation, legal page templates |

**Deliberately not included:** live bank feeds (PSD2 needs a licensed provider), sending
invoices over Peppol (needs a certified access point), filing returns through Digipoort (needs a
PKIoverheid certificate), payment links, foreign currency, a native app-store app.

## Quick start (Docker)

```bash
cp .env.example .env          # set LEDGERLINE_SECRET_KEY and POSTGRES_PASSWORD
docker compose up -d --build  # app on http://127.0.0.1:8000, Postgres, nightly backups
docker compose exec app ledgerline create-user --email you@example.com --role owner
```

For plain-HTTP local use set `LEDGERLINE_COOKIE_SECURE=false`; behind HTTPS keep it `true`.
To explore with fictional data: `docker compose exec app ledgerline seed-demo`.

New here? The setup wizard opens from the dashboard; the [user guide](docs/USER_GUIDE.md) is also
available in the app under Help.

Email intake and photo extraction are off until configured: see `.env.example`,
[docs/EXTRACTION.md](docs/EXTRACTION.md) and [docs/IPHONE.md](docs/IPHONE.md). Phone and email use
require the server to be reachable over HTTPS.

Deployment on a VPS with HTTPS: [docs/DEPLOY.md](docs/DEPLOY.md).
Backups and restore drill: [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).
Security notes: [docs/SECURITY.md](docs/SECURITY.md).

## Development

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), Docker (tests start a throw-away
PostgreSQL) and Pango for PDF rendering (`brew install pango` on macOS).

On macOS, a uv-managed Python cannot see Homebrew's libraries; `.python-version` pins 3.13 so
uv uses Homebrew's interpreter, or run with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.

```bash
uv sync
uv run ruff check src tests
uv run pytest                  # unit + integration tests against real PostgreSQL
```

### End-to-end tests

```bash
docker compose up -d --build && docker compose exec app ledgerline seed-demo
cd e2e && npm ci && npx playwright install chromium && npx playwright test
```

The suite signs in, renders every page on desktop and mobile with zero console or CSP errors,
creates, issues and downloads an invoice through the UI, and turns an uploaded supplier PDF
into a purchase. It expects a freshly seeded database. CI runs all three layers.

## Design decisions

- **Money is integer cents**, maths in `Decimal`, floats refused at the boundary. VAT is
  calculated per invoice per rate and rounded half-up.
- **The ledger is append-only at the database level.** PostgreSQL triggers refuse updates and
  deletes of posted entries, refuse lines added to an earlier entry, check every entry balances
  at commit, and refuse entries inside a locked period. Corrections are reversing entries.
  The audit log is append-only the same way.
- **VAT tagging on journal lines** (`vat_code` + `base`/`vat` role) drives the return, so every
  rubriek total can be traced to the bookings behind it.
- **Server-rendered UI** (FastAPI, Jinja, htmx) with a strict Content Security Policy: no
  inline scripts or styles, no external hosts, only functional cookies.
- **One database holds everything**, attachments included, so one `pg_dump` is a full backup.

## Licence

Copyright (c) 2026 Raju Shanigarapu. Licensed under the GNU Affero General Public License v3.0 only
([LICENSE](LICENSE)). If you run a modified version for other people over a network, you must offer
them its source code; the app links to it from the sidebar (`LEDGERLINE_SOURCE_URL`).

Versions published before the commit that introduced the AGPL remain available under the MIT
licence they were released with. See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

Third-party components and their licences:
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
