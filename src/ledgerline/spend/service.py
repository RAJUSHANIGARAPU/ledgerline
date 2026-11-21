"""Spend overview: every purchase bill in one list, with totals.

Only purchase invoices count as spend. Sales, bank lines and journal entries never appear here.
Booked bills (open or paid) make up the totals; drafts are listed but kept out of them, and inbox
documents still awaiting review are counted separately, because nobody has checked their figures.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.inbox.review import document_type
from ledgerline.models import (
    Account,
    InboxDocument,
    InboxStatus,
    InvoiceStatus,
    PurchaseInvoice,
    PurchaseInvoiceLine,
)
from ledgerline.vat import codes


@dataclass(frozen=True)
class SpendFilter:
    date_from: date | None = None
    date_to: date | None = None
    contact_id: int | None = None
    include_drafts: bool = False


@dataclass(frozen=True)
class SpendRow:
    invoice_id: int
    supplier: str
    contact_id: int
    supplier_reference: str
    invoice_date: date
    source: str  # email | upload | phone | manual
    status: str  # draft | open | paid
    excl_cents: int
    vat_cents: int
    incl_cents: int
    open_cents: int
    has_attachment: bool
    is_opening: bool


@dataclass(frozen=True)
class Totals:
    count: int = 0
    excl_cents: int = 0
    vat_cents: int = 0
    incl_cents: int = 0
    open_cents: int = 0


@dataclass(frozen=True)
class Group:
    key: str  # supplier name, "YYYY-MM", or "<code> <account name>"
    totals: Totals


@dataclass(frozen=True)
class Pending:
    """Inbox documents not yet booked; amounts are as extracted, unchecked."""

    count: int = 0
    extracted_incl_cents: int = 0
    unreadable: int = 0  # documents with no extracted total


@dataclass(frozen=True)
class SpendOverview:
    rows: list[SpendRow] = field(default_factory=list)
    totals: Totals = Totals()  # booked rows only, never drafts
    by_supplier: list[Group] = field(default_factory=list)  # biggest incl first
    by_month: list[Group] = field(default_factory=list)  # chronological
    by_category: list[Group] = field(default_factory=list)  # biggest excl first
    pending: Pending = Pending()


class _Sum:
    """Running totals of one group."""

    def __init__(self) -> None:
        self.count = self.excl = self.vat = self.incl = self.open = 0

    def add(self, excl: int, vat: int, open_: int = 0) -> None:
        self.count += 1
        self.excl += excl
        self.vat += vat
        self.incl += excl + vat
        self.open += open_

    def totals(self) -> Totals:
        return Totals(self.count, self.excl, self.vat, self.incl, self.open)


def _where(administration_id: int, flt: SpendFilter) -> list:
    """Cancelled bills (a removed opening item, its entry reversed) are never spend."""
    statuses = [InvoiceStatus.OPEN, InvoiceStatus.PAID]
    if flt.include_drafts:
        statuses.append(InvoiceStatus.DRAFT)
    clauses = [PurchaseInvoice.administration_id == administration_id,
               PurchaseInvoice.status.in_(statuses)]
    if flt.date_from:
        clauses.append(PurchaseInvoice.invoice_date >= flt.date_from)
    if flt.date_to:
        clauses.append(PurchaseInvoice.invoice_date <= flt.date_to)
    if flt.contact_id is not None:
        clauses.append(PurchaseInvoice.contact_id == flt.contact_id)
    return clauses


def _sources(session: Session, administration_id: int, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    found: dict[int, str] = {}
    for invoice_id, source in session.execute(
        select(InboxDocument.purchase_invoice_id, InboxDocument.source)
        .where(InboxDocument.administration_id == administration_id,
               InboxDocument.purchase_invoice_id.in_(ids))
        .order_by(InboxDocument.id.desc())
    ):
        found[invoice_id] = source  # the oldest document wins when several point at one bill
    return found


def _pending(session: Session, administration_id: int) -> Pending:
    """Not narrowed by the filter: an unreviewed document has no checked date or supplier."""
    docs = session.scalars(select(InboxDocument).where(
        InboxDocument.administration_id == administration_id,
        InboxDocument.status.in_([InboxStatus.NEW, InboxStatus.REVIEW]),
        document_type() == "invoice",
    )).all()
    total = unreadable = 0
    for doc in docs:
        incl = (doc.extraction or {}).get("total_incl_cents")
        if isinstance(incl, int) and not isinstance(incl, bool):
            total += incl
        else:
            unreadable += 1
    return Pending(len(docs), total, unreadable)


def overview(session: Session, administration_id: int, flt: SpendFilter) -> SpendOverview:
    invoices = session.scalars(
        select(PurchaseInvoice).where(*_where(administration_id, flt))
        .order_by(PurchaseInvoice.invoice_date.desc(), PurchaseInvoice.id.desc())
    ).unique().all()
    sources = _sources(session, administration_id, [i.id for i in invoices])

    rows: list[SpendRow] = []
    booked_ids: list[int] = []
    total = _Sum()
    suppliers: dict[int, tuple[str, _Sum]] = {}
    months: dict[str, _Sum] = {}
    for inv in invoices:
        booked = inv.status != InvoiceStatus.DRAFT
        open_cents = inv.total_incl_cents - inv.paid_cents if booked else 0
        rows.append(SpendRow(
            invoice_id=inv.id, supplier=inv.contact.name, contact_id=inv.contact_id,
            supplier_reference=inv.supplier_reference, invoice_date=inv.invoice_date,
            source=sources.get(inv.id, "manual"), status=str(inv.status),
            excl_cents=inv.total_excl_cents, vat_cents=inv.total_vat_cents,
            incl_cents=inv.total_incl_cents, open_cents=open_cents,
            has_attachment=inv.attachment_id is not None, is_opening=inv.is_opening,
        ))
        if not booked:
            continue
        booked_ids.append(inv.id)
        total.add(inv.total_excl_cents, inv.total_vat_cents, open_cents)
        suppliers.setdefault(inv.contact_id, (inv.contact.name, _Sum()))[1].add(
            inv.total_excl_cents, inv.total_vat_cents, open_cents)
        months.setdefault(inv.invoice_date.strftime("%Y-%m"), _Sum()).add(
            inv.total_excl_cents, inv.total_vat_cents, open_cents)

    by_supplier = sorted(suppliers.values(), key=lambda g: (-g[1].incl, g[0]))
    return SpendOverview(
        rows=rows,
        totals=total.totals(),
        by_supplier=[Group(name, s.totals()) for name, s in by_supplier],
        by_month=[Group(key, months[key].totals()) for key in sorted(months)],
        by_category=_by_category(session, booked_ids),
        pending=_pending(session, administration_id),
    )


def _by_category(session: Session, invoice_ids: list[int]) -> list[Group]:
    """Per expense account, from the lines. Reverse-charge VAT is self-assessed and not part of
    the bill's total, so it is left out here too and the groups add up to the totals."""
    if not invoice_ids:
        return []
    cats: dict[str, _Sum] = {}
    seen: dict[str, set[int]] = {}
    for line, code, name in session.execute(
        select(PurchaseInvoiceLine, Account.code, Account.name)
        .join(Account, Account.id == PurchaseInvoiceLine.expense_account_id)
        .where(PurchaseInvoiceLine.invoice_id.in_(invoice_ids))
    ):
        key = f"{code} {name}"
        vat = 0 if codes.get(line.vat_code).reverse_charge else line.vat_cents
        group = cats.setdefault(key, _Sum())
        group.add(line.amount_excl_cents, vat)
        seen.setdefault(key, set()).add(line.invoice_id)
    for key, group in cats.items():
        group.count = len(seen[key])  # bills, not lines
    ordered = sorted(cats.items(), key=lambda kv: (-kv[1].excl, kv[0]))
    return [Group(key, s.totals()) for key, s in ordered]
