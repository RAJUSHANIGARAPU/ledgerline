# ADR 0002: Opening items for a client who already has a business

- Status: accepted
- Date: 2026-11-10

## Context

A client who moves in on a start date (usually the first day of a quarter) already has unpaid
sales invoices (customers still owe them) and unpaid supplier invoices (the client still owes
them). Their revenue, costs and VAT were reported in the old system and the old VAT returns. In
Ledgerline those invoices must:

- be matchable by bank payments that arrive after the start date,
- show in the aging reports,
- **not** create revenue, expense or VAT in the new period, which would be reported twice,
- leave the balance sheet on the start date consistent: assets = liabilities + equity.

The existing "Opening balances" step books bank balances against owner's equity (account 0500).
It does not know about debtors or creditors.

## Options considered

| Option | What it is | Problem |
|---|---|---|
| A. Re-issue the old invoices through the normal flow, dated in the old period | Draft, issue, book with VAT codes | Needs the old period unlocked; puts revenue and VAT into the old period of a ledger that never had them; reissues a number the old system already owns; the VAT split is usually unknown |
| B. One summary journal entry (Dr Debtors, Cr equity) and no invoice rows | Balance only | Nothing to match a payment against, no aging per customer, a payment gets booked to the suspense account |
| C. Invoice rows flagged as opening items, each with its own opening entry (**chosen**) | An invoice record with the original number, never issued through the normal flow, plus Dr Debtors / Cr opening equity per item | One new flag and some guards |
| D. A separate table for open items | Own model, own matching | Duplicates the matching, aging and bank code; a second place for the tenancy rules; every report learns a new source |

## Decision

Option C.

1. **Records.** An opening item is a normal `sales_invoices` or `purchase_invoices` row with
   `is_opening = true` (migration 0007, no new table, so the tenancy rules are unchanged). It
   keeps the original number (sales) or the supplier reference (purchase), the contact, the
   invoice and due dates and the total including VAT. It has status `open` and no lines. The
   amount that was already paid before the start date is stored as `paid_cents`, so
   `total - paid` is the open amount and partial payments carry over. The split into excl. and
   VAT is unknown and unused: `total_excl_cents` equals the total and `total_vat_cents` is 0.
2. **Posting.** One journal entry per item, dated on the start date, source `opening_item`:
   sales `Dr 1300 Accounts receivable (contact on the line) / Cr 0580 Opening balance, open items`;
   purchase `Dr 0580 / Cr 1600 Accounts payable (contact on the line)`. The amount is the open
   amount. The lines carry no `vat_code` and no `vat_role`. Account 0580 is an equity account
   created on first use; the bank opening balance keeps using 0500.
3. **Never through the normal flow.** Opening items are created by the import code only. They
   cannot be edited, issued, credited (refused with a message) or downloaded as PDF or UBL.
   A mistake is corrected with "Remove": the entry is reversed and the record is cancelled;
   this is refused once a bank payment is matched to it.
4. **Matching and aging** read invoice rows with status `open`, so opening items appear in both
   without changes. A matched payment posts the usual `Dr bank / Cr 1300 (contact)`, which
   clears the receivable the opening entry created.
5. **Exclusions follow from the entries.** The VAT return and ICP overview read journal lines
   with a VAT code; profit and loss and the dashboard series read revenue and expense accounts.
   An opening entry touches none of them. Tests pin each of the four.
6. **One start date.** The first posting fixes the date; the bank opening balance and every item
   must use it, otherwise the balance check would compare different days.
7. **Balance check.** The setup step shows "Assets = Liabilities + Equity on <date>" from the
   balance sheet at the start date, with the carried-over receivables and payables.
8. **Import.** CSV with a preview, all-or-nothing for items (a half-imported set would leave the
   books wrong), re-uploading the same file skips what is already there.

## Consequences

- Account 0580 carries the net of receivables minus payables until the accountant moves it to
  equity or the owner's current account with a manual journal entry. This is deliberate and
  visible; the alternative (crediting 0500) would hide the movement.
- A credit note against an opening item cannot be issued here. The customer's credit is entered
  as a manual correction or handled in the old system.
- The sales number is unique per administration. An old number that equals a number the
  sequence will produce later (same prefix and year) will block that issue; use an invoice
  prefix the old system did not use.
- Removing an item keeps its number reserved (the row stays, cancelled), so the same number
  cannot be re-added; use a corrected number.
- Open amounts are only as good as the old system's list. The balance check proves the books
  balance, not that the list is complete; the runbook says to compare the totals with the
  old system's debtor and creditor reports.
