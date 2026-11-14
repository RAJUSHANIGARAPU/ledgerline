from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.config import get_settings
from ledgerline.invoicing import forms
from ledgerline.invoicing.calc import InvoiceError
from ledgerline.invoicing.pdf import render_invoice_pdf, vat_summary
from ledgerline.invoicing.service import (
    book_purchase_invoice,
    create_credit_note,
    create_purchase_invoice,
    create_sales_invoice,
    delete_draft_purchase_invoice,
    delete_draft_sales_invoice,
    issue_sales_invoice,
    purchase_line_accounts,
    store_attachment,
    update_purchase_invoice,
    update_sales_invoice,
)
from ledgerline.invoicing.ubl import build_ubl
from ledgerline.models import (
    Account,
    AccountType,
    Attachment,
    Contact,
    ContactKind,
    InvoiceStatus,
    JournalEntry,
    PurchaseInvoice,
    SalesInvoice,
)
from ledgerline.money import percent_of
from ledgerline.vat import codes
from ledgerline.vat.codes import Direction
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()

STATUS_FILTERS = ("draft", "open", "paid", "cancelled", "overdue")
FormList = Annotated[list[str] | None, Form()]
FormText = Annotated[str, Form()]


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _display_status(invoice: SalesInvoice | PurchaseInvoice, today: date) -> str:
    remaining = invoice.total_incl_cents - invoice.paid_cents
    if invoice.status == InvoiceStatus.OPEN and invoice.due_date < today and remaining > 0:
        return "overdue"
    return invoice.status


def _contacts(session: Session, admin_id: int, kinds: tuple[str, ...]) -> list[Contact]:
    return list(session.scalars(
        select(Contact)
        .where(Contact.administration_id == admin_id, Contact.kind.in_(kinds),
               Contact.anonymised_at.is_(None))
        .order_by(Contact.name)
    ))


def _accounts(session: Session, admin_id: int, kind: AccountType) -> list[Account]:
    return list(session.scalars(
        select(Account)
        .where(Account.administration_id == admin_id, Account.type == kind,
               Account.is_active.is_(True))
        .order_by(Account.code)
    ))


def _owned_contact(session: Session, admin_id: int, raw: str) -> Contact:
    contact = session.get(Contact, int(raw)) if raw.isdigit() else None
    if contact is None or contact.administration_id != admin_id:
        raise InvoiceError("choose a contact")
    return contact


# ================================================================= sales


@dataclass
class SalesForm:
    contact_id: str
    issue_date: str
    due_date: str
    supply_date: str
    reference: str
    notes: str
    rows: list[dict[str, str]]


def sales_form(
    contact_id: FormText = "", issue_date: FormText = "", due_date: FormText = "",
    supply_date: FormText = "", reference: FormText = "", notes: FormText = "",
    line_description: FormList = None, line_qty: FormList = None, line_price: FormList = None,
    line_vat_code: FormList = None, line_account: FormList = None,
    op: FormText = "", remove: FormText = "",
) -> SalesForm:
    rows = forms.sales_rows(
        line_description, line_qty, line_price, line_vat_code, line_account, op, remove
    )
    return SalesForm(contact_id, issue_date, due_date, supply_date, reference, notes, rows)


SalesFormDep = Annotated[SalesForm, Depends(sales_form)]


def _sales_or_404(session: Session, admin_id: int, invoice_id: int) -> SalesInvoice:
    invoice = session.get(SalesInvoice, invoice_id)
    if invoice is None or invoice.administration_id != admin_id:
        raise HTTPException(404, "invoice not found")
    return invoice


