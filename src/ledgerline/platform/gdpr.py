import json
import tempfile
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, inspect, select

from ledgerline import audit
from ledgerline.models import (
    Account,
    Administration,
    Attachment,
    AuditEvent,
    BankAccount,
    BankStatement,
    BankTransaction,
    Contact,
    FeedbackEntry,
    JournalEntry,
    JournalLine,
    NumberSequence,
    PaymentAllocation,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    Role,
    SalesInvoice,
    SalesInvoiceLine,
)
from ledgerline.platform.validation import retention_until
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()
RETENTION_YEARS = 7


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def dump_json(payload) -> bytes:
    return json.dumps(payload, default=_json_default, indent=2, ensure_ascii=False).encode()


def row_dict(obj) -> dict:
    return {c.key: getattr(obj, c.key) for c in inspect(type(obj)).column_attrs
            if c.key != "content"}


def owner_only(user: UserDep) -> None:
    if user.role != Role.OWNER:
        raise HTTPException(403, "owner access required")


OwnerRead = Depends(owner_only)


def contact_retention(session, admin, contact_id: int) -> dict:
    dates = [
        session.scalar(select(func.max(SalesInvoice.issue_date)).where(
            SalesInvoice.administration_id == admin.id, SalesInvoice.contact_id == contact_id)),
        session.scalar(select(func.max(PurchaseInvoice.invoice_date)).where(
            PurchaseInvoice.administration_id == admin.id,
            PurchaseInvoice.contact_id == contact_id)),
        session.scalar(select(func.max(JournalEntry.entry_date))
                       .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
                       .where(JournalEntry.administration_id == admin.id,
                              JournalLine.contact_id == contact_id)),
    ]
    last = max((d for d in dates if d), default=None)
    until = retention_until(last, RETENTION_YEARS)
    return {"last_activity": last, "until": until, "years": RETENTION_YEARS,
            "running": until is not None and until >= date.today()}


def _get_contact(session, admin, contact_id: int) -> Contact:
    contact = session.get(Contact, contact_id)
    if contact is None or contact.administration_id != admin.id:
        raise HTTPException(404, "contact not found")
    return contact


def contact_export(session, admin, contact: Contact) -> dict:
    sales = session.scalars(select(SalesInvoice).where(
        SalesInvoice.administration_id == admin.id,
        SalesInvoice.contact_id == contact.id).order_by(SalesInvoice.id)).all()
    purchases = session.scalars(select(PurchaseInvoice).where(
        PurchaseInvoice.administration_id == admin.id,
        PurchaseInvoice.contact_id == contact.id).order_by(PurchaseInvoice.id)).all()
    entries = session.execute(
        select(JournalEntry, JournalLine).join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(JournalEntry.administration_id == admin.id, JournalLine.contact_id == contact.id)
        .order_by(JournalEntry.id, JournalLine.id)).all()
    bank = []
    if contact.iban:
        bank = session.scalars(
            select(BankTransaction).join(BankAccount,
                                         BankAccount.id == BankTransaction.bank_account_id)
            .where(BankAccount.administration_id == admin.id,
                   BankTransaction.counterparty_iban == contact.iban)
            .order_by(BankTransaction.id)).all()
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.administration_id == admin.id, AuditEvent.entity_type == "contact",
        AuditEvent.entity_id == contact.id).order_by(AuditEvent.id)).all()
    return {
        "exported_at": datetime.now(UTC),
        "administration": admin.name,
        "contact": row_dict(contact),
        "sales_invoices": [
            {**row_dict(i), "lines": [row_dict(line) for line in i.lines]} for i in sales],
        "purchase_invoices": [
            {**row_dict(i), "lines": [row_dict(line) for line in i.lines]} for i in purchases],
        "journal_lines": [
            {"entry_number": e.number, "entry_date": e.entry_date, **row_dict(line)}
            for e, line in entries],
        "bank_transactions": [row_dict(t) for t in bank],
        "audit_events": [row_dict(e) for e in events],
    }


