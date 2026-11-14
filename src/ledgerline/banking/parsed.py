"""Format-neutral statement structures shared by the CAMT.053, MT940 and CSV parsers."""
from dataclasses import dataclass, field
from datetime import date


class StatementError(ValueError):
    """The file is not a statement we can import."""


@dataclass
class ParsedTx:
    booking_date: date
    amount_cents: int  # signed: positive = money in
    value_date: date | None = None
    counterparty_name: str | None = None
    counterparty_iban: str | None = None
    description: str | None = None
    end_to_end_id: str | None = None
    bank_reference: str | None = None


@dataclass
class ParsedStatement:
    file_format: str
    iban: str
    opening_cents: int | None
    closing_cents: int | None
    transactions: list[ParsedTx] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