def _sales_editor_ctx(session: Session, admin, rows, invoice=None, values=None, errors=None):
    accounts = _accounts(session, admin.id, AccountType.REVENUE)
    today = date.today()
    values = values or {
        "contact_id": str(invoice.contact_id) if invoice else "",
        "issue_date": (invoice.issue_date if invoice else today).isoformat(),
        "due_date": (invoice.due_date if invoice
                     else today + timedelta(days=admin.payment_terms_days)).isoformat(),
        "supply_date": invoice.supply_date.isoformat() if invoice and invoice.supply_date else "",
        "reference": (invoice.reference or "") if invoice else "",
        "notes": (invoice.notes or "") if invoice else "",
    }
    return {
        "invoice": invoice, "values": values, "errors": errors or [],
        "customers": _contacts(session, admin.id, (ContactKind.CUSTOMER, ContactKind.BOTH)),
        **_sales_lines_ctx(admin, rows, accounts),
    }


def _sales_lines_ctx(admin, rows, accounts):
    return {
        "rows": rows,
        "accounts": accounts,
        "vat_codes": [c for c in codes.for_direction(Direction.SALES)
                      if not admin.kor or c.code == "VKOR"],
        "kor": admin.kor,
        "preview": forms.sales_preview(rows, "VKOR" if admin.kor else None),
    }


def _default_sales_row(accounts: list[Account], admin) -> dict[str, str]:
    account = next((a for a in accounts if a.code == "8000"), accounts[0] if accounts else None)
    vat = "VKOR" if admin.kor else ((account.default_vat_code if account else None) or "V21")
    return forms.blank_sales_row(account.id if account else "", vat)


@router.get("/sales")
def sales_list(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
               status: str = ""):
    today = date.today()
    stmt = select(SalesInvoice).where(SalesInvoice.administration_id == admin.id)
    if status == "overdue":
        stmt = stmt.where(SalesInvoice.status == InvoiceStatus.OPEN, SalesInvoice.due_date < today,
                          SalesInvoice.total_incl_cents > SalesInvoice.paid_cents)
    elif status in STATUS_FILTERS:
        stmt = stmt.where(SalesInvoice.status == status)
    invoices = session.scalars(stmt.order_by(SalesInvoice.id.desc())).all()
    rows = [(inv, _display_status(inv, today)) for inv in invoices]
    return render(request, "sales/list.html",
                  {"rows": rows, "status": status, "filters": STATUS_FILTERS})


