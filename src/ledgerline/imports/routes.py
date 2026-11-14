from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from ledgerline.imports import contacts as contact_import
from ledgerline.imports import csvio
from ledgerline.imports import opening as opening_import
from ledgerline.models import WRITE_ROLES, User
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()


def writer_page(user: UserDep) -> User:
    if user.role not in WRITE_ROLES:
        raise HTTPException(403, "importing needs a write role")
    return user


WriterPage = Annotated[User, Depends(writer_page)]


def _read_upload(upload: UploadFile | None) -> tuple[str, bytes]:
    if upload is None or not upload.filename:
        raise csvio.CsvError("Choose a CSV file first.")
    content = upload.file.read(csvio.MAX_BYTES + 1)
    return upload.filename, content


def _csv_response(content: bytes, name: str) -> Response:
    return Response(content, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}"'})


def _delimiter(sep: str) -> str:
    return "," if sep == "comma" else ";"


@router.get("/contacts/import")
def contacts_upload(request: Request, user: WriterPage, admin: AdminDep):
    return render(request, "imports/contacts.html", {"columns": contact_import.COLUMNS})


@router.get("/contacts/import/template.csv")
def contacts_template(user: UserDep, sep: str = "semicolon"):
    return _csv_response(contact_import.template_csv(_delimiter(sep)),
                         "ledgerline-contacts-template.csv")


@router.post("/contacts/import/preview")
def contacts_preview(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                     file: Annotated[UploadFile | None, File()] = None):
    try:
        filename, content = _read_upload(file)
        plan = contact_import.plan_contacts(session, admin, content)
    except csvio.CsvError as exc:
        return render(request, "imports/contacts.html",
                      {"columns": contact_import.COLUMNS, "error": str(exc)}, 422)
    return render(request, "imports/contacts_preview.html",
                  {"plan": plan, "filename": filename, "payload": csvio.decode(content)[0]})


@router.post("/contacts/import/confirm")
def contacts_confirm(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                     payload: Annotated[str, Form()] = "",
                     filename: Annotated[str, Form()] = ""):
    try:
        plan = contact_import.plan_contacts(session, admin, payload.encode("utf-8"))
    except csvio.CsvError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/contacts/import", 303)
    created = contact_import.apply_plan(session, admin, plan, user.id, filename)
    session.commit()
    flash(request, f"Imported {created} contact{'' if created == 1 else 's'}; "
                   f"{plan.count('skip')} skipped, {plan.count('error')} with errors.")
    return RedirectResponse("/contacts", 303)


@router.get("/setup/opening-items/template.csv")
def opening_template(user: UserDep, sep: str = "semicolon"):
    return _csv_response(opening_import.template_csv(_delimiter(sep)),
                         "ledgerline-opening-items-template.csv")


def _step(request: Request, session, admin, status_code: int = 200, **extra):
    from ledgerline.onboarding.routes import _page

    return _page(request, session, admin, "opening-items", status_code, **extra)


@router.post("/setup/opening-items/add")
def opening_add(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                start_date: Annotated[str, Form()] = "", type: Annotated[str, Form()] = "",
                number: Annotated[str, Form()] = "", contact: Annotated[str, Form()] = "",
                invoice_date: Annotated[str, Form()] = "", due_date: Annotated[str, Form()] = "",
                total: Annotated[str, Form()] = "", open_amount: Annotated[str, Form()] = ""):
    values = {"type": type, "number": number, "contact": contact, "invoice_date": invoice_date,
              "due_date": due_date, "total": total, "open_amount": open_amount}
    try:
        start = opening_import.resolve_start_date(session, admin, start_date)
        row = opening_import.evaluate(session, admin, [(1, values)], start)[0]
        if row.action == "error":
            raise opening_import.OpeningError(row.reason)
        if row.action == "skip":
            raise opening_import.OpeningError(row.reason.capitalize() + ".")
        opening_import.create_item(session, admin, row, start, user.id)
        session.commit()
    except opening_import.OpeningError as exc:
        session.rollback()
        return _step(request, session, admin, 422, form_error=str(exc),
                     values={**values, "start_date": start_date})
    flash(request, f"Opening item {number.strip()} added.")
    return RedirectResponse("/setup/opening-items", 303)


@router.post("/setup/opening-items/preview")
def opening_preview(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                    start_date: Annotated[str, Form()] = "",
                    file: Annotated[UploadFile | None, File()] = None):
    try:
        start = opening_import.resolve_start_date(session, admin, start_date)
        filename, content = _read_upload(file)
        rows, warnings, _ = opening_import.plan_csv(session, admin, content, start)
    except (opening_import.OpeningError, csvio.CsvError) as exc:
        return _step(request, session, admin, 422, import_error=str(exc),
                     values={"start_date": start_date})
    return render(request, "imports/opening_preview.html", {
        "rows": rows, "warnings": warnings, "filename": filename, "start": start,
        "payload": csvio.decode(content)[0],
        "errors": sum(1 for r in rows if r.action == "error"),
        "creates": sum(1 for r in rows if r.action == "create"),
        "skips": sum(1 for r in rows if r.action == "skip"),
        "total_open": sum(r.open_cents for r in rows if r.action == "create")})


@router.post("/setup/opening-items/confirm")
def opening_confirm(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                    payload: Annotated[str, Form()] = "", filename: Annotated[str, Form()] = "",
                    start_date: Annotated[str, Form()] = ""):
    try:
        start = opening_import.resolve_start_date(session, admin, start_date)
        rows, _, _ = opening_import.plan_csv(session, admin, payload.encode("utf-8"), start)
        created = opening_import.apply_items(session, admin, rows, start, user.id, filename)
        session.commit()
    except (opening_import.OpeningError, csvio.CsvError) as exc:
        session.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/setup/opening-items", 303)
    flash(request, f"Imported {created} opening item{'' if created == 1 else 's'}.")
    return RedirectResponse("/setup/opening-items", 303)


def _remove(kind: str, invoice_id: int, request: Request, session, user, admin):
    if opening_import.opening_invoice(session, admin.id, kind, invoice_id) is None:
        raise HTTPException(404, "opening item not found")
    try:
        opening_import.remove_item(session, admin, kind, invoice_id, user.id)
        session.commit()
    except opening_import.OpeningError as exc:
        session.rollback()
        flash(request, str(exc), "error")
    else:
        flash(request, "Opening item removed (the entry was reversed).")
    return RedirectResponse("/setup/opening-items", 303)


@router.post("/sales/{invoice_id}/opening-remove")
def opening_remove_sales(invoice_id: int, request: Request, session: SessionDep,
                         user: OwnerDep, admin: AdminDep):
    return _remove(opening_import.SALES, invoice_id, request, session, user, admin)


@router.post("/purchases/{invoice_id}/opening-remove")
def opening_remove_purchase(invoice_id: int, request: Request, session: SessionDep,
                            user: OwnerDep, admin: AdminDep):
    return _remove(opening_import.PURCHASE, invoice_id, request, session, user, admin)
