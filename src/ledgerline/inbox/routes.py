import logging
from datetime import date
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.config import get_settings
from ledgerline.db import tenant_session
from ledgerline.inbox import email_intake, platform_booking, review, service, worker
from ledgerline.inbox.extract import pipeline
from ledgerline.invoicing import forms
from ledgerline.invoicing.calc import InvoiceError
from ledgerline.models import (
    WRITE_ROLES,
    Account,
    AccountType,
    Attachment,
    InboxDocument,
    InboxStatus,
    PurchaseInvoice,
)
from ledgerline.money import MoneyError, to_cents
from ledgerline.vat import codes
from ledgerline.vat.codes import Direction
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

log = logging.getLogger(__name__)
router = APIRouter()

TABS = (
    ("review", "To review", InboxStatus.REVIEW),
    ("new", "Processing", InboxStatus.NEW),
    ("booked", "Booked", InboxStatus.BOOKED),
    ("rejected", "Rejected", InboxStatus.REJECTED),
)
FormText = Annotated[str, Form()]
FormList = Annotated[list[str] | None, Form()]
PDF_FRAME_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'self'",
    "X-Frame-Options": "SAMEORIGIN",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _document_or_404(session: Session, admin_id: int, document_id: int) -> InboxDocument:
    document = session.get(InboxDocument, document_id)
    if document is None or document.administration_id != admin_id:
        raise HTTPException(404, "document not found")
    return document


def _process_in_background(tenant_id: int, ids: list[int]) -> None:
    with tenant_session(tenant_id) as session:
        worker.process_all_pending(session, ids)


@router.get("/inbox")
def inbox_list(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
               tab: str = "review", dup: str = ""):
    tab = tab if tab in {t[0] for t in TABS} else "review"
    status = next(t[2] for t in TABS if t[0] == tab)
    documents = list(session.scalars(
        select(InboxDocument)
        .where(InboxDocument.administration_id == admin.id, InboxDocument.status == status)
        .order_by(InboxDocument.id.desc()).limit(200)
    ))
    counts = review.status_counts(session, admin.id)
    dup_ids = [int(d) for d in dup.split(",") if d.isdigit()][:20]
    duplicates = list(session.scalars(
        select(InboxDocument).where(InboxDocument.administration_id == admin.id,
                                    InboxDocument.id.in_(dup_ids))
    )) if dup_ids else []
    wanted = [d.attachment_id for d in [*documents, *duplicates]]
    names = dict(session.execute(
        select(Attachment.id, Attachment.filename).where(Attachment.id.in_(wanted))).all())
    return render(request, "inbox/index.html", {
        "documents": documents, "tab": tab, "tabs": TABS, "counts": counts,
        "duplicates": duplicates, "names": names, "labels": review.METHODS,
        "max_mb": get_settings().max_upload_bytes // (1024 * 1024),
        "inbox_address": email_intake.inbox_address(get_settings(), admin.inbox_token)
        if user.role in WRITE_ROLES else None,
    })


@router.post("/inbox/upload")
def inbox_upload(request: Request, session: SessionDep, owner: OwnerDep, admin: AdminDep,
                 background: BackgroundTasks, files: Annotated[list[UploadFile], File()]):
    limit = get_settings().max_upload_bytes
    new_ids, duplicate_ids = [], []
    for upload in files:
        if not upload.filename:
            continue
        try:
            document, created = service.receive(
                session, admin.id, upload.filename, upload.file.read(limit + 1), "upload",
                user_id=owner.id,
            )
        except service.InboxError as exc:
            flash(request, f"{upload.filename}: {exc}", "error")
            continue
        (new_ids if created else duplicate_ids).append(document.id)
    session.commit()
    if new_ids:
        flash(request, f"{len(new_ids)} document(s) received.")
        background.add_task(_process_in_background, admin.tenant_id, new_ids)
    if duplicate_ids:
        return _redirect("/inbox?dup=" + ",".join(map(str, duplicate_ids)))
    return _redirect("/inbox?tab=new" if new_ids else "/inbox")


def _accounts(session: Session, admin_id: int) -> list[Account]:
    return list(session.scalars(
        select(Account).where(Account.administration_id == admin_id,
                              Account.type == AccountType.EXPENSE, Account.is_active.is_(True))
        .order_by(Account.code)
    ))


