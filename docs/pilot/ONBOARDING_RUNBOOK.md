# Pilot onboarding runbook

What the operator does for each new pilot client, in order. Commands run on the server in the deploy directory. `docker compose exec app ledgerline ...` is how this document writes the CLI; `ledgerline --help` lists every subcommand.

Steps that depend on features still being merged carry a verify marker in the source of this file.

## 0. Before the first client (once)

- Run the full [acceptance test](../ACCEPTANCE_TEST.md) on the production server and file the signed sheet.
- Review the data processing agreement and pilot agreement templates with a lawyer or privacy adviser, and keep a clean copy with the brackets filled in as your master.
- Fill in the operator details on the legal pages (`/legal/privacy`, `/legal/terms`) and check them (acceptance test TC-22).
- Decide whether document extraction stays off (default). If you want it, complete the provider terms check in article 6.3 of the data processing agreement first.

## 1. Paperwork first

1. Send the client the data processing agreement (`VERWERKERSOVEREENKOMST.md`) and the pilot agreement (`PILOT_AGREEMENT.md`) with the brackets filled in.
2. Both signed and stored (folder outside the app, e.g. `[LOCATION]`) before any data is entered. Record date and version in the client register `[LOCATION]`.
3. Agree who the owner user is, the email address to use, the support channel and the 24-hour security contact (the data processing agreement, annex 2).

## 2. Create the tenant and the owner

Today's CLI (see `src/ledgerline/cli.py`):

```sh
docker compose exec app ledgerline create-tenant --name "<Client name>"
# prints: Created tenant <id>: <Client name>

docker compose exec app ledgerline create-administration \
  --name "<Client name>" --legal-form eenmanszaak --tenant <id>
# legal forms: see the dropdown under Settings; the chart of accounts is created with it
```

Then the owner login. Two routes:

- **Invite by email** (preferred, no password passes through you): <!-- verify after feat/account-security merges --> create the owner membership as the first owner user and send the invitation from Settings, Users (the exact labels are confirmed after the feature merges). The owner accepts and sets a password. Until the feature is merged use the second route.
- **CLI, with a password you hand over**:

  ```sh
  docker compose exec -e LEDGERLINE_INITIAL_PASSWORD='<12+ char passphrase>' app \
    ledgerline create-user --email owner@client.example --role owner --tenant <id>
  ```

  Send the password over a different channel than the login address and ask the owner to change it at once (Settings, Change password). Never reuse a password across clients.

If the person already has a login for another tenant (an accountant serving several clients): `ledgerline add-membership --email accountant@firm.example --tenant <id> --role accountant`. Roles are `owner`, `bookkeeper` and `accountant` (read-only). After sign-in they pick the client on the "Choose a workspace" screen.

<!-- verify after feat/account-security merges: ask the owner to enrol two-factor authentication at first login and store the recovery codes. -->

Check: sign in as the owner in a private window. You should land on the dashboard of that tenant and nothing else (acceptance test TC-01, TC-18).

## 3. Set the administration up

1. Sign in as the owner (or sit with them) and finish the [setup wizard](../USER_GUIDE.md#getting-started): business details (KvK, VAT number, address, IBAN, invoice prefix), VAT situation (regular or KOR), business bank account, optional opening balances.
2. Check one issued sample invoice against the invoice requirements (see the user guide, "What the invoice must contain").
3. Create the accountant user with role Accountant if the client's bookkeeper wants read access.

## 4. Inbox address and phone

1. Hand over the private inbox address from the Inbox page (`mailbox+<token>@domain`). Tell the client it is private, how to forward supplier invoices to it, and that the owner can replace it under Settings, Inbox email address, Create a new address (which disables the old one).
2. Phone: the owner opens `/capture` on the phone, adds it to the home screen (docs/IPHONE.md, route 1), then under Settings, Phone devices creates a token and builds the "Send to Ledgerline" shortcut (route 2). Test both with a real receipt, check the document appears in the Inbox, and that a repeat is reported as a duplicate.
3. Remind the client what is checked by eye: a photo or scan always needs every figure checked.

<!-- verify after feat/ops-backups-monitoring merges: confirm the mailbox polling and backup monitoring include the new tenant. -->

## 5. First-quarter parallel run

The aim is to trust the figures before the first filing.

1. Month 1 to 3: the client keeps entering the same data in their existing method (or their bookkeeper does) and in Ledgerline.
2. Every month: compare bank balance, open debtors and creditors, and revenue and costs per account between the two. Write the differences and their cause down.
3. End of the quarter, before the VAT filing: print the [VAT return](/vat) overview, compare every rubriek (1a, 1b, 3b, 4b, 5a, 5b, total) with the existing method; check the ICP overview if VEU lines exist. Differences are resolved first, the client files from their own method in the first quarter, and only after a match do they file from Ledgerline.
4. Lock the filed quarter on the period lock page (`/ledger/lock`).

## 6. Check-ins and feedback

- Day 7: brief call. Is the client able to sign in, issue, process the inbox, import the bank file?
- Day 30: the review in the pilot agreement (article 9). Collect: what took longest, what was wrong or unclear, what was missing. Note them as issues with the client's name removed.
- After the first VAT quarter: second review and the decision under article 11 (convert, export, delete).

## 7. Offboarding

1. Confirm the choice in writing (convert, export or delete).
2. **Export:** the owner downloads the full administration ZIP from Settings, Privacy and data (Export administration (ZIP)); you check it opens. Optionally also hand over the CSV reports.
3. **Revoke access:** deactivate the client's users in Settings, Users (Deactivate), revoke phone tokens under Phone devices, and create a new inbox address so the old one stops working.
4. **Delete the data:** **tenant deletion is not built.** There is no CLI command or screen that removes a tenant, and the journal and audit tables are append-only by database trigger, so a plain `DELETE` is refused. That is a gap, not a procedure. What exists today:
   - Until a deletion command exists: deactivate the users (the data remains in the database and in the backups, and is now inaccessible through the app), tell the client honestly that deletion is not complete, and record the date.
   - Complete deletion today means dropping data directly in PostgreSQL as the owner role (the migration role), which has to bypass the triggers and the row-level security and must clear every tenant table, the attachments inside the database and all backups taken so far. This is destructive and error-prone and must not be improvised for a client; write and test the procedure on a drill copy first, or build a `delete-tenant` command with tests (see Gaps).
   - The backups age out after `BACKUP_KEEP_DAYS` (default 30). Off-site copies are yours to purge.
5. Write the confirmation of deletion required by article 12.3 of the data processing agreement only for what is actually deleted.
6. Keep the signed paperwork for as long as your own obligations require.

## Gaps this runbook exposes

- No `delete-tenant` (and no administration delete): offboarding cannot honestly end with "deleted".
- No per-tenant database dump: the `backup` service dumps the whole database; a per-client export beyond the ZIP needs a script.
- No invitation flow, 2FA or password reset until the account-security work merges (see the verify markers).
- Account-existence oracle when adding a user that is registered in another tenant (docs/SECURITY.md): when you onboard a person who already has a login, use `add-membership`, not `create-user`.
