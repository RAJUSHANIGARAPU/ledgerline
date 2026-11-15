"""The query layer: reads booked history through the normal tenant session, writes nothing."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.advisory import rules
from ledgerline.advisory.rules import Advice
from ledgerline.models import (
    Administration,
    InvoiceStatus,
    JournalEntry,
    PurchaseInvoice,
    VatApproval,
)
from ledgerline.vat.vat_return import period_bounds

HISTORY_LIMIT = 50
SETTLEMENT_PREFIX = "vat_settlement:"
BOOKED = (InvoiceStatus.OPEN, InvoiceStatus.PAID)


def _booked_invoices(session: Session, admin_id: int, contact_id: int) -> list[PurchaseInvoice]:
    return list(session.scalars(
        select(PurchaseInvoice).where(
            PurchaseInvoice.administration_id == admin_id,
            PurchaseInvoice.contact_id == contact_id,
            PurchaseInvoice.status.in_(BOOKED),
            PurchaseInvoice.is_opening.is_(False),
        ).order_by(PurchaseInvoice.id.desc()).limit(HISTORY_LIMIT)
    ))


def vat_history(invoices: list[PurchaseInvoice]) -> list[frozenset[str]]:
    return [frozenset(line.vat_code for line in i.lines) for i in invoices if i.lines]


def _period_labels(day: date) -> list[str]:
    return [period_bounds(kind, day.year, number)[2]
            for kind, number in (("month", day.month), ("quarter", (day.month - 1) // 3 + 1),
                                 ("year", 1))]


def _settled_period(session: Session, admin_id: int, day: date) -> str | None:
    keys = {f"{SETTLEMENT_PREFIX}{label}"[:40]: label for label in _period_labels(day)}
    reversed_ids = select(JournalEntry.reversal_of_id).where(
        JournalEntry.reversal_of_id.is_not(None))
    found = session.scalars(select(JournalEntry.source_type).where(
        JournalEntry.administration_id == admin_id,
        JournalEntry.source_type.in_(keys), JournalEntry.id.not_in(reversed_ids)))
    return next((keys[k] for k in found), None)


def _approved_period(session: Session, admin_id: int, day: date) -> str | None:
    approval = session.scalar(select(VatApproval).where(
        VatApproval.administration_id == admin_id,
        VatApproval.period_start <= day, VatApproval.period_end >= day).limit(1))
    if approval is None:
        return None
    return f"{approval.period_start.isoformat()} to {approval.period_end.isoformat()}"


def review_advice(
    session: Session, admin: Administration, *, contact, line_codes: list[str],
    total_excl_cents: int | None, document_date: date | None,
) -> list[Advice]:
    """Advice for a proposed purchase. Pure reads; the caller decides how to show it."""
    found: list[Advice] = []
    if contact is not None:
        invoices = _booked_invoices(session, admin.id, contact.id)
        found += rules.vat_rate_advice(contact.name, vat_history(invoices), line_codes)
        found += rules.amount_advice(
            contact.name, [i.total_excl_cents for i in invoices], total_excl_cents)
    if document_date is not None:
        found += rules.period_advice(
            document_date, locked_until=admin.locked_until,
            approved_period=_approved_period(session, admin.id, document_date),
            settled_period=_settled_period(session, admin.id, document_date))
    return found
