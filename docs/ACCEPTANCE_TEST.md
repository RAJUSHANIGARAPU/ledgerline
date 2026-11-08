# Acceptance test

The script the owner runs on the **production server** before onboarding each pilot client. It doubles as the release checklist: run it again after every deploy that touches code.

It takes about two to three hours the first time. Every case lists a precondition, the steps with the exact screen, button and URL, the expected result, and a pass/fail box. A failed case blocks onboarding until the cause is understood and the case is repeated. Write down what you saw next to every failure.

Steps that depend on features still being merged carry a marker (`<!-- verify after ... -->` in the source). Treat them as drafts until the marker has been removed after a real run.

## Ground rules

- **Use a throw-away tenant, never a client tenant.** Create a new one per run (the name carries the date):

  ```sh
  docker compose exec app ledgerline create-tenant --name "Acceptance 2026-11-08"
  docker compose exec -e LEDGERLINE_INITIAL_PASSWORD='<12+ chars>' app \
    ledgerline create-user --email tester@example.com --role owner --tenant <tenant id>
  docker compose exec app ledgerline create-administration --name "Acceptatie Testbedrijf" \
    --legal-form eenmanszaak --tenant <tenant id>
  ```

  The id is printed by `create-tenant`. After the run, remove it with `delete-tenant` (see ONBOARDING_RUNBOOK.md, "Offboarding"); never reuse it for a later run, because the VAT figures in TC-13 assume an empty administration.
- Use the production URL, over HTTPS, in a normal browser profile. `https://ledger.example.com` below stands for your `LEDGERLINE_PUBLIC_BASE_URL`.
- All data is fictional. Test IBANs are the standard documentation examples.
- Dates: the VAT test data is written for the fourth quarter of 2026 (1 October to 31 December 2026). Running in another quarter is fine: move every date into one quarter of your choice and the figures stay identical. Do not use a quarter that already has a period lock.
- Keep the downloaded PDF, UBL and CSV files together with this sheet as evidence.

## Test data (used from TC-08 onwards)

Create these contacts under [Contacts](/contacts) (New contact) in TC-07. Names must match exactly; the bank file in TC-11 matches on them.

| Contact | Kind | Country | VAT number | KvK | IBAN |
|---|---|---|---|---|---|
| Acceptatie Klant BV | customer | NL | NL123456789B01 | 12345678 | NL02ABNA0123456789 |
| Acceptatie EU Klant GmbH | customer | DE | DE123456789 | | |
| Acceptatie Leverancier BV | supplier | NL | NL987654321B01 | 87654321 | NL86INGB0002445588 |
| Acceptatie EU Leverancier GmbH | supplier | DE | DE987654321 | | |

Business account for the administration (TC-06): name "Zakelijk", IBAN `NL91ABNA0417164300`.

---

## A. Access and accounts

### TC-01 Login and tenant chooser

- Precondition: two tenants exist, each with the tester user as a member (create the second with `create-tenant` and `ledgerline add-membership --email tester@example.com --tenant <id> --role owner`).
- Steps:
  1. Open `/login`. Enter a wrong password. Press Sign in.
  2. Enter the right password.
  3. On "Choose a workspace" (`/tenants`) check both tenants are listed with the role in brackets, then press the button of the first one.
  4. Press Sign out in the sidebar, then open `/` directly.
- Expected: step 1 shows "Wrong email or password." and no session; step 3 lands on the dashboard of the chosen tenant; step 4 redirects to `/login`. With a single membership the chooser is skipped.
- [ ] Pass  [ ] Fail

### TC-02 Invite a member, accept, set a password

<!-- verify after feat/account-security merges -->

- Precondition: signed in as owner. Use a mailbox you can read.
- Steps:
  1. Settings (`/settings`), Users, enter the mailbox address and role Bookkeeper, press the invite button (label to be confirmed after the feature merges).
  2. Open the invitation email. Check the sender, that the link starts with the production HTTPS address, and that no password is in the mail.
  3. Open the link in a private window, choose a password of at least 12 characters, submit.
  4. Sign in with the new password.
  5. Open the same link again.
- Expected: the email arrives within one minute; step 3 creates the account and the user lands in the right tenant with role Bookkeeper; step 5 is refused (link is single-use). The owner sees the user in the Users table.
- [ ] Pass  [ ] Fail