@router.get("/settings/privacy")
def privacy_page(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    contacts = session.scalars(select(Contact).where(Contact.administration_id == admin.id)
                               .order_by(func.lower(Contact.name), Contact.id)).all()
    rows = [{"contact": c, "retention": contact_retention(session, admin, c.id)}
            for c in contacts]
    return render(request, "settings/privacy.html",
                  {"rows": rows, "years": RETENTION_YEARS, "is_owner": user.role == Role.OWNER})


@router.get("/settings/privacy/contacts/{contact_id}/export.json", dependencies=[OwnerRead])
def export_contact(contact_id: int, request: Request, session: SessionDep, user: UserDep,
                   admin: AdminDep):
    contact = _get_contact(session, admin, contact_id)
    payload = contact_export(session, admin, contact)
    audit.record(session, "contact.export", user_id=user.id, administration_id=admin.id,
                 entity_type="contact", entity_id=contact.id)
    session.commit()
    return Response(dump_json(payload), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="contact-{contact.id}.json"'})


def _table_dumps(session, admin: Administration):
    aid = admin.id
    sales_ids = select(SalesInvoice.id).where(SalesInvoice.administration_id == aid)
    purchase_ids = select(PurchaseInvoice.id).where(PurchaseInvoice.administration_id == aid)
    bank_ids = select(BankAccount.id).where(BankAccount.administration_id == aid)
    entry_ids = select(JournalEntry.id).where(JournalEntry.administration_id == aid)
    tx_ids = select(BankTransaction.id).where(BankTransaction.bank_account_id.in_(bank_ids))
    queries = {
        "administration": select(Administration).where(Administration.id == aid),
        "accounts": select(Account).where(Account.administration_id == aid),
        "contacts": select(Contact).where(Contact.administration_id == aid),
        "journal_entries": select(JournalEntry).where(JournalEntry.administration_id == aid),
        "journal_lines": select(JournalLine).where(JournalLine.entry_id.in_(entry_ids)),
        "sales_invoices": select(SalesInvoice).where(SalesInvoice.administration_id == aid),
        "sales_invoice_lines": select(SalesInvoiceLine).where(
            SalesInvoiceLine.invoice_id.in_(sales_ids)),
        "purchase_invoices": select(PurchaseInvoice).where(
            PurchaseInvoice.administration_id == aid),
        "purchase_invoice_lines": select(PurchaseInvoiceLine).where(
            PurchaseInvoiceLine.invoice_id.in_(purchase_ids)),
        "bank_accounts": select(BankAccount).where(BankAccount.administration_id == aid),
        "bank_statements": select(BankStatement).where(
            BankStatement.bank_account_id.in_(bank_ids)),
        "bank_transactions": select(BankTransaction).where(
            BankTransaction.bank_account_id.in_(bank_ids)),
        "payment_allocations": select(PaymentAllocation).where(
            PaymentAllocation.bank_transaction_id.in_(tx_ids)),
        "number_sequences": select(NumberSequence).where(NumberSequence.administration_id == aid),
        "attachments": select(Attachment).where(Attachment.administration_id == aid),
        "audit_events": select(AuditEvent).where(AuditEvent.administration_id == aid),
        "feedback_entries": select(FeedbackEntry).where(FeedbackEntry.administration_id == aid),
    }
    for name, stmt in queries.items():
        pk = inspect(stmt.column_descriptions[0]["entity"]).primary_key[0]
        yield name, [row_dict(o) for o in session.scalars(stmt.order_by(pk))]


def build_export_zip(session, admin: Administration):
    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)  # noqa: SIM115
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, rows in _table_dumps(session, admin):
            zf.writestr(f"data/{name}.json", dump_json(rows))
        att = session.execute(select(Attachment.id, Attachment.filename).where(
            Attachment.administration_id == admin.id)).all()
        for att_id, filename in att:
            content = session.scalar(select(Attachment.content).where(Attachment.id == att_id))
            safe = filename.replace("/", "_").replace("\\", "_")
            zf.writestr(f"attachments/{att_id}-{safe}", content or b"")
    spool.seek(0)
    return spool


@router.get("/settings/privacy/export.zip", dependencies=[OwnerRead])
def export_administration(session: SessionDep, user: UserDep, admin: AdminDep):
    spool = build_export_zip(session, admin)
    audit.record(session, "administration.export", user_id=user.id,
                 administration_id=admin.id, entity_type="administration", entity_id=admin.id)
    session.commit()

    def chunks():
        try:
            while block := spool.read(64 * 1024):
                yield block
        finally:
            spool.close()

    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return StreamingResponse(chunks(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="ledgerline-{admin.id}-{stamp}.zip"'})


@router.post("/contacts/{contact_id}/anonymise")
def anonymise(request: Request, contact_id: int, session: SessionDep, user: OwnerDep,
              admin: AdminDep, confirm: Annotated[str, Form()] = ""):
    contact = _get_contact(session, admin, contact_id)
    target = RedirectResponse(f"/contacts/{contact.id}", 303)
    if contact.anonymised_at is not None:
        flash(request, "This contact is already anonymised.", "info")
        return target
    if confirm != "yes":
        flash(request, "Tick the confirmation box to anonymise this contact.", "error")
        return target
    retention = contact_retention(session, admin, contact.id)
    contact.name = f"Anonymised contact #{contact.id}"
    contact.email = contact.address_line = contact.postcode = contact.city = None
    contact.iban = contact.kvk_number = None
    contact.anonymised_at = datetime.now(UTC)
    audit.record(session, "contact.anonymise", user_id=user.id, administration_id=admin.id,
                 entity_type="contact", entity_id=contact.id, details={
                     "blanked": ["name", "email", "address_line", "postcode", "city", "iban",
                                 "kvk_number"],
                     "kept": ["vat_number", "country", "kind"],
                     "retention_until": retention["until"].isoformat()
                     if retention["until"] else None})
    session.commit()
    flash(request, "Contact anonymised. Invoices and journal entries are unchanged.")
    return target
