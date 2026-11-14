"""Opening items: invoices that were already open on the start date (ADR 0002)."""
import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.imports import csvio
from ledgerline.ledger.service import (
    LedgerError,
    LineSpec,
    post_entry,
    reverse_entry,
    system_account,
)
from ledgerline.models import (
    Account,
    AccountType,
    Administration,
    Contact,
    ContactKind,
    InvoiceStatus,
    JournalEntry,
    PaymentAllocation,
    PurchaseInvoice,
    SalesInvoice,
    SystemRole,
)
from ledgerline.money import MoneyError, format_eur
from ledgerline.onboarding.service import opening_entry
from ledgerline.platform import validation as v
from ledgerline.reports.financial import balance_sheet

OPENING_SOURCE = "opening_item"
ACCOUNT_CODE = "0580"
ACCOUNT_NAME = "Opening balance, open items"
SALES, PURCHASE = "sales", "purchase"
NOTE = "Opening item carried over from the previous system"

COLUMNS = ("type", "number", "contact", "contact_vat_number", "invoice_date", "due_date",
           "total", "open_amount")
ALIASES = {
    "type": ("kind", "soort", "direction"),
    "number": ("invoice_number", "reference", "supplier_reference", "factuurnummer", "nummer"),
    "contact": ("customer", "supplier", "relatie", "name", "naam"),
    "contact_vat_number": ("vat_number", "vat", "btw", "btw_nummer"),
    "invoice_date": ("date", "factuurdatum"),
    "due_date": ("vervaldatum", "due"),
    "total": ("total_incl_vat", "total_incl", "totaal", "bedrag"),
    "open_amount": ("open", "openstaand", "outstanding", "open_bedrag"),
}
TYPE_WORDS = {"sales": SALES, "sale": SALES, "verkoop": SALES, "debiteur": SALES,
              "receivable": SALES, "purchase": PURCHASE, "inkoop": PURCHASE,
              "crediteur": PURCHASE, "payable": PURCHASE}
TEMPLATE_ROWS = [
    ["sales", "2026-0412", "Voorbeeld Klant BV", "", "2026-09-14", "2026-10-14",
     "1.210,00", "1.210,00"],
    ["purchase", "INV-88231", "Voorbeeld Leverancier", "", "2026-09-30", "2026-10-30",
     "605,00", "300,00"],
]


class OpeningError(ValueError):
    """A rule was broken; the message is safe to show."""


@dataclass
class ItemRow:
    row: int
    kind: str | None = None
    number: str = ""
    contact: Contact | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    total_cents: int = 0
    open_cents: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    action: str = "create"  # create | skip | error
    reason: str = ""

    @property
    def contact_name(self) -> str:
        return self.contact.name if self.contact else ""


def template_csv(delimiter: str = ";") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(TEMPLATE_ROWS)
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def default_start_date(today: date) -> date:
    """First day of the current quarter, the usual moment to move a client in."""
    return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)


def opening_account(session: Session, admin_id: int) -> Account:
    """The equity account opposite every opening item; created on first use."""
    account = session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.code == ACCOUNT_CODE))
    if account is None:
        account = Account(administration_id=admin_id, code=ACCOUNT_CODE, name=ACCOUNT_NAME,
                          type=AccountType.EQUITY)
        session.add(account)
        session.flush()
    elif account.type != AccountType.EQUITY or not account.is_active:
        raise OpeningError(f"Account {ACCOUNT_CODE} exists but is not an active equity account.")
    return account


def fixed_start_date(session: Session, admin: Administration) -> date | None:
    """The start date once something has been posted for it, else None."""
    entry = opening_entry(session, admin.id)
    if entry is not None:
        return entry.entry_date
    return session.scalar(
        select(func.min(JournalEntry.entry_date)).where(
            JournalEntry.administration_id == admin.id,
            JournalEntry.source_type == OPENING_SOURCE))


def resolve_start_date(session: Session, admin: Administration, raw: str) -> date:
    fixed = fixed_start_date(session, admin)
    try:
        chosen = csvio.parse_date(raw) if raw.strip() else None
    except ValueError as exc:
        raise OpeningError(str(exc)) from exc
    if fixed is not None:
        if chosen is not None and chosen != fixed:
            raise OpeningError(
                f"The start date is already {fixed.isoformat()} (opening balances or items "
                "exist); all opening figures must share it.")
        return fixed
    if chosen is None:
        raise OpeningError("Enter the start date.")
    return chosen


