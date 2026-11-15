# Delivery-platform statements

Ledgerline reads two documents from the inbox (PDF with a text layer):

- the Uber Eats weekly "Betaaloverzicht" (saved from the email as PDF);
- the Thuisbezorgd invoice with its overview (the "TAKEAWAY_EXPORT" PDF).

Both are parsed deterministically. The statement is shown next to the PDF and nothing is booked
until the owner submits the review form.

## What is read, and what you must decide

Read from the document: period, gross sales, platform fees, VAT on the fees as printed, tips,
cash collected by the restaurant (Thuisbezorgd), refunds, promotions and corrections, and the
payout. The components must add up to the payout; if they do not, the statement is flagged and
can only be booked after you tick that you checked the difference (it goes to suspense).

Neither document splits sales by VAT rate, so you enter the split yourself. Tips, promotions,
refunds and corrections are booked to an account you choose. Sales amounts are treated as
including VAT. Confirm with your adviser how each of these is taxed.

Uber Eats does not print the VAT rate on its service fee. The rate is worked out from the
amounts and shown as a warning; confirm it on the Uber invoice. A document that mentions
reverse-charged VAT is not booked from here.

## Input VAT on the platform fees needs the commission invoice

The payout statement alone is not a valid VAT invoice, so Ledgerline claims input VAT on the fees only when the booking carries the platform's commission invoice number. The Thuisbezorgd document prints it ("Factuurnummer") and the field is filled in; an Uber Eats payout overview does not, so you enter the number from the Uber invoice. The number is stored on the fee lines of the entry and in the audit log.

If you book without a number, the fees are booked including VAT on 4610 and no input VAT is claimed. The page says so before you book, in the message after booking, and on the booked statement. [adviser to confirm] that a platform's commission invoice is the document that supports the deduction, and how to correct a statement that was booked gross.

## Postings

One clearing account per platform (1330 Uber Eats, 1340 Thuisbezorgd), found by system role and created when first used.
The statement entry credits sales, tips; debits fees (4610), input VAT as printed (only with a commission invoice number, otherwise the VAT is added to the fees), cash, and
adjustments; and debits the clearing account with the printed payout. When the payout reaches
the bank, the bank screen offers to book that line against the clearing account, which brings
it back to zero.
