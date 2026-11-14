# Ledgerline user guide

This guide is for a Dutch sole trader (eenmanszaak, ZZP) who keeps their own books in Ledgerline. It follows the screens as they are. Ledgerline prepares figures and files; you check them and file them yourself. It is bookkeeping software, not tax advice. Sources for the Dutch rules are listed in `COMPLIANCE_NL.md` in the docs folder of the repository.

## Getting started

1. Install it: see the README (quick start) and `DEPLOY.md` in the docs folder. Both explain the one-command Docker setup and how to create the first owner.
2. Sign in at `/login` with the owner email and password. Use at least 12 characters for the password; you can change it under [Settings](/settings).
3. Open the [setup wizard](/setup). It has seven short steps. Every step can be skipped and picked up later: progress is read from your data, so a step shows "done" once the data exists.

| Step | What you do |
|---|---|
| 1. Business details | Name, legal form, KvK number (8 digits), VAT number (BTW-id), address, IBAN, email, invoice prefix, payment terms |
| 2. VAT situation | Regular VAT or the small business scheme (KOR) |
| 3. Bank account | Register your business account |
| 4. Opening balances | Optional: the bank balance on your start date |
| 5. First customer and invoice | Shortcuts to add a customer and create an invoice |
| 6. Inbox and phone | Where supplier invoices come in |
| 7. Accountant review | Reminder to have the first quarter reviewed, and the exports |

The VAT choice matters: with the KOR (turnover at most EUR 20,000 per calendar year) you do not charge VAT and cannot deduct input VAT. You register for the KOR with the Belastingdienst yourself; the setting only tells Ledgerline how to book. Details: [belastingdienst.nl/kleineondernemersregeling](https://www.belastingdienst.nl/kleineondernemersregeling). Ledgerline does not watch the turnover limit.

Opening balances: if you start mid-year, enter the balance of each bank account on your start date. Ledgerline posts one journal entry (bank against owner's equity). A second one is refused until you reverse the first (see [Troubleshooting](#troubleshooting-and-faq)).

## Daily and weekly routine

- Once a week: process the [Inbox](/inbox) (supplier invoices that arrived by email, upload or phone).
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

## Bank

- Add the account on [Bank](/bank) (name, IBAN). In your online banking look for "Download transactions" and choose CAMT.053 or MT940, then upload it with Import. Importing posts nothing to the ledger.
- Reconcile: for each transaction, Match selected pairs it with open invoices, "Accept all exact matches" takes every exact amount match in one go, "Book to an account" posts a cost or receipt with an optional VAT code, Ignore sets it aside.
- Undo: on the Matched and Ignored tabs, Undo reverses the match (a reversing journal entry is posted) and puts the transaction back to Unmatched.
- The same file twice is refused ("this file has already been imported").
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

Settings, Users: invite a user with role accountant. That user can read everything and change nothing. The bookkeeper role can write the books but cannot manage users or phone tokens; only the owner can. Send reports as CSV, or the full administration ZIP from [Privacy and data](/settings/privacy) (owner only).

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
