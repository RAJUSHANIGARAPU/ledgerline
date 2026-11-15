"""VAT return overview, drill-down, settlement entry and ICP overview."""
from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ledgerline import audit
from ledgerline.ledger.service import LedgerError, LineSpec, post_entry, system_account
from ledgerline.models import (
    WRITE_ROLES,
    JournalEntry,
    JournalLine,
    SystemRole,
    User,
    VatApproval,
)
from ledgerline.reports.export import Cents, csv_response
from ledgerline.reports.params import parse_int
from ledgerline.vat.approval import approval_state, digest, rubrieken, snapshot
from ledgerline.vat.icp import icp_overview
from ledgerline.vat.sources import SOURCES
from ledgerline.vat.vat_return import (
    RUBRIEKEN,
    VERIFY,
    compute_vat_return,
    filing_deadline,
    lines_for_bucket,
    period_bounds,
    whole_euros,
)
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter()

Opt = Annotated[str | None, Query()]
KINDS = ("month", "quarter", "year")
SETTLEMENT_PREFIX = "vat_settlement:"


def _period(kind: str | None, year: str | None, number: str | None):
    today = date.today()
    kind = kind or "quarter"
    if kind not in KINDS:
        raise HTTPException(422, "invalid period type")
    y = parse_int(year) or today.year
    default = (today.month - 1) // 3 + 1 if kind == "quarter" else today.month
    n = parse_int(number) or default
    try:
        start, end, label = period_bounds(kind, y, n)
    except ValueError:
        raise HTTPException(422, "invalid period") from None
    qs = urlencode({"kind": kind, "year": y, "number": n})
    return {"kind": kind, "year": y, "number": n, "start": start, "end": end,
            "label": label, "qs": qs}


def _years() -> range:
    return range(date.today().year - 5, date.today().year + 2)


def _settlement_key(label: str) -> str:
    return f"{SETTLEMENT_PREFIX}{label}"[:40]


def _existing_settlement(session, admin, label: str) -> JournalEntry | None:
    reversed_ids = select(JournalEntry.reversal_of_id).where(
        JournalEntry.reversal_of_id.is_not(None))
    return session.scalar(select(JournalEntry).where(
        JournalEntry.administration_id == admin.id,
        JournalEntry.source_type == _settlement_key(label),
        JournalEntry.id.not_in(reversed_ids)))


@router.get("/vat")
def vat_overview(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
                 kind: Opt = None, year: Opt = None, number: Opt = None):
    p = _period(kind, year, number)
    result = compute_vat_return(session, admin.id, p["start"], p["end"])
    state = approval_state(session, admin.id, p["start"], p["end"], result)
    approver = session.get(User, state.approval.approved_by_id) if state.approval else None
    return render(request, "vat/index.html", {
        "approval": state.approval, "approval_current": state.current, "approver": approver,
        "figures_digest": digest(snapshot(result)), "p": p, "r": result, "whole": whole_euros,
        "verify": VERIFY, "sources": SOURCES,
        "deadline": filing_deadline(p["kind"], p["end"]), "kor": admin.kor,
        "settlement": _existing_settlement(session, admin, p["label"]),
        "can_write": user.role in WRITE_ROLES, "years": _years(),
    })


@router.get("/vat/lines")
def vat_lines(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
              bucket: Opt = None, kind: Opt = None, year: Opt = None, number: Opt = None):
    valid = {r[0] for r in RUBRIEKEN} | {"5b"}
    if bucket not in valid:
        raise HTTPException(404)
    p = _period(kind, year, number)
    rows = lines_for_bucket(session, admin.id, p["start"], p["end"], bucket)
    return render(request, "vat/lines.html", {
        "p": p, "bucket": bucket, "rows": rows, "total": sum(r[3] for r in rows)})


@router.post("/vat/approve")
def vat_approve(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                kind: Annotated[str, Form()], year: Annotated[str, Form()],
                number: Annotated[str, Form()], figures: Annotated[str, Form()],
                documents_checked: Annotated[str | None, Form()] = None,
                confirm: Annotated[list[str] | None, Form()] = None):
    p = _period(kind, year, number)
    back = RedirectResponse(f"/vat?{p['qs']}", status.HTTP_303_SEE_OTHER)
    if _existing_settlement(session, admin, p["label"]):
        flash(request, f"{p['label']} has already been settled; reverse that entry first.",
              "error")
        return back
    result = compute_vat_return(session, admin.id, p["start"], p["end"])
    current = snapshot(result)
    if figures != digest(current):
        flash(request, "The figures changed while you were reviewing them. Check them again.",
              "error")
        return back
    if documents_checked is None or set(confirm or []) != set(rubrieken(result)):
        flash(request, "Confirm every rubriek and that you checked the documents behind them.",
              "error")
        return back
    approval = session.scalar(select(VatApproval).where(
        VatApproval.administration_id == admin.id, VatApproval.period_start == p["start"],
        VatApproval.period_end == p["end"]))
    if approval is None:
        session.add(VatApproval(administration_id=admin.id, period_start=p["start"],
                                period_end=p["end"], figures=current, approved_by_id=user.id))
    else:
        approval.figures, approval.approved_by_id = current, user.id
        approval.approved_at = func.now()
    audit.record(session, "vat.approve", user_id=user.id, administration_id=admin.id,
                 entity_type="vat_approval", details={"period": p["label"], "figures": current})
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        flash(request, "Someone approved this return at the same moment. Check it again.",
              "error")
        return back
    flash(request, f"VAT return {p['label']} approved.")
    return back


