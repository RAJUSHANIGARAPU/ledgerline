"""Daily till takings: validate, post through the journal, reverse, replace. Callers commit."""
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.ledger.service import (
    LedgerError,
    LineSpec,
    post_entry,
    reverse_entry,
    system_account,
)
from ledgerline.models import (
    Account,
    AccountType,
    Administration,
    DailyTakings,
    JournalEntry,
    SystemRole,
)
from ledgerline.money import format_eur, vat_in_gross
from ledgerline.vat import codes

# (VAT code, revenue account code, field on the input) per rate bucket.
BUCKETS = (
    ("V21", "8000", "gross_high_cents"),
    ("V9", "8010", "gross_low_cents"),
    ("V0", "8020", "gross_zero_cents"),
)
# Receipt method -> (system role, account code and name used when a book predates the role).
RECEIPTS = {
    "cash_cents": (SystemRole.CASH, "1000", "Cash"),
    "card_cents": (SystemRole.CARD_CLEARING, "1310", "Card payments to receive"),
    "other_cents": (SystemRole.TAKINGS_OTHER, "1320", "Vouchers and other receipts to settle"),
}
RECEIPT_LABELS = {"cash_cents": "cash", "card_cents": "card", "other_cents": "other"}
# A typo in the year should not post into the far future.
MAX_DAYS_AHEAD = 1


class TakingsError(ValueError):
    """A user-correctable problem; `problems` lists every reason."""

    def __init__(self, *problems: str):
        self.problems = [p for p in problems if p]
        super().__init__("; ".join(self.problems))


@dataclass
class TakingsInput:
    takings_date: date
    gross_high_cents: int = 0
    gross_low_cents: int = 0
    gross_zero_cents: int = 0
    cash_cents: int = 0
    card_cents: int = 0
    other_cents: int = 0
    note: str | None = None

    @property
    def gross_total_cents(self) -> int:
        return self.gross_high_cents + self.gross_low_cents + self.gross_zero_cents

    @property
    def received_total_cents(self) -> int:
        return self.cash_cents + self.card_cents + self.other_cents


def problems_for(inp: TakingsInput, locked_until: date | None = None, today: date | None = None
                 ) -> list[str]:
    """Everything wrong with one day, without touching the database."""
    found = []
    amounts = [getattr(inp, name) for _, _, name in BUCKETS] + [
        getattr(inp, name) for name in RECEIPTS]
    if any(a < 0 for a in amounts):
        found.append("amounts must not be negative")
    if inp.gross_total_cents == 0 and inp.received_total_cents == 0:
        found.append("enter at least one amount")
    elif inp.gross_total_cents != inp.received_total_cents:
        found.append(
            f"sales per rate ({format_eur(inp.gross_total_cents)}) do not equal the amounts "
            f"received ({format_eur(inp.received_total_cents)}); they differ by "
            f"{format_eur(abs(inp.gross_total_cents - inp.received_total_cents))}")
    limit = (today or datetime.now(UTC).date()) + timedelta(days=MAX_DAYS_AHEAD)
    if inp.takings_date > limit:
        found.append(f"{inp.takings_date.isoformat()} is in the future")
    if locked_until and inp.takings_date <= locked_until:
        found.append(f"the period is closed up to {locked_until.isoformat()}")
    return found


def _receipt_account(session: Session, admin_id: int, role: SystemRole, code: str, name: str
                     ) -> Account:
    """The account for a receipt method; older books get the role on first use."""
    account = session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.system_role == role))
    if account is not None:
        return account
    account = session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.code == code))
    if account is not None:
        if account.system_role is not None or account.type != AccountType.ASSET:
            raise TakingsError(f"account {code} is in use for something else; "
                               f"create an asset account with system role {role}")
        account.system_role = role
        return account
    account = Account(administration_id=admin_id, code=code, name=name,
                      type=AccountType.ASSET, system_role=role)
    session.add(account)
    session.flush()
    return account


def _revenue_account(session: Session, admin_id: int, code: str) -> Account:
    account = session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.code == code, Account.is_active.is_(True)))
    if account is None:
        raise TakingsError(f"revenue account {code} is missing or inactive")
    return account