def _find_contact(session: Session, admin_id: int, name: str,
                  vat_raw: str) -> tuple[Contact | None, str | None]:
    base = select(Contact).where(Contact.administration_id == admin_id,
                                 Contact.anonymised_at.is_(None))
    if vat_raw.strip():
        vat, err = v.check_vat(vat_raw)
        if err:
            return None, f"contact VAT number: {err}"
        found = session.scalars(base.where(Contact.vat_number == vat)).all()
    elif name.strip():
        found = session.scalars(base.where(
            func.lower(func.trim(Contact.name)) == name.strip().lower())).all()
    else:
        return None, "contact is empty"
    if not found:
        return None, "unknown contact; import or create the contact first"
    if len(found) > 1:
        return None, "more than one contact matches; add the VAT number column to pick one"
    return found[0], None


def _existing(session: Session, admin_id: int, kind: str, number: str,
              contact_id: int) -> SalesInvoice | PurchaseInvoice | None:
    if kind == SALES:
        return session.scalar(select(SalesInvoice).where(
            SalesInvoice.administration_id == admin_id, SalesInvoice.number == number))
    return session.scalar(select(PurchaseInvoice).where(
        PurchaseInvoice.administration_id == admin_id, PurchaseInvoice.contact_id == contact_id,
        func.lower(func.trim(PurchaseInvoice.supplier_reference)) == number.lower()))


def evaluate(session: Session, admin: Administration, raw_rows: list[tuple[int, dict[str, str]]],
             start: date, structural: dict[int, str] | None = None) -> list[ItemRow]:
    rows: list[ItemRow] = []
    seen: dict[tuple, int] = {}
    for number, raw in raw_rows:
        item = _evaluate_row(session, admin, number, raw, start)
        if structural and number in structural:
            item.errors.append(structural[number])
        if not item.errors:
            key = (item.kind, item.number.lower(),
                   item.contact.id if item.kind == PURCHASE else 0)
            if key in seen:
                item.errors.append(f"same invoice number as row {seen[key]}")
            else:
                seen[key] = number
        if item.errors:
            item.action, item.reason = "error", "; ".join(item.errors)
        rows.append(item)
    return rows


def _evaluate_row(session: Session, admin: Administration, number: int, raw: dict[str, str],
                  start: date) -> ItemRow:
    item = ItemRow(number)
    errors = item.errors
    kind = TYPE_WORDS.get(raw.get("type", "").strip().lower())
    if kind is None:
        errors.append("type must be sales or purchase")
    item.kind = kind
    item.number = raw.get("number", "").strip()
    limit = 30 if kind == SALES else 100
    if not item.number:
        errors.append("invoice number is empty")
    elif len(item.number) > limit:
        errors.append(f"invoice number is longer than {limit} characters")
    contact, problem = _find_contact(session, admin.id, raw.get("contact", ""),
                                     raw.get("contact_vat_number", ""))
    item.contact = contact
    if problem:
        errors.append(problem)
    elif contact and kind:
        wanted = (ContactKind.CUSTOMER, ContactKind.BOTH) if kind == SALES else (
            ContactKind.SUPPLIER, ContactKind.BOTH)
        if contact.kind not in wanted:
            errors.append(f"{contact.name} is a {contact.kind}, not a "
                          f"{'customer' if kind == SALES else 'supplier'}")
    for column, attr in (("invoice_date", "invoice_date"), ("due_date", "due_date")):
        try:
            setattr(item, attr, csvio.parse_date(raw.get(column, "")))
        except ValueError as exc:
            errors.append(f"{column}: {exc}")
    if item.invoice_date and item.invoice_date > start:
        errors.append(f"invoice date is after the start date {start.isoformat()}")
    if item.invoice_date and item.due_date and item.due_date < item.invoice_date:
        errors.append("due date is before the invoice date")
    _amounts(item, raw)
    if errors or kind is None or contact is None:
        return item
    existing = _existing(session, admin.id, kind, item.number, contact.id)
    if existing is not None:
        same = (existing.contact_id == contact.id
                and existing.total_incl_cents == item.total_cents)
        if existing.is_opening and existing.status == InvoiceStatus.OPEN and same:
            item.action, item.reason = "skip", "already imported as an opening item"
        else:
            errors.append("this invoice number already exists in the books")
    return item


