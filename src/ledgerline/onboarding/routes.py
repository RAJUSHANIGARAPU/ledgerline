from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ledgerline import audit
from ledgerline.banking import service as banking
from ledgerline.models import BankAccount, Role, User
from ledgerline.money import MoneyError, to_cents
from ledgerline.onboarding import service
from ledgerline.onboarding.markdown import render_markdown
from ledgerline.platform import validation as v
from ledgerline.platform.settings import FORM_KEYS, parse_admin
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()

GUIDE_PATH = Path(__file__).resolve().parents[3] / "docs" / "USER_GUIDE.md"
WIZARD_LEGAL_FORMS = [("eenmanszaak", "Eenmanszaak (sole proprietorship)"),
                      ("vof", "VOF (general partnership)"), ("bv", "BV (private limited company)")]
BUSINESS_FORM_KEYS = ("name", "legal_form", "kvk_number", "vat_number", "address_line",
                      "postcode", "city", "iban", "email", "invoice_prefix", "payment_terms_days")


def owner_page(user: UserDep) -> User:
    if user.role != Role.OWNER:
        raise HTTPException(403, "the setup wizard is for the owner")
    return user


OwnerPage = Annotated[User, Depends(owner_page)]


def _to_step(key: str) -> RedirectResponse:
    return RedirectResponse(f"/setup/{key}", 303)


def _advance(session, admin, key: str) -> RedirectResponse:
    session.refresh(admin)
    return _to_step(service.next_step(service.steps(session, admin), key).key)


def _context(session, admin, key: str, **extra) -> dict:
    all_steps = service.steps(session, admin)
    current = next(s for s in all_steps if s.key == key)
    index = service.STEP_KEYS.index(key)
    ctx = {
        "steps": all_steps, "step": current, "total": len(all_steps),
        "prev_key": service.STEP_KEYS[index - 1] if index else None,
        "next_key": service.STEP_KEYS[index + 1] if index + 1 < len(all_steps) else None,
        "complete": all(s.done for s in all_steps if s.required),
        "admin": admin, "errors": {}, "values": None, "legal_forms": WIZARD_LEGAL_FORMS,
    }
    if key == "business":
        ctx["values"] = {k: getattr(admin, k) for k in BUSINESS_FORM_KEYS}
    elif key in ("bank", "opening"):
        ctx["bank_accounts"] = session.scalars(select(BankAccount).where(
            BankAccount.administration_id == admin.id).order_by(BankAccount.id)).all()
        ctx["opening"] = service.opening_entry(session, admin.id)
        ctx["today"] = date.today()
    elif key == "first-invoice":
        ctx["customers"] = service.customer_count(session, admin.id)
    ctx.update(extra)
    return ctx


def _page(request, session, admin, key: str, status_code: int = 200, **extra):
    return render(request, "onboarding/step.html",
                  _context(session, admin, key, **extra), status_code)


@router.get("/setup")
def setup_index(request: Request, session: SessionDep, user: OwnerPage, admin: AdminDep):
    all_steps = service.steps(session, admin)
    done = sum(1 for s in all_steps if s.done)
    countable = sum(1 for s in all_steps if s.done is not None)
    return render(request, "onboarding/index.html", {
        "steps": all_steps, "done": done, "countable": countable,
        "complete": all(s.done for s in all_steps if s.required),
        "resume": service.next_step(all_steps), "admin": admin})


@router.get("/setup/{key}")
def setup_step(key: str, request: Request, session: SessionDep, user: OwnerPage, admin: AdminDep):
    if key not in service.STEP_KEYS:
        raise HTTPException(404)
    return _page(request, session, admin, key)