@router.post("/vat/settle")
def vat_settle(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
               kind: Annotated[str, Form()], year: Annotated[str, Form()],
               number: Annotated[str, Form()]):
    p = _period(kind, year, number)
    back = RedirectResponse(f"/vat?{p['qs']}", status.HTTP_303_SEE_OTHER)
    try:
        if _existing_settlement(session, admin, p["label"]):
            raise LedgerError(f"{p['label']} has already been settled; reverse that entry first")
        result = compute_vat_return(session, admin.id, p["start"], p["end"])
        state = approval_state(session, admin.id, p["start"], p["end"], result)
        if state.approval is None:
            raise LedgerError(f"the {p['label']} return has not been approved; approve it first")
        if not state.current:
            raise LedgerError(
                f"the figures for {p['label']} changed after they were approved; approve again")
        specs = _settlement_specs(session, admin, p["start"], p["end"])
        entry = post_entry(session, admin.id, p["end"], f"VAT settlement {p['label']}", specs,
                           source_type=_settlement_key(p["label"]), user_id=user.id)
        audit.record(session, "vat.settle", user_id=user.id, administration_id=admin.id,
                     entity_type="journal_entry", entity_id=entry.id,
                     details={"period": p["label"], "number": entry.number})
        session.commit()
    except LedgerError as exc:
        session.rollback()
        flash(request, str(exc), "error")
        return back
    flash(request, f"VAT settlement {entry.number} posted for {p['label']}.")
    return back


def _settlement_specs(session, admin, start: date, end: date) -> list[LineSpec]:
    """Clear the period's movement on the VAT accounts into the settlement account."""
    payable = (SystemRole.VAT_PAYABLE_HIGH, SystemRole.VAT_PAYABLE_LOW,
               SystemRole.VAT_PAYABLE_REVERSE)
    specs: list[LineSpec] = []
    net_owed = 0
    for role in (*payable, SystemRole.VAT_RECEIVABLE):
        account = system_account(session, admin.id, role)
        debit, credit = session.execute(
            select(func.coalesce(func.sum(JournalLine.debit_cents), 0),
                   func.coalesce(func.sum(JournalLine.credit_cents), 0))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == account.id, JournalEntry.entry_date >= start,
                   JournalEntry.entry_date <= end)
        ).one()
        movement = int(credit) - int(debit)
        if role is SystemRole.VAT_RECEIVABLE:
            movement = -movement  # positive = debit balance
        if movement == 0:
            continue
        net_owed += -movement if role is SystemRole.VAT_RECEIVABLE else movement
        # Payable (credit-natured) clears with a debit; receivable (debit-natured) with a credit.
        clears_with_debit = (movement > 0) == (role is not SystemRole.VAT_RECEIVABLE)
        amount = abs(movement)
        specs.append(LineSpec(account.id, debit_cents=amount if clears_with_debit else 0,
                              credit_cents=0 if clears_with_debit else amount,
                              description="Clear VAT for the period"))
    if not specs:
        raise LedgerError("there is no VAT movement in this period to settle")
    settlement = system_account(session, admin.id, SystemRole.VAT_SETTLEMENT)
    if net_owed:
        specs.append(LineSpec(settlement.id, debit_cents=0 if net_owed > 0 else -net_owed,
                              credit_cents=net_owed if net_owed > 0 else 0,
                              description="VAT payable to / receivable from the tax authority"))
    return specs


@router.get("/vat/icp")
def vat_icp(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
            kind: Opt = None, year: Opt = None, number: Opt = None,
            export: Opt = None, comma: Opt = None):
    p = _period(kind, year, number)
    rows = icp_overview(session, admin.id, p["start"], p["end"])
    if export == "csv":
        data = [(r.vat_number, r.name, Cents(r.amount_cents)) for r in rows]
        return csv_response(f"icp-{p['label']}.csv", ["VAT number", "Customer", "Amount"], data,
                            comma=comma == "1")
    return render(request, "vat/icp.html", {
        "p": p, "rows": rows, "total": sum(r.amount_cents for r in rows),
        "years": _years(),
    })