def _amounts(item: ItemRow, raw: dict[str, str]) -> None:
    try:
        item.total_cents = csvio.parse_amount(raw.get("total", ""))
    except MoneyError as exc:
        item.errors.append(f"total: {exc}")
        return
    if item.total_cents <= 0:
        item.errors.append("total must be more than zero")
        return
    open_raw = raw.get("open_amount", "")
    if not open_raw.strip():
        item.open_cents = item.total_cents
        item.warnings.append("open amount is empty; the full total is taken as open")
        return
    try:
        item.open_cents = csvio.parse_amount(open_raw)
    except MoneyError as exc:
        item.errors.append(f"open_amount: {exc}")
        return
    if item.open_cents <= 0:
        item.errors.append("open amount must be more than zero; leave paid invoices out")
    elif item.open_cents > item.total_cents:
        item.errors.append("open amount is more than the total")
    elif item.open_cents < item.total_cents:
        item.warnings.append(
            f"partly paid: {format_eur(item.total_cents - item.open_cents)} was paid before "
            "the start date")


def plan_csv(session: Session, admin: Administration, content: bytes,
             start: date) -> tuple[list[ItemRow], list[str], str]:
    text, warnings = csvio.decode(content)
    table = csvio.read_table(text, ALIASES, required=("type", "number", "contact",
                                                      "invoice_date", "due_date", "total"))
    rows = evaluate(session, admin, table.rows, start, table.row_errors)
    return rows, [*warnings, *table.warnings], table.delimiter


def create_item(session: Session, admin: Administration, item: ItemRow, start: date,
                user_id: int | None) -> SalesInvoice | PurchaseInvoice:
    if item.action != "create" or item.contact is None or item.kind is None:
        raise OpeningError("this row cannot be imported")
    counter = opening_account(session, admin.id)
    common = {
        "administration_id": admin.id, "contact_id": item.contact.id,
        "status": InvoiceStatus.OPEN, "due_date": item.due_date, "is_opening": True,
        "total_excl_cents": item.total_cents, "total_vat_cents": 0,
        "total_incl_cents": item.total_cents, "paid_cents": item.total_cents - item.open_cents,
    }
    if item.kind == SALES:
        invoice = SalesInvoice(number=item.number, issue_date=item.invoice_date, notes=NOTE,
                               issued_at=datetime.now(UTC), **common)
        control = system_account(session, admin.id, SystemRole.DEBTORS)
        lines = [
            LineSpec(control.id, debit_cents=item.open_cents, contact_id=item.contact.id,
                     description=f"Opening item {item.number}"),
            LineSpec(counter.id, credit_cents=item.open_cents,
                     description=f"Opening item {item.number}"),
        ]
        label, entity = "Sales", "sales_invoice"
    else:
        invoice = PurchaseInvoice(supplier_reference=item.number, invoice_date=item.invoice_date,
                                  description=NOTE, **common)
        control = system_account(session, admin.id, SystemRole.CREDITORS)
        lines = [
            LineSpec(counter.id, debit_cents=item.open_cents,
                     description=f"Opening item {item.number}"),
            LineSpec(control.id, credit_cents=item.open_cents, contact_id=item.contact.id,
                     description=f"Opening item {item.number}"),
        ]
        label, entity = "Purchase", "purchase_invoice"
    session.add(invoice)
    session.flush()
    try:
        entry = post_entry(
            session, admin.id, start, f"Opening item {item.number} {item.contact.name}", lines,
            source_type=OPENING_SOURCE, source_id=invoice.id, user_id=user_id)
    except LedgerError as exc:
        raise OpeningError(str(exc)) from exc
    invoice.journal_entry_id = entry.id
    audit.record(session, "opening_item.create", user_id=user_id, administration_id=admin.id,
                 entity_type=entity, entity_id=invoice.id,
                 details={"kind": label.lower(), "number": item.number,
                          "total_cents": item.total_cents, "open_cents": item.open_cents})
    return invoice


