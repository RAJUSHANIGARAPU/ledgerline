# ADR 0001: Multi-tenancy with a shared schema and PostgreSQL row-level security

- Status: accepted
- Date: 2026-11-08

## Context

Ledgerline started as a single-owner, self-hosted app. One installation holds one or more
administrations (sets of books), and every logged-in user can open every administration:
`/administration/switch` only checks that the id exists. That is fine for one person's books and
wrong as soon as two businesses share a server.

Ledgerline is now meant to be sold as a hosted service as well as self-hosted. Each paying customer
must see only their own data, an accounting office must be able to serve several clients from one
login, and isolation must hold even when an application query forgets a filter.

## Terms

- **Tenant**: one customer account (a sole trader, a BV, or an accounting office).
- **Administration**: one set of books inside a tenant. A tenant has one or more.
- **User**: a login, global across the installation.
- **Membership**: a user's role in one tenant: `owner`, `bookkeeper` (writes, no user management)
  or `accountant` (read-only). A user can belong to several tenants.

## Options considered

| Option | Isolation | Cost per tenant | Operations | Verdict |
|---|---|---|---|---|
| Shared schema, `tenant_id` column, row-level security (pool) | Enforced by the database per row | Lowest | One deployment, one migration run | **Chosen** |
| Schema per tenant (bridge) | Separate schemas, shared server | Low | Migrations run once per tenant; hard past ~100 tenants | Rejected |
| Database or stack per tenant (silo) | Strongest | Highest | N deployments to upgrade and back up | Kept as a possible paid "dedicated" tier; same image, no code change |

The pool model is the only one whose cost fits a low-price product for sole traders. Its main
risk, a missing filter, is moved from the application into the database.

## Decision

1. **Schema.** New tables `tenants` and `memberships(user_id, tenant_id, role)`. Every table that
   holds bookkeeping data gets a `tenant_id` column, including child tables such as
   `journal_lines` and invoice lines, so every policy is a single-column comparison.
   `users`, `tenants` and `memberships` are not tenant-scoped; the application reads them to
   decide which tenant a request may act for.
2. **Row-level security** on every tenant-scoped table, `ENABLE` and `FORCE`, with one policy
   `USING (tenant_id = ledgerline_current_tenant()) WITH CHECK (same)`.
   `ledgerline_current_tenant()` reads the transaction-local setting `app.tenant_id` and
   **raises when it is missing**, so a query without tenant context fails loudly instead of
   returning nothing or everything.
3. **No bypass switch.** There is no setting that turns the policies off; a flag the application
   role can set would let any code path (or an injection) disable isolation. Work that spans
   tenants (the background worker, email routing) loops over tenants and sets each one in turn.
   Looking up the tenant of an administration from an email address uses a narrow
   `SECURITY DEFINER` function that returns only the tenant id.
4. **New rows get their tenant from the database.** `tenant_id` has a server default of the
   current setting, so service code does not pass it and cannot pass the wrong one; the
   `WITH CHECK` clause rejects a mismatch.
5. **Two database roles.** Migrations run as the owner role. The application and worker connect
   as `ledgerline_app`: `LOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`, with only data privileges.
   Superusers and `BYPASSRLS` roles ignore policies, so this split is what makes the policies
   apply at all. Settings: `LEDGERLINE_DATABASE_URL` (application role) and
   `LEDGERLINE_MIGRATION_DATABASE_URL` (owner).
6. **Per-request context.** After login the user picks a tenant if they have more than one. The
   session stores the tenant id; every request checks the membership and its role, and a
   SQLAlchemy `after_begin` hook runs `set_config('app.tenant_id', …, true)` at the start of
   every transaction, so the context survives commits in the middle of a request.
   Switching administrations is limited to administrations of the current tenant (the database
   would refuse others anyway).
7. **Roles move from users to memberships.** `users.role` is replaced by `memberships.role`.
8. **Upgrade path for existing installations.** The migration creates tenant 1, assigns every
   administration and row to it, and gives every existing user a membership in it with their
   current role. A self-hosted single-owner installation keeps working unchanged.
9. **Per-tenant features.** Exports, GDPR anonymisation, phone upload keys and the email inbox
   address work per administration and therefore per tenant. Billing, sign-up and a per-tenant
   mail domain are out of scope for this change.

## How isolation is proven

- The whole existing test suite runs as `ledgerline_app`, so every feature is exercised with the
  policies active.
- For every tenant-scoped table: as `ledgerline_app` with no tenant set, a query raises; with
  tenant A set, no row of tenant B is returned, and inserting or updating a row of tenant B fails.
- For every route that takes an id: tenant A's user requesting tenant B's id gets 404. The route
  list is generated from the application, not maintained by hand.
- A test fails when a table with an `administration_id` or `tenant_id` column exists without an
  enabled, forced policy.
- Memberships: a user without a membership in a tenant cannot select it; an `accountant` member
  gets 403 on every write route.

## Consequences

- Restoring one tenant from backup becomes an export and import, not a database restore.
- Every new table must carry `tenant_id` and a policy; the guard test enforces it.
- Queries pay a small cost for the policy predicate; `tenant_id` is indexed.
- The silo option stays open: a dedicated deployment is the same image with one tenant.
- Moving to hosted billing and sign-up later builds on `tenants` without schema changes to the
  books.
