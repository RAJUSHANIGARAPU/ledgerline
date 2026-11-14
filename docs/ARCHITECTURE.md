# Architecture

How Ledgerline is put together and deployed. Decisions behind it are in [docs/adr](adr); how to
run it is in [OPERATIONS.md](OPERATIONS.md).

## 1. Deployment (one server)

Everything runs on one Linux server (Hetzner Cloud in the reference setup) from
`docker-compose.yml`. Only Caddy is reachable from the internet.

```mermaid
flowchart LR
    subgraph users["People and devices"]
        browser["Browser<br/>(owner, bookkeeper, accountant)"]
        phone["iPhone shortcut<br/>(upload key)"]
        uptime["Uptime monitor"]
    end

    subgraph server["Server (EU)"]
        caddy["Caddy<br/>HTTPS, 12 MB body limit"]
        subgraph compose["docker compose"]
            app["app<br/>FastAPI + htmx<br/>role: ledgerline_app"]
            worker["worker<br/>inbox extraction + mailbox polling<br/>role: ledgerline_app"]
            migrate["migrate (one-shot)<br/>migrations + grants<br/>role: owner"]
            backup["backup<br/>pg_dump + restic, nightly<br/>role: owner"]
            db[("PostgreSQL 16<br/>row-level security")]
            ops[["ops volume<br/>heartbeat, backup markers"]]
            local[["./backups<br/>local dumps"]]
        end
    end

    subgraph external["External services"]
        imap["Mailbox (IMAP)<br/>invoices+token@domain"]
        smtp["SMTP<br/>invites, password reset"]
        s3["Object storage (S3)<br/>encrypted restic repository"]
        hc["Dead-man's-switch pings<br/>(healthchecks.io or similar)"]
        llm["Anthropic API<br/>(opt-in photo extraction)"]
    end

    browser -->|HTTPS| caddy
    phone -->|"POST /api/v1/inbox"| caddy
    uptime -->|"GET /healthz/deep"| caddy
    caddy --> app

    app --> db
    worker --> db
    migrate --> db
    backup --> db

    app --- ops
    worker --- ops
    backup --- ops
    backup --> local

    worker -->|poll| imap
    app -->|send| smtp
    app -.->|opt-in| llm
    worker -.->|opt-in| llm
    backup -->|encrypted| s3
    worker -->|ping| hc
    backup -->|ping| hc
```

Not connected on purpose: banks (statements are uploaded as CAMT.053/MT940/CSV files), the
Belastingdienst (the VAT return is prepared, the owner files it in Mijn Belastingdienst Zakelijk)
and Peppol (UBL files are generated, not sent).

| Service | Runs as | Purpose |
|---|---|---|
| `migrate` | database owner | Runs migrations and `ensure-app-role`, then exits; app and worker start only after it succeeded. Also runs `delete-tenant`. |
| `app` | `ledgerline_app` | Web UI, phone upload API, health endpoints. Never receives owner credentials. |
| `worker` | `ledgerline_app` | Reads new inbox documents and polls the mailbox, one tenant at a time; writes a heartbeat. |
| `backup` | database owner | Nightly dump with a row-count manifest, local copy plus encrypted off-site snapshot, weekly `restic check`. |
| `db` | — | PostgreSQL 16 with forced row-level security on every bookkeeping table. |

## 2. How a request gets its tenant

Isolation is enforced by the database, not by remembering filters in the code
([ADR 0001](adr/0001-multi-tenancy.md)).

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as app (FastAPI)
    participant S as SQLAlchemy session
    participant P as PostgreSQL

    B->>A: request with signed session cookie
    A->>P: load user, check session version
    A->>P: load membership (user, tenant) and its role
    alt no active membership in that tenant
        A-->>B: back to the tenant chooser / sign-in
    end
    A->>S: session.info["tenant_id"] = tenant
    S->>P: after_begin: set_config('app.tenant_id', tenant, true)
    A->>P: queries without tenant filters
    P-->>A: only rows where tenant_id = ledgerline_current_tenant()
    Note over P: no tenant set: the query raises (fail loud)<br/>row of another tenant: invisible, insert refused
    A-->>B: page
