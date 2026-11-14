"""Money is stored as whole euro cents (int) and computed in Decimal; never float."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


class MoneyError(ValueError):
    pass


def to_decimal(value: str | int | Decimal) -> Decimal:
    if isinstance(value, float):
        raise MoneyError("floats are not accepted for money")
    try:
        text = value.strip().replace(" ", "") if isinstance(value, str) else value
        if isinstance(text, str) and "," in text:
            # Dutch notation: 1.234,56
            text = text.replace(".", "").replace(",", ".")
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError(f"not a valid amount: {value!r}") from exc


def to_cents(value: str | int | Decimal) -> int:
    """Euros to cents, rounded half-up (the common Dutch commercial rounding)."""
    amount = to_decimal(value)
    if not amount.is_finite():
        raise MoneyError(f"not a valid amount: {value!r}")
    return int((amount / CENT).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) * CENT).quantize(CENT)


def percent_of(base_cents: int, rate_percent: Decimal) -> int:
    """rate_percent of base_cents, rounded half-up to the cent."""
    exact = Decimal(base_cents) * rate_percent / 100
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def vat_in_gross(gross_cents: int, rate_percent: Decimal) -> int:
    """The VAT inside a VAT-inclusive amount (gross * rate / (100 + rate)), rounded half-up."""
    exact = Decimal(gross_cents) * rate_percent / (Decimal(100) + rate_percent)
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_eur(cents: int) -> str:
    """Dutch display format with a no-break space: € 1.234,56 and -€ 1.234,56."""
    sign = "-" if cents < 0 else ""
    euros, rest = divmod(abs(cents), 100)
    grouped = f"{euros:,}".replace(",", ".")
    return f"{sign}€\u00a0{grouped},{rest:02d}"
