"""Double-entry posting. The database triggers enforce the same rules as a backstop."""
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ledgerline.models import Account, Administration, JournalEntry, JournalLine, SystemRole


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class LineSpec:
    account_id: int
    debit_cents: int = 0
    credit_cents: int = 0
    description: str | None = None
    contact_id: int | None = None
    vat_code: str | None = None
    vat_role: str | None = None


@dataclass
class AccountBalance:
    account: Account
    debit_cents: int = 0
    credit_cents: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def balance_cents(self) -> int:
        """Positive on the account's natural side (debit for assets/expenses)."""
        if self.account.type in ("asset", "expense"):
            return self.debit_cents - self.credit_cents
        return self.credit_cents - self.debit_cents


def next_number(session: Session, administration_id: int, kind: str, year: int) -> int:
    """Gap-free per (administration, kind, year): the row lock is held until commit."""
    return session.execute(
        text(
            "INSERT INTO number_sequences (administration_id, kind, year, last_value) "
            "VALUES (:a, :k, :y, 1) "
            "ON CONFLICT (administration_id, kind, year) "
            "DO UPDATE SET last_value = number_sequences.last_value + 1 "
            "RETURNING last_value"
        ),
        {"a": administration_id, "k": kind, "y": year},
    ).scalar_one()


def system_account(session: Session, administration_id: int, role: SystemRole) -> Account:
    account = session.scalar(
        select(Account).where(
            Account.administration_id == administration_id, Account.system_role == role
        )
    )
    if account is None:
        raise LedgerError(f"no account with system role {role} in this administration")
    return account


def validate_lines(lines: Sequence[LineSpec]) -> None:
    if len(lines) < 2:
        raise LedgerError("an entry needs at least two lines")
    for line in lines:
        if line.debit_cents < 0 or line.credit_cents < 0:
            raise LedgerError("amounts must not be negative")
        if (line.debit_cents == 0) == (line.credit_cents == 0):
            raise LedgerError("each line is either a debit or a credit")
    debit = sum(line.debit_cents for line in lines)
    credit = sum(line.credit_cents for line in lines)
    if debit != credit:
        raise LedgerError(f"entry does not balance: debit {debit} != credit {credit}")


def post_entry(
    session: Session,
    administration_id: int,
    entry_date: date,
    description: str,
    lines: Sequence[LineSpec],
    *,
    source_type: str | None = None,
    source_id: int | None = None,
    reversal_of_id: int | None = None,
    user_id: int | None = None,
) -> JournalEntry:
    validate_lines(lines)
    admin = session.get(Administration, administration_id)
    if admin is None:
        raise LedgerError("unknown administration")
    if admin.locked_until and entry_date <= admin.locked_until:
        raise LedgerError(f"period is closed up to {admin.locked_until.isoformat()}")
    account_ids = {line.account_id for line in lines}
    owned = set(
        session.scalars(
            select(Account.id).where(
                Account.id.in_(account_ids),
                Account.administration_id == administration_id,
                Account.is_active.is_(True),
            )
        )
    )
    if owned != account_ids:
        raise LedgerError("an account does not exist, is inactive, or belongs elsewhere")

    seq = next_number(session, administration_id, "journal", entry_date.year)
    entry = JournalEntry(
        administration_id=administration_id,
        number=f"{entry_date.year}-{seq:05d}",
        entry_date=entry_date,
        description=description[:300],
        source_type=source_type,
        source_id=source_id,
        reversal_of_id=reversal_of_id,
        created_by_id=user_id,
        lines=[
            JournalLine(
                account_id=line.account_id,
                debit_cents=line.debit_cents,
                credit_cents=line.credit_cents,
                description=line.description,
                contact_id=line.contact_id,
                vat_code=line.vat_code,
                vat_role=line.vat_role,
            )
            for line in lines
        ],
    )
    session.add(entry)
    session.flush()
    return entry


def reverse_entry(
    session: Session, entry: JournalEntry, on_date: date, reason: str, user_id: int | None = None
) -> JournalEntry:
    already = session.scalar(
        select(func.count())
        .select_from(JournalEntry)
        .where(JournalEntry.reversal_of_id == entry.id)
    )
    if already:
        raise LedgerError("entry has already been reversed")
    specs = [
        LineSpec(
            account_id=line.account_id,
            debit_cents=line.credit_cents,
            credit_cents=line.debit_cents,
            description=line.description,
            contact_id=line.contact_id,
            vat_code=line.vat_code,
            vat_role=line.vat_role,
        )
        for line in entry.lines
    ]
    try:
        with session.begin_nested():
            return post_entry(
                session, entry.administration_id, on_date,
                f"Reversal of {entry.number}: {reason}", specs,
                source_type="reversal", source_id=entry.id, reversal_of_id=entry.id,
                user_id=user_id,
            )
    except IntegrityError as exc:
        if "uq_journal_entries_reversal_of" not in str(exc):
            raise
        raise LedgerError("entry has already been reversed") from exc


def account_balances(
    session: Session,
    administration_id: int,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[AccountBalance]:
    """Debit/credit totals per account for entries dated in [start, end]."""
    conditions = [JournalEntry.administration_id == administration_id]
    if start:
        conditions.append(JournalEntry.entry_date >= start)
    if end:
        conditions.append(JournalEntry.entry_date <= end)
    totals = session.execute(
        select(
            JournalLine.account_id,
            func.coalesce(func.sum(JournalLine.debit_cents), 0),
            func.coalesce(func.sum(JournalLine.credit_cents), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(*conditions)
        .group_by(JournalLine.account_id)
    ).all()
    by_id = {account_id: (int(d), int(c)) for account_id, d, c in totals}
    accounts = session.scalars(
        select(Account)
        .where(Account.administration_id == administration_id)
        .order_by(Account.code)
    )
    result = []
    for account in accounts:
        debit, credit = by_id.get(account.id, (0, 0))
        result.append(AccountBalance(account, debit, credit))
    return result


def account_balance_cents(session: Session, account_id: int, end: date | None = None) -> int:
    """Signed debit-minus-credit balance of one account."""
    stmt = (
        select(func.coalesce(func.sum(JournalLine.debit_cents - JournalLine.credit_cents), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == account_id)
    )
    if end:
        stmt = stmt.where(JournalEntry.entry_date <= end)
    return int(session.scalar(stmt))
