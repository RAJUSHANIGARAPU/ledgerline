"""Aged receivables and payables from open invoices."""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import InvoiceStatus, PurchaseInvoice, SalesInvoice

BUCKETS = ["Not yet due", "1-30", "31-60", "61-90", "Over 90"]


def bucket_index(due: date, as_of: date) -> int:
    days = (as_of - due).days
    if days <= 0:
        return 0
    if days <= 30:
        return 1
    if days <= 60:
        return 2
    if days <= 90:
        return 3
    return 4


@dataclass
class AgingRow:
    contact_id: int
    name: str
    buckets: list[int] = field(default_factory=lambda: [0] * len(BUCKETS))
    invoices: int = 0

    @property
    def total_cents(self) -> int:
        return sum(self.buckets)


def aging(
    session: Session, administration_id: int, kind: str, as_of: date
) -> list[AgingRow]:
    """kind is 'receivables' (sales invoices) or 'payables' (purchase invoices)."""
    model = SalesInvoice if kind == "receivables" else PurchaseInvoice
    invoices = session.scalars(
        select(model).where(
            model.administration_id == administration_id, model.status == InvoiceStatus.OPEN
        )
    ).all()
    rows: dict[int, AgingRow] = {}
    for invoice in invoices:
        outstanding = invoice.total_incl_cents - invoice.paid_cents
        if outstanding == 0:
            continue
        row = rows.setdefault(
            invoice.contact_id, AgingRow(invoice.contact_id, invoice.contact.name))
        row.buckets[bucket_index(invoice.due_date, as_of)] += outstanding
        row.invoices += 1
    return sorted(rows.values(), key=lambda r: r.name.lower())


def totals(rows: list[AgingRow]) -> list[int]:
    return [sum(r.buckets[i] for r in rows) for i in range(len(BUCKETS))]