### TC-03 Two-factor enrolment, login with code, recovery code

<!-- verify after feat/account-security merges -->

- Precondition: a user with a password, on a phone with an authenticator app.
- Steps:
  1. Open the account security page (path to be confirmed after the feature merges). Start enrolment, scan the QR code, enter the six-digit code.
  2. Write down the recovery codes shown once.
  3. Sign out. Sign in with email and password. Enter the current code when asked.
  4. Sign out. Sign in again but enter a wrong code three times.
  5. Sign out. Sign in and use one recovery code instead of the code.
  6. Sign out and try the same recovery code again.
- Expected: login without the second factor never opens a session; a right code does; the recovery code works exactly once; wrong codes are refused and throttled.
- [ ] Pass  [ ] Fail

### TC-04 Password reset

<!-- verify after feat/account-security merges -->

- Steps:
  1. On `/login` press the forgotten-password link (label to be confirmed). Enter the user's email. Then enter an address that is not registered.
  2. Open the reset email, follow the link, choose a new password.
  3. Sign in with the new password; try the old one.
  4. Open the reset link a second time.
- Expected: the screen reads the same for a known and an unknown address (no account oracle); the email arrives; the old password stops working; the link works once and expires. An account with 2FA still asks for the second factor after the reset.
- [ ] Pass  [ ] Fail

## B. Setup

### TC-05 Setup wizard

- Precondition: signed in as the tester owner in the new tenant. Open [`/setup`](/setup).
- Steps: complete the seven steps as the user guide describes.
  1. Business details: name "Acceptatie Testbedrijf", legal form eenmanszaak, KvK 12345670, VAT number NL123456789B01, address, postcode, city, IBAN `NL91ABNA0417164300`, invoice prefix, payment terms 30 days. Save.
  2. VAT situation: Regular VAT. Save.
  3. Bank account: "Zakelijk", `NL91ABNA0417164300`.
  4. Opening balances: skip.
  5. Skip. 6. Check the inbox address is shown. 7. Skip.
- Expected: each step shows "done" afterwards on `/setup`; a malformed VAT number or KvK number is refused with a message; the settings page (`/settings`) shows the saved values.
- [ ] Pass  [ ] Fail

### TC-06 Business account

- Steps: [Bank](/bank), check "Zakelijk" is listed (created in TC-05). Try adding the same IBAN again under Add bank account; try the invalid IBAN `NL00ABNA0000000000`.
- Expected: duplicate refused ("this IBAN is already registered"); invalid IBAN refused.
- [ ] Pass  [ ] Fail

### TC-07 Contacts

- Steps: [Contacts](/contacts), New contact. Create the four contacts from the test data table. Open "Acceptatie Klant BV" and press Edit, change the city, Save. Search for "Acceptatie".
- Expected: all four are listed and searchable; edits persist; a malformed VAT or IBAN is refused with a message.
- [ ] Pass  [ ] Fail

## C. Sales

### TC-08 Sales invoices: create, issue, PDF, UBL

- Steps: [Sales invoices](/sales), New invoice (`/sales/new`). Create five drafts dated within the quarter (the number is assigned on issue):

  | Ref | Customer | Line | Account | VAT code | Net |
  |---|---|---|---|---|---|
  | S1 | Acceptatie Klant BV | Advies | 8000 Revenue 21% | V21 | 1.000,00 |
  | S2 | Acceptatie Klant BV | Training | 8010 Revenue 9% | V9 | 500,00 |
  | S3 | Acceptatie EU Klant GmbH | Dienst EU | 8030 Revenue EU | VEU | 800,00 |
  | S4 | Acceptatie Klant BV | Onderdeel | 8000 Revenue 21% | V21 | 200,00 |
  | S5 | Acceptatie Klant BV | Afrondingstest (1 x 10,10) | 8000 Revenue 21% | V21 | 10,10 |

  1. Save draft for S1, open it, check the totals (1.210,00), then Issue invoice.
  2. Open the issued invoice and press PDF. Check name, address, VAT number, KvK, invoice number, dates, lines, VAT amount, IBAN and payment terms; open the file in a PDF reader other than the browser viewer.
  3. Press UBL (Peppol), open the XML in a text editor and check it is well-formed and shows the same number and totals.
  4. Issue S2 to S5. For S3 check the PDF says "btw verlegd".
  5. On S4 press Credit note, confirm.
  6. Try to Issue a draft for "Acceptatie EU Klant GmbH" after removing that customer's VAT number (Edit contact); restore it afterwards.
