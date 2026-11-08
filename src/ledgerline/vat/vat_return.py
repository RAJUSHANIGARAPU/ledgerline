"""Assembles the figures for the btw-aangifte from tagged journal lines."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import Account, JournalEntry, JournalLine, SystemRole
from ledgerline.vat.codes import VAT_CODES, Direction
from ledgerline.vat.sources import SOURCES

PAYABLE_ROLES = {
    SystemRole.VAT_PAYABLE_HIGH,
    SystemRole.VAT_PAYABLE_LOW,
    SystemRole.VAT_PAYABLE_REVERSE,
}

# Rubrieken in form order: (rubriek, description, has a base amount, has a VAT amount)
RUBRIEKEN: list[tuple[str, str, bool, bool]] = [
    ("1a", "Supplies and services at 21%", True, True),
    ("1b", "Supplies and services at 9%", True, True),
    ("1c", "Supplies and services at other rates (not 0%)", True, True),
    ("1d", "Private use", False, True),
    ("1e", "Supplies at 0% or not taxed in NL (incl. reverse charge to Dutch customers)",
     True, False),
    ("2a", "Domestic reverse charge (VAT shifted to you)", True, True),
    ("3a", "Export of goods outside the EU", True, False),
    ("3b", "Supplies of goods and services to EU businesses", True, False),
    ("3c", "Installation and distance sales within the EU", True, False),
    ("4a", "Purchases from outside the EU", True, True),
    ("4b", "Purchases from within the EU", True, True),
]

VERIFY = {rubriek for rubriek, (status, _url) in SOURCES.items() if status != "current"}


class Kind(StrEnum):
    BASE = "base"
    VAT = "vat"


@dataclass(frozen=True)
class Row:
    """Debit and credit totals for one (vat code, vat role, account role) combination."""

    vat_code: str
    vat_role: str
    system_role: str | None
    debit_cents: int
    credit_cents: int


@dataclass
class RubriekLine:
    rubriek: str
    description: str
    base_cents: int = 0
    vat_cents: int = 0
    has_base: bool = True
    has_vat: bool = True
    verify: bool = False

    @property
    def form_vat_euros(self) -> int:
        return euros_down(self.vat_cents)

    @property
    def form_base_euros(self) -> int:
        return euros_down(self.base_cents)


@dataclass
class VatReturn:
    start: date
    end: date
    lines: list[RubriekLine] = field(default_factory=list)
    owed_cents: int = 0  # 5a
    deductible_cents: int = 0  # 5b

    @property
    def balance_cents(self) -> int:
        """Positive: payable to the Belastingdienst; negative: refundable."""
        return self.owed_cents - self.deductible_cents

    def line(self, rubriek: str) -> RubriekLine:
        return next(line for line in self.lines if line.rubriek == rubriek)

    # The form takes whole euros for every amount, rounded in the taxpayer's favour
    # (SOURCES["rounding_all"]): VAT owed and bases down, deductible VAT up.
    @property
    def form_owed_euros(self) -> int:
        return sum(line.form_vat_euros for line in self.lines)

    @property
    def form_deductible_euros(self) -> int:
        return euros_up(self.deductible_cents)

    @property
    def form_balance_euros(self) -> int:
        return self.form_owed_euros - self.form_deductible_euros


def whole_euros(cents: int) -> int:
    """Cents to whole euros, dropping the cents (towards zero); used for the base column."""
    return int(abs(cents) // 100) * (1 if cents >= 0 else -1)


def euros_down(cents: int) -> int:
    return cents // 100


def euros_up(cents: int) -> int:
    return -(-cents // 100)


def filing_deadline(kind: str, end: date) -> date | None:
    """Last day of the month after a month or quarter (SOURCES["deadline"]); None for a year."""
    if kind not in ("month", "quarter"):
        return None
    year, month = (end.year + 1, 1) if end.month == 12 else (end.year, end.month + 1)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return following - timedelta(days=1)


def contribution(
    vat_code: str, vat_role: str, system_role: str | None, debit: int, credit: int
) -> tuple[str, Kind | None, int] | None:
    """Where a tagged amount lands: (bucket, kind, cents). Bucket is a rubriek or '5b'."""
    code = VAT_CODES.get(vat_code)
    if code is None:
        return None
    if vat_role == Kind.BASE:
        if code.base_rubriek is None:
            return None
        amount = credit - debit if code.direction is Direction.SALES else debit - credit
        return code.base_rubriek, Kind.BASE, amount
    if vat_role == Kind.VAT:
        if system_role == SystemRole.VAT_RECEIVABLE:
            return "5b", None, debit - credit
        if system_role in PAYABLE_ROLES and code.vat_rubriek is not None:
            return code.vat_rubriek, Kind.VAT, credit - debit
    return None


def build_return(rows: list[Row], start: date, end: date) -> VatReturn:
    result = VatReturn(
        start,
        end,
        [RubriekLine(r, d, has_base=b, has_vat=v, verify=r in VERIFY) for r, d, b, v in RUBRIEKEN],
    )
    by_rubriek = {line.rubriek: line for line in result.lines}
    for row in rows:
        hit = contribution(
            row.vat_code, row.vat_role, row.system_role, row.debit_cents, row.credit_cents
        )
        if hit is None:
            continue
        bucket, kind, cents = hit
        if bucket == "5b":
            result.deductible_cents += cents
        elif kind is Kind.BASE:
            by_rubriek[bucket].base_cents += cents
        else:
            by_rubriek[bucket].vat_cents += cents
    result.owed_cents = sum(line.vat_cents for line in result.lines)
    return result


def tagged_lines(session: Session, administration_id: int, start: date, end: date):
    """(line, entry, account) for every VAT-tagged line dated within [start, end]."""
    return session.execute(
        select(JournalLine, JournalEntry, Account)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(
            JournalEntry.administration_id == administration_id,
            JournalEntry.entry_date >= start,
            JournalEntry.entry_date <= end,
            JournalLine.vat_code.is_not(None),
        )
        .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id)
    ).all()


def compute_vat_return(
    session: Session, administration_id: int, start: date, end: date
) -> VatReturn:
    totals: dict[tuple[str, str, str | None], list[int]] = {}
    for line, _entry, account in tagged_lines(session, administration_id, start, end):
        if line.vat_role is None:
            continue
        key = (line.vat_code, line.vat_role, account.system_role)
        bucket = totals.setdefault(key, [0, 0])
        bucket[0] += line.debit_cents
        bucket[1] += line.credit_cents
    rows = [Row(code, role, sys, d, c) for (code, role, sys), (d, c) in totals.items()]
    return build_return(rows, start, end)


def lines_for_bucket(
    session: Session, administration_id: int, start: date, end: date, bucket: str
) -> list[tuple[JournalLine, JournalEntry, Account, int]]:
    """The journal lines behind one rubriek (or '5b'), each with its contribution in cents."""
    found = []
    for line, entry, account in tagged_lines(session, administration_id, start, end):
        if line.vat_role is None:
            continue
        hit = contribution(
            line.vat_code, line.vat_role, account.system_role, line.debit_cents, line.credit_cents
        )
        if hit and hit[0] == bucket:
            found.append((line, entry, account, hit[2]))
    return found


def period_bounds(kind: str, year: int, number: int = 1) -> tuple[date, date, str]:
    """Start, end and label for a month (1-12), quarter (1-4) or year."""
    if kind == "month" and 1 <= number <= 12:
        start = date(year, number, 1)
        end = date(year + (number == 12), number % 12 + 1, 1)
        return start, _previous_day(end), f"{year}-{number:02d}"
    if kind == "quarter" and 1 <= number <= 4:
        first = 3 * (number - 1) + 1
        start = date(year, first, 1)
        end = date(year + (first == 10), (first + 3 - 1) % 12 + 1, 1)
        return start, _previous_day(end), f"{year}-Q{number}"
    if kind == "year":
        return date(year, 1, 1), date(year, 12, 31), str(year)
    raise ValueError("invalid period")


def _previous_day(d: date) -> date:
    return date.fromordinal(d.toordinal() - 1)
