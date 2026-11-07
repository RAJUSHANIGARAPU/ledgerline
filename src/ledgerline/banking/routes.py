from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

from ledgerline import audit
from ledgerline.banking import matching, service
from ledgerline.config import get_settings
from ledgerline.ledger.service import LedgerError
from ledgerline.models import (
    Account,
    AccountType,
    BankAccount,
    BankStatement,
    BankTransaction,
    InvoiceStatus,
    PurchaseInvoice,
    TxStatus,
)
from ledgerline.money import MoneyError
from ledgerline.vat.codes import VAT_CODES, Direction
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter(prefix="/bank")

ERRORS = (service.BankingError, LedgerError, MoneyError)


def _redirect(url: str = "/bank") -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _account_or_404(session, admin, account_id: int) -> BankAccount:
    account = service.get_bank_account(session, admin.id, account_id)
    if account is None:
        raise HTTPException(404)
    return account


def _tx_or_404(session, admin, tx_id: int) -> BankTransaction:
    tx = service.get_transaction(session, admin.id, tx_id)
    if tx is None:
        raise HTTPException(404)
    return tx


def _back(tx: BankTransaction) -> str:
    return f"/bank/reconcile?account_id={tx.bank_account_id}"


@router.get("")
def index(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    accounts = session.scalars(
        select(BankAccount)
        .where(BankAccount.administration_id == admin.id)
        .order_by(BankAccount.id)
    ).all()
    unmatched = dict(
        session.execute(
            select(BankTransaction.bank_account_id, func.count())
            .where(BankTransaction.status == TxStatus.UNMATCHED)
            .group_by(BankTransaction.bank_account_id)
        ).all()
    )
    statements = session.scalars(
        select(BankStatement)
        .join(BankAccount, BankAccount.id == BankStatement.bank_account_id)
        .where(BankAccount.administration_id == admin.id)
        .order_by(BankStatement.imported_at.desc())
        .limit(10)
    ).all()
    ledger_accounts = session.scalars(
        select(Account)
        .where(
            Account.administration_id == admin.id,
            Account.type == AccountType.ASSET,
            Account.is_active.is_(True),
        )
        .order_by(Account.code)
    ).all()
    return render(
        request, "bank/index.html",
        {
            "accounts": accounts, "unmatched": unmatched, "statements": statements,
            "ledger_accounts": ledger_accounts,
            "max_mb": get_settings().max_upload_bytes // (1024 * 1024),
        },
    )


@router.post("/accounts")
def create_account(
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    iban: Annotated[str, Form()],
    name: Annotated[str, Form()],
    ledger_account_id: Annotated[int | None, Form()] = None,
):
    try:
        service.create_bank_account(session, admin, iban, name, ledger_account_id, user.id)
        session.commit()
        flash(request, "Bank account added.")
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
    return _redirect()


@router.post("/accounts/{account_id}/import")
def import_file(
    account_id: int,
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    file: Annotated[UploadFile, File()],
):
    account = _account_or_404(session, admin, account_id)
    limit = get_settings().max_upload_bytes
    content = file.file.read(limit + 1)
    try:
        result = service.import_statement(
            session, account, file.filename or "statement", content, user.id
        )
        session.commit()
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
        return _redirect()
    flash(
        request,
        f"Imported {result.created} new transactions ({result.skipped} already known).",
    )
    for warning in result.warnings:
        flash(request, warning, "info")
    return _redirect(f"/bank/reconcile?account_id={account.id}")


@router.get("/reconcile")
def reconcile(
    request: Request,
    session: SessionDep,
    user: UserDep,
    admin: AdminDep,
    account_id: int | None = None,
    status: str = TxStatus.UNMATCHED,
):
    accounts = session.scalars(
        select(BankAccount)
        .where(BankAccount.administration_id == admin.id)
        .order_by(BankAccount.id)
    ).all()
    if not accounts:
        return _redirect()
    if status not in {s.value for s in TxStatus}:
        raise HTTPException(404)
    account = _account_or_404(session, admin, account_id) if account_id else accounts[0]
    txs = session.scalars(
        select(BankTransaction)
        .where(BankTransaction.bank_account_id == account.id, BankTransaction.status == status)
        .order_by(BankTransaction.booking_date, BankTransaction.id)
    ).all()
    candidates = matching.load_candidates(session, admin.id)
    rows = []
    for tx in txs:
        ranked = (
            matching.suggest(session, tx, candidates, include_all=True)
            if status == TxStatus.UNMATCHED
            else []
        )
        rows.append(
            {
                "tx": tx,
                "suggestions": [s for s in ranked if s.score > 0],
                "others": [s for s in ranked if s.score == 0],
            }
        )
    chart = session.scalars(
        select(Account)
        .where(Account.administration_id == admin.id, Account.is_active.is_(True))
        .order_by(Account.code)
    ).all()
    return render(
        request, "bank/reconcile.html",
        {
            "accounts": accounts, "account": account, "rows": rows, "status": status,
            "chart": chart,
            "vat_in": [c for c in VAT_CODES.values()
                       if c.direction == Direction.SALES],
            "vat_out": [c for c in VAT_CODES.values()
                        if c.direction == Direction.PURCHASE and not c.reverse_charge],
            "counts": {
                s.value: session.scalar(
                    select(func.count()).select_from(BankTransaction).where(
                        BankTransaction.bank_account_id == account.id,
                        BankTransaction.status == s.value,
                    )
                )
                for s in TxStatus
            },
        },
    )


@router.post("/transactions/{tx_id}/match")
def match_tx(
    tx_id: int,
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    allocation: Annotated[list[str] | None, Form()] = None,
):
    tx = _tx_or_404(session, admin, tx_id)
    try:
        allocations = [_parse_allocation(v) for v in allocation or []]
        service.match(session, tx, allocations, user.id)
        session.commit()
        flash(request, "Transaction matched.")
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
    return _redirect(_back(tx))


def _parse_allocation(value: str) -> service.Allocation:
    try:
        kind, invoice_id, cents = value.split(":")
        if kind not in (matching.SALES, matching.PURCHASE):
            raise ValueError
        return service.Allocation(kind, int(invoice_id), int(cents))
    except ValueError as exc:
        raise service.BankingError("invalid selection") from exc


@router.post("/transactions/{tx_id}/book")
def book_tx(
    tx_id: int,
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    account_id: Annotated[int, Form()],
    vat_code: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
):
    tx = _tx_or_404(session, admin, tx_id)
    try:
        service.book_to_account(
            session, tx, account_id, vat_code or None, user.id, description or None
        )
        session.commit()
        flash(request, "Transaction booked.")
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
    return _redirect(_back(tx))


@router.post("/transactions/{tx_id}/ignore")
def ignore_tx(tx_id: int, request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep):
    tx = _tx_or_404(session, admin, tx_id)
    try:
        service.ignore(session, tx, user.id)
        session.commit()
        flash(request, "Transaction ignored.")
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
    return _redirect(_back(tx))


@router.post("/transactions/{tx_id}/undo")
def undo_tx(tx_id: int, request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep):
    tx = _tx_or_404(session, admin, tx_id)
    try:
        service.undo(session, tx, user.id)
        session.commit()
        flash(request, "Transaction is unmatched again; the entry was reversed.")
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
    return _redirect(_back(tx))


@router.post("/accounts/{account_id}/accept-exact")
def accept_exact(
    account_id: int, request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep
):
    account = _account_or_404(session, admin, account_id)
    count = service.accept_exact_matches(session, account, user.id)
    session.commit()
    flash(request, f"Accepted {count} exact matches.")
    return _redirect(f"/bank/reconcile?account_id={account.id}")


@router.get("/sepa")
def sepa_page(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    accounts = session.scalars(
        select(BankAccount)
        .where(BankAccount.administration_id == admin.id)
        .order_by(BankAccount.id)
    ).all()
    invoices = session.scalars(
        select(PurchaseInvoice)
        .where(
            PurchaseInvoice.administration_id == admin.id,
            PurchaseInvoice.status == InvoiceStatus.OPEN,
        )
        .order_by(PurchaseInvoice.due_date, PurchaseInvoice.id)
    ).all()
    return render(
        request, "bank/sepa.html",
        {"accounts": accounts, "invoices": invoices, "today": date.today()},
    )


@router.post("/sepa/export")
def sepa_export(
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    account_id: Annotated[int, Form()],
    execution_date: Annotated[date, Form()],
    invoice_id: Annotated[list[int] | None, Form()] = None,
):
    account = _account_or_404(session, admin, account_id)
    try:
        filename, xml = service.sepa_export(
            session, admin, account, invoice_id or [], execution_date
        )
    except ERRORS as exc:
        flash(request, str(exc), "error")
        return _redirect("/bank/sepa")
    audit.record(
        session, "sepa.export", user_id=user.id, administration_id=admin.id,
        entity_type="bank_account", entity_id=account.id, details={"file": filename},
    )
    session.commit()
    return Response(
        xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
