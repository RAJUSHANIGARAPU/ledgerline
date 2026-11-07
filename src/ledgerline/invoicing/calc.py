"""Pure invoice arithmetic shared by sales and purchase invoices (no database)."""
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from ledgerline.money import percent_of
from ledgerline.vat import codes


class InvoiceError(ValueError):
    """A user-correctable problem; `problems` lists every reason."""

    def __init__(self, *problems: str):
        self.problems = [p for p in problems if p]
        super().__init__("; ".join(self.problems))


def line_amount(quantity: Decimal, unit_price_cents: int) -> int:
    return int((Decimal(unit_price_cents) * quantity).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def vat_by_code(items: Iterable[tuple[str, int]]) -> dict[str, tuple[int, int]]:
    """code -> (net base, VAT): VAT is computed once per code on the summed base."""
    bases: dict[str, int] = {}
    for code, excl in items:
        bases[code] = bases.get(code, 0) + excl
    return {code: (base, percent_of(base, codes.get(code).rate)) for code, base in bases.items()}


def side(amount: int, credit_normal: bool) -> tuple[int, int]:
    """(debit, credit) for a signed amount whose natural side is credit or debit."""
    if credit_normal:
        return (0, amount) if amount >= 0 else (-amount, 0)
    return (amount, 0) if amount >= 0 else (0, -amount)


def allocate_vat(amounts: list[int], code: str) -> list[int]:
    """Spread the per-code VAT (computed on the sum) over lines so they add up exactly."""
    rate = codes.get(code).rate
    total = percent_of(sum(amounts), rate)
    shares = [percent_of(a, rate) for a in amounts]
    if shares:
        shares[-1] = total - sum(shares[:-1])
    return shares
