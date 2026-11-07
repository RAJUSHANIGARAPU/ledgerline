"""Query/form parsing shared by the report, VAT and ledger routes."""
from datetime import date

from fastapi import HTTPException


def parse_date(value: str | None, default: date | None = None) -> date | None:
    if value is None or not value.strip():
        return default
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(422, f"invalid date: {value!r}") from None


def parse_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        raise HTTPException(422, f"invalid number: {value!r}") from None
