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
SCORE_DATE_NEAR = 10
SCORE_DATE_CLOSE = 5
PENALTY_BEFORE_INVOICE = 40
NAME_THRESHOLD = 0.6
RECOMMEND_MARGIN = 10
DATE_NEAR_DAYS = 4
DATE_CLOSE_DAYS = 14
# Banks book a few days after the date printed on an invoice, never before it.
INVOICE_DATE_SLACK_DAYS = 4


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
    invoice_date: date | None = None


@dataclass
class Suggestion:
    candidate: Candidate
    score: int
    reasons: list[str] = field(default_factory=list)
    exact_amount: bool = False
    number_hit: bool = False
    # The payment predates the invoice: a same-amount hit is a coincidence until the owner says so.
    before_invoice: bool = False
    gap: int = 10**6
    # Safe to pre-select: nothing else fits as well and the dates are plausible.
    recommended: bool = False

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


def _date_gap(booking_date: date, cand: Candidate) -> int | None:
    reference = cand.due_date or cand.invoice_date
    return abs((booking_date - reference).days) if reference else None


def score_candidates(
    amount_cents: int,
    description: str | None,
    counterparty_iban: str | None,
    counterparty_name: str | None,
    candidates: list[Candidate],
    booking_date: date | None = None,
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
        if booking_date is not None and sug.score:
            _score_dates(sug, booking_date)
        suggestions.append(sug)
    suggestions.sort(key=lambda s: (-s.score, s.gap, s.candidate.invoice_id))
    return suggestions


def _score_dates(sug: Suggestion, booking_date: date) -> None:
    cand = sug.candidate
    gap = _date_gap(booking_date, cand)
    sug.gap = gap if gap is not None else 10**6
    if cand.invoice_date and (cand.invoice_date - booking_date).days > INVOICE_DATE_SLACK_DAYS:
        sug.before_invoice = True
        sug.score -= PENALTY_BEFORE_INVOICE
        sug.reasons.append(f"invoice dated {cand.invoice_date.isoformat()}, after this payment")
    elif gap is not None and gap <= DATE_NEAR_DAYS:
        sug.score += SCORE_DATE_NEAR
        sug.reasons.append("paid around the due date")
    elif gap is not None and gap <= DATE_CLOSE_DAYS:
        sug.score += SCORE_DATE_CLOSE
        sug.reasons.append("paid within two weeks of the due date")


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
                inv.issue_date,
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
                inv.invoice_date,
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
        tx.amount_cents, tx.description, tx.counterparty_iban, tx.counterparty_name, candidates,
        tx.booking_date,
    )
    _recommend(ranked)
    return ranked if include_all else [s for s in ranked if s.score > 0]


def _recommend(ranked: list[Suggestion]) -> None:
    """Pre-select the top suggestion only when it clearly beats the runner-up."""
    live = [s for s in ranked if s.score > 0]
    if not live or live[0].before_invoice:
        return
    if len(live) == 1 or live[0].score - live[1].score >= RECOMMEND_MARGIN:
        live[0].recommended = True


PAYOUT_WINDOW_DAYS = 21


@dataclass(frozen=True)
class PayoutHint:
    document_id: int
    label: str
    clearing_account_id: int


def platform_payout_hints(session: Session, tx: BankTransaction) -> list[PayoutHint]:
    """Booked delivery-platform statements whose payout equals this bank line."""
    from ledgerline.inbox.platform_booking import clearing_account, statement_of
    from ledgerline.models import InboxDocument, InboxStatus

    admin_id = session.get(BankAccount, tx.bank_account_id).administration_id
    hints = []
    for doc in session.scalars(select(InboxDocument).where(
            InboxDocument.administration_id == admin_id,
            InboxDocument.status == InboxStatus.BOOKED,
            InboxDocument.purchase_invoice_id.is_(None))):
        st = statement_of(doc)
        if st is None or st.payout_cents != tx.amount_cents:
            continue
        days = (tx.booking_date - st.period_end).days
        if 0 <= days <= PAYOUT_WINDOW_DAYS:
            hints.append(PayoutHint(doc.id, st.label,
                                    clearing_account(session, admin_id, st.platform).id))
    return hints
