# Ledgerline user guide

This guide is for a Dutch sole trader (eenmanszaak, ZZP) who keeps their own books in Ledgerline. It follows the screens as they are. Ledgerline prepares figures and files; you check them and file them yourself. It is bookkeeping software, not tax advice. Sources for the Dutch rules are listed in `COMPLIANCE_NL.md` in the docs folder of the repository.

## Getting started

1. Install it: see the README (quick start) and `DEPLOY.md` in the docs folder. Both explain the one-command Docker setup and how to create the first owner.
2. Sign in at `/login` with the owner email and password. Use at least 12 characters for the password; you can change it under [Settings](/settings).
3. Open the [setup wizard](/setup). It has eight short steps. Every step can be skipped and picked up later: progress is read from your data, so a step shows "done" once the data exists.

| Step | What you do |
|---|---|
| 1. Business details | Name, legal form, KvK number (8 digits), VAT number (BTW-id), address, IBAN, email, invoice prefix, payment terms |
| 2. VAT situation | Regular VAT or the small business scheme (KOR) |
| 3. Bank account | Register your business account |
| 4. Opening balances | Optional: the bank balance on your start date |
| 5. Open invoices | Optional: unpaid sales and purchase invoices on your start date, when you move from another system |
| 6. First customer and invoice | Shortcuts to add a customer and create an invoice |
| 7. Inbox and phone | Where supplier invoices come in |
| 8. Accountant review | Reminder to have the first quarter reviewed, and the exports |

