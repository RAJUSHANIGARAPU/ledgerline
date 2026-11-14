"""Deterministic checks on extracted invoice data. They flag; they never correct.

Used on model output, where every field may be a misread. Identifier fields that fail
are reported in `flagged_fields` so supplier matching ignores them.
"""

from datetime import date, timedelta
from decimal import Decimal

from ledgerline.inbox.extract import ExtractedInvoice
from ledgerline.money import format_eur, percent_of
from ledgerline.platform.validation import check_iban, check_kvk, check_vat

EARLIEST_DATE = date(2000, 1, 1)
MAX_AGE = timedelta(days=730)
MAX_PAYMENT_TERM = timedelta(days=365)
NL_RATES = {0, 9, 21}
SUMMARY_TOLERANCE = 2  # cents per VAT rate


def check_identifiers(invoice: ExtractedInvoice) -> list[str]:
    """Normalise supplier IBAN, VAT number and KvK; flag the ones that do not validate."""
    found: list[str] = []
    checks = (
        ("supplier_iban", "IBAN", check_iban),
        ("supplier_vat_number", "VAT number", check_vat),
        ("supplier_kvk", "KvK number", check_kvk),
    )
    for attr, label, check in checks:
        raw = getattr(invoice, attr)
        if not raw:
            continue
        value, error = check(raw)
        if error:
            invoice.flagged_fields.append(attr)
            found.append(f"Supplier {label} {raw!r} looks misread: {error}")
        elif value:
            setattr(invoice, attr, value)
    return found


def check_dates(invoice: ExtractedInvoice, today: date) -> list[str]:
    found: list[str] = []
    issued, due = invoice.invoice_date, invoice.due_date
    if issued is not None:
        if issued < EARLIEST_DATE:
            found.append(f"Invoice date {issued.isoformat()} is before 2000")
        elif issued > today:
            found.append(f"Invoice date {issued.isoformat()} is in the future")
        elif today - issued > MAX_AGE:
            found.append(f"Invoice date {issued.isoformat()} is more than two years ago")
    if due is not None:
        if due < EARLIEST_DATE:
            found.append(f"Due date {due.isoformat()} is before 2000")
        elif issued is not None and due < issued:
            found.append(f"Due date {due.isoformat()} is before the invoice date")
        elif issued is not None and due - issued > MAX_PAYMENT_TERM:
            found.append(f"Due date {due.isoformat()} is more than a year after the invoice date")
    return found


def check_vat_summary(invoice: ExtractedInvoice) -> list[str]:
    """Net + VAT per rate, and the summary against the printed totals."""
    found: list[str] = []
    rows = invoice.vat_summary
    if not rows:
        return found
    for row in rows:
        if row.rate_percent not in NL_RATES:
            found.append(f"VAT rate {row.rate_percent}% is not a Dutch rate (0, 9 or 21)")
        if row.vat_cents is not None:
            expected = percent_of(row.base_cents, Decimal(row.rate_percent))
            if abs(row.vat_cents - expected) > SUMMARY_TOLERANCE:
                found.append(
                    f"VAT at {row.rate_percent}% is {format_eur(row.vat_cents)} but "
                    f"{row.rate_percent}% of {format_eur(row.base_cents)} is {format_eur(expected)}"
                )
    tolerance = SUMMARY_TOLERANCE * len(rows)
    base_sum = sum(row.base_cents for row in rows)
    excl = invoice.total_excl_cents
    if excl is not None and abs(base_sum - excl) > tolerance:
        found.append(
            f"VAT summary bases add up to {format_eur(base_sum)} but the total excl. VAT "
            f"is {format_eur(invoice.total_excl_cents)}"
        )
    if all(row.vat_cents is not None for row in rows) and invoice.total_vat_cents is not None:
        vat_sum = sum(row.vat_cents for row in rows)
        if abs(vat_sum - invoice.total_vat_cents) > tolerance:
            found.append(
                f"VAT summary adds up to {format_eur(vat_sum)} but the total VAT "
                f"is {format_eur(invoice.total_vat_cents)}"
            )
    return found


def check_kind(invoice: ExtractedInvoice) -> list[str]:
    """Credit-note sign, deposits and payment status: things the reviewer must act on."""
    found: list[str] = []
    total = invoice.total_incl_cents
    if total is not None and invoice.credit_note and total > 0:
        found.append("Marked as credit note but the amounts are positive")
    if total is not None and not invoice.credit_note and total < 0:
        found.append("The total is negative but the document is not marked as a credit note")
    if any(line.is_deposit for line in invoice.lines):
        found.append("Includes deposit (statiegeld) lines; check their VAT code and account")
    if invoice.paid_at_delivery:
        found.append("Printed as already paid (pin or cash); register the payment after booking")
    return found
