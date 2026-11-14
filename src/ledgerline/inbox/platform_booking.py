"""Proposing and booking the journal entry for a delivery-platform statement.

The statement is read from the document; the owner supplies what the document does not print
(the VAT split of sales, where tips and adjustments go). Nothing is booked until the owner
submits the review form. The payout leg is booked later from the bank screen, against the
platform's clearing account.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.inbox.extract.platform import THUISBEZORGD, UBER, PlatformStatement
from ledgerline.ledger.service import LedgerError, LineSpec, post_entry, system_account
from ledgerline.models import (
    Account,
    AccountType,
    Administration,
    InboxDocument,
    InboxStatus,
    JournalEntry,
    SystemRole,
    User,
)
from ledgerline.vat import codes
from ledgerline.vat.codes import Direction

SOURCE_TYPE = "platform_statement"
# Platform -> (system role, account code and name used when a book predates the role).
CLEARING = {
    UBER: (SystemRole.UBER_EATS_CLEARING, "1330", "Uber Eats to settle"),
    THUISBEZORGD: (SystemRole.THUISBEZORGD_CLEARING, "1340", "Thuisbezorgd to settle"),
}
FEES = (SystemRole.PLATFORM_FEES, "4610", "Delivery platform fees", "I21")
CASH_CODE = "1000"
FEE_VAT_CODES = {21: "I21", 9: "I9"}


class PlatformBookingError(ValueError):
    def __init__(self, *problems: str):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass
class StatementChoices:
    """What the document does not say. Sales amounts are gross, per VAT code."""

    sales_split: list[tuple[str, int]] = field(default_factory=list)
    tips_account_id: int | None = None
    adjustments_account_id: int | None = None
    accept_difference: bool = False
    # The owner's answer when the statement does not print the VAT rate on the fees.
    fee_vat_rate: int | None = None


def statement_of(document: InboxDocument) -> PlatformStatement | None:
    if not document.extraction or not document.extraction.get("platform_statement"):
        return None
    return PlatformStatement.from_json(document.extraction["platform_statement"])


def _account(session: Session, admin_id: int, code: str, name: str, kind: str,
             vat: str | None = None, role: SystemRole | None = None) -> Account:
    """The account with this role; a book that predates the role gets it created by code."""
    if role is not None:
        found = session.scalar(select(Account).where(
            Account.administration_id == admin_id, Account.system_role == role))
        if found is not None:
            return found
    account = session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.code == code))
    if account is None:
        account = Account(administration_id=admin_id, code=code, name=name, type=kind,
                          default_vat_code=vat, system_role=role)
        session.add(account)
        session.flush()
    elif role is not None:
        raise PlatformBookingError(
            f"account {code} is already in use; give {name} an account with the {role} role")
    return account


def clearing_account(session: Session, admin_id: int, platform: str) -> Account:
    role, code, name = CLEARING[platform]
    return _account(session, admin_id, code, name, AccountType.ASSET, role=role)


def _revenue_account(session: Session, admin_id: int, vat_code: str) -> Account:
    account = session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.type == AccountType.REVENUE,
        Account.default_vat_code == vat_code, Account.is_active.is_(True)
    ).order_by(Account.code))
    if account is None:
        raise PlatformBookingError(f"no revenue account is set up for VAT code {vat_code}")
    return account


def _owned(session: Session, admin_id: int, account_id: int | None, what: str) -> Account:
    account = session.get(Account, account_id) if account_id else None
    if account is None or account.administration_id != admin_id or not account.is_active:
        raise PlatformBookingError(f"choose an account for {what}")
    return account


def split_inclusive(gross_cents: int, rate: Decimal) -> int:
    """The VAT inside a VAT-inclusive amount, rounded half-up."""
    vat = Decimal(gross_cents) * rate / (Decimal(100) + rate)
    return int(vat.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fee_rate_needs_input(st: PlatformStatement) -> bool:
    """True when the fees carry VAT but the document does not print its rate."""
    return bool(st.fee_vat_cents) and not st.fee_vat_rate_printed


def fee_rate(st: PlatformStatement, choices: StatementChoices) -> int | None:
    """The printed rate, or the one the owner confirmed; never a silent default."""
    return choices.fee_vat_rate if fee_rate_needs_input(st) else st.fee_vat_rate_percent


def pending_decisions(st: PlatformStatement) -> list[str]:
    """What the owner must still decide; shown with the proposal."""
    items = ["Split sales by VAT rate: neither document prints the split."]
    if fee_rate_needs_input(st):
        items.append("Confirm the VAT rate on the fees: the document does not print it.")
    if st.tips_cents:
        items.append("Choose where tips are booked: the document does not say how they are taxed.")
    if st.adjustments_cents:
        items.append("Choose where promotions, refunds and corrections are booked.")
    return items


def _validate(st: PlatformStatement, choices: StatementChoices) -> None:
    problems = []
    if st.reverse_charge:
        problems.append("the document mentions reverse-charged VAT; book it by hand")
    if st.fee_vat_cents and fee_rate(st, choices) not in FEE_VAT_CODES:
        problems.append("choose the VAT rate on the fees (21% or 9%); the document does not "
                        "print it" if fee_rate_needs_input(st)
                        else "the VAT rate on the fees is unknown; book it by hand")
    if not st.arithmetic_ok and not choices.accept_difference:
        problems.append("the figures do not add up; tick that you checked the difference or "
                        "reject the document")
    total = sum(cents for _, cents in choices.sales_split)
    if total != st.gross_sales_cents:
        problems.append("the sales split must add up to the gross sales of the statement")
    for code, cents in choices.sales_split:
        try:
            vat_code = codes.get(code)
        except ValueError:
            problems.append(f"unknown VAT code {code}")
            continue
        if vat_code.direction != Direction.SALES or vat_code.reverse_charge or cents < 0:
            problems.append(f"VAT code {code} cannot be used for these sales")
    if problems:
        raise PlatformBookingError(*problems)


def build_lines(session: Session, admin: Administration, st: PlatformStatement,
                choices: StatementChoices) -> list[LineSpec]:
    """Debits positive. The clearing account carries the printed payout; any difference between
    the components and the payout goes to suspense, so the entry always balances."""
    _validate(st, choices)
    aid = admin.id
    rows: list[tuple[Account, int, str | None, str | None, str | None]] = []
    for code, gross in choices.sales_split:
        if not gross:
            continue
        rate = codes.get(code).rate
        vat = split_inclusive(gross, rate) if rate else 0
        rows.append((_revenue_account(session, aid, code), -(gross - vat), code, "base", None))
        if vat:
            role = SystemRole.VAT_PAYABLE_HIGH if rate >= 15 else SystemRole.VAT_PAYABLE_LOW
            rows.append((system_account(session, aid, role), -vat, code, "vat", None))
    if st.tips_cents:
        tips = _owned(session, aid, choices.tips_account_id, "tips")
        rows.append((tips, -st.tips_cents, None, None, "Tips"))
    if st.cash_collected_cents:
        cash = _account(session, aid, CASH_CODE, "Cash", AccountType.ASSET)
        rows.append((cash, st.cash_collected_cents, None, None, "Cash collected by the restaurant"))
    fee_code = FEE_VAT_CODES.get(fee_rate(st, choices) or 0) if st.fee_vat_cents else "I0"
    fees = _account(session, aid, FEES[1], FEES[2], AccountType.EXPENSE, FEES[3], FEES[0])
    rows.append((fees, st.fees_cents, fee_code, "base", f"{st.name} fees"))
    if st.fee_vat_cents:
        rows.append((system_account(session, aid, SystemRole.VAT_RECEIVABLE), st.fee_vat_cents,
                     fee_code, "vat", f"VAT on {st.name} fees"))
    if st.adjustments_cents:
        target = _owned(session, aid, choices.adjustments_account_id, "adjustments")
        rows.append((target, -st.adjustments_cents, None, None, "Promotions and corrections"))
    clearing = clearing_account(session, aid, st.platform)
    rows.append((clearing, st.payout_cents, None, None, f"{st.name} payout"))
    imbalance = sum(cents for _, cents, *_ in rows)
    if imbalance:
        suspense = system_account(session, aid, SystemRole.SUSPENSE)
        rows.append((suspense, -imbalance, None, None, "Difference to check"))
    return [
        LineSpec(
            account.id, debit_cents=max(cents, 0), credit_cents=max(-cents, 0),
            description=text, vat_code=code, vat_role=role,
        )
        for account, cents, code, role, text in rows if cents
    ]


def existing_entry(session: Session, admin_id: int, st: PlatformStatement) -> JournalEntry | None:
    return session.scalar(select(JournalEntry).where(
        JournalEntry.administration_id == admin_id, JournalEntry.source_type == SOURCE_TYPE,
        JournalEntry.description == st.label, JournalEntry.reversal_of_id.is_(None),
    ))


def book_statement(session: Session, admin: Administration, user: User, document: InboxDocument,
                   choices: StatementChoices) -> JournalEntry:
    """Post the statement entry. The caller commits."""
    st = statement_of(document)
    if st is None:
        raise PlatformBookingError("this document is not a platform statement")
    # Serialise on the statement period so a double submit books one entry, not two.
    session.execute(text("SELECT pg_advisory_xact_lock(:admin, hashtext(:label))"),
                    {"admin": admin.id, "label": st.label})
    session.refresh(document)
    if document.status == InboxStatus.BOOKED:
        raise PlatformBookingError("this statement is already booked")
    if existing_entry(session, admin.id, st):
        raise PlatformBookingError(f"{st.label} is already booked from another document")
    lines = build_lines(session, admin, st, choices)
    try:
        with session.begin_nested():
            entry = post_entry(session, admin.id, st.period_end, st.label, lines,
                               source_type=SOURCE_TYPE, source_id=document.id, user_id=user.id)
    except LedgerError as exc:
        raise PlatformBookingError(str(exc)) from exc
    document.status = InboxStatus.BOOKED
    document.reviewed_by_id = user.id
    audit.record(
        session, "inbox.book_platform_statement", user_id=user.id, administration_id=admin.id,
        entity_type="inbox_document", entity_id=document.id,
        details={"entry": entry.number, "platform": st.platform,
                 "payout_cents": st.payout_cents, "fee_vat_rate": fee_rate(st, choices)},
    )
    return entry
