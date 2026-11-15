"""Pure rules: history in, advice out. No database, no side effects."""
from dataclasses import dataclass
from datetime import date
from statistics import median

from ledgerline.money import format_eur
from ledgerline.vat import codes

VAT_MIN_INVOICES = 3
VAT_MIN_SHARE = 0.8
AMOUNT_MIN_INVOICES = 5
AMOUNT_FACTOR = 5


@dataclass(frozen=True)
class Advice:
    code: str
    message: str


def _percent(code: str) -> str:
    return f"{codes.get(code).rate:f}".rstrip("0").rstrip(".") or "0"


def usual_vat_code(history: list[frozenset[str]]) -> str | None:
    """The one code that at least VAT_MIN_SHARE of the earlier invoices use alone, if any.

    Each item is the set of VAT codes on one booked invoice. An invoice that mixes codes
    counts against every code, so a supplier that mixes 9% and 21% never has a usual code.
    """
    if len(history) < VAT_MIN_INVOICES:
        return None
    counts: dict[str, int] = {}
    for used in history:
        if len(used) == 1:
            (only,) = tuple(used)
            counts[only] = counts.get(only, 0) + 1
    if not counts:
        return None
    code, seen = max(counts.items(), key=lambda item: item[1])
    return code if seen / len(history) >= VAT_MIN_SHARE else None


def vat_rate_advice(
    supplier: str, history: list[frozenset[str]], line_codes: list[str]
) -> list[Advice]:
    usual = usual_vat_code(history)
    if usual is None:
        return []
    found = []
    for number, code in enumerate(line_codes, 1):
        if not code or code == usual or code not in codes.VAT_CODES:
            continue
        if codes.get(code).reverse_charge or codes.get(usual).reverse_charge:
            continue
        found.append(Advice("vat_rate", (
            f"Line {number}: {supplier} is usually booked with {_percent(usual)}% VAT "
            f"({len(history)} earlier invoices); this line says {_percent(code)}%. "
            "Check the document.")))
    return found


def amount_advice(
    supplier: str, history_totals: list[int], total_excl_cents: int | None
) -> list[Advice]:
    usual = [abs(t) for t in history_totals if t]
    if total_excl_cents is None or len(usual) < AMOUNT_MIN_INVOICES:
        return []
    typical = median(usual)
    if abs(total_excl_cents) <= AMOUNT_FACTOR * typical:
        return []
    return [Advice("amount", (
        f"The total excl. VAT is more than {AMOUNT_FACTOR} times the median of {supplier}'s "
        f"{len(usual)} earlier invoices ({format_eur(int(typical))}). Check the amounts."))]


def period_advice(
    document_date: date | None, *, locked_until: date | None,
    approved_period: str | None, settled_period: str | None,
) -> list[Advice]:
    if document_date is None:
        return []
    found = []
    if locked_until and document_date <= locked_until:
        found.append(Advice("period_locked", (
            f"The books are closed up to {locked_until.isoformat()}; an invoice dated "
            f"{document_date.isoformat()} cannot be booked until the period is reopened.")))
    if settled_period:
        found.append(Advice("period_settled", (
            f"The VAT return for {settled_period} is already settled. Booking this invoice "
            "will need a correction on a later return.")))
    elif approved_period:
        found.append(Advice("period_approved", (
            f"The VAT return for {approved_period} is already approved. Booking this invoice "
            "changes its figures; the approval will show as out of date.")))
    return found

