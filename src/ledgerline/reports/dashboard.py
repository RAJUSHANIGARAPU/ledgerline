"""Numbers for the dashboard. Everything is scoped to one administration."""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from ledgerline.models import (
    Account,
    Administration,
    BankAccount,
    BankTransaction,
    InvoiceStatus,
    JournalEntry,
    JournalLine,
    PurchaseInvoice,
    SalesInvoice,
    SystemRole,
    TxStatus,
)
from ledgerline.onboarding.service import setup_complete
from ledgerline.reports.financial import result_between
from ledgerline.vat.vat_return import compute_vat_return, period_bounds


@dataclass
class Dashboard:
    bank_cents: int = 0
    receivable_cents: int = 0
    receivable_overdue: int = 0
    payable_cents: int = 0
    vat_cents: int = 0
    vat_label: str = ""
    result_ytd_cents: int = 0
    months: list[dict] = field(default_factory=list)
    recent: list[JournalEntry] = field(default_factory=list)
    unmatched: int = 0
    draft_sales: int = 0
    draft_purchases: int = 0
    has_data: bool = False
    setup_complete: bool = True
    checklist: list[tuple[str, bool, str]] = field(default_factory=list)


def last_months(today: date, count: int = 12) -> list[tuple[int, int]]:
    index = today.year * 12 + today.month - 1
    return [divmod(index - offset, 12) for offset in range(count - 1, -1, -1)]


def _month_key(pair: tuple[int, int]) -> tuple[int, int]:
    year, month0 = pair
    return year, month0 + 1


def monthly_series(session: Session, administration_id: int, today: date) -> list[dict]:
    keys = [_month_key(p) for p in last_months(today)]
    start = date(keys[0][0], keys[0][1], 1)
    year = extract("year", JournalEntry.entry_date)
    month = extract("month", JournalEntry.entry_date)
    rows = session.execute(
        select(
            year, month, Account.type,
            func.coalesce(func.sum(JournalLine.debit_cents), 0),
            func.coalesce(func.sum(JournalLine.credit_cents), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(
            JournalEntry.administration_id == administration_id,
            JournalEntry.entry_date >= start,
            JournalEntry.entry_date <= today,
            Account.type.in_(("revenue", "expense")),
        )
        .group_by(year, month, Account.type)
    ).all()
    series = {k: {"label": f"{k[0]}-{k[1]:02d}", "revenue": 0, "expenses": 0} for k in keys}
    for y, m, kind, debit, credit in rows:
        bucket = series.get((int(y), int(m)))
        if bucket is None:
            continue
        if kind == "revenue":
            bucket["revenue"] += int(credit) - int(debit)
        else:
            bucket["expenses"] += int(debit) - int(credit)
    return list(series.values())


def _open_totals(session: Session, model, administration_id: int, today: date) -> tuple[int, int]:
    rows = session.execute(
        select(model.total_incl_cents - model.paid_cents, model.due_date).where(
            model.administration_id == administration_id, model.status == InvoiceStatus.OPEN
        )
    ).all()
    total = sum(int(r[0]) for r in rows)
    overdue = sum(1 for amount, due in rows if amount > 0 and due < today)
    return total, overdue


def _count(session: Session, stmt) -> int:
    return int(session.scalar(stmt) or 0)


def build_dashboard(session: Session, admin: Administration, today: date) -> Dashboard:
    aid = admin.id
    d = Dashboard()
    ledger_ids = set(
        session.scalars(
            select(Account.id).where(
                Account.administration_id == aid, Account.system_role == SystemRole.BANK
            )
        )
    )
    ledger_ids |= set(
        session.scalars(
            select(BankAccount.ledger_account_id).where(BankAccount.administration_id == aid))
    )
    if ledger_ids:
        d.bank_cents = int(
            session.scalar(
                select(func.coalesce(
                    func.sum(JournalLine.debit_cents - JournalLine.credit_cents), 0))
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalEntry.administration_id == aid,
                       JournalLine.account_id.in_(ledger_ids),
                       JournalEntry.entry_date <= today)
            )
        )
    d.receivable_cents, d.receivable_overdue = _open_totals(session, SalesInvoice, aid, today)
    d.payable_cents, _ = _open_totals(session, PurchaseInvoice, aid, today)
    quarter = (today.month - 1) // 3 + 1
    start, end, d.vat_label = period_bounds("quarter", today.year, quarter)
    d.vat_cents = compute_vat_return(session, aid, start, end).balance_cents
    d.result_ytd_cents = result_between(session, aid, date(today.year, 1, 1), today)
    d.months = monthly_series(session, aid, today)
    d.recent = list(
        session.scalars(
            select(JournalEntry).where(JournalEntry.administration_id == aid)
            .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).limit(8)
        )
    )
    d.unmatched = _count(
        session,
        select(func.count()).select_from(BankTransaction)
        .join(BankAccount, BankAccount.id == BankTransaction.bank_account_id)
        .where(BankAccount.administration_id == aid, BankTransaction.status == TxStatus.UNMATCHED),
    )
    d.draft_sales = _count(
        session,
        select(func.count()).select_from(SalesInvoice)
        .where(SalesInvoice.administration_id == aid, SalesInvoice.status == InvoiceStatus.DRAFT),
    )
    d.draft_purchases = _count(
        session,
        select(func.count()).select_from(PurchaseInvoice)
        .where(PurchaseInvoice.administration_id == aid,
               PurchaseInvoice.status == InvoiceStatus.DRAFT),
    )
    has_entries = _count(session, select(func.count()).select_from(JournalEntry)
                         .where(JournalEntry.administration_id == aid)) > 0
    has_invoice = _count(session, select(func.count()).select_from(SalesInvoice)
                         .where(SalesInvoice.administration_id == aid)) > 0
    has_bank = _count(session, select(func.count()).select_from(BankAccount)
                      .where(BankAccount.administration_id == aid)) > 0
    has_statement = d.unmatched > 0 or _count(
        session,
        select(func.count()).select_from(BankTransaction)
        .join(BankAccount, BankAccount.id == BankTransaction.bank_account_id)
        .where(BankAccount.administration_id == aid),
    ) > 0
    d.has_data = has_entries or has_invoice
    d.setup_complete = setup_complete(session, admin)
    d.checklist = [
        ("Add your business details", bool(admin.kvk_number and admin.vat_number), "/settings"),
        ("Add a bank account", has_bank, "/bank"),
        ("Create your first invoice", has_invoice, "/sales"),
        ("Import a bank statement", has_statement, "/bank"),
    ]
    return d