```

Writes are allowed for `owner` and `bookkeeper`; user management, devices, business details
and new administrations are `owner` only; `accountant` is read-only. Foreign keys between
bookkeeping tables are composite `(tenant_id, id)`, so a row can never point into another tenant.

## 3. Document intake

```mermaid
flowchart TD
    mail["Email to invoices+&lt;inbox token&gt;@domain"] --> poll["worker polls IMAP"]
    poll -->|valid token| route["route to administration + tenant<br/>(definer function, exact token match)"]
    poll -->|no valid token, several tenants| rejected["moved to Rejected folder"]
    upload["Upload on /inbox"] --> receive
    phone["Phone capture or iOS shortcut"] --> receive
    route --> receive["receive(): sniff type, dedupe by SHA-256,<br/>store attachment in the database"]
    receive --> extract{"extract"}
    extract -->|"UBL / e-invoice"| exact["read exactly"]
    extract -->|"PDF with text"| text["read text + reconciliation checks"]
    extract -->|"photo or scan, extraction on"| vision["Anthropic API, monthly cap"]
    extract -->|nothing readable| manual["manual entry"]
    exact --> review["Review screen: owner checks every figure"]
    text --> review
    vision --> review
    manual --> review
    review -->|create| draft["Purchase invoice draft"]
    review -->|create and book| booked["Booked: balanced journal entry"]
```

## 4. Backups and restore

```mermaid
flowchart LR
    db[("PostgreSQL")] -->|"pg_dump + per-table row counts<br/>(same snapshot)"| job["backup job (nightly)"]
    job --> local[["./backups<br/>local tier"]]
    job -->|"restic, encrypted"| s3["Object storage<br/>14 daily · 8 weekly · 24 monthly · 8 yearly"]
    job -->|marker| ops[["ops volume"]]
    ops --> deep["/healthz/deep<br/>backup younger than 26 h?"]
    job -->|"success / fail ping"| hc["dead-man's switch"]
    s3 -->|"monthly restore drill"| drill["throw-away stack:<br/>restore, migrate, compare row counts,<br/>start app, RESULT: PASS/FAIL"]
```

A restore brings back tenants that were deleted after the backup was taken: re-run
`delete-tenant` for every tenant in the offboarding log afterwards (OPERATIONS.md, Restore).

## 5. Code map

| Package | Responsibility |
|---|---|
| `ledgerline.ledger` | Double-entry posting, chart of accounts, period lock (rules also enforced by database triggers) |
| `ledgerline.invoicing` | Sales and purchase invoices, PDF (WeasyPrint), UBL export |
| `ledgerline.banking` | CAMT.053 / MT940 / CSV import, matching, SEPA payment files |
| `ledgerline.vat` | VAT codes, return per rubriek with sources (`vat/sources.py`), ICP |
| `ledgerline.reports` | Dashboard, P&L, balance sheet, aged debtors and creditors |
| `ledgerline.inbox` | Intake, extraction pipeline, email polling, worker |
| `ledgerline.mobile` | Phone capture page, upload API, device tokens |
| `ledgerline.accounts` | Invitations, password reset, two-factor login |
| `ledgerline.platform` | Contacts, settings, GDPR export and anonymisation, legal pages, tenant offboarding |
| `ledgerline.onboarding` | Setup wizard, in-app user guide |
| `ledgerline.imports` | CSV import of contacts and opening items (open invoices on the start date), CLI `import-contacts` and `import-opening-items` |
| `ledgerline.ops` | Deep health check |
| `ledgerline.db`, `web`, `auth` | Tenant context per transaction, request dependencies, sign-in |
