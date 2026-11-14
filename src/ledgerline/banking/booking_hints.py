"""Proposals for bank lines without a document: repeat an earlier booking, or a known pattern.

Nothing is booked from here; the owner confirms each proposal on the reconcile screen.
"""
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import (
    Account,
    BankAccount,
    BankTransaction,
    JournalEntry,
    SystemRole,
    TxStatus,
)

CARD_WORDS = re.compile(
    r"settlement|card sales|pin(?:betaling|transactie)|adyen|worldline|ccv|payplaza|"
    r"mollie|stripe|teya|sumup|nets\b|paynl|pay\.nl", re.IGNORECASE)
CASH_WORDS = re.compile(
    r"cash deposit|contant|storting|stortings?automaat|geldmaat|dagstorting", re.IGNORECASE)
BANK_COST_WORDS = re.compile(
    r"kosten (?:zakelijke )?rekening|bankkosten|bank charges|rekeningkosten", re.IGNORECASE)


@dataclass(frozen=True)
class BookingHint:
    account_id: int
    label: str
    vat_code: str | None
    reason: str


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _earlier_booking(
    session: Session, tx: BankTransaction, bank: BankAccount
) -> BookingHint | None:
    iban, name = _norm(tx.counterparty_iban), _norm(tx.counterparty_name)
    if not (iban or name):
        return None
    candidates = session.scalars(
        select(BankTransaction).where(
            BankTransaction.bank_account_id == tx.bank_account_id,
            BankTransaction.status == TxStatus.MATCHED,
            BankTransaction.journal_entry_id.is_not(None),
            BankTransaction.id != tx.id,
        ).order_by(BankTransaction.booking_date.desc(), BankTransaction.id.desc())
    )
    for other in candidates:
        same = (_norm(other.counterparty_iban) == iban) if iban else (
            not other.counterparty_iban and _norm(other.counterparty_name) == name)
        if not same or (other.amount_cents > 0) != (tx.amount_cents > 0):
            continue
        entry = session.get(JournalEntry, other.journal_entry_id)
        if entry is None or entry.source_type != "bank_transaction":
            continue
        line = next((ln for ln in entry.lines
                     if ln.account_id != bank.ledger_account_id and ln.vat_role != "vat"), None)
        account = session.get(Account, line.account_id) if line else None
        if account is None or not account.is_active:
            continue
        return BookingHint(account.id, f"{account.code} {account.name}", line.vat_code,
                           "booked this way before for the same counterparty")
    return None


def _role_account(session: Session, admin_id: int, role: SystemRole) -> Account | None:
    return session.scalar(select(Account).where(
        Account.administration_id == admin_id, Account.system_role == role,
        Account.is_active.is_(True)))


def booking_hint(session: Session, tx: BankTransaction) -> BookingHint | None:
    bank = session.get(BankAccount, tx.bank_account_id)
    hint = _earlier_booking(session, tx, bank)
    if hint:
        return hint
    text = f"{tx.counterparty_name or ''} {tx.description or ''}"
    if tx.amount_cents > 0:
        patterns = (
            (CARD_WORDS, SystemRole.CARD_CLEARING, "looks like a card settlement", None),
            (CASH_WORDS, SystemRole.CASH, "looks like a cash deposit", None),
        )
    else:
        patterns = ((BANK_COST_WORDS, SystemRole.BANK_COSTS, "looks like bank charges", "I0"),)
    for words, role, why, vat_code in patterns:
        if words.search(text):
            account = _role_account(session, bank.administration_id, role)
            if account:
                return BookingHint(account.id, f"{account.code} {account.name}", vat_code, why)
    return None
