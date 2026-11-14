from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ledgerline import audit
from ledgerline.accounts import invitations
from ledgerline.config import get_settings
from ledgerline.inbox.email_intake import inbox_address
from ledgerline.ledger.chart import create_administration
from ledgerline.models import (
    WRITE_ROLES,
    Administration,
    Membership,
    Role,
    User,
    new_inbox_token,
)
from ledgerline.platform import validation as v
from ledgerline.web import (
    AdminDep,
    ManagerDep,
    SessionDep,
    UserDep,
    flash,
    render,
)

router = APIRouter(prefix="/settings")

LEGAL_FORMS = ["eenmanszaak", "vof", "maatschap", "bv", "stichting", "other"]
FORM_KEYS = ["name", "legal_form", "kvk_number", "vat_number", "iban", "bic", "email",
             "address_line", "postcode", "city", "country", "invoice_prefix",
             "payment_terms_days"]


def _context(session, user, admin, **extra) -> dict:
    tenant_id = admin.tenant_id
    ctx = {
        "admin": admin, "legal_forms": LEGAL_FORMS, "errors": {}, "values": None,
        "is_owner": user.role == Role.OWNER,
        "administrations": session.scalars(
            select(Administration).order_by(Administration.id)).all(),
        "users": [], "failed_invites": set(),
        "inbox_address": inbox_address(get_settings(), admin.inbox_token)
        if user.role in WRITE_ROLES else None,
    }
    if user.role == Role.OWNER:
        rows = session.execute(
            select(User, Membership.role, Membership.status, Membership.id)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.tenant_id == tenant_id).order_by(User.id)).all()
        ctx["users"] = [row[:3] for row in rows]
        failed = invitations.failed_memberships(
            session, [r[3] for r in rows if r[2] == "pending"])
        ctx["failed_invites"] = {r[0].id for r in rows if r[3] in failed}
    ctx.update(extra)
    return ctx


def _admin_values(admin: Administration) -> dict:
    values = {k: getattr(admin, k) for k in FORM_KEYS}
    values["kor"] = admin.kor
    return values


@router.get("")
def settings_page(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    return render(request, "settings/index.html", _context(session, user, admin,
                                                           values=_admin_values(admin)))


def parse_admin(raw: dict[str, str]) -> tuple[dict, dict[str, str]]:
    data: dict = {}
    errors: dict[str, str] = {}
    name = v.clean(raw.get("name"))
    if not name:
        errors["name"] = "Name is required."
    data["name"] = name
    legal_form = raw.get("legal_form") or "eenmanszaak"
    if legal_form not in LEGAL_FORMS:
        errors["legal_form"] = "Unknown legal form."
    data["legal_form"] = legal_form
    for key in ("address_line", "postcode", "city", "bic"):
        data[key] = v.clean(raw.get(key))
    checks = {"email": v.check_email, "country": v.check_country, "vat_number": v.check_vat,
              "kvk_number": v.check_kvk, "iban": v.check_iban, "invoice_prefix": v.check_prefix}
    for key, check in checks.items():
        data[key], err = check(raw.get(key))
        if err:
            errors[key] = err
    try:
        days = int(raw.get("payment_terms_days") or 30)
        if not 0 <= days <= 365:
            raise ValueError
        data["payment_terms_days"] = days
    except ValueError:
        errors["payment_terms_days"] = "Payment terms must be a whole number of days, 0 to 365."
    return data, errors


@router.post("")
def save_settings(request: Request, session: SessionDep, user: ManagerDep, admin: AdminDep,
                  name: Annotated[str, Form()] = "", legal_form: Annotated[str, Form()] = "",
                  kvk_number: Annotated[str, Form()] = "", vat_number: Annotated[str, Form()] = "",
                  iban: Annotated[str, Form()] = "", bic: Annotated[str, Form()] = "",
                  email: Annotated[str, Form()] = "", address_line: Annotated[str, Form()] = "",
                  postcode: Annotated[str, Form()] = "", city: Annotated[str, Form()] = "",
                  country: Annotated[str, Form()] = "NL",
                  invoice_prefix: Annotated[str, Form()] = "",
                  payment_terms_days: Annotated[str, Form()] = "30",
                  kor: Annotated[str, Form()] = ""):
    raw = {k: val for k, val in locals().items() if k in FORM_KEYS}
    data, errors = parse_admin(raw)
    if errors:
        values = {**raw, "kor": kor == "on"}
        return render(request, "settings/index.html",
                      _context(session, user, admin, values=values, errors=errors), 422)
    for key, value in data.items():
        setattr(admin, key, value)
    admin.kor = kor == "on"
    audit.record(session, "administration.update", user_id=user.id, administration_id=admin.id,
                 entity_type="administration", entity_id=admin.id)
    session.commit()
    flash(request, "Settings saved.")
    return RedirectResponse("/settings", 303)


@router.post("/administrations")
def add_administration(request: Request, session: SessionDep, user: ManagerDep,
                       new_name: Annotated[str, Form()] = "",
                       new_legal_form: Annotated[str, Form()] = "eenmanszaak"):
    name = v.clean(new_name)
    if not name or new_legal_form not in LEGAL_FORMS:
        flash(request, "A name and a valid legal form are required.", "error")
        return RedirectResponse("/settings", 303)
    created = create_administration(session, name=name, legal_form=new_legal_form)
    audit.record(session, "administration.create", user_id=user.id,
                 administration_id=created.id, entity_type="administration",
                 entity_id=created.id)
    session.commit()
    request.session["administration_id"] = created.id
    flash(request, f"Administration '{name}' created and selected.")
    return RedirectResponse("/settings", 303)


@router.post("/inbox-address")
def regenerate_inbox_address(request: Request, session: SessionDep, user: ManagerDep,
                             admin: AdminDep):
    admin.inbox_token = new_inbox_token()
    audit.record(session, "administration.inbox_token_regenerate", user_id=user.id,
                 administration_id=admin.id, entity_type="administration", entity_id=admin.id)
    session.commit()
    flash(request, "New inbox address created. The old address no longer works.")
    return RedirectResponse("/settings", 303)


@router.post("/users/{user_id}/deactivate")
def deactivate_user(request: Request, user_id: int, session: SessionDep, user: ManagerDep,
                    admin: AdminDep):
    membership = session.scalar(select(Membership).where(
        Membership.user_id == user_id, Membership.tenant_id == admin.tenant_id))
    if membership is None:
        raise HTTPException(404, "user not found")
    target = session.get(User, user_id)
    if target.id == user.id:
        flash(request, "You cannot deactivate your own account.", "error")
    else:
        session.delete(membership)
        session.flush()
        if session.scalar(select(func.count()).select_from(Membership).where(
                Membership.user_id == target.id)) == 0:
            target.is_active = False
        audit.record(session, "user.deactivate", user_id=user.id, administration_id=admin.id,
                     entity_type="user", entity_id=target.id)
        session.commit()
        flash(request, f"{target.email} deactivated.")
    return RedirectResponse("/settings", 303)