def build_lines(session: Session, admin_id: int, inp: TakingsInput) -> list[LineSpec]:
    """Receipts debited, revenue (net) and output VAT credited, tagged for the VAT return."""
    lines = []
    for name, (role, code, label) in RECEIPTS.items():
        amount = getattr(inp, name)
        if amount:
            account = _receipt_account(session, admin_id, role, code, label)
            lines.append(LineSpec(account.id, debit_cents=amount,
                                  description=f"Takings {RECEIPT_LABELS[name]}"))
    for vat_code, revenue_code, name in BUCKETS:
        gross = getattr(inp, name)
        if not gross:
            continue
        rate = codes.get(vat_code).rate
        vat = vat_in_gross(gross, rate)
        revenue = _revenue_account(session, admin_id, revenue_code)
        lines.append(LineSpec(revenue.id, credit_cents=gross - vat, vat_code=vat_code,
                              vat_role="base", description=f"Takings {_rate_label(rate)}"))
        if vat:
            role = SystemRole.VAT_PAYABLE_HIGH if rate >= 15 else SystemRole.VAT_PAYABLE_LOW
            lines.append(LineSpec(system_account(session, admin_id, role).id, credit_cents=vat,
                                  vat_code=vat_code, vat_role="vat",
                                  description=f"VAT takings {_rate_label(rate)}"))
    return lines


def _rate_label(rate: Decimal) -> str:
    return f"{rate.normalize():f}%"


def active_for_day(session: Session, admin_id: int, day: date) -> DailyTakings | None:
    return session.scalar(select(DailyTakings).where(
        DailyTakings.administration_id == admin_id, DailyTakings.takings_date == day,
        DailyTakings.reversed_at.is_(None)))


def post_takings(session: Session, admin: Administration, inp: TakingsInput,
                 user_id: int | None = None) -> DailyTakings:
    found = problems_for(inp, admin.locked_until)
    if active_for_day(session, admin.id, inp.takings_date):
        found.append(f"{inp.takings_date.isoformat()} is already recorded; "
                     "correct it from the list (reverse and repost)")
    if found:
        raise TakingsError(*found)
    lines = build_lines(session, admin.id, inp)
    try:
        entry = post_entry(
            session, admin.id, inp.takings_date,
            f"Daily takings {inp.takings_date.isoformat()}", lines,
            source_type="daily_takings", user_id=user_id)
    except LedgerError as exc:
        raise TakingsError(str(exc)) from exc
    row = DailyTakings(
        administration_id=admin.id, takings_date=inp.takings_date,
        gross_high_cents=inp.gross_high_cents, gross_low_cents=inp.gross_low_cents,
        gross_zero_cents=inp.gross_zero_cents, cash_cents=inp.cash_cents,
        card_cents=inp.card_cents, other_cents=inp.other_cents,
        note=(inp.note or "").strip()[:200] or None, journal_entry_id=entry.id,
        created_by_id=user_id)
    session.add(row)
    session.flush()
    audit.record(session, "takings.post", user_id=user_id, administration_id=admin.id,
                 entity_type="daily_takings", entity_id=row.id,
                 details={"date": inp.takings_date.isoformat(), "entry": entry.number,
                          "gross_cents": inp.gross_total_cents})
    return row


def reverse_takings(session: Session, row: DailyTakings, user_id: int | None = None,
                    reason: str = "correction") -> JournalEntry:
    if row.reversed_at is not None:
        raise TakingsError("this day has already been reversed")
    entry = session.get(JournalEntry, row.journal_entry_id)
    try:
        reversal = reverse_entry(session, entry, row.takings_date, reason, user_id)
    except LedgerError as exc:
        raise TakingsError(str(exc)) from exc
    row.reversal_entry_id = reversal.id
    row.reversed_at = datetime.now(UTC)
    session.flush()
    audit.record(session, "takings.reverse", user_id=user_id,
                 administration_id=row.administration_id, entity_type="daily_takings",
                 entity_id=row.id, details={"entry": reversal.number, "reason": reason})
    return reversal


def replace_takings(session: Session, admin: Administration, row: DailyTakings,
                    inp: TakingsInput, user_id: int | None = None) -> DailyTakings:
    """Reverse the old day and post the corrected one; the caller commits both or neither."""
    if inp.takings_date != row.takings_date:
        raise TakingsError("the date of a recorded day cannot change; reverse it and enter "
                           "the other day")
    found = problems_for(inp, admin.locked_until)
    if found:
        raise TakingsError(*found)
    reverse_takings(session, row, user_id, "corrected takings")
    return post_takings(session, admin, inp, user_id)
