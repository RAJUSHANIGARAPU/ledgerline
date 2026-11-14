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

## Postings

One clearing account per platform (1310 Uber Eats, 1320 Thuisbezorgd), created when first used.
The statement entry credits sales, tips; debits fees (4610), input VAT as printed, cash, and
adjustments; and debits the clearing account with the printed payout. When the payout reaches
the bank, the bank screen offers to book that line against the clearing account, which brings
it back to zero.