- Expected: S1 totals are 1.210,00; S2 545,00; S3 800,00; S5 12,22 (VAT 2,12 rounded half up). Numbers are consecutive and gap-free in the list. Issued invoices cannot be edited. S4 now shows cancelled, with a credit note in the list that has negative amounts. Step 6 refuses issuing for a reverse-charge supply without the customer's VAT number and says what is missing.
- [ ] Pass  [ ] Fail

## D. Purchases and inbox

### TC-09 Purchase via inbox upload

- Precondition: any one-page PDF named like a supplier invoice. Extraction may be off; then the figures are typed by hand.
- Steps: [Inbox](/inbox), choose the file, Upload. Open the document. Pick supplier "Acceptatie Leverancier BV", invoice number `ACC-2026-01`, invoice date in the quarter, one line: account 4200 Office supplies, net 400,00, code I21, VAT 84,00. Press Create and book. Upload the same file again.
- Expected: the document moves from New to Booked; the purchase shows total 484,00 and a journal entry; the second upload reports the file is already in the inbox. The attachment opens from the purchase.
- [ ] Pass  [ ] Fail

### TC-10 Purchase via email, via phone capture and the iOS shortcut

<!-- verify after feat/ops-backups-monitoring merges (mailbox polling health is monitored there) -->

- Precondition: mailbox intake is configured (`LEDGERLINE_IMAP_*`); the inbox token address is shown on [Inbox](/inbox).
- Steps:
  1. Email: from a normal mail account send a PDF to the private address shown on the Inbox page (`mailbox+<token>@domain`). Wait up to five minutes. Reload `/inbox`. Open it: supplier "Acceptatie Leverancier BV", number `ACC-2026-02`, account 4500 Training and books, net 100,00, code I9, VAT 9,00, Create and book. Then send a PDF to the address with one character of the token changed.
  2. Phone capture: on an iPhone in Safari open `https://ledger.example.com/capture`, sign in, take a photo or choose a PDF, press Upload to inbox. Open the result link. Book it for supplier "Acceptatie EU Leverancier GmbH", number `ACC-2026-03`, account 4300 Software and subscriptions, VAT code IEU21, net 300,00 (the VAT is computed, 63,00).
  3. Shortcut: under Settings, Phone devices (`/settings/devices`) press Create token, copy the `ll_` token. Build "Send to Ledgerline" as described in docs/IPHONE.md and share a photo through it. In `/inbox` check a new document with source phone, then press Reject on it. Revoke the token and share again.
- Expected: the first mail appears in the Inbox with source email; the mail with the changed token does not appear in any tenant (it is rejected); the phone upload shows "In your inbox"; the shortcut returns `"duplicate": false` the first time; after Revoke the shortcut gets 401. Each of the three purchases books to a balanced entry.
- [ ] Pass  [ ] Fail

## E. Bank

### TC-11 CAMT.053 import, match, undo

- Precondition: TC-08 to TC-10 done; S1 and the purchase `ACC-2026-01` are open. Take `docs/samples/acceptance-camt053.xml` from the repository (three fictional transactions on 20 November 2026, account `NL91ABNA0417164300`).
- Steps:
  1. [Bank](/bank), in the "Zakelijk" row choose the file and press Import. Repeat the same import.
  2. Open the reconcile screen (`/bank/reconcile`), tab Unmatched. Expected rows: +1.210,00 "Acceptatie Klant BV", -484,00 "Acceptatie Leverancier BV", -12,50 without counterparty.
  3. Press Accept all exact matches (Bank page or reconcile screen).
  4. For the -12,50 row press Book: account 4800 Bank charges, VAT code No VAT.
  5. Open the Matched tab. On the 1.210,00 row press Undo. Check the row is Unmatched again and S1 is open. Match it again with Match selected.
