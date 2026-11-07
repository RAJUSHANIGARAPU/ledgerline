from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ledgerline import audit
from ledgerline.auth import hash_password, hasher
from ledgerline.ledger.chart import create_administration
from ledgerline.models import Administration, Role, User
from ledgerline.platform import validation as v
from ledgerline.web import (
    AdminDep,
    OwnerDep,
    SessionDep,
    UserDep,
    flash,
    render,
    verify_csrf,
)

router = APIRouter(prefix="/settings")

LEGAL_FORMS = ["eenmanszaak", "vof", "maatschap", "bv", "stichting", "other"]
FORM_KEYS = ["name", "legal_form", "kvk_number", "vat_number", "iban", "bic", "email",
             "address_line", "postcode", "city", "country", "invoice_prefix",
             "payment_terms_days"]


def _context(session, user, admin, **extra) -> dict:
    ctx = {
        "admin": admin, "legal_forms": LEGAL_FORMS, "errors": {}, "values": None,
        "is_owner": user.role == Role.OWNER,
        "administrations": session.scalars(
            select(Administration).order_by(Administration.id)).all(),
        "users": [],
    }
    if user.role == Role.OWNER:
        ctx["users"] = session.scalars(select(User).order_by(User.id)).all()
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
def save_settings(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
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
def add_administration(request: Request, session: SessionDep, user: OwnerDep,
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


@router.post("/users")
def add_user(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
             email: Annotated[str, Form()] = "", role: Annotated[str, Form()] = "accountant",
             password: Annotated[str, Form()] = "", confirm: Annotated[str, Form()] = ""):
    address, err = v.check_email(email)
    err = err or (None if address else "Email is required.")
    err = err or v.check_password(password, confirm)
    if role not in (Role.OWNER, Role.ACCOUNTANT):
        err = "Unknown role."
    if not err and session.scalar(select(User.id).where(
            func.lower(User.email) == (address or "").lower())):
        err = "A user with this email already exists."
    if err:
        flash(request, err, "error")
        return RedirectResponse("/settings", 303)
    created = User(email=(address or "").lower(), password_hash=hash_password(password),
                   role=role, is_active=True)
    session.add(created)
    session.flush()
    audit.record(session, "user.create", user_id=user.id, administration_id=admin.id,
                 entity_type="user", entity_id=created.id, details={"role": role})
    session.commit()
    flash(request, f"User {created.email} created as {role}.")
    return RedirectResponse("/settings", 303)


@router.post("/users/{user_id}/deactivate")
def deactivate_user(request: Request, user_id: int, session: SessionDep, user: OwnerDep,
                    admin: AdminDep):
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.id == user.id:
        flash(request, "You cannot deactivate your own account.", "error")
    else:
        target.is_active = False
        audit.record(session, "user.deactivate", user_id=user.id, administration_id=admin.id,
                     entity_type="user", entity_id=target.id)
        session.commit()
        flash(request, f"{target.email} deactivated.")
    return RedirectResponse("/settings", 303)


@router.post("/password", dependencies=[Depends(verify_csrf)])
def change_password(request: Request, session: SessionDep, user: UserDep,
                    current: Annotated[str, Form()] = "", password: Annotated[str, Form()] = "",
                    confirm: Annotated[str, Form()] = ""):
    try:
        hasher.verify(user.password_hash, current)
        ok = True
    except Exception:
        ok = False
    err = None if ok else "Current password is wrong."
    err = err or v.check_password(password, confirm)
    if err:
        flash(request, err, "error")
        return RedirectResponse("/settings", 303)
    user.password_hash = hash_password(password)
    audit.record(session, "user.password_change", user_id=user.id, entity_type="user",
                 entity_id=user.id)
    session.commit()
    flash(request, "Password changed.")
    return RedirectResponse("/settings", 303)
