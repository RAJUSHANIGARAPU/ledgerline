"""MT940 adapter on top of the mt-940 library, mapped to the shared statement structures."""
import re
from datetime import date
from decimal import Decimal

import mt940
from schwifty import IBAN

from ledgerline.banking.parsed import ParsedStatement, ParsedTx, StatementError
from ledgerline.money import to_cents

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_TAGS = "TRTP|CSID|NAME|MARF|REMI|IBAN|BIC|EREF|ORDP|BENM|ADDR|ISDT|PREF|ID"
_TAG_RE = re.compile(rf"/({_TAGS})/")


def _find_iban(text: str) -> str | None:
    for candidate in _IBAN_RE.findall(text.replace(" ", "")):
        try:
            return IBAN(candidate).compact
        except ValueError:
            continue
    return None


def _structured(details: str) -> dict[str, str]:
    parts = _TAG_RE.split("/" + details.lstrip("/"))
    return {parts[i]: parts[i + 1].strip().strip("/") for i in range(1, len(parts) - 1, 2)}


def _cents(amount: Decimal, currency: str | None) -> int:
    if currency and currency != "EUR":
        raise StatementError(f"only EUR statements are supported (found {currency})")
    return to_cents(Decimal(amount))


def _as_date(value) -> date | None:
    return date(value.year, value.month, value.day) if value else None


def _map(data: dict) -> ParsedTx:
    details = " ".join(str(data.get("transaction_details") or "").split())
    tags = _structured(details) if details.startswith("/") else {}
    if tags:
        description = tags.get("REMI") or tags.get("TRTP")
        name, iban, e2e = tags.get("NAME"), tags.get("IBAN"), tags.get("EREF")
        iban = iban or _find_iban(details)
    else:
        description, name, e2e, iban = details or None, None, None, _find_iban(details)
    amount = data["amount"]
    booking = _as_date(data.get("entry_date")) or _as_date(data["date"])
    reference = (data.get("bank_reference") or "").strip() or None
    return ParsedTx(
        booking_date=booking,
        value_date=_as_date(data["date"]),
        amount_cents=_cents(amount.amount, getattr(amount, "currency", None)),
        counterparty_name=name or None,
        counterparty_iban=iban.replace(" ", "").upper() if iban else None,
        description=description or None,
        end_to_end_id=None if e2e in (None, "", "NOTPROVIDED") else e2e[:100],
        bank_reference=reference[:100] if reference else None,
    )


def _balance(balance) -> int | None:
    if balance is None:
        return None
    return _cents(balance.amount.amount, getattr(balance.amount, "currency", None))


def parse(content: bytes) -> ParsedStatement:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    try:
        statement = mt940.parse(text + ("\n" if not text.endswith("\n") else ""))
        data = statement.data
        transactions = [_map(t.data) for t in statement]
    except StatementError:
        raise
    except Exception as exc:
        raise StatementError(f"not a valid MT940 file: {exc}") from exc
    iban = _find_iban(str(data.get("account_identification") or ""))
    if iban is None:
        raise StatementError("statement has no valid account IBAN")
    return ParsedStatement(
        file_format="mt940",
        iban=iban,
        opening_cents=_balance(data.get("final_opening_balance")),
        closing_cents=_balance(data.get("final_closing_balance")),
        transactions=transactions,
    )
