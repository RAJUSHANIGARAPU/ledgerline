# Bookkeeping oracle: findings

`oracle.py` is an independent model of the expected figures (plain Decimal maths, no ledgerline
imports). `conftest.py` drives the application with the same scenario and compares balances, trial
balance, P&L, balance sheet, VAT return (cents and whole-euro form values), ICP, aging and dashboard.
Every finding is a strict xfail: the test starts failing the day the bug is fixed, which flags the
marker for removal.

| Id | Severity | Summary | Test |
|---|---|---|---|
| BUG-1 | major | Aging and dashboard receivables count a credit note without the original it offsets | test_01 `..._with_credit_note`, test_07 `..._pairs_...` |
| BUG-2 | minor | Quantity stored with 3 decimals, line total computed from the unrounded quantity | test_02 `test_quantity_precision_...` |
| BUG-3 | major | A credit note can never be settled (refund cannot be matched) | test_03 `test_refund_of_a_credit_note_...` |
| BUG-4 | major | KOR administration still deducts input VAT (1400 / rubriek 5b) | test_05 `test_kor_purchase_...` |
| BUG-5 | minor | EU VAT-shifted supply to a Dutch customer is accepted | test_06 `test_eu_supply_to_a_dutch_...` |
| BUG-6 | major | Reversing an invoice or bank entry from the ledger desyncs the sub-ledger | test_07 `test_reversing_...` (2) |
| BUG-7 | minor | Fully credited EU customer stays on the ICP overview as a zero line | test_06 `test_zero_net_icp_...` |
| BUG-8 | major | Aging ignores its as-of date (later invoices and payments leak in) | test_01 `test_aging_as_of_...` |
| BUG-9 | minor | Stated purchase VAT is never plausibility-checked | test_07 `test_implausible_...` |

## Details (observed with the exact numbers of the tests)

**BUG-1.** Invoice 121.00 issued, then fully credited: the ledger has debtors = 0, but
`reports/aging.aging` returns -121.00 (original is CANCELLED and skipped, the credit note is OPEN
and counted alone). The dashboard `receivable_cents` has the same error. In the quarter scenario
the customer's 20,185 cents were missing from the 1-30 bucket (app 38,065, expected 58,250). When
the original was part paid (50.00 of 121.00) the customer's true balance is -50.00; aging shows
-121.00. Cause: `reports/aging.py` and `reports/dashboard._open_totals` sum OPEN rows only.

**BUG-2.** `0.3333 x 75.00`: `line_excl_cents` 2500 (unrounded quantity), stored quantity 0.333
(`Numeric(12,3)`), which gives 2498 when the printed line is recalculated. The invoice document
shows a quantity and total that disagree. The form accepts any number of decimals. Fix: quantise
the quantity before computing the line.

**BUG-3.** After a paid 121.00 invoice is credited, the customer is owed 121.00. A bank
transaction of -121.00 raises "money in matches sales invoices, money out purchase invoices", so
the credit note stays OPEN at -121.00 forever. The same holds for a pure-discount (negative total)
invoice. Booking the refund by hand to account 1300 carries no contact (unverified: not tested).

**BUG-4.** `docs/USER_GUIDE.md:21`: under the KOR you "cannot deduct input VAT". Purchase of net
100.00 at I21 on a KOR administration posts 4000 = 10,000 and 1400 = 2,100 (5b = 21.00). Expected:
4000 = 12,100, 1400 = 0. The P&L is understated by the VAT and a VAT receivable is invented.

**BUG-5.** `VEU` to a customer with country NL and a Dutch VAT number issues without complaint;
it lands in 3b and on the ICP instead of 1a at 21%.

**BUG-6.** `ledger/routes.entry_reverse` does not look at `source_type`. Reversing the entry of an
issued invoice sets debtors to 0 while the invoice stays OPEN and aging lists 121.00. Reversing a
bank match entry leaves bank = 0 but the invoice PAID (`paid_cents` 12,100) and the transaction
MATCHED. Fix: refuse reversal of entries that belong to an invoice or bank transaction (point to
credit note / undo).

**BUG-7.** `vat/icp.icp_overview` returns `('EU Klant GmbH', 0)` after a same-quarter credit note.

**BUG-8.** `/reports/aging/...?end=` takes only OPEN status and current `paid_cents`. As of
2026-02-15 the app shows Alfa BV 147,131 not yet due; the books at that date held 33,086.

**BUG-9.** `create_purchase_invoice(..., vat_cents=999999)` on a net of 100.00 at I21 is accepted
and 9,999.99 is deducted in 5b.

## Rules of the app that look questionable (not marked as bugs)

1. Purchase VAT rounding differs from the sales rule: normal purchases use the supplier's stated
   VAT (default: per line), reverse-charge purchases use the per-code sum. Three lines of 0.02 at
   I21 give VAT 0, the same lines at IRV21 give 0.01. Consistent with "the invoice states the VAT";
   unverified against Belastingdienst guidance.
2. Whole-euro form values use floor, so a negative amount (credit note in a later quarter, VAT
   -0.32) shows -1 euro. `USER_GUIDE.md:102` says "negative amounts keep the minus sign", which
   reads like truncation. `vat_return.whole_euros` (truncation) exists but is unused. Unverified
   which one the Belastingdienst expects.
3. VAT settlement is dated the last day of the period, so after the lock it cannot be posted.
   Consistent with the guide ("lock after filing") but worth stating next to the settle button.
4. A dot in a form amount is a decimal point: `1.234` = 1.234 euro, not 1,234. Dutch users type
   `1.234` for a thousand. Only a comma triggers Dutch notation.
5. Reverse-charge purchases on a KOR administration are treated like normal VAT (unverified rule).

## Held (passing) properties

Per-invoice per-code sales VAT rounding, half-up, mirrored on credit notes; whole-euro 5a/5b
form rules; all VAT rubrieken incl. 1d, 3b, 4a, 4b, 2a; ICP per customer and its CSV; payment
allocation, partial payment, overpayment refusal, multi-invoice receipts, undo and re-match; bank
VAT split; period lock on every path with no side effects and a gap-free number sequence; VAT
settlement and its reversal; two administrations in one tenant stay separate in every view.
