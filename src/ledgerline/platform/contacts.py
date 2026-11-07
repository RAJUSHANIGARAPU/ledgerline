from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select

from ledgerline import audit
from ledgerline.models import (
    Contact,
    ContactKind,
    JournalLine,
    PurchaseInvoice,
    SalesInvoice,
)
from ledgerline.platform import validation as v
from ledgerline.platform.gdpr import contact_retention
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter(prefix="/contacts")

KINDS = [k.value for k in ContactKind]
FIELDS = ["name", "kind", "email", "address_line", "postcode", "city", "country",
          "vat_number", "kvk_number", "iban"]


def get_contact(session, admin, contact_id: int) -> Contact:
    contact = session.get(Contact, contact_id)
    if contact is None or contact.administration_id != admin.id:
        raise HTTPException(404, "contact not found")
    return contact


def parse_form(raw: dict[str, str]) -> tuple[dict, dict[str, str]]:
    data: dict = {}
    errors: dict[str, str] = {}
    name = v.clean(raw.get("name"))
    if not name:
        errors["name"] = "Name is required."
    data["name"] = name
    kind = raw.get("kind") or ContactKind.CUSTOMER
    if kind not in KINDS:
        errors["kind"] = "Unknown contact kind."
    data["kind"] = kind
    for key in ("address_line", "postcode", "city"):
        data[key] = v.clean(raw.get(key))
    checks = {
        "email": v.check_email, "country": v.check_country, "vat_number": v.check_vat,
        "kvk_number": v.check_kvk, "iban": v.check_iban,
    }
    for key, check in checks.items():
        data[key], err = check(raw.get(key))
        if err:
            errors[key] = err
    return data, errors


@router.get("")
def list_contacts(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
                  q: str = "", kind: str = ""):
    stmt = select(Contact).where(Contact.administration_id == admin.id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Contact.name.ilike(like), Contact.email.ilike(like),
                              Contact.city.ilike(like), Contact.vat_number.ilike(like)))
    if kind in KINDS:
        stmt = stmt.where(Contact.kind == kind)
    contacts = session.scalars(stmt.order_by(func.lower(Contact.name), Contact.id)).all()
    return render(request, "contacts/list.html",
                  {"contacts": contacts, "q": q, "kind": kind, "kinds": KINDS})


@router.get("/new")
def new_form(request: Request, user: UserDep, admin: AdminDep):
    return render(request, "contacts/form.html",
                  {"contact": None, "values": {"country": "NL", "kind": "customer"},
                   "errors": {}, "kinds": KINDS})


@router.post("/new")
def create(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
           name: Annotated[str, Form()] = "", kind: Annotated[str, Form()] = "customer",
           email: Annotated[str, Form()] = "", address_line: Annotated[str, Form()] = "",
           postcode: Annotated[str, Form()] = "", city: Annotated[str, Form()] = "",
           country: Annotated[str, Form()] = "NL", vat_number: Annotated[str, Form()] = "",
           kvk_number: Annotated[str, Form()] = "", iban: Annotated[str, Form()] = ""):
    raw = {k: val for k, val in locals().items() if k in FIELDS}
    data, errors = parse_form(raw)
    if errors:
        return render(request, "contacts/form.html",
                      {"contact": None, "values": raw, "errors": errors, "kinds": KINDS}, 422)
    contact = Contact(administration_id=admin.id, **data)
    session.add(contact)
    session.flush()
    audit.record(session, "contact.create", user_id=user.id, administration_id=admin.id,
                 entity_type="contact", entity_id=contact.id)
    session.commit()
    flash(request, "Contact created.")
    return RedirectResponse(f"/contacts/{contact.id}", 303)


def _usage(session, admin, contact_id: int) -> tuple[list, list, int]:
    sales = session.scalars(select(SalesInvoice).where(
        SalesInvoice.administration_id == admin.id, SalesInvoice.contact_id == contact_id)
        .order_by(SalesInvoice.issue_date.desc(), SalesInvoice.id.desc())).all()
    purchases = session.scalars(select(PurchaseInvoice).where(
        PurchaseInvoice.administration_id == admin.id, PurchaseInvoice.contact_id == contact_id)
        .order_by(PurchaseInvoice.invoice_date.desc(), PurchaseInvoice.id.desc())).all()
    lines = session.scalar(select(func.count(JournalLine.id)).where(
        JournalLine.contact_id == contact_id)) or 0
    return sales, purchases, lines


@router.get("/{contact_id}")
def detail(request: Request, contact_id: int, session: SessionDep, user: UserDep, admin: AdminDep):
    contact = get_contact(session, admin, contact_id)
    sales, purchases, lines = _usage(session, admin, contact.id)
    return render(request, "contacts/detail.html", {
        "contact": contact, "sales": sales, "purchases": purchases, "journal_lines": lines,
        "retention": contact_retention(session, admin, contact.id),
        "can_delete": not (sales or purchases or lines),
    })


@router.get("/{contact_id}/edit")
def edit_form(request: Request, contact_id: int, session: SessionDep, user: UserDep,
              admin: AdminDep):
    contact = get_contact(session, admin, contact_id)
    values = {k: getattr(contact, k) for k in FIELDS}
    return render(request, "contacts/form.html",
                  {"contact": contact, "values": values, "errors": {}, "kinds": KINDS})


@router.post("/{contact_id}/edit")
def update(request: Request, contact_id: int, session: SessionDep, user: OwnerDep,
           admin: AdminDep,
           name: Annotated[str, Form()] = "", kind: Annotated[str, Form()] = "customer",
           email: Annotated[str, Form()] = "", address_line: Annotated[str, Form()] = "",
           postcode: Annotated[str, Form()] = "", city: Annotated[str, Form()] = "",
           country: Annotated[str, Form()] = "NL", vat_number: Annotated[str, Form()] = "",
           kvk_number: Annotated[str, Form()] = "", iban: Annotated[str, Form()] = ""):
    contact = get_contact(session, admin, contact_id)
    if contact.anonymised_at is not None:
        flash(request, "An anonymised contact cannot be edited.", "error")
        return RedirectResponse(f"/contacts/{contact.id}", 303)
    raw = {k: val for k, val in locals().items() if k in FIELDS}
    data, errors = parse_form(raw)
    if errors:
        return render(request, "contacts/form.html",
                      {"contact": contact, "values": raw, "errors": errors, "kinds": KINDS}, 422)
    for key, value in data.items():
        setattr(contact, key, value)
    audit.record(session, "contact.update", user_id=user.id, administration_id=admin.id,
                 entity_type="contact", entity_id=contact.id)
    session.commit()
    flash(request, "Contact saved.")
    return RedirectResponse(f"/contacts/{contact.id}", 303)


@router.post("/{contact_id}/delete")
def delete(request: Request, contact_id: int, session: SessionDep, user: OwnerDep,
           admin: AdminDep):
    contact = get_contact(session, admin, contact_id)
    sales, purchases, lines = _usage(session, admin, contact.id)
    if sales or purchases or lines:
        flash(request, "This contact has bookkeeping records and cannot be deleted. "
                       "Anonymise it instead.", "error")
        return RedirectResponse(f"/contacts/{contact.id}", 303)
    audit.record(session, "contact.delete", user_id=user.id, administration_id=admin.id,
                 entity_type="contact", entity_id=contact.id)
    session.delete(contact)
    session.commit()
    flash(request, "Contact deleted.")
    return RedirectResponse("/contacts", 303)