@router.get("/sales/new")
def sales_new(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    accounts = _accounts(session, admin.id, AccountType.REVENUE)
    ctx = _sales_editor_ctx(session, admin, [_default_sales_row(accounts, admin)])
    return render(request, "sales/form.html", ctx)


def _save_sales(session, request, admin, owner, data: SalesForm, invoice: SalesInvoice | None):
    try:
        contact = _owned_contact(session, admin.id, data.contact_id)
        issue = forms.parse_date(data.issue_date, "invoice date")
        due = forms.parse_date(data.due_date, "due date")
        supply = forms.parse_optional_date(data.supply_date, "supply date")
        lines = forms.sales_inputs(data.rows)
        if invoice is None:
            invoice = create_sales_invoice(
                session, admin, contact, lines, issue_date=issue, due_date=due,
                supply_date=supply, reference=data.reference.strip(), notes=data.notes.strip(),
            )
        else:
            update_sales_invoice(
                session, admin, invoice, contact, lines, issue_date=issue, due_date=due,
                supply_date=supply, reference=data.reference.strip(), notes=data.notes.strip(),
            )
    except InvoiceError as exc:
        session.rollback()
        values = {k: getattr(data, k) for k in
                  ("contact_id", "issue_date", "due_date", "supply_date", "reference", "notes")}
        return render(
            request, "sales/form.html",
            _sales_editor_ctx(session, admin, data.rows, invoice, values, exc.problems),
            status_code=422,
        )
    session.commit()
    flash(request, "Draft saved.")
    return _redirect(f"/sales/{invoice.id}")


@router.post("/sales/new")
def sales_create(request: Request, session: SessionDep, owner: OwnerDep, admin: AdminDep,
                 data: SalesFormDep):
    return _save_sales(session, request, admin, owner, data, None)


@router.post("/sales/preview")
def sales_preview(request: Request, session: SessionDep, owner: OwnerDep, admin: AdminDep,
                  data: SalesFormDep):
    accounts = _accounts(session, admin.id, AccountType.REVENUE)
    rows = data.rows
    if not rows:
        rows = [_default_sales_row(accounts, admin)]
    if rows and any(r["vat_code"] == "" for r in rows):
        default = _default_sales_row(accounts, admin)
        for row in rows:
            row["vat_code"] = row["vat_code"] or default["vat_code"]
    return render(request, "sales/_lines.html", _sales_lines_ctx(admin, rows, accounts))


@router.get("/sales/{invoice_id}")
def sales_detail(invoice_id: int, request: Request, session: SessionDep, user: UserDep,
                 admin: AdminDep):
    invoice = _sales_or_404(session, admin.id, invoice_id)
    if invoice.status == InvoiceStatus.DRAFT:
        rows = [
            {"description": line.description, "qty": forms.decimal_text(line.quantity),
             "price": forms.money_text(line.unit_price_cents), "vat_code": line.vat_code,
             "account": str(line.revenue_account_id)}
            for line in invoice.lines
        ] or [_default_sales_row(_accounts(session, admin.id, AccountType.REVENUE), admin)]
        return render(request, "sales/form.html", _sales_editor_ctx(session, admin, rows, invoice))
    original = session.get(SalesInvoice, invoice.credit_note_of_id) if invoice.credit_note_of_id \
        else None
    credit_notes = session.scalars(
        select(SalesInvoice).where(SalesInvoice.credit_note_of_id == invoice.id,
                                   SalesInvoice.administration_id == admin.id)
    ).all()
    entry = session.get(JournalEntry, invoice.journal_entry_id) if invoice.journal_entry_id \
        else None
    accounts = {a.id: a for a in _accounts(session, admin.id, AccountType.REVENUE)}
    return render(request, "sales/detail.html", {
        "invoice": invoice, "original": original, "credit_notes": credit_notes, "entry": entry,
        "display_status": _display_status(invoice, date.today()),
        "remaining": invoice.total_incl_cents - invoice.paid_cents,
        "vat_summary": vat_summary(invoice), "accounts": accounts,
        "can_credit": invoice.status in (InvoiceStatus.OPEN, InvoiceStatus.PAID)
        and not invoice.credit_note_of_id and not invoice.is_opening,
    })


@router.post("/sales/{invoice_id}/save")
def sales_save(invoice_id: int, request: Request, session: SessionDep, owner: OwnerDep,
               admin: AdminDep, data: SalesFormDep):
    invoice = _sales_or_404(session, admin.id, invoice_id)
    if invoice.status != InvoiceStatus.DRAFT:
        flash(request, "An issued invoice cannot be edited; issue a credit note instead.", "error")
        return _redirect(f"/sales/{invoice.id}")
    return _save_sales(session, request, admin, owner, data, invoice)


@router.post("/sales/{invoice_id}/issue")
def sales_issue(invoice_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                admin: AdminDep):
    invoice = _sales_or_404(session, admin.id, invoice_id)
    try:
        issue_sales_invoice(session, admin, invoice, owner.id)
    except InvoiceError as exc:
        session.rollback()
        flash(request, "Cannot issue yet: " + "; ".join(exc.problems), "error")
        return _redirect(f"/sales/{invoice_id}")
    session.commit()
    flash(request, f"Invoice {invoice.number} issued.")
    return _redirect(f"/sales/{invoice_id}")


@router.post("/sales/{invoice_id}/delete")
def sales_delete(invoice_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                 admin: AdminDep):
    invoice = _sales_or_404(session, admin.id, invoice_id)
    try:
        delete_draft_sales_invoice(session, invoice)
    except InvoiceError as exc:
        session.rollback()
        flash(request, str(exc), "error")
        return _redirect(f"/sales/{invoice_id}")
    session.commit()
    flash(request, "Draft deleted.")
    return _redirect("/sales")


@router.post("/sales/{invoice_id}/credit")
def sales_credit(invoice_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                 admin: AdminDep):
    invoice = _sales_or_404(session, admin.id, invoice_id)
    try:
        note = create_credit_note(session, admin, invoice, owner.id)
    except InvoiceError as exc:
        session.rollback()
        flash(request, "Cannot credit: " + "; ".join(exc.problems), "error")
        return _redirect(f"/sales/{invoice_id}")
    session.commit()
    flash(request, f"Credit note {note.number} issued; invoice {invoice.number} is cancelled.")
    return _redirect(f"/sales/{note.id}")


def _original_number(session: Session, invoice: SalesInvoice) -> str | None:
    if not invoice.credit_note_of_id:
        return None
    original = session.get(SalesInvoice, invoice.credit_note_of_id)
    return original.number if original else None


def _issued_or_409(session: Session, admin_id: int, invoice_id: int) -> SalesInvoice:
    invoice = _sales_or_404(session, admin_id, invoice_id)
    if not invoice.number:
        raise HTTPException(409, "issue the invoice first")
    if invoice.is_opening:
        raise HTTPException(409, "an opening item has no invoice document here")
    return invoice


def _filename(invoice: SalesInvoice, ext: str) -> str:
    safe = "".join(ch for ch in invoice.number if ch.isalnum() or ch in "-_")
    return f"{safe}.{ext}"


@router.get("/sales/{invoice_id}/pdf")
def sales_pdf(invoice_id: int, session: SessionDep, user: UserDep, admin: AdminDep):
    invoice = _issued_or_409(session, admin.id, invoice_id)
    pdf = render_invoice_pdf(invoice, admin, _original_number(session, invoice))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{_filename(invoice, "pdf")}"'})


@router.get("/sales/{invoice_id}/ubl")
def sales_ubl(invoice_id: int, session: SessionDep, user: UserDep, admin: AdminDep):
    invoice = _issued_or_409(session, admin.id, invoice_id)
    xml = build_ubl(invoice, admin, _original_number(session, invoice))
    return Response(xml, media_type="application/xml", headers={
        "Content-Disposition": f'attachment; filename="{_filename(invoice, "xml")}"'})


# ============================================================== purchases


@dataclass
class PurchaseForm:
    contact_id: str
    supplier_reference: str
    invoice_date: str
    due_date: str
    description: str
    rows: list[dict[str, str]]


def purchase_form(
    contact_id: FormText = "", supplier_reference: FormText = "", invoice_date: FormText = "",
    due_date: FormText = "", description: FormText = "",
    line_description: FormList = None, line_account: FormList = None,
    line_amount: FormList = None, line_vat_code: FormList = None, line_vat: FormList = None,
    line_vat_auto: FormList = None, op: FormText = "", remove: FormText = "",
) -> PurchaseForm:
    rows = forms.purchase_rows(
        line_description, line_account, line_amount, line_vat_code, line_vat, line_vat_auto,
        op, remove,
    )
    return PurchaseForm(contact_id, supplier_reference, invoice_date, due_date, description, rows)


PurchaseFormDep = Annotated[PurchaseForm, Depends(purchase_form)]


def _purchase_or_404(session: Session, admin_id: int, invoice_id: int) -> PurchaseInvoice:
    invoice = session.get(PurchaseInvoice, invoice_id)
    if invoice is None or invoice.administration_id != admin_id:
        raise HTTPException(404, "invoice not found")
    return invoice


def _default_purchase_row(accounts: list[Account]) -> dict[str, str]:
    account = next((a for a in accounts if a.code == "4900"), accounts[0] if accounts else None)
    return forms.blank_purchase_row(
        account.id if account else "", (account.default_vat_code if account else None) or "I21"
    )


def _purchase_lines_ctx(accounts, rows):
    rows, preview, locked = forms.purchase_preview(rows)
    return {
        "rows": list(zip(rows, locked, strict=True)), "accounts": accounts,
        "vat_codes": codes.for_direction(Direction.PURCHASE), "preview": preview,
    }


def _purchase_editor_ctx(session, admin, rows, invoice=None, values=None, errors=None):
    accounts = purchase_line_accounts(session, admin.id)
    today = date.today()
    values = values or {
        "contact_id": str(invoice.contact_id) if invoice else "",
        "supplier_reference": invoice.supplier_reference if invoice else "",
        "invoice_date": (invoice.invoice_date if invoice else today).isoformat(),
        "due_date": (invoice.due_date if invoice
                     else today + timedelta(days=admin.payment_terms_days)).isoformat(),
        "description": (invoice.description or "") if invoice else "",
    }
    attachment = session.get(Attachment, invoice.attachment_id) \
        if invoice and invoice.attachment_id else None
    return {
        "invoice": invoice, "values": values, "errors": errors or [], "attachment": attachment,
        "suppliers": _contacts(session, admin.id, (ContactKind.SUPPLIER, ContactKind.BOTH)),
        **_purchase_lines_ctx(accounts, rows),
    }


@router.get("/purchases")
def purchases_list(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
                   status: str = ""):
    today = date.today()
    stmt = select(PurchaseInvoice).where(PurchaseInvoice.administration_id == admin.id)
    if status == "overdue":
        stmt = stmt.where(
            PurchaseInvoice.status == InvoiceStatus.OPEN, PurchaseInvoice.due_date < today,
            PurchaseInvoice.total_incl_cents > PurchaseInvoice.paid_cents)
    elif status in STATUS_FILTERS:
        stmt = stmt.where(PurchaseInvoice.status == status)
    invoices = session.scalars(stmt.order_by(PurchaseInvoice.id.desc())).all()
    rows = [(inv, _display_status(inv, today)) for inv in invoices]
    return render(request, "purchases/list.html",
                  {"rows": rows, "status": status, "filters": STATUS_FILTERS[:3] + ("overdue",)})


@router.get("/purchases/new")
def purchase_new(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    accounts = purchase_line_accounts(session, admin.id)
    return render(request, "purchases/form.html",
                  _purchase_editor_ctx(session, admin, [_default_purchase_row(accounts)]))


def _read_upload(session: Session, admin, upload: UploadFile | None) -> Attachment | None:
    if upload is None or not upload.filename:
        return None
    limit = get_settings().max_upload_bytes
    data = upload.file.read(limit + 1)
    return store_attachment(session, admin, upload.filename, data)


def _save_purchase(session, request, admin, data: PurchaseForm, upload, invoice):
    try:
        contact = _owned_contact(session, admin.id, data.contact_id)
        invoice_date = forms.parse_date(data.invoice_date, "invoice date")
        due = forms.parse_date(data.due_date, "due date")
        lines = forms.purchase_inputs(data.rows)
        attachment = _read_upload(session, admin, upload)
        if invoice is None:
            invoice = create_purchase_invoice(
                session, admin, contact, lines, supplier_reference=data.supplier_reference,
                invoice_date=invoice_date, due_date=due, description=data.description.strip(),
                attachment=attachment,
            )
        else:
            update_purchase_invoice(
                session, admin, invoice, contact, lines,
                supplier_reference=data.supplier_reference, invoice_date=invoice_date,
                due_date=due, description=data.description.strip(), attachment=attachment,
            )
    except InvoiceError as exc:
        session.rollback()
        values = {k: getattr(data, k) for k in
                  ("contact_id", "supplier_reference", "invoice_date", "due_date", "description")}
        return render(
            request, "purchases/form.html",
            _purchase_editor_ctx(session, admin, data.rows, invoice, values, exc.problems),
            status_code=422,
        )
    session.commit()
    flash(request, "Draft saved.")
    return _redirect(f"/purchases/{invoice.id}")


@router.post("/purchases/new")
def purchase_create(request: Request, session: SessionDep, owner: OwnerDep, admin: AdminDep,
                    data: PurchaseFormDep, attachment: Annotated[UploadFile | None, File()] = None):
    return _save_purchase(session, request, admin, data, attachment, None)


@router.post("/purchases/preview")
def purchase_preview(request: Request, session: SessionDep, owner: OwnerDep, admin: AdminDep,
                     data: PurchaseFormDep):
    accounts = purchase_line_accounts(session, admin.id)
    rows = data.rows or [_default_purchase_row(accounts)]
    return render(request, "purchases/_lines.html", _purchase_lines_ctx(accounts, rows))


@router.get("/purchases/{invoice_id}")
def purchase_detail(invoice_id: int, request: Request, session: SessionDep, user: UserDep,
                    admin: AdminDep):
    invoice = _purchase_or_404(session, admin.id, invoice_id)
    if invoice.status == InvoiceStatus.DRAFT:
        rows = [
            {"description": line.description, "account": str(line.expense_account_id),
             "amount": forms.money_text(line.amount_excl_cents), "vat_code": line.vat_code,
             "vat": forms.money_text(line.vat_cents),
             "vat_auto": "1" if line.vat_cents == percent_of(
                 line.amount_excl_cents, codes.get(line.vat_code).rate) else "0"}
            for line in invoice.lines
        ]
        ctx = _purchase_editor_ctx(session, admin, rows, invoice)
        return render(request, "purchases/form.html", ctx)
    entry = session.get(JournalEntry, invoice.journal_entry_id) if invoice.journal_entry_id \
        else None
    attachment = session.get(Attachment, invoice.attachment_id) if invoice.attachment_id else None
    accounts = {a.id: a for a in purchase_line_accounts(session, admin.id)}
    return render(request, "purchases/detail.html", {
        "invoice": invoice, "entry": entry, "attachment": attachment, "accounts": accounts,
        "display_status": _display_status(invoice, date.today()),
        "remaining": invoice.total_incl_cents - invoice.paid_cents,
        "reverse": any(codes.get(line.vat_code).reverse_charge for line in invoice.lines),
    })


@router.post("/purchases/{invoice_id}/save")
def purchase_save(invoice_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                  admin: AdminDep, data: PurchaseFormDep,
                  attachment: Annotated[UploadFile | None, File()] = None):
    invoice = _purchase_or_404(session, admin.id, invoice_id)
    if invoice.status != InvoiceStatus.DRAFT:
        flash(request, "A booked invoice cannot be edited.", "error")
        return _redirect(f"/purchases/{invoice.id}")
    return _save_purchase(session, request, admin, data, attachment, invoice)


@router.post("/purchases/{invoice_id}/book")
def purchase_book(invoice_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                  admin: AdminDep):
    invoice = _purchase_or_404(session, admin.id, invoice_id)
    try:
        book_purchase_invoice(session, admin, invoice, owner.id)
    except InvoiceError as exc:
        session.rollback()
        flash(request, "Cannot book: " + "; ".join(exc.problems), "error")
        return _redirect(f"/purchases/{invoice_id}")
    session.commit()
    flash(request, "Purchase invoice booked.")
    return _redirect(f"/purchases/{invoice_id}")


@router.post("/purchases/{invoice_id}/delete")
def purchase_delete(invoice_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                    admin: AdminDep):
    invoice = _purchase_or_404(session, admin.id, invoice_id)
    try:
        delete_draft_purchase_invoice(session, invoice)
    except InvoiceError as exc:
        session.rollback()
        flash(request, str(exc), "error")
        return _redirect(f"/purchases/{invoice_id}")
    session.commit()
    flash(request, "Draft deleted.")
    return _redirect("/purchases")


@router.get("/purchases/{invoice_id}/attachment")
def purchase_attachment(invoice_id: int, session: SessionDep, user: UserDep, admin: AdminDep):
    invoice = _purchase_or_404(session, admin.id, invoice_id)
    attachment = session.get(Attachment, invoice.attachment_id) if invoice.attachment_id else None
    if attachment is None or attachment.administration_id != admin.id:
        raise HTTPException(404, "no attachment")
    safe = "".join(ch for ch in attachment.filename if ch.isalnum() or ch in "-_. ") or "file"
    return Response(attachment.content, media_type=attachment.content_type, headers={
        "Content-Disposition": f'attachment; filename="{safe}"',
        "X-Content-Type-Options": "nosniff"})
