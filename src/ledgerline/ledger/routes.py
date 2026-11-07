"""Journal, manual entries, period lock and the chart of accounts."""
from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select

from ledgerline import audit
from ledgerline.ledger.service import (
    LedgerError,
    LineSpec,
    account_balances,
    post_entry,
    reverse_entry,
)
from ledgerline.models import Account, AccountType, JournalEntry, JournalLine
from ledgerline.money import MoneyError, to_cents
from ledgerline.reports.params import parse_date, parse_int
from ledgerline.vat.codes import VAT_CODES
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()

PER_PAGE = 25
LEDGER_PAGE = 100
Opt = Annotated[str | None, Query()]
Many = Annotated[list[str], Form()]
TYPE_ORDER = [AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY,
              AccountType.REVENUE, AccountType.EXPENSE]
DEBIT_NATURE = (AccountType.ASSET, AccountType.EXPENSE)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status.HTTP_303_SEE_OTHER)


def _entry_or_404(session, admin, entry_id: int) -> JournalEntry:
    entry = session.get(JournalEntry, entry_id)
    if entry is None or entry.administration_id != admin.id:
        raise HTTPException(404)
    return entry


def _account_or_404(session, admin, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None or account.administration_id != admin.id:
        raise HTTPException(404)
    return account


def _accounts(session, admin, *, active_only: bool = True) -> list[Account]:
    stmt = select(Account).where(Account.administration_id == admin.id).order_by(Account.code)
    if active_only:
        stmt = stmt.where(Account.is_active.is_(True))
    return list(session.scalars(stmt))


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


# ---------------------------------------------------------------- journal list

@router.get("/ledger")
def journal(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
            start: Opt = None, end: Opt = None, account_id: Opt = None, q: Opt = None,
            page: Opt = None):
    s, e = parse_date(start), parse_date(end)
    acc_id = parse_int(account_id)
    page_no = max(parse_int(page) or 1, 1)
    conditions = [JournalEntry.administration_id == admin.id]
    if s:
        conditions.append(JournalEntry.entry_date >= s)
    if e:
        conditions.append(JournalEntry.entry_date <= e)
    if acc_id is not None:
        _account_or_404(session, admin, acc_id)
        conditions.append(JournalEntry.id.in_(
            select(JournalLine.entry_id).where(JournalLine.account_id == acc_id)))
    if q and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        conditions.append(or_(JournalEntry.description.ilike(like, escape="\\"),
                              JournalEntry.number.ilike(like, escape="\\")))
    total = session.scalar(select(func.count()).select_from(JournalEntry).where(*conditions))
    entries = session.scalars(
        select(JournalEntry).where(*conditions)
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
        .limit(PER_PAGE).offset((page_no - 1) * PER_PAGE)
    ).all()
    params = {"start": start, "end": end, "account_id": account_id, "q": q}
    return render(request, "ledger/index.html", {
        "entries": entries, "total": total, "page": page_no,
        "pages": max((total + PER_PAGE - 1) // PER_PAGE, 1),
        "accounts": _accounts(session, admin, active_only=False),
        "f": params, "admin": admin,
        "qs": urlencode({k: v for k, v in params.items() if v}),
    })


# ------------------------------------------------------------ manual entries

def _blank_line() -> dict:
    return {"account_id": "", "debit": "", "credit": "", "description": "",
            "vat_code": "", "vat_role": "base"}


def _line_rows(account_id, debit, credit, description, vat_code, vat_role) -> list[dict]:
    count = len(account_id)
    rows = []
    for i in range(count):
        rows.append({
            "account_id": account_id[i],
            "debit": debit[i] if i < len(debit) else "",
            "credit": credit[i] if i < len(credit) else "",
            "description": description[i] if i < len(description) else "",
            "vat_code": vat_code[i] if i < len(vat_code) else "",
            "vat_role": vat_role[i] if i < len(vat_role) else "base",
        })
    return rows


def _amount(text: str) -> int:
    return to_cents(text) if text and text.strip() else 0


def _specs(rows: list[dict]) -> list[LineSpec]:
    specs = []
    for n, row in enumerate(rows, start=1):
        debit, credit = _amount(row["debit"]), _amount(row["credit"])
        account = (row["account_id"] or "").strip()
        if not account and not debit and not credit:
            continue
        if not account:
            raise LedgerError(f"line {n}: choose an account")
        try:
            account_id = int(account)
        except ValueError:
            raise LedgerError(f"line {n}: choose an account") from None
        code = (row["vat_code"] or "").strip() or None
        if code and code not in VAT_CODES:
            raise LedgerError(f"line {n}: unknown VAT code {code}")
        role = row["vat_role"] if code else None
        if code and role not in ("base", "vat"):
            raise LedgerError(f"line {n}: choose a VAT role")
        specs.append(LineSpec(account_id, debit, credit, (row["description"] or "").strip() or None,
                              None, code, role))
    return specs


def _form_context(session, admin, form: dict, errors: list[str] | None = None) -> dict:
    return {"accounts": _accounts(session, admin), "vat_codes": list(VAT_CODES.values()),
            "form": form, "errors": errors or [], "blank": _blank_line()}


@router.get("/ledger/new")
def new_entry_form(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    form = {"date": date.today().isoformat(), "description": "",
            "lines": [_blank_line(), _blank_line()]}
    return render(request, "ledger/new.html", _form_context(session, admin, form))


@router.get("/ledger/new/line", response_class=HTMLResponse)
def new_entry_line(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    ctx = _form_context(session, admin, {})
    ctx["line"] = _blank_line()
    return render(request, "ledger/_line.html", ctx)


@router.get("/ledger/new/remove", response_class=HTMLResponse)
def new_entry_remove(user: UserDep):
    return HTMLResponse("")


@router.get("/ledger/new/balance", response_class=HTMLResponse)
def new_entry_balance(request: Request, user: UserDep):
    qp = request.query_params
    debit = credit = 0
    unreadable = False
    for text in qp.getlist("debit"):
        try:
            debit += _amount(text)
        except MoneyError:
            unreadable = True
    for text in qp.getlist("credit"):
        try:
            credit += _amount(text)
        except MoneyError:
            unreadable = True
    return render(request, "ledger/_balance.html", {
        "debit": debit, "credit": credit, "diff": debit - credit, "unreadable": unreadable,
        "balanced": debit == credit and debit > 0 and not unreadable,
    })


@router.post("/ledger/new")
def new_entry(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
              entry_date: Annotated[str, Form(alias="date")],
              description: Annotated[str, Form()],
              account_id: Many = [], debit: Many = [], credit: Many = [],  # noqa: B006
              line_description: Many = [], vat_code: Many = [], vat_role: Many = []):  # noqa: B006
    rows = _line_rows(account_id, debit, credit, line_description, vat_code, vat_role)
    form = {"date": entry_date, "description": description, "lines": rows or [_blank_line()] * 2}
    try:
        day = parse_date(entry_date)
        if day is None:
            raise LedgerError("enter a date")
        if not description.strip():
            raise LedgerError("enter a description")
        entry = post_entry(session, admin.id, day, description.strip(), _specs(rows),
                           source_type="manual", user_id=user.id)
        audit.record(session, "journal.post", user_id=user.id, administration_id=admin.id,
                     entity_type="journal_entry", entity_id=entry.id,
                     details={"number": entry.number, "source": "manual"})
        session.commit()
    except (LedgerError, MoneyError, HTTPException) as exc:
        session.rollback()
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return render(request, "ledger/new.html", _form_context(session, admin, form, [message]),
                      status.HTTP_422_UNPROCESSABLE_ENTITY)
    flash(request, f"Entry {entry.number} posted.")
    return _redirect(f"/ledger/{entry.id}")


# ----------------------------------------------------------------- period lock

@router.get("/ledger/lock")
def lock_form(request: Request, user: UserDep, admin: AdminDep):
    return render(request, "ledger/lock.html", {"admin": admin, "errors": []})


@router.post("/ledger/lock")
def lock_set(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
             locked_until: Annotated[str, Form()] = "", confirm: Annotated[str, Form()] = ""):
    try:
        new = parse_date(locked_until)
    except HTTPException as exc:
        return render(request, "ledger/lock.html", {"admin": admin, "errors": [exc.detail]}, 422)
    current = admin.locked_until
    if new == current:
        flash(request, "The period lock is unchanged.", "info")
        return _redirect("/ledger/lock")
    moving_back = current is not None and (new is None or new < current)
    if moving_back and confirm != "1":
        return render(request, "ledger/lock.html", {
            "admin": admin, "need_confirm": True, "proposed": new,
            "errors": ["Moving the lock backwards reopens closed periods. Confirm with the box."],
        }, status.HTTP_422_UNPROCESSABLE_ENTITY)
    admin.locked_until = new
    audit.record(session, "period.lock", user_id=user.id, administration_id=admin.id,
                 entity_type="administration", entity_id=admin.id,
                 details={"from": current.isoformat() if current else None,
                          "to": new.isoformat() if new else None, "backwards": moving_back})
    session.commit()
    flash(request, f"Entries up to {new.isoformat()} are now locked." if new
          else "The period lock was removed.")
    return _redirect("/ledger/lock")


# ---------------------------------------------------------------- entry detail

@router.get("/ledger/{entry_id}")
def entry_detail(entry_id: int, request: Request, session: SessionDep, user: UserDep,
                 admin: AdminDep):
    entry = _entry_or_404(session, admin, entry_id)
    accounts = {a.id: a for a in _accounts(session, admin, active_only=False)}
    reversal = session.scalar(select(JournalEntry).where(
        JournalEntry.reversal_of_id == entry.id, JournalEntry.administration_id == admin.id))
    original = session.get(JournalEntry, entry.reversal_of_id) if entry.reversal_of_id else None
    return render(request, "ledger/detail.html", {
        "entry": entry, "accounts": accounts, "reversal": reversal, "original": original,
        "debit": sum(line.debit_cents for line in entry.lines),
        "credit": sum(line.credit_cents for line in entry.lines),
        "today": date.today(), "locked_until": admin.locked_until,
    })


@router.post("/ledger/{entry_id}/reverse")
def entry_reverse(entry_id: int, request: Request, session: SessionDep, user: OwnerDep,
                  admin: AdminDep, reason: Annotated[str, Form()],
                  reversal_date: Annotated[str, Form(alias="date")] = ""):
    entry = _entry_or_404(session, admin, entry_id)
    try:
        if not reason.strip():
            raise LedgerError("give a reason for the reversal")
        day = parse_date(reversal_date, date.today())
        reversal = reverse_entry(session, entry, day, reason.strip(), user.id)
        audit.record(session, "journal.reverse", user_id=user.id, administration_id=admin.id,
                     entity_type="journal_entry", entity_id=entry.id,
                     details={"reversal": reversal.number, "reason": reason.strip()[:200]})
        session.commit()
    except (LedgerError, HTTPException) as exc:
        session.rollback()
        flash(request, exc.detail if isinstance(exc, HTTPException) else str(exc), "error")
        return _redirect(f"/ledger/{entry_id}")
    flash(request, f"Entry {entry.number} reversed by {reversal.number}.")
    return _redirect(f"/ledger/{reversal.id}")


# ------------------------------------------------------------ chart of accounts

@router.get("/accounts")
def accounts_index(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    balances = account_balances(session, admin.id)
    groups = [(kind, [b for b in balances if b.account.type == kind]) for kind in TYPE_ORDER]
    return render(request, "accounts/index.html", {"groups": groups})


def _account_form_ctx(account=None, values=None, errors=None, locked_type=False) -> dict:
    base = {"code": "", "name": "", "type": AccountType.EXPENSE, "default_vat_code": "",
            "rgs_code": ""}
    if account is not None:
        base = {"code": account.code, "name": account.name, "type": account.type,
                "default_vat_code": account.default_vat_code or "",
                "rgs_code": account.rgs_code or ""}
    return {"account": account, "v": values or base, "errors": errors or [],
            "types": TYPE_ORDER, "vat_codes": list(VAT_CODES.values()), "locked_type": locked_type}


def _has_lines(session, account_id: int) -> bool:
    return bool(session.scalar(
        select(func.count()).select_from(JournalLine).where(JournalLine.account_id == account_id)))


def _validate_account(session, admin, values: dict, current: Account | None) -> list[str]:
    errors = []
    code, name = values["code"].strip(), values["name"].strip()
    if not code or len(code) > 10:
        errors.append("The code is required and at most 10 characters.")
    if not name or len(name) > 120:
        errors.append("The name is required and at most 120 characters.")
    if values["type"] not in set(AccountType):
        errors.append("Choose an account type.")
    if values["default_vat_code"] and values["default_vat_code"] not in VAT_CODES:
        errors.append("Unknown default VAT code.")
    if len(values["rgs_code"]) > 40:
        errors.append("The RGS code is at most 40 characters.")
    clash = session.scalar(select(Account.id).where(
        Account.administration_id == admin.id, Account.code == code,
        Account.id != (current.id if current else 0)))
    if clash:
        errors.append(f"Code {code} is already used by another account.")
    return errors


def _values(code, name, type_, default_vat_code, rgs_code) -> dict:
    return {"code": code.strip(), "name": name.strip(), "type": type_,
            "default_vat_code": default_vat_code.strip(), "rgs_code": rgs_code.strip()}


@router.get("/accounts/new")
def account_new_form(request: Request, user: UserDep, admin: AdminDep):
    return render(request, "accounts/form.html", _account_form_ctx())


@router.post("/accounts/new")
def account_create(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                   code: Annotated[str, Form()], name: Annotated[str, Form()],
                   type: Annotated[str, Form()], default_vat_code: Annotated[str, Form()] = "",
                   rgs_code: Annotated[str, Form()] = ""):
    values = _values(code, name, type, default_vat_code, rgs_code)
    errors = _validate_account(session, admin, values, None)
    if errors:
        ctx = _account_form_ctx(values=values, errors=errors)
        return render(request, "accounts/form.html", ctx, status.HTTP_422_UNPROCESSABLE_ENTITY)
    account = Account(administration_id=admin.id, code=values["code"], name=values["name"],
                      type=values["type"], default_vat_code=values["default_vat_code"] or None,
                      rgs_code=values["rgs_code"] or None)
    session.add(account)
    session.flush()
    audit.record(session, "account.create", user_id=user.id, administration_id=admin.id,
                 entity_type="account", entity_id=account.id, details={"code": account.code})
    session.commit()
    flash(request, f"Account {account.code} created.")
    return _redirect("/accounts")


@router.get("/accounts/{account_id}")
def account_ledger(account_id: int, request: Request, session: SessionDep, user: UserDep,
                   admin: AdminDep, start: Opt = None, end: Opt = None, page: Opt = None):
    account = _account_or_404(session, admin, account_id)
    s, e = parse_date(start), parse_date(end)
    sign = 1 if account.type in DEBIT_NATURE else -1
    opening = 0
    if s:
        opening = sign * int(session.scalar(
            select(func.coalesce(func.sum(JournalLine.debit_cents - JournalLine.credit_cents), 0))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == account.id, JournalEntry.entry_date < s)))
    stmt = (select(JournalLine, JournalEntry)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == account.id)
            .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id))
    if s:
        stmt = stmt.where(JournalEntry.entry_date >= s)
    if e:
        stmt = stmt.where(JournalEntry.entry_date <= e)
    running = opening
    rows = []
    for line, entry in session.execute(stmt):
        running += sign * (line.debit_cents - line.credit_cents)
        rows.append((line, entry, running))
    page_no = max(parse_int(page) or 1, 1)
    pages = max((len(rows) + LEDGER_PAGE - 1) // LEDGER_PAGE, 1)
    shown = rows[(page_no - 1) * LEDGER_PAGE: page_no * LEDGER_PAGE]
    return render(request, "accounts/ledger.html", {
        "account": account, "rows": shown, "opening": opening, "closing": running,
        "page": page_no, "pages": pages, "f": {"start": start, "end": end},
    })


@router.get("/accounts/{account_id}/edit")
def account_edit_form(account_id: int, request: Request, session: SessionDep, user: UserDep,
                      admin: AdminDep):
    account = _account_or_404(session, admin, account_id)
    locked = bool(account.system_role) or _has_lines(session, account.id)
    return render(request, "accounts/form.html", _account_form_ctx(account, locked_type=locked))


@router.post("/accounts/{account_id}/edit")
def account_update(account_id: int, request: Request, session: SessionDep, user: OwnerDep,
                   admin: AdminDep, code: Annotated[str, Form()], name: Annotated[str, Form()],
                   type: Annotated[str, Form()], default_vat_code: Annotated[str, Form()] = "",
                   rgs_code: Annotated[str, Form()] = ""):
    account = _account_or_404(session, admin, account_id)
    values = _values(code, name, type, default_vat_code, rgs_code)
    errors = _validate_account(session, admin, values, account)
    locked = bool(account.system_role) or _has_lines(session, account.id)
    if values["type"] != account.type and locked:
        reason = "is a system account" if account.system_role else "already has journal lines"
        errors.append(f"The type cannot change: this account {reason}.")
    if errors:
        return render(request, "accounts/form.html",
                      _account_form_ctx(account, values, errors, locked),
                      status.HTTP_422_UNPROCESSABLE_ENTITY)
    changes = {k: [getattr(account, k), v] for k, v in (
        ("code", values["code"]), ("name", values["name"]), ("type", values["type"]),
        ("default_vat_code", values["default_vat_code"] or None),
        ("rgs_code", values["rgs_code"] or None)) if getattr(account, k) != v}
    for key, (_, new) in changes.items():
        setattr(account, key, new)
    audit.record(session, "account.update", user_id=user.id, administration_id=admin.id,
                 entity_type="account", entity_id=account.id, details={"changes": changes})
    session.commit()
    flash(request, f"Account {account.code} updated.")
    return _redirect("/accounts")


@router.post("/accounts/{account_id}/deactivate")
def account_deactivate(account_id: int, request: Request, session: SessionDep, user: OwnerDep,
                       admin: AdminDep):
    account = _account_or_404(session, admin, account_id)
    if account.system_role:
        flash(request, f"{account.code} is used by the application and cannot be deactivated.",
              "error")
        return _redirect("/accounts")
    account.is_active = False
    audit.record(session, "account.deactivate", user_id=user.id, administration_id=admin.id,
                 entity_type="account", entity_id=account.id, details={"code": account.code})
    session.commit()
    flash(request, f"Account {account.code} deactivated. Its history is kept.")
    return _redirect("/accounts")


@router.post("/accounts/{account_id}/activate")
def account_activate(account_id: int, request: Request, session: SessionDep, user: OwnerDep,
                     admin: AdminDep):
    account = _account_or_404(session, admin, account_id)
    account.is_active = True
    audit.record(session, "account.activate", user_id=user.id, administration_id=admin.id,
                 entity_type="account", entity_id=account.id, details={"code": account.code})
    session.commit()
    flash(request, f"Account {account.code} is active again.")
    return _redirect("/accounts")
