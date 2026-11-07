"""Form <-> service translation for the invoice line editors."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ledgerline.invoicing.calc import InvoiceError, allocate_vat, line_amount, vat_by_code
from ledgerline.invoicing.service import PurchaseLineInput, SalesLineInput
from ledgerline.money import MoneyError, from_cents, percent_of, to_cents, to_decimal
from ledgerline.vat import codes


def money_text(cents: int) -> str:
    return f"{from_cents(cents):.2f}"


def parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvoiceError(f"{label} is not a valid date") from None


def parse_optional_date(value: str, label: str) -> date | None:
    return parse_date(value, label) if value.strip() else None


def _columns(lists: dict[str, list[str] | None]) -> list[dict[str, str]]:
    columns = {key: value or [] for key, value in lists.items()}
    count = max((len(v) for v in columns.values()), default=0)
    return [{k: (v[i] if i < len(v) else "") for k, v in columns.items()} for i in range(count)]


def sales_rows(
    description, qty, price, vat_code, account, op: str = "", remove: str = ""
) -> list[dict[str, str]]:
    rows = _columns({
        "description": description, "qty": qty, "price": price,
        "vat_code": vat_code, "account": account,
    })
    if remove.isdigit() and int(remove) < len(rows):
        del rows[int(remove)]
    if op == "add":
        rows.append(blank_sales_row())
    return rows


def blank_sales_row(account_id: int | str = "", vat: str = "") -> dict[str, str]:
    return {"description": "", "qty": "1", "price": "", "vat_code": vat, "account": str(account_id)}


def sales_inputs(rows: list[dict[str, str]]) -> list[SalesLineInput]:
    inputs = []
    for number, row in enumerate(rows, 1):
        if not any(row[k].strip() for k in ("description", "price")):
            continue
        try:
            quantity = to_decimal(row["qty"] or "0")
            price = to_cents(row["price"] or "0")
            account = int(row["account"]) if row["account"] else None
        except (MoneyError, InvalidOperation, ValueError):
            raise InvoiceError(f"line {number}: quantity, price or account is not valid") from None
        inputs.append(
            SalesLineInput(row["description"], quantity, price, row["vat_code"] or None, account)
        )
    return inputs


@dataclass
class Preview:
    excl: list[int] = field(default_factory=list)
    vat_rows: list[tuple[str, int, int]] = field(default_factory=list)
    total_excl: int = 0
    total_vat: int = 0

    @property
    def total_incl(self) -> int:
        return self.total_excl + self.total_vat


def sales_preview(rows: list[dict[str, str]], force_code: str | None) -> Preview:
    preview = Preview()
    pairs = []
    for row in rows:
        try:
            excl = line_amount(to_decimal(row["qty"] or "0"), to_cents(row["price"] or "0"))
        except (MoneyError, InvalidOperation):
            excl = 0
        preview.excl.append(excl)
        code = force_code or row["vat_code"]
        if code in codes.VAT_CODES:
            pairs.append((code, excl))
    for code, (base, vat) in vat_by_code(pairs).items():
        preview.vat_rows.append((code, base, vat))
    preview.total_excl = sum(preview.excl)
    preview.total_vat = sum(vat for _, _, vat in preview.vat_rows)
    return preview


def purchase_rows(
    description, account, amount, vat_code, vat, vat_auto, op: str = "", remove: str = ""
) -> list[dict[str, str]]:
    rows = _columns({
        "description": description, "account": account, "amount": amount,
        "vat_code": vat_code, "vat": vat, "vat_auto": vat_auto,
    })
    if remove.isdigit() and int(remove) < len(rows):
        del rows[int(remove)]
    if op == "add":
        rows.append(blank_purchase_row())
    return rows


def blank_purchase_row(account_id: int | str = "", vat: str = "") -> dict[str, str]:
    return {"description": "", "account": str(account_id), "amount": "", "vat_code": vat,
            "vat": "", "vat_auto": "1"}


def _amount(row: dict[str, str]) -> int:
    return to_cents(row["amount"] or "0")


def purchase_inputs(rows: list[dict[str, str]]) -> list[PurchaseLineInput]:
    inputs = []
    for number, row in enumerate(rows, 1):
        if not any(row[k].strip() for k in ("description", "amount")):
            continue
        try:
            amount = _amount(row)
            account = int(row["account"])
            manual = row["vat_auto"] == "0" and row["vat"].strip()
            stated = to_cents(row["vat"]) if manual else None
        except (MoneyError, InvalidOperation, ValueError):
            raise InvoiceError(f"line {number}: amount, VAT or account is not valid") from None
        inputs.append(
            PurchaseLineInput(row["description"], account, amount, row["vat_code"] or None, stated)
        )
    return inputs


def purchase_preview(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], Preview, list[bool]]:
    """Fill computed VAT into the rows; returns (rows, totals, per-row 'VAT is computed')."""
    amounts = []
    for row in rows:
        try:
            amounts.append(_amount(row))
        except (MoneyError, InvalidOperation):
            amounts.append(0)
    locked = []
    vat_values = [0] * len(rows)
    for code in {r["vat_code"] for r in rows if r["vat_code"] in codes.VAT_CODES}:
        members = [i for i, r in enumerate(rows) if r["vat_code"] == code]
        if codes.get(code).reverse_charge:
            for i, share in zip(members, allocate_vat([amounts[i] for i in members], code),
                                strict=True):
                vat_values[i] = share
    preview = Preview()
    for i, row in enumerate(rows):
        code = row["vat_code"]
        info = codes.VAT_CODES.get(code)
        is_locked = bool(info and (info.reverse_charge or info.rate == 0))
        locked.append(is_locked)
        if info and info.reverse_charge:
            vat = vat_values[i]
        elif info and info.rate == 0:
            vat = 0
        elif row["vat_auto"] == "0" and row["vat"].strip():
            try:
                vat = to_cents(row["vat"])
            except (MoneyError, InvalidOperation):
                vat = 0
        else:
            vat = percent_of(amounts[i], info.rate) if info else 0
        row["vat"] = money_text(vat)
        preview.excl.append(amounts[i])
        if info and not info.reverse_charge:
            preview.total_vat += vat
    preview.total_excl = sum(amounts)
    return rows, preview, locked


def decimal_text(value: Decimal) -> str:
    return f"{value.normalize():f}"