- Expected: step 1 first import adds 3 transactions, the repeat says "this file has already been imported"; step 3 matches the 1.210,00 receipt to S1 and the -484,00 payment to `ACC-2026-01`, both invoices show paid; step 5 posts a reversing journal entry (visible in the [journal](/ledger)) and restores the open state. Importing posted nothing to the ledger by itself.
- [ ] Pass  [ ] Fail

## F. Journal

### TC-12 Manual journal entry and reversal

- Steps: [Journal](/ledger), New entry (`/ledger/new`). Date in the quarter, description "Acceptatie storting", line 1 account 1000 Cash debit 100,00, line 2 account 0520 Owner's contributions credit 100,00, no VAT codes. Check the balance indicator, press Post entry. Open the entry and press Reverse this entry. Then try an entry where debit and credit differ.
- Expected: the entry gets a number and shows both lines; the reversal is a second entry with swapped sides, the original is unchanged; the unbalanced entry is refused and nothing is posted.
- [ ] Pass  [ ] Fail

## G. VAT

### TC-13 VAT return for the quarter with known figures

- Precondition: TC-07 to TC-12 done in the fresh administration, all dates inside one quarter, S4 credited, no period lock.
- Steps: open [VAT return](/vat). Period quarter, year 2026, number 4, Show.
- Expected figures (computed by hand from the test data):

  | Rubriek | Base (exact) | VAT (exact) | Base on form | VAT on form | From |
  |---|---|---|---|---|---|
  | 1a | 1.010,10 | 212,12 | 1010 | 212 | S1 1.000,00 + S5 10,10; S4 and its credit note net to zero |
  | 1b | 500,00 | 45,00 | 500 | 45 | S2 |
  | 3b | 800,00 | | 800 | | S3 (VEU) |
  | 4b | 300,00 | 63,00 | 300 | 63 | purchase 3, IEU21: 21% of 300,00 |
  | 5a | | 320,12 | | 320 | 212,12 + 45,00 + 63,00 |
  | 5b | | 156,00 | | 156 | 84,00 + 9,00 + 63,00 |
  | Total | | to pay 164,12 | | 164 | 5a minus 5b |

  Every other rubriek (1c, 1d, 1e, 2a, 3a, 4a) is zero. Check the arithmetic: the form column rounds owed amounts and bases down and deductible VAT up, so 5a on the form is 320 and not 320,12.
  Click rubriek 1a: the lines behind it are the S1 and S5 revenue and VAT lines plus the S4 and credit-note lines that cancel. Press "Post VAT settlement" and check the journal entry clears the VAT accounts. Check the filing deadline shown is 31 January 2027.
- [ ] Pass  [ ] Fail

### TC-14 ICP

- Steps: [ICP overview](/vat/icp) for the same period. Press Download CSV.
- Expected: one row, customer "Acceptatie EU Klant GmbH" with VAT number DE123456789, 800,00 services; the CSV holds the same row.
- [ ] Pass  [ ] Fail

### TC-15 Period lock

- Steps: [`/ledger/lock`](/ledger/lock). Enter 31 December 2026, Save lock date. Then try to post a manual entry dated 15 December 2026, and try to reverse the TC-12 entry again. Return to the lock page, clear the date and Save lock date; tick the confirmation checkbox "I understand this reopens closed periods" when it appears and save again.
- Expected: dated-in-lock entries are refused with a message; moving the lock back without the confirmation is refused; with it the lock is removed. Both changes appear in the audit log.
- [ ] Pass  [ ] Fail

## H. Reports

### TC-16 Profit and loss, balance check

- Steps: [Reports](/reports), Profit and loss (`/reports/pnl`) for the quarter; Balance sheet (`/reports/balance`); Trial balance (`/reports/trial`); Aged receivables. Press Download CSV on one of them.
- Expected: revenue 2.310,10 (1.000 + 500 + 800 + 10,10; S4 nets to zero), costs 812,50 (400 + 100 + 300 + 12,50), profit 1.497,60. The balance sheet shows "✓ Assets = Liabilities + Equity". The trial balance debit and credit totals are equal. Open receivables list S2, S3 and S5 (S1 is paid; S4 cancelled).
- [ ] Pass  [ ] Fail

## I. Privacy

### TC-17 Data export and anonymisation

