"""Setup wizard state, derived from the data (no stored progress flag), and its opening write."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.ledger.service import LedgerError, LineSpec, post_entry
from ledgerline.models import (
    Account,
    AccountType,
    Administration,
    ApiToken,
    BankAccount,
    Contact,
    ContactKind,
    InboxDocument,
    JournalEntry,
    PurchaseInvoice,
    SalesInvoice,
)

OPENING_SOURCE = "opening_balance"
EQUITY_CODE = "0500"
BUSINESS_FIELDS = ("kvk_number", "vat_number", "iban", "address_line", "postcode", "city")


class OnboardingError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    key: str
    number: int
    title: str
    required: bool
    done: bool | None  # None = informational, nothing in the data can prove it


STEP_TITLES = [
    ("business", "Business details", True),
    ("vat", "VAT situation", True),
    ("bank", "Bank account", True),
    ("opening", "Opening balances", False),
    ("opening-items", "Open invoices", False),
    ("first-invoice", "First customer and invoice", False),
    ("inbox", "Inbox and phone", False),
    ("review", "Accountant review and exports", False),
]
STEP_KEYS = [key for key, _, _ in STEP_TITLES]


def _exists(session: Session, query) -> bool:
    return session.scalar(query.limit(1)) is not None


def opening_entry(session: Session, admin_id: int) -> JournalEntry | None:
    """The opening-balance entry that is still in force (not reversed), if any."""
    reversed_ids = select(JournalEntry.reversal_of_id).where(
        JournalEntry.reversal_of_id.is_not(None))
    return session.scalar(
        select(JournalEntry)
        .where(JournalEntry.administration_id == admin_id,
               JournalEntry.source_type == OPENING_SOURCE,
               JournalEntry.id.not_in(reversed_ids))
        .order_by(JournalEntry.id)
    )


def steps(session: Session, admin: Administration) -> list[Step]:
    aid = admin.id
    done = {
        "business": all((getattr(admin, f) or "").strip() for f in BUSINESS_FIELDS),
        "vat": bool(admin.kor or (admin.vat_number or "").strip()),
        "bank": _exists(session, select(BankAccount.id).where(
            BankAccount.administration_id == aid)),
        "opening": opening_entry(session, aid) is not None,
        "opening-items": _exists(session, select(SalesInvoice.id).where(
            SalesInvoice.administration_id == aid, SalesInvoice.is_opening.is_(True))) or _exists(
            session, select(PurchaseInvoice.id).where(
                PurchaseInvoice.administration_id == aid, PurchaseInvoice.is_opening.is_(True))),
        "first-invoice": _exists(session, select(SalesInvoice.id).where(
            SalesInvoice.administration_id == aid, SalesInvoice.is_opening.is_(False))),
        "inbox": _exists(session, select(InboxDocument.id).where(
            InboxDocument.administration_id == aid)) or _exists(
            session, select(ApiToken.id).where(ApiToken.administration_id == aid,
                                               ApiToken.revoked_at.is_(None))),
        "review": None,
    }
    return [Step(key, i, title, required, done[key])
            for i, (key, title, required) in enumerate(STEP_TITLES, 1)]


def setup_complete(session: Session, admin: Administration) -> bool:
    return all(s.done for s in steps(session, admin) if s.required)


def next_step(all_steps: list[Step], after: str | None = None) -> Step:
    """First unfinished step after `after`, else the first unfinished one, else the last."""
    start = STEP_KEYS.index(after) + 1 if after in STEP_KEYS else 0
    for s in [*all_steps[start:], *all_steps[:start]]:
        if s.done is False:
            return s
    return all_steps[-1]


def _equity_account(session: Session, admin_id: int) -> Account:
    account = session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.code == EQUITY_CODE,
        Account.is_active.is_(True)))
    if account is None:
        account = session.scalar(select(Account).where(
            Account.administration_id == admin_id, Account.type == AccountType.EQUITY,
            Account.system_role.is_(None), Account.is_active.is_(True)).order_by(Account.code))
    if account is None:
        raise OnboardingError("No equity account found in the chart of accounts.")
    return account


def post_opening_balances(session: Session, admin: Administration, entry_date: date,
                          balances: dict[int, int], user_id: int | None) -> JournalEntry:
    """One balanced entry: bank accounts against owner's equity. Refuses a second one."""
    existing = opening_entry(session, admin.id)
    if existing is not None:
        raise OnboardingError(
            f"Opening balances were already posted in {existing.number}. "
            "Reverse that entry first to enter them again.")
    item_date = session.scalar(select(func.min(JournalEntry.entry_date)).where(
        JournalEntry.administration_id == admin.id, JournalEntry.source_type == "opening_item"))
    if item_date is not None and item_date != entry_date:
        raise OnboardingError(
            f"Open invoices are already dated {item_date.isoformat()}; use the same start date.")
    banks = {b.id: b for b in session.scalars(
        select(BankAccount).where(BankAccount.administration_id == admin.id))}
    lines: list[LineSpec] = []
    net = 0
    for bank_id, cents in balances.items():
        bank = banks.get(bank_id)
        if bank is None:
            raise OnboardingError("Unknown bank account.")
        if cents == 0:
            continue
        net += cents
        lines.append(LineSpec(bank.ledger_account_id, debit_cents=max(cents, 0),
                              credit_cents=max(-cents, 0),
                              description=f"Opening balance {bank.name}"))
    if not lines:
        raise OnboardingError("Enter a balance for at least one bank account.")
    if net:
        equity = _equity_account(session, admin.id)
        lines.append(LineSpec(equity.id, debit_cents=max(-net, 0), credit_cents=max(net, 0),
                              description="Opening balance counter-entry"))
    try:
        entry = post_entry(session, admin.id, entry_date, "Opening balances", lines,
                           source_type=OPENING_SOURCE, user_id=user_id)
    except LedgerError as exc:
        raise OnboardingError(str(exc)) from exc
    audit.record(session, "opening_balance.post", user_id=user_id, administration_id=admin.id,
                 entity_type="journal_entry", entity_id=entry.id,
                 details={"date": entry_date.isoformat(), "total_cents": net})
    return entry


def customer_count(session: Session, admin_id: int) -> int:
    return session.scalar(select(func.count()).select_from(Contact).where(
        Contact.administration_id == admin_id,
        Contact.kind.in_([ContactKind.CUSTOMER, ContactKind.BOTH]))) or 0


def contact_count(session: Session, admin_id: int) -> int:
    return session.scalar(select(func.count()).select_from(Contact).where(
        Contact.administration_id == admin_id)) or 0