def _detail_ctx(session: Session, admin, document: InboxDocument, proposal=None, values=None,
                rows=None, errors=None):
    attachment = session.get(Attachment, document.attachment_id)
    proposal = proposal or review.build_proposal(session, admin, document)
    label, tone = review.method_label(document.extraction_method)
    purchase = session.get(PurchaseInvoice, document.purchase_invoice_id) \
        if document.purchase_invoice_id else None
    values = values or {
        "contact_id": str(proposal.contact.id) if proposal.contact else "",
        "supplier_reference": proposal.reference, "invoice_date": proposal.invoice_date,
        "due_date": proposal.due_date, "description": "",
    }
    return {
        "document": document, "attachment": attachment, "proposal": proposal, "values": values,
        "rows": rows if rows is not None else proposal.rows, "errors": errors or [],
        "method_label": label, "method_tone": tone, "purchase": purchase,
        "accounts": _accounts(session, admin.id),
        "vat_codes": codes.for_direction(Direction.PURCHASE),
        "suppliers": review._suppliers(session, admin.id),
        "today": date.today().isoformat(),
    }


def _statement_ctx(session: Session, admin, document: InboxDocument, errors=None, values=None):
    st = platform_booking.statement_of(document)
    attachment = session.get(Attachment, document.attachment_id)
    entry = platform_booking.existing_entry(session, admin.id, st)
    return {
        "document": document, "attachment": attachment, "st": st, "errors": errors or [],
        "decisions": platform_booking.pending_decisions(st), "entry": entry,
        "values": values or {}, "sales_codes": [
            c for c in codes.for_direction(Direction.SALES) if not c.reverse_charge],
        "accounts": list(session.scalars(
            select(Account).where(Account.administration_id == admin.id,
                                  Account.is_active.is_(True),
                                  Account.type.in_((AccountType.REVENUE, AccountType.EXPENSE,
                                                    AccountType.LIABILITY)))
            .order_by(Account.code))),
    }


@router.get("/inbox/{document_id}")
def inbox_detail(document_id: int, request: Request, session: SessionDep, user: UserDep,
                 admin: AdminDep):
    document = _document_or_404(session, admin.id, document_id)
    if platform_booking.statement_of(document) is not None:
        return render(request, "inbox/statement.html",
                      _statement_ctx(session, admin, document))
    return render(request, "inbox/detail.html", _detail_ctx(session, admin, document))


@router.get("/inbox/{document_id}/file")
def inbox_file(document_id: int, session: SessionDep, user: UserDep, admin: AdminDep,
               download: int = 0):
    document = _document_or_404(session, admin.id, document_id)
    attachment = session.get(Attachment, document.attachment_id)
    safe = "".join(ch for ch in attachment.filename if ch.isalnum() or ch in "-_. ") or "file"
    inline = not download and attachment.content_type in {"application/pdf", "image/jpeg",
                                                           "image/png"}
    headers = {
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe}"',
        "X-Content-Type-Options": "nosniff",
    }
    if inline and attachment.content_type == "application/pdf":
        headers.update(PDF_FRAME_HEADERS)
    return Response(attachment.content, media_type=attachment.content_type, headers=headers)


def review_form(
    contact_id: FormText = "", supplier_reference: FormText = "", invoice_date: FormText = "",
    due_date: FormText = "", description: FormText = "", new_name: FormText = "",
    new_vat: FormText = "", new_kvk: FormText = "", new_iban: FormText = "",
    new_country: FormText = "NL",
    line_description: FormList = None, line_account: FormList = None,
    line_amount: FormList = None, line_vat_code: FormList = None, line_vat: FormList = None,
) -> review.ReviewForm:
    count = len(line_description or [])
    vat = (line_vat or []) + [""] * count
    rows = forms.purchase_rows(
        line_description, line_account, line_amount, line_vat_code, vat[:count], ["0"] * count,
    )
    return review.ReviewForm(
        contact_id, supplier_reference, invoice_date, due_date, description, rows,
        new_name, new_vat, new_kvk, new_iban, new_country,
    )



ReviewFormDep = Annotated[review.ReviewForm, Depends(review_form)]