def apply_items(session: Session, admin: Administration, rows: list[ItemRow], start: date,
                user_id: int | None, filename: str | None = None) -> int:
    if any(r.action == "error" for r in rows):
        raise OpeningError("Fix the rows with errors first; opening items are imported "
                           "all together or not at all.")
    created = 0
    for item in rows:
        if item.action == "create":
            create_item(session, admin, item, start, user_id)
            created += 1
    audit.record(session, "opening_item.import", user_id=user_id, administration_id=admin.id,
                 details={"file": (filename or "")[:255], "created": created,
                          "skipped": sum(1 for r in rows if r.action == "skip"),
                          "start_date": start.isoformat()})
    return created


def opening_invoice(session: Session, admin_id: int, kind: str, invoice_id: int):
    model = SalesInvoice if kind == SALES else PurchaseInvoice
    invoice = session.get(model, invoice_id)
    if invoice is None or invoice.administration_id != admin_id or not invoice.is_opening:
        return None
    return invoice


def remove_item(session: Session, admin: Administration, kind: str, invoice_id: int,
                user_id: int | None) -> None:
    invoice = opening_invoice(session, admin.id, kind, invoice_id)
    if invoice is None or invoice.status != InvoiceStatus.OPEN:
        raise OpeningError("This opening item does not exist or is no longer open.")
    column = (PaymentAllocation.sales_invoice_id if kind == SALES
              else PaymentAllocation.purchase_invoice_id)
    if session.scalar(select(func.count()).select_from(PaymentAllocation).where(
            column == invoice.id)):
        raise OpeningError("A bank payment is matched to this item; unmatch it first.")
    entry = session.get(JournalEntry, invoice.journal_entry_id)
    try:
        reverse_entry(session, entry, entry.entry_date, "opening item removed", user_id)
    except LedgerError as exc:
        raise OpeningError(str(exc)) from exc
    invoice.status = InvoiceStatus.CANCELLED
    audit.record(session, "opening_item.remove", user_id=user_id, administration_id=admin.id,
                 entity_type="sales_invoice" if kind == SALES else "purchase_invoice",
                 entity_id=invoice.id)


@dataclass
class OpeningSummary:
    sales: list[SalesInvoice]
    purchases: list[PurchaseInvoice]
    start: date | None
    receivable_cents: int
    payable_cents: int
    check: dict | None

    @property
    def net_cents(self) -> int:
        return self.receivable_cents - self.payable_cents


def _carried_over(session: Session, admin_id: int) -> tuple[int, int]:
    """Amounts moved on the start date, read from the entries still in force."""
    reversed_ids = select(JournalEntry.reversal_of_id).where(
        JournalEntry.reversal_of_id.is_not(None))
    debtors = system_account(session, admin_id, SystemRole.DEBTORS).id
    creditors = system_account(session, admin_id, SystemRole.CREDITORS).id
    receivable = payable = 0
    for entry in session.scalars(select(JournalEntry).where(
            JournalEntry.administration_id == admin_id,
            JournalEntry.source_type == OPENING_SOURCE,
            JournalEntry.id.not_in(reversed_ids))):
        for line in entry.lines:
            if line.account_id == debtors:
                receivable += line.debit_cents
            elif line.account_id == creditors:
                payable += line.credit_cents
    return receivable, payable


def summary(session: Session, admin: Administration) -> OpeningSummary:
    live = (InvoiceStatus.OPEN, InvoiceStatus.PAID)
    sales = session.scalars(select(SalesInvoice).where(
        SalesInvoice.administration_id == admin.id, SalesInvoice.is_opening.is_(True),
        SalesInvoice.status.in_(live)).order_by(SalesInvoice.due_date, SalesInvoice.id)).all()
    purchases = session.scalars(select(PurchaseInvoice).where(
        PurchaseInvoice.administration_id == admin.id, PurchaseInvoice.is_opening.is_(True),
        PurchaseInvoice.status.in_(live)).order_by(PurchaseInvoice.due_date,
                                                   PurchaseInvoice.id)).all()
    start = fixed_start_date(session, admin)
    receivable, payable = _carried_over(session, admin.id)
    check = None
    if start is not None:
        sheet = balance_sheet(session, admin.id, start)
        check = {
            "date": start, "assets": sheet.assets.total_cents,
            "liabilities": sheet.liabilities.total_cents, "equity": sheet.equity.total_cents,
            "balanced": sheet.balanced,
        }
    return OpeningSummary(list(sales), list(purchases), start, receivable, payable, check)