The VAT choice matters: with the KOR (turnover at most EUR 20,000 per calendar year) you do not charge VAT and cannot deduct input VAT. You register for the KOR with the Belastingdienst yourself; the setting only tells Ledgerline how to book. Details: [belastingdienst.nl/kleineondernemersregeling](https://www.belastingdienst.nl/kleineondernemersregeling). Ledgerline does not watch the turnover limit.

Opening balances: if you start mid-year, enter the balance of each bank account on your start date. Ledgerline posts one journal entry (bank against owner's equity). A second one is refused until you reverse the first (see [Troubleshooting](#troubleshooting-and-faq)).

## Moving in from another system

If you already have a business, pick a start date (usually the first day of a quarter) and bring three things over: your contacts, your unpaid invoices and your bank balance. Everything before the start date stays in the old system and in the VAT returns you filed from it.

1. **Contacts.** On [Contacts](/contacts) choose Import CSV. Download the template (separator ; for Dutch Excel, or ,), fill it in or export from your old system, upload it and read the preview. Each row says create, skip or error. A row is skipped as a duplicate when its VAT number or KvK number is already known, or its name and postcode are. Rows with errors are not imported: correct them in your file and upload again. UTF-8 files with or without a BOM work; amounts and dates are not needed here.
2. **Bank balance.** Step 4 of the [setup wizard](/setup), dated on your start date.
3. **Open invoices.** Step 5 of the wizard. Add them one by one or import a CSV: type (sales or purchase), your original invoice number or the supplier's reference, the contact, invoice date, due date, total including VAT and the amount still open. Amounts like 1.234,56 are fine and dates are yyyy-mm-dd or dd-mm-yyyy. The contacts must exist first. The whole file is imported or none of it.
4. **Check.** The step shows "Assets = Liabilities + Equity" on the start date. Compare the receivables and payables it lists with your old system.

What an open invoice does: you see it in the aging reports, and a bank payment that mentions its number is suggested for matching like any open invoice. What it does not do: it is not revenue, a cost or VAT in Ledgerline, so it is not on your VAT return, the ICP overview or your profit and loss; you reported it in the old system. Opening items cannot be edited, credited or downloaded as a PDF; remove one (this reverses its entry) and add it again if it was entered wrongly. Command line: `ledgerline import-contacts --file contacts.csv` and `ledgerline import-opening-items --file open.csv --start-date 2026-10-01` preview first and only save with `--apply`.

## Daily and weekly routine

- Once a week: process the [Inbox](/inbox) (supplier invoices that arrived by email, upload or phone).
- If you sell over a counter: enter the till's daily takings on [Daily takings](/takings).
- Download your bank file and import it on the [Bank](/bank) page.
- Match payments to invoices on the reconcile screen; book everything else to an account.
- Look at the [dashboard](/): unmatched bank transactions, overdue and draft invoices.

## Sending an invoice

1. Go to [Sales invoices](/sales) and choose New invoice ([new invoice](/sales/new)).
2. Pick the customer, set the invoice date and due date. "Supply date" is only needed if it differs from the invoice date.
3. Add lines: description, quantity, price and revenue account. The VAT code follows the account; change it per line where needed.
4. Save draft, review it, then Issue invoice. Issuing gives it a gap-free number and books it. An issued invoice can no longer be edited.
5. Use PDF to send it. UBL (Peppol) downloads an e-invoicing file; Ledgerline does not send it over Peppol.

### What the invoice must contain

Ledgerline refuses to issue an invoice when something required is missing: your business name, address, postcode, city, VAT number and KvK number, and the customer's name, address and city. The Belastingdienst lists the full requirements on its [invoice requirements page](https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/zakelijk/btw/administratie_bijhouden/facturen_maken/factuureisen/factuureisen). Ledgerline does not check free-text fields; compare a sample invoice with that page once.

### Credit notes

Open an issued invoice and choose Credit note. It credits the full amount and cancels the invoice. For a partial correction, issue a new invoice or ask your accountant.

### Reverse charge, EU and export

Choose the VAT code on the line:

| Code | Use |
|---|---|
| V21, V9 | Domestic sales at 21% or 9% |
| V0 | 0% / not taxed in NL |
| VRV | Domestic reverse charge (verlegd); the invoice says "btw verlegd" |
| VEU | Sales to an EU business (ICP); the invoice says "btw verlegd" |
| VEX | Export outside the EU |
| VVR | Exempt from VAT |
| VKOR | Used automatically for every line when the KOR is on |

VRV and VEU need the customer's VAT number; without it the invoice is not issued.

## Recording purchases

Four ways in, all ending as a purchase invoice you book:

1. Email: forward or have suppliers send invoices to your administration's private address
   (`mailbox+<token>@domain`, shown on the [Inbox](/inbox) page and in [Settings](/settings); the
   mailbox itself is set up by whoever runs the server, see `LEDGERLINE_IMAP_*` in `.env.example`).
   Keep the address private; the owner can create a new one in Settings, which disables the old one.
   Attachments land in the Inbox.
2. Upload: drop PDFs, photos or e-invoice (UBL/XML) files on the [Inbox](/inbox) page.
3. Phone: open [Capture](/capture) on your phone and take a photo, or use the "Send to Ledgerline"
   share-sheet shortcut. Create its upload key under [Phone devices](/settings/devices); the steps
   are in `docs/IPHONE.md` in the repository.

Each inbox document is read automatically and shows how it was read: an e-invoice (UBL) is exact,
PDF text needs a check, and a photo or scan (only if extraction is switched on, see
`docs/EXTRACTION.md`) needs every figure checked. Pick or create the supplier, correct anything that
is off, then choose **Create purchase draft** or **Create and book**. If the supplier and invoice
number already exist, the inbox warns you and offers to link the document to that purchase instead.

Or enter it by hand: [New purchase invoice](/purchases/new). Fill in the supplier, the supplier's invoice number, dates and lines, attach the PDF, PNG or JPEG, save the draft, then Book invoice.

Always check the amounts and the VAT code before booking. Reverse-charge codes (for example IEU21 for EU purchases) post both the VAT you owe and the VAT you deduct. Keep the original documents: records must be kept 7 years (10 for real estate). Ledgerline stores the attachment in its database, so it is part of every backup.

## Forward invoices from Gmail / Outlook automatically

Ledgerline never connects to your mailbox. Instead your mailbox forwards the invoice mails to your administration's private address, shown on the [Inbox](/inbox) page (`mailbox+<token>@domain`). Keep the address private. Only mail with attachments (PDF, JPEG, PNG, UBL XML) creates documents, and nothing is booked without your review. Only new mail is forwarded, not mail already in the mailbox.

### Gmail

1. Gmail, Settings, **Forwarding and POP/IMAP**, add your Ledgerline address as a forwarding address. Gmail sends it a verification link ([Google: forward Gmail messages](https://support.google.com/mail/answer/10957)).
2. That mail arrives in Ledgerline. The owner sees the link as a notice at the top of the Inbox page, for 7 days or until dismissed. Open it while signed in to Gmail, then refresh Gmail's forwarding settings. Ledgerline never opens the link itself and shows only links on google.com.
3. Turn off "Forward a copy of incoming mail" if it was switched on: by default Gmail forwards all mail to a new address. Then create a filter instead ([Google: create filters](https://support.google.com/mail/answer/6579)): in the search options use a query like

   `has:attachment filename:pdf (factuur OR invoice OR rekening)`

   and add your suppliers with `from:` (for example `{from:leverancier.example from:energie.example}`, which matches either). Operators are described in [Google: search operators](https://support.google.com/mail/answer/7190). Click **Create filter**, choose **Forward it to** and select the Ledgerline address.
4. Test the query in the Gmail search bar first. If an invoice is missed, add its sender to the `from:` list.

### Outlook

1. Outlook on the web: Settings, Mail, Rules, Add rule. Choose the conditions you need, and the action **Forward to** with your Ledgerline address ([Microsoft: use rules to automatically forward messages](https://support.microsoft.com/office/45aa9664-4911-4f96-9663-ece42816d746)). Use "Forward to", not "Forward as attachment": the invoice must arrive as an attachment of its own.
2. Business Microsoft 365: an administrator can block automatic forwarding to external addresses. The rule then does not work and the sender gets a bounce ([Microsoft: control automatic external email forwarding](https://learn.microsoft.com/defender-office-365/outbound-spam-policies-external-email-forwarding)). Ask your IT administrator to allow it for this mailbox.
3. Not verified: whether Outlook needs a separate confirmation step for the new address, and which sender such a mail would use. Ledgerline only shows the confirmation notice for Gmail.

## Daily takings (till sales)

For businesses that sell over a counter. Until the till's own data is connected, you enter the figures from its daily report (Z-report) by hand. Open [Daily takings](/takings).

- Record a day: the date, the sales including VAT per rate (21%, 9%, 0%) and how the money came in (cash, card/pin, other such as vouchers). The sales per rate must add up to the amounts received; otherwise the day is refused and the difference is shown.
- Ledgerline posts one journal entry per day: revenue per rate, the VAT inside the gross amounts (gross x rate / (100 + rate), rounded half-up to the cent per rate per day), and the amounts received. The VAT lands in the VAT return like an invoice at the same rate.
- Cash goes to Cash. Card payments go to Card payments to receive and other receipts to Vouchers and other receipts to settle. When the card payout reaches your bank, import the statement and on the reconcile screen use "Book to an account" with Card payments to receive; the balance of that account then shows what the acquirer still owes you.
- Bulk entry: Import CSV. Download the template (one row per day; columns date, gross_21, gross_9, gross_0, cash, card, other, note), fill in a quarter and upload it. The whole file is checked first: every problem is listed with its line number and nothing is posted unless all rows are valid.
- Each date can be recorded once. To change a day use Correct (reverses the day with a reversing entry and posts the new amounts) or Reverse. A day in a locked period cannot be recorded, corrected or reversed.
- Negative days (more refunds than sales) are not accepted; book those with a journal entry.
- Weekly and monthly totals: [Totals](/takings/totals) sums the recorded days per week (Monday to Sunday) or month, per rate and per receipt method, so you can compare them with the till's own period report.
- The 0% column uses the code for sales at 0% or not taxed in NL. Ledgerline does not decide for you which of your sales are exempt or at 0%; check that with your accountant or the Belastingdienst.

## Bank

- Add the account on [Bank](/bank) (name, IBAN). In your online banking look for "Download transactions" and choose CAMT.053 or MT940 if your bank offers them, then upload the file with Import. If it does not, a CSV works too. Importing posts nothing to the ledger.
- Reconcile: for each transaction, Match selected pairs it with open invoices, "Accept all exact matches" takes every exact amount match in one go, "Book to an account" posts a cost or receipt with an optional VAT code, Ignore sets it aside.
- Undo: on the Matched and Ignored tabs, Undo reverses the match (a reversing journal entry is posted) and puts the transaction back to Unmatched.
- The same file twice is refused ("this file has already been imported").

### CSV files

- Rabobank: the standard 26-column CSV (header starts with "IBAN/BBAN", "Munt", "BIC", "Volgnr") is recognised and needs no setup. Layout source: Rabobank, "Formaatbeschrijving CSV (csv-extensie)", version 1.2, April 2022, <https://media.rabobank.com/m/4a7130f6f0042a57/original/pdf_formaatbeschrijving_csv-pdf.pdf> (read 2026-11-14). A file holding several accounts is imported for the account you chose; the other rows are left out and reported.
- ING, ABN AMRO, bunq and Knab have no built-in layout, because none of them publishes the column layout of its CSV or TXT export on its own site (checked 2026-11-14; ING's layout only turned up on third-party mirrors, the others in forum posts and converters). Their files use the column mapper below.
- Column mapper: when a CSV is not recognised, Ledgerline shows the first rows and asks which column is the date, the amount (one signed column, an amount with an Af/Bij or D/C column, or separate debit and credit columns), the counterparty, IBAN and description. Date format and decimal separator can stay on Detect. Tick "Remember this mapping" and the next file of that account imports without the step; if its headers change you are asked again.
- Read as: separators ; , tab; UTF-8 (with or without BOM) or Windows-1252; amounts like 1.234,56, 1,234.56, -12,50, 12,50-, (12,50) and +99,00; dates like 2026-06-30, 30-06-2026, 30/06/2026, 30.06.2026 and 20260630 (day first when in doubt). An amount such as 1.234 or 1,234 with exactly three digits after one separator is refused as ambiguous: set the decimal separator in the mapper. Only EUR is supported. Any bad row stops the import and names its row number; nothing is half-imported.

### Importing the same payments in two formats

Say you import a CAMT.053 for June and later a CSV that also covers June. Different formats carry different references and wording, so file contents cannot be compared. Ledgerline instead treats a CSV or CAMT line as already stored when a line of another format (or a manual line) on the same bank account has the same amount, a booking or value date that agrees on both sides, and a counterparty IBAN that does not contradict (an unknown IBAN is not a contradiction). Each stored line is used up once, so two equal payments on one day need two stored lines. Lines left out are counted in the import message. Within one format, repeated payments are handled by the bank reference or, without one, by date, amount, IBAN and text. The rule can wrongly drop a genuinely new payment that has the same amount and date as a stored one from a counterparty with no IBAN in the file; check the "left out" count after mixing formats.

### Lines entered by hand

Use "Add a line by hand" under the bank account only when no file is available. Enter date, amount and direction, counterparty and a reason (for example "read from the banking app"). The line is labelled "Manually entered" in the reconciliation, also after matching, and the audit log records who entered it, when, and the reason. A later file containing the same payment does not add it again (see above). A manual line can be matched, booked, ignored or undone like any other.
- A payment to the Belastingdienst: book it to the VAT settlement account (see below).

## The BTW quarter

Open [VAT return](/vat). Choose Period (month, quarter or year), the year and the number, then Show.

- The table lists each rubriek (1a, 1b, 2a, 3a, 3b, 4a, 4b, 5b ...) with the base and the VAT. Click a rubriek to see the lines behind it.
- Each rubriek links to the belastingdienst.nl page it was checked against. 1c (sports canteens at 13%) and 3c (installation or distance sales in other EU countries) have no VAT code and show zero.
- The two right-hand columns are what you type into the form: whole euros, rounded in your favour as the Belastingdienst allows (amounts owed and bases down, deductible VAT up; negative amounts keep the minus sign). Rubriek 5a is filled in for you in the online return.
- The page shows the deadline for filing and paying: the last day of the month after the quarter.
- Private use of business assets (rubriek 1d): book a journal entry on the VAT payable account with VAT code VPRIV, usually in the last return of the year.
- Under the KOR you normally file no VAT return; the page says so when KOR is switched on.
- File the return yourself in Mijn Belastingdienst Zakelijk (DigiD, eHerkenning or a European login). Ledgerline does not submit it. See the [Belastingdienst page](https://www.belastingdienst.nl/wps/wcm/connect/nl/btw/content/ik-moet-btw-aangifte-doen-hoe-vul-ik-die-in).
- Optional: Post VAT settlement books one entry that clears the period's VAT accounts into the VAT settlement account. When you pay (or get a refund), book that bank transaction to the VAT settlement account.
- After filing, lock the period on the [period lock](/ledger/lock) page: entries on or before that date can no longer be posted. Moving the lock back needs a confirmation and is audited.

## ICP

If you supplied services to EU businesses (VEU), see the [ICP overview](/vat/icp) for the same period. Download the CSV and file the opgaaf intracommunautaire prestaties yourself.

## Year end

Ledgerline does not produce annual accounts, does not prepare your income tax return, does not automate depreciation (you can post it by hand in the [journal](/ledger/new)) and does not track the urencriterium (hours criterion). Talk to your accountant about those. What it gives you: the [reports](/reports) below, the full ledger and exports.

## Reports

On [Reports](/reports): profit and loss, balance sheet, trial balance, aged receivables and aged payables. Each can be downloaded as CSV (Download CSV). The [chart of accounts](/accounts) lists all accounts; the [journal](/ledger) lists every entry.

## Account, sign-in and two-factor login

Your own settings are under [Account](/account) (link in the sidebar): change your password, see your workspaces, accept or decline invitations, and "Sign out everywhere" to end every session on every device.

- **Invite someone:** an owner opens Settings, Users, Invite a user, enters an email address and a role. A new person gets an email with a link (valid for 7 days, works once) to choose a password. Someone who already has a Ledgerline login gets an email that they were added and accepts the invitation under Account. The confirmation looks the same in both cases, so it does not reveal whether an address already has an account. Emails are written in Dutch first, then English.
- **Forgot your password:** on the sign-in page choose "Forgot password?". If an account exists you get a link (valid for 1 hour, works once). Using it signs you out everywhere else and cancels any other open reset links. The page always gives the same answer, and requests are limited per address and per network address.
- **Two-factor login:** [Account, Security](/account/security) shows a QR code and a key for an authenticator app. Confirm with a code to switch it on, then save the 10 recovery codes: each works once and they are shown only now. At sign-in you enter your password, then the 6-digit code (a recovery code also works). To switch it off you need your password and a current code.
- **Require it for everyone:** an owner can tick "Require two-factor login" in Settings, Users, once their own account has it. Members without it are sent to set it up after they sign in.

If email is not set up on the server, invitations are refused with a message and reset emails are not sent: see DEPLOY.md.

## Accountant access

Settings, Users: invite a user with role accountant. That user can read everything and change nothing. The bookkeeper role can write the books but cannot manage users or phone tokens; only the owner can. Roles are owner, bookkeeper, accountant and uploader. Send reports as CSV, or the full administration ZIP from [Privacy and data](/settings/privacy) (owner only).

## Staff who only upload receipts

Settings, Users: invite a user with role uploader (phone capture only). Kitchen or floor staff can sign in on their phone, open [Capture](/capture), photograph a supplier invoice and send it. The two-factor and password rules are the same as for everyone else. An uploader sees a confirmation and nothing more: no inbox, ledger, reports, contacts, bank data, settings or users, not even the documents they sent. The bookkeeper reviews what arrives in the inbox, and the audit log records which user uploaded each document.

Uploaders cannot create phone tokens for the iOS shortcut; only the owner can, under Settings, Devices. Deactivate an uploader from Settings, Users when they leave: access ends on their next request.

## Privacy requests

[Privacy and data](/settings/privacy) lets the owner export one contact as JSON or the whole administration as ZIP. To honour an erasure request, open the contact and choose Anonymise contact: it blanks name, email, address, IBAN and KvK number but keeps invoices and journal entries, because bookkeeping records must be kept 7 years (10 for real estate). Read `COMPLIANCE_NL.md` for the trade-off: regenerated invoices of an anonymised contact no longer meet the invoice content rules, so keep copies of issued PDFs first.

## Backups

A nightly database dump runs by default. See `BACKUP_RESTORE.md` in the docs folder for restore steps and off-site copies. Test a restore before you rely on it.

## Troubleshooting and FAQ

**I booked something wrong.** Journal entries cannot be edited. Open the entry in the [journal](/ledger) and use Reverse this entry, then book it again correctly. For a bank match, use Undo.

**The period is locked.** Entries dated on or before the lock date are refused. Date the correction after the lock, or move the lock back on the [period lock](/ledger/lock) page (confirmation required).

**Opening balances were posted twice or wrongly.** Reverse the first entry in the journal, then enter them again in the [wizard](/setup/opening).

**Import says the file was already imported.** That is the duplicate check; nothing was added.

**I cannot issue an invoice.** The message lists what is missing: usually your VAT number, KvK number or address under [Settings](/settings), or the customer's address. For reverse-charge or EU supplies the customer's VAT number is required.

**I cannot sign in over plain http.** See `DEPLOY.md`: local http needs the secure-cookie setting off.