@router.post("/setup/business")
def save_business(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                  name: Annotated[str, Form()] = "", legal_form: Annotated[str, Form()] = "",
                  kvk_number: Annotated[str, Form()] = "", vat_number: Annotated[str, Form()] = "",
                  address_line: Annotated[str, Form()] = "", postcode: Annotated[str, Form()] = "",
                  city: Annotated[str, Form()] = "", iban: Annotated[str, Form()] = "",
                  email: Annotated[str, Form()] = "", invoice_prefix: Annotated[str, Form()] = "",
                  payment_terms_days: Annotated[str, Form()] = "30"):
    submitted = {k: val for k, val in locals().items() if k in BUSINESS_FORM_KEYS}
    raw = {k: getattr(admin, k) for k in FORM_KEYS}
    raw["payment_terms_days"] = str(raw["payment_terms_days"])
    raw.update(submitted)
    if legal_form not in dict(WIZARD_LEGAL_FORMS):
        raw["legal_form"] = admin.legal_form
    data, errors = parse_admin(raw)
    if errors:
        return _page(request, session, admin, "business", 422, errors=errors, values=submitted)
    for key, value in data.items():
        setattr(admin, key, value)
    audit.record(session, "administration.update", user_id=user.id, administration_id=admin.id,
                 entity_type="administration", entity_id=admin.id, details={"via": "setup"})
    session.commit()
    flash(request, "Business details saved.")
    return _advance(session, admin, "business")


@router.post("/setup/vat")
def save_vat(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
             scheme: Annotated[str, Form()] = ""):
    if scheme not in ("regular", "kor"):
        return _page(request, session, admin, "vat", 422,
                     errors={"scheme": "Choose regular VAT or the KOR."})
    admin.kor = scheme == "kor"
    audit.record(session, "administration.update", user_id=user.id, administration_id=admin.id,
                 entity_type="administration", entity_id=admin.id,
                 details={"via": "setup", "kor": admin.kor})
    session.commit()
    flash(request, "VAT situation saved.")
    return _advance(session, admin, "vat")


@router.post("/setup/bank")
def save_bank(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
              name: Annotated[str, Form()] = "", iban: Annotated[str, Form()] = ""):
    errors: dict[str, str] = {}
    clean_iban, err = v.check_iban(iban)
    if err or not clean_iban:
        errors["iban"] = err or "IBAN is required."
    if not v.clean(name):
        errors["name"] = "Name is required."
    if not errors:
        try:
            banking.create_bank_account(session, admin, clean_iban or "", name, None, user.id)
            session.commit()
        except banking.BankingError as exc:
            session.rollback()
            errors["iban"] = str(exc)
    if errors:
        return _page(request, session, admin, "bank", 422, errors=errors,
                     values={"name": name, "iban": iban})
    flash(request, "Bank account added.")
    return _advance(session, admin, "bank")


@router.post("/setup/opening")
async def save_opening(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep):
    form = await request.form()
    values = {k: str(val) for k, val in form.items() if k != "csrf_token"}
    errors: dict[str, str] = {}
    try:
        entry_date = date.fromisoformat(values.get("entry_date", ""))
    except ValueError:
        entry_date = None
        errors["entry_date"] = "Enter the start date."
    balances: dict[int, int] = {}
    own = session.scalars(select(BankAccount.id).where(
        BankAccount.administration_id == admin.id)).all()
    for bank_id in own:
        raw = values.get(f"balance_{bank_id}", "").strip()
        if not raw:
            continue
        try:
            balances[bank_id] = to_cents(raw)
        except MoneyError:
            errors[f"balance_{bank_id}"] = "Enter an amount such as 1.234,56."
    if not errors and entry_date is not None:
        try:
            service.post_opening_balances(session, admin, entry_date, balances, user.id)
            session.commit()
        except service.OnboardingError as exc:
            session.rollback()
            errors["form"] = str(exc)
    if errors:
        return _page(request, session, admin, "opening", 422, errors=errors, values=values)
    flash(request, "Opening balances posted.")
    return _advance(session, admin, "opening")


@router.get("/help")
def help_page(request: Request, user: UserDep):
    try:
        text = GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        return render(request, "onboarding/help.html",
                      {"body": None, "toc": []}, 404)
    body, toc = render_markdown(text)
    return render(request, "onboarding/help.html", {"body": body, "toc": toc})
