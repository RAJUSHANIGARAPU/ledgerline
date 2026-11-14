import base64
import binascii
import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

from ledgerline import audit
from ledgerline.banking import csv_import, matching, service
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
from ledgerline.money import MoneyError, to_cents
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


def _after_import(request: Request, account: BankAccount, result: service.ImportResult):
    flash(
        request,
        f"Imported {result.created} new transactions ({result.skipped} already known).",
    )
    for warning in result.warnings:
        flash(request, warning, "info")
    if result.left_out:
        # Show each left-out line: a genuine payment with the same amount and date can look
        # like a duplicate, so the owner decides line by line.
        return render(
            request, "bank/import_left_out.html",
            {"account": account, "statement": result.statement,
             "lines": [{**ln, "json": json.dumps(ln)} for ln in result.left_out]},
        )
    return _redirect(f"/bank/reconcile?account_id={account.id}")


@router.post("/accounts/{account_id}/import/left-out")
def import_left_out(
    account_id: int,
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    statement_id: Annotated[int, Form()],
    line: Annotated[list[str] | None, Form()] = None,
):
    account = _account_or_404(session, admin, account_id)
    try:
        chosen = [json.loads(raw) for raw in line or []]
        if not all(isinstance(c, dict) for c in chosen):
            raise service.BankingError("invalid line data")
        created = service.import_left_out_lines(session, account, statement_id, chosen, user.id)
        session.commit()
    except (json.JSONDecodeError, *ERRORS) as exc:
        session.rollback()
        flash(request, str(exc) if isinstance(exc, ERRORS) else "invalid line data", "error")
        return _redirect()
    flash(request, f"Imported {created} line(s) that looked like duplicates.")
    return _redirect(f"/bank/reconcile?account_id={account.id}")


def _map_page(request, account, filename: str, content: bytes, error: str | None = None,
              chosen: dict | None = None):
    table = csv_import.read_table(content)
    return render(
        request, "bank/csv_map.html",
        {
            "account": account, "filename": filename, "headers": table.headers,
            "sample": table.rows[:5], "error": error,
            "content_b64": base64.b64encode(content).decode(),
            "chosen": chosen or csv_import.suggest_mapping(table.headers),
        },
    )


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
    filename = file.filename or "statement"
    try:
        result = service.import_statement(session, account, filename, content, user.id)
        session.commit()
    except service.MappingNeeded:
        session.rollback()
        return _map_page(request, account, filename, content)
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
        return _redirect()
    return _after_import(request, account, result)


@router.post("/accounts/{account_id}/import/csv")
def import_csv_mapped(
    account_id: int,
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    filename: Annotated[str, Form()],
    content_b64: Annotated[str, Form()],
    date_column: Annotated[str, Form()],
    amount_mode: Annotated[str, Form()] = "single",
    amount: Annotated[str, Form()] = "",
    direction: Annotated[str, Form()] = "",
    debit: Annotated[str, Form()] = "",
    credit: Annotated[str, Form()] = "",
    counterparty: Annotated[str, Form()] = "",
    iban: Annotated[str, Form()] = "",
    description: Annotated[list[str] | None, Form()] = None,
    date_format: Annotated[str, Form()] = "auto",
    decimal: Annotated[str, Form()] = "auto",
    remember: Annotated[str, Form()] = "",
):
    account = _account_or_404(session, admin, account_id)
    try:
        content = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "invalid file data") from exc
    if len(content) > get_settings().max_upload_bytes:
        raise HTTPException(413, "the file is too large")
    single = amount_mode == "single"
    mapping = csv_import.CsvMapping(
        date=date_column,
        amount=amount or None if single else None,
        direction=direction or None if single else None,
        debit=debit or None if not single else None,
        credit=credit or None if not single else None,
        counterparty=counterparty or None,
        iban=iban or None,
        description=[c for c in description or [] if c],
        date_format=date_format,
        decimal=decimal,
    )
    try:
        result = service.import_statement(
            session, account, filename, content, user.id, mapping=mapping,
            save_mapping=remember == "true",
        )
        session.commit()
    except ERRORS as exc:
        session.rollback()
        return _map_page(request, account, filename, content, str(exc), mapping.to_json())
    return _after_import(request, account, result)


@router.post("/accounts/{account_id}/manual")
def add_manual(
    account_id: int,
    request: Request,
    session: SessionDep,
    user: OwnerDep,
    admin: AdminDep,
    booking_date: Annotated[date, Form()],
    amount: Annotated[str, Form()],
    direction: Annotated[str, Form()],
    reason: Annotated[str, Form()],
    counterparty_name: Annotated[str, Form()] = "",
    counterparty_iban: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
):
    account = _account_or_404(session, admin, account_id)
    try:
        cents = abs(to_cents(amount))
        if direction not in ("in", "out"):
            raise service.BankingError("choose money in or money out")
        service.add_manual_transaction(
            session, account, booking_date, cents if direction == "in" else -cents,
            counterparty_name, counterparty_iban, description, reason, user.id,
        )
        session.commit()
        flash(request, "Line added, marked as manually entered.")
    except ERRORS as exc:
        session.rollback()
        flash(request, str(exc), "error")
        return _redirect()
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
                "payout_hints": matching.platform_payout_hints(session, tx)
                if status == TxStatus.UNMATCHED else [],
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
