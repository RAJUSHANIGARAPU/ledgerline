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

The migrations seed an empty tenant called "Default". `create-tenant` removes it, and prints that it did, when it has no logins, administrations, documents or other data; it never touches one that is in use or has been renamed. Run the command with the database owner role configured (`LEDGERLINE_MIGRATION_DATABASE_URL`); without it the seeded tenant is left in place. On an install with real tenants the leftover "Default" would clutter the tenant list and stop the single-tenant mail default (`LEDGERLINE_IMAP_DEFAULT_ADMINISTRATION_ID`) from working.

Then the owner login. Two routes:

- **Invite by email** (preferred, no password passes through you): send the invitation from the server, so you never sign in inside the client's tenant:

  ```sh
  docker compose exec app ledgerline invite-owner --tenant <id> --email owner@client.example
  ```

  The owner gets the normal invitation mail (Dutch and English, a single-use link valid for 7 days) and chooses their own password. The command prints an error and exits 1 if the mail could not be sent (check the SMTP settings); run it again to issue a fresh link. It needs `LEDGERLINE_SMTP_HOST` and `LEDGERLINE_SMTP_FROM` on the `app` service. Once the owner is in, further invitations come from Settings, Users, where "Resend invitation" retries a failed mail.
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

## 3a. Moving a client in (they already have a business)

Use this when the client starts on a date in the middle of their business life, ideally the first day of a quarter, and has contacts, unpaid invoices and a bank balance in an existing system. Samples: [contacts](../samples/contacts-import.csv), [Excel NL, comma and BOM](../samples/contacts-import-excel-nl.csv), [open invoices](../samples/opening-items.csv). Design and trade-offs: [ADR 0002](../adr/0002-opening-balances.md).

Prepare with the client before the session:

1. Agree the **start date** (the evening of the day before is the balance date). Everything before it stays in the old system and its filed VAT returns; everything from it is Ledgerline's.
2. Get three exports from the old system as at the day before the start date: contacts, the open debtors list (unpaid sales invoices with the original number, date, due date, total incl. VAT and the amount still open) and the open creditors list, plus the bank balance per account.
3. Save each as CSV from Excel ("CSV UTF-8" or Dutch "CSV (gescheiden door lijstscheidingsteken)"; separator ; or , both work). Use the template downloads on the import pages for the exact columns.

Then, in this order, as owner or bookkeeper:

1. **Contacts.** Contacts, Import CSV (or `ledgerline import-contacts --file contacts.csv`, which previews and only saves with `--apply`). Read the preview: rows with errors are fixed in the file and uploaded again (already imported rows are skipped as duplicates, so re-uploading is safe), duplicates are matched on VAT number, KvK number, or name plus postcode. Customers without address or city can be imported, but no invoice can be issued to them until both are filled in.
2. **Bank accounts and opening balances.** Setup steps 3 and 4. Use the start date as the date.
3. **Open invoices.** Setup step 5. Enter the start date once (it is then fixed for everything), upload the open debtors and creditors, read the preview, confirm. Items are imported all together or not at all. A partly paid invoice has an open amount lower than its total.
4. **Check.** The step shows "Assets = Liabilities + Equity on <start date>" and the carried-over receivables and payables. Compare them with the old system's debtor and creditor totals on that date; they must agree to the cent. The difference between receivables and payables sits on equity account 0580 ("Opening balance, open items"); ask the accountant whether it stays there or is moved to the owner's equity.
5. **Look at what the items do and do not do.** Reports, Aging shows them under their due dates; the VAT return, the ICP overview and the profit and loss for the first quarter show none of them (their revenue and VAT were reported in the old system). Check this once with the client on the first quarter's VAT page.
6. **First bank file.** Import the bank statement from the start date. Payments of the old invoices are suggested by invoice number and amount like any other open invoice; match them.

Things to tell the client:

- Opening items cannot be edited or credited in Ledgerline. A mistake before anything is matched is fixed with Remove (it reverses the entry), then add it again with a corrected number. After a payment is matched, undo the match first.
- Use an invoice prefix the old system did not use (Setup step 1), so a new number never equals an imported one.
- A credit note for an old invoice is a manual correction or stays in the old system.

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
4. **Delete the data** on the server, as the owner role (the `migrate` service has it):
   ```
   sudo install -d -o 10001 -g 10001 -m 750 offboarding
   docker compose run --rm -v "$PWD/offboarding:/export" migrate \
     ledgerline delete-tenant --tenant <id> --confirm "<exact tenant name>" --export-dir /export
   ```
   The container runs as uid 10001. If `offboarding/` is missing, Docker creates it owned by root and the export fails with `PermissionError: ... /export/tenant-<id>-administration-<n>.zip`; nothing is deleted in that case, because the export runs first. Create it with the first command, once, before the delete. The ZIPs it writes belong to uid 10001: use `sudo` to copy or remove them.
   The command refuses unless `--confirm` is the tenant's exact name, writes one export ZIP per
   administration first, then deletes every row of that tenant (bookkeeping, attachments, inbox,
   tokens, memberships) and the logins that belonged to no other tenant, and checks that nothing
   is left. Hand the ZIPs to the client, then delete your copy.
   - **Backups still contain the data** until their retention ends (off-site: up to 14 daily,
     8 weekly, 24 monthly and 8 yearly snapshots; local dumps: `BACKUP_KEEP_DAYS`). Individual
     tenants cannot be removed from a whole-database snapshot. State this in the confirmation.
   - **Keep an offboarding log outside the database** (tenant id, name, date). After any restore
     from backup, run `delete-tenant` again for every tenant in that log, otherwise deleted clients
     come back.
5. Write the confirmation of deletion required by article 12.3 of the data processing agreement only for what is actually deleted.
6. Keep the signed paperwork for as long as your own obligations require.

## Gaps this runbook exposes

- No per-tenant database dump: backups cover the whole database; the export ZIP is the per-client copy.
- Deleted tenants remain in backups until the backups expire (see step 4 of offboarding).