def _rerender(request, session, admin, document, data: review.ReviewForm, errors: list[str]):
    session.rollback()
    document = session.get(InboxDocument, document.id)
    values = {
        "contact_id": data.contact_id, "supplier_reference": data.supplier_reference,
        "invoice_date": data.invoice_date, "due_date": data.due_date,
        "description": data.description,
    }
    ctx = _detail_ctx(session, admin, document, values=values, rows=data.rows, errors=errors)
    ctx["new_supplier"] = data
    return render(request, "inbox/detail.html", ctx, status_code=422)


@router.post("/inbox/{document_id}/create")
def inbox_create(document_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                 admin: AdminDep, data: ReviewFormDep, action: FormText = "draft"):
    document = _document_or_404(session, admin.id, document_id)
    try:
        if action == "link":
            invoice = review.link_existing(
                session, admin, owner, document, data.contact_id, data.supplier_reference)
            session.commit()
            flash(request, "Linked to the purchase invoice that already exists.")
            return _redirect(f"/purchases/{invoice.id}")
        invoice = review.create_purchase(session, admin, owner, document, data)
        session.commit()
    except InvoiceError as exc:
        return _rerender(request, session, admin, document, data, exc.problems)
    if action == "book":
        try:
            review.book(session, admin, owner, invoice)
            session.commit()
        except InvoiceError as exc:
            session.rollback()
            flash(request, "Draft created, but it could not be booked: " + "; ".join(exc.problems),
                  "error")
            return _redirect(f"/purchases/{invoice.id}")
        flash(request, "Purchase invoice created and booked.")
    else:
        flash(request, "Purchase draft created. Check it and book it from the purchase page.")
    return _redirect(f"/purchases/{invoice.id}")


@router.post("/inbox/{document_id}/reject")
def inbox_reject(document_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                 admin: AdminDep):
    document = _document_or_404(session, admin.id, document_id)
    if document.status == InboxStatus.BOOKED:
        flash(request, "A document with a purchase invoice cannot be rejected.", "error")
        return _redirect(f"/inbox/{document.id}")
    review.reject(session, admin, owner, document)
    session.commit()
    flash(request, "Document rejected.")
    return _redirect("/inbox")


@router.post("/inbox/{document_id}/rerun")
def inbox_rerun(document_id: int, request: Request, session: SessionDep, owner: OwnerDep,
                admin: AdminDep):
    document = _document_or_404(session, admin.id, document_id)
    if document.status == InboxStatus.BOOKED:
        flash(request, "A document with a purchase invoice cannot be read again.", "error")
        return _redirect(f"/inbox/{document.id}")
    pipeline.run(session, document)
    session.commit()
    flash(request, "The document was read again.")
    return _redirect(f"/inbox/{document.id}")


def _split(sales_code: FormList, sales_amount: FormList) -> list[tuple[str, int]]:
    pairs = zip(sales_code or [], sales_amount or [], strict=False)
    try:
        return [(code, to_cents(amount)) for code, amount in pairs if amount.strip()]
    except MoneyError as exc:
        raise platform_booking.PlatformBookingError(str(exc)) from exc


def _account_id(value: str) -> int | None:
    return int(value) if value.isdigit() else None


@router.post("/inbox/{document_id}/book-statement")
def inbox_book_statement(
    document_id: int, request: Request, session: SessionDep, owner: OwnerDep, admin: AdminDep,
    sales_code: FormList = None, sales_amount: FormList = None, tips_account: FormText = "",
    adjustments_account: FormText = "", accept_difference: FormText = "",
):
    document = _document_or_404(session, admin.id, document_id)
    try:
        choices = platform_booking.StatementChoices(
            _split(sales_code, sales_amount), _account_id(tips_account),
            _account_id(adjustments_account), accept_difference == "1")
        entry = platform_booking.book_statement(session, admin, owner, document, choices)
        session.commit()
    except platform_booking.PlatformBookingError as exc:
        session.rollback()
        document = session.get(InboxDocument, document.id)
        values = {"tips_account": tips_account, "adjustments_account": adjustments_account,
                  "split": dict(zip(sales_code or [], sales_amount or [], strict=False))}
        ctx = _statement_ctx(session, admin, document, exc.problems, values)
        return render(request, "inbox/statement.html", ctx, status_code=422)
    flash(request, f"Statement booked as journal entry {entry.number}. Book the payout from "
                   "the bank screen when it arrives.")
    return _redirect(f"/inbox/{document.id}")
