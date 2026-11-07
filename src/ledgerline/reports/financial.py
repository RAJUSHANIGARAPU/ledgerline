"""Profit and loss, balance sheet and trial balance from account totals."""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from ledgerline.ledger.service import AccountBalance, account_balances
from ledgerline.models import AccountType


@dataclass
class Section:
    title: str
    rows: list[AccountBalance] = field(default_factory=list)
    extra: list[tuple[str, int]] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return sum(r.balance_cents for r in self.rows) + sum(v for _, v in self.extra)


@dataclass
class ProfitAndLoss:
    start: date
    end: date
    revenue: Section
    expenses: Section

    @property
    def result_cents(self) -> int:
        return self.revenue.total_cents - self.expenses.total_cents


@dataclass
class BalanceSheet:
    as_of: date
    assets: Section
    liabilities: Section
    equity: Section

    @property
    def balanced(self) -> bool:
        return self.assets.total_cents == self.liabilities.total_cents + self.equity.total_cents


@dataclass
class TrialRow:
    account: object
    debit_cents: int
    credit_cents: int


def _active(balances: list[AccountBalance], kind: AccountType) -> list[AccountBalance]:
    return [
        b for b in balances
        if b.account.type == kind and (b.debit_cents or b.credit_cents)
    ]


def profit_and_loss(session: Session, administration_id: int, start: date, end: date):
    balances = account_balances(session, administration_id, start=start, end=end)
    return ProfitAndLoss(
        start,
        end,
        Section("Revenue", _active(balances, AccountType.REVENUE)),
        Section("Expenses", _active(balances, AccountType.EXPENSE)),
    )


def result_between(
    session: Session, administration_id: int, start: date | None, end: date
) -> int:
    balances = account_balances(session, administration_id, start=start, end=end)
    revenue = sum(b.balance_cents for b in balances if b.account.type == AccountType.REVENUE)
    expense = sum(b.balance_cents for b in balances if b.account.type == AccountType.EXPENSE)
    return revenue - expense


def balance_sheet(session: Session, administration_id: int, as_of: date) -> BalanceSheet:
    balances = account_balances(session, administration_id, end=as_of)
    year_start = date(as_of.year, 1, 1)
    # No year-end close exists, so earlier results are shown separately from this year's.
    last_year_end = date.fromordinal(year_start.toordinal() - 1)
    previous = result_between(session, administration_id, None, last_year_end)
    current = result_between(session, administration_id, year_start, as_of)
    equity = Section(
        "Equity",
        _active(balances, AccountType.EQUITY),
        [("Result of previous years (not yet closed)", previous),
         ("Result of the year to date", current)],
    )
    return BalanceSheet(
        as_of,
        Section("Assets", _active(balances, AccountType.ASSET)),
        Section("Liabilities", _active(balances, AccountType.LIABILITY)),
        equity,
    )


def trial_balance(session: Session, administration_id: int, end: date, start: date | None = None):
    rows = []
    for b in account_balances(session, administration_id, start=start, end=end):
        net = b.debit_cents - b.credit_cents
        if b.debit_cents or b.credit_cents:
            rows.append(TrialRow(b.account, max(net, 0), max(-net, 0)))
    return rows