- Steps: [Privacy and data](/settings/privacy). Press Export administration (ZIP) and open the archive. Open the contact "Acceptatie EU Klant GmbH", press Export personal data (JSON), then Anonymise contact and confirm. Open S3 afterwards, then repeat TC-13 and TC-14.
- Expected: the ZIP opens and contains only this administration's data; the JSON holds the contact's details; after anonymising, name, email, address, IBAN and KvK are blank (the VAT number and country are kept on purpose, see COMPLIANCE_NL.md) and the contact is marked "anonymised", S3 and the journal entries are still there, the VAT figures of TC-13 are unchanged. Only an owner sees these buttons.
- [ ] Pass  [ ] Fail

## J. Isolation and roles

### TC-18 Tenant isolation smoke test

- Precondition: a second tenant with its own owner (`create-tenant`, `create-user --tenant`, `create-administration --tenant`), and the numeric ids of S1 (`/sales/<id>`), a contact (`/contacts/<id>`), an inbox document (`/inbox/<id>`) and a journal entry (`/ledger/<id>`) from the first tenant.
- Steps: sign in as the second owner. Check `/sales`, `/contacts`, `/inbox`, `/ledger`, `/bank` are empty. Open each of the four ids above, plus `/sales/<id>/pdf`, `/purchases/<id>/attachment` and `/settings/privacy/export.zip`.
- Expected: lists are empty; every direct URL answers 404 (or 403), never the data; the ZIP contains nothing from the first tenant. Email sent to the first tenant's inbox address never shows up here.
- [ ] Pass  [ ] Fail

### TC-19 Read-only accountant cannot change anything

- Precondition: a user with role Accountant in the first tenant (Settings, Users, role "Accountant (read-only)").
- Steps: sign in as that user. Read the dashboard, [VAT return](/vat), [Reports](/reports) and a sales invoice. Then try to write: open `/sales/new` and press Save draft; Contacts, New contact and Save; Settings, Save settings; press Issue invoice or Post entry wherever offered; open `/settings/privacy/export.zip`.
- Expected: every read works; every write is refused with 403 and changes nothing (check the invoice list and the audit log afterwards); the owner-only exports are refused.
- [ ] Pass  [ ] Fail

## K. Operations

### TC-20 Backup ran, restore drill, deep health check

<!-- verify after feat/ops-backups-monitoring merges -->

- Steps:
  1. List `./backups` on the server. Check that a dump from the last 24 hours exists, that its size is plausible, and (once the feature merges) that the monitoring reports the last run as successful.
  2. Do the restore drill from BACKUP_RESTORE.md on a second machine or project name: take a fresh dump, restore it into a clean stack, sign in, open the VAT return of TC-13 and compare 5a, 5b and the total with production, open one purchase attachment. Tear the drill stack down.
  3. Request `https://ledger.example.com/healthz/deep` (path to be confirmed after the feature merges) and `/healthz`.
- Expected: dump present and recent; the restored stack shows identical figures (164,12 to pay, attachment opens); `/healthz` answers `{"status":"ok"}`; the deep check is green for database, migrations, backup age and the mailbox worker.
- [ ] Pass  [ ] Fail

### TC-21 Security headers and TLS

- Steps: run `curl -sI https://ledger.example.com/login` and read the headers. Open `http://ledger.example.com/login` (plain HTTP). Check the certificate in the browser. Open the browser console on a few pages.
- Expected headers: `Content-Security-Policy` (same-origin, no inline script or style), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy`. The session cookie `ledgerline_session` is marked `Secure` and `SameSite=Lax` (check in the browser's storage tab). HTTP redirects to HTTPS or is refused; certificate valid; no console errors about the policy.
- [ ] Pass  [ ] Fail

### TC-22 Legal pages reachable

- Steps: without signing in open `/legal/privacy`, `/legal/terms`, `/legal/cookies`, `/legal/disclaimer`.
- Expected: all four load without a session. Check that the operator name, contact address and the processor details on the privacy notice are filled in for the pilot, not template text.
- [ ] Pass  [ ] Fail

---

## Sign-off

| Field | Value |
|---|---|
| Version (commit hash running in production, `git rev-parse HEAD` in the deploy checkout) | |
| Server and URL | |
| Date | |
| Tester | |
| Cases passed / failed / skipped | / / |
| Failed or skipped cases and why | |
| Decision | [ ] Release / onboarding approved  [ ] Blocked |
| Signature | |
