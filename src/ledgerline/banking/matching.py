"""Rank open invoices against a bank transaction."""
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import (
    BankAccount,
    BankTransaction,
    InvoiceStatus,
    PurchaseInvoice,
    SalesInvoice,
)

SALES = "sales"
PURCHASE = "purchase"

SCORE_AMOUNT = 50
SCORE_NUMBER = 40
SCORE_IBAN = 25
SCORE_NAME = 15
NAME_THRESHOLD = 0.6


@dataclass(frozen=True)
class Candidate:
    kind: str
    invoice_id: int
    number: str
    contact_id: int
    contact_name: str
    contact_iban: str | None
    remaining_cents: int
    due_date: date | None


@dataclass
class Suggestion:
    candidate: Candidate
    score: int
    reasons: list[str] = field(default_factory=list)
    exact_amount: bool = False
    number_hit: bool = False

    @property
    def reason(self) -> str:
        return ", ".join(self.reasons)

    @property
    def amount_cents(self) -> int:
        return self.candidate.remaining_cents


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _words(text: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def number_in_text(number: str, text: str | None) -> bool:
    if not number or not text:
        return False
    needle = _norm(number)
    if len(needle) < 4:
        pattern = rf"(?<![a-z0-9]){re.escape(number.lower())}(?![a-z0-9])"
        return re.search(pattern, text.lower()) is not None
    return needle in _norm(text)


def name_similarity(a: str | None, b: str | None) -> float:
    left, right = _words(a), _words(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def score_candidates(
    amount_cents: int,
    description: str | None,
    counterparty_iban: str | None,
    counterparty_name: str | None,
    candidates: list[Candidate],
) -> list[Suggestion]:
    kind = SALES if amount_cents > 0 else PURCHASE
    paid = abs(amount_cents)
    text = f"{description or ''}"
    suggestions = []
    for cand in candidates:
        if cand.kind != kind or cand.remaining_cents <= 0:
            continue
        sug = Suggestion(cand, 0)
        if cand.remaining_cents == paid:
            sug.score += SCORE_AMOUNT
            sug.exact_amount = True
            sug.reasons.append("exact amount")
        if number_in_text(cand.number, text):
            sug.score += SCORE_NUMBER
            sug.number_hit = True
            sug.reasons.append(f"invoice number {cand.number} in description")
        same_iban = _norm(counterparty_iban) == _norm(cand.contact_iban)
        if counterparty_iban and cand.contact_iban and same_iban:
            sug.score += SCORE_IBAN
            sug.reasons.append("IBAN matches contact")
        similarity = name_similarity(counterparty_name, cand.contact_name)
        if similarity >= NAME_THRESHOLD:
            sug.score += round(SCORE_NAME * similarity)
            sug.reasons.append(f"name similar ({round(similarity * 100)}%)")
        if sug.score and not sug.exact_amount and paid < cand.remaining_cents:
            sug.reasons.append("partial payment")
        suggestions.append(sug)
    suggestions.sort(
        key=lambda s: (-s.score, s.candidate.due_date or date.max, s.candidate.invoice_id)
    )
    return suggestions


def load_candidates(session: Session, administration_id: int) -> list[Candidate]:
    result = []
    for inv in session.scalars(
        select(SalesInvoice).where(
            SalesInvoice.administration_id == administration_id,
            SalesInvoice.status == InvoiceStatus.OPEN,
        )
    ):
        result.append(
            Candidate(
                SALES, inv.id, inv.number or f"#{inv.id}", inv.contact_id, inv.contact.name,
                inv.contact.iban, inv.total_incl_cents - inv.paid_cents, inv.due_date,
            )
        )
    for inv in session.scalars(
        select(PurchaseInvoice).where(
            PurchaseInvoice.administration_id == administration_id,
            PurchaseInvoice.status == InvoiceStatus.OPEN,
        )
    ):
        result.append(
            Candidate(
                PURCHASE, inv.id, inv.supplier_reference, inv.contact_id, inv.contact.name,
                inv.contact.iban, inv.total_incl_cents - inv.paid_cents, inv.due_date,
            )
        )
    return result


def suggest(
    session: Session,
    tx: BankTransaction,
    candidates: list[Candidate] | None = None,
    *,
    include_all: bool = False,
) -> list[Suggestion]:
    if candidates is None:
        admin_id = session.get(BankAccount, tx.bank_account_id).administration_id
        candidates = load_candidates(session, admin_id)
    ranked = score_candidates(
        tx.amount_cents, tx.description, tx.counterparty_iban, tx.counterparty_name, candidates
    )
    return ranked if include_all else [s for s in ranked if s.score > 0]
