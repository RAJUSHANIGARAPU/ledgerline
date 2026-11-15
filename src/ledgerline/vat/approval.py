"""Approval of a VAT return: the figures an owner confirmed, compared with today's figures."""
import hashlib
import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import VatApproval
from ledgerline.vat.vat_return import VatReturn


def snapshot(result: VatReturn) -> dict[str, list[int]]:
    """Every rubriek as [base, vat] in cents, plus the totals 5a, 5b and the balance."""
    figures = {line.rubriek: [line.base_cents, line.vat_cents] for line in result.lines}
    figures["5a"] = [0, result.owed_cents]
    figures["5b"] = [0, result.deductible_cents]
    figures["balance"] = [0, result.balance_cents]
    return figures


def rubrieken(result: VatReturn) -> list[str]:
    """What the approver must tick: each rubriek line and the totals 5a and 5b."""
    return [*(line.rubriek for line in result.lines), "5a", "5b"]


def digest(figures: dict) -> str:
    return hashlib.sha256(json.dumps(figures, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ApprovalState:
    approval: VatApproval | None
    current: bool  # the approved figures equal the figures now


def approval_state(session: Session, administration_id: int, start: date, end: date,
                   result: VatReturn) -> ApprovalState:
    approval = session.scalar(select(VatApproval).where(
        VatApproval.administration_id == administration_id,
        VatApproval.period_start == start, VatApproval.period_end == end))
    if approval is None:
        return ApprovalState(None, False)
    return ApprovalState(approval, digest(approval.figures) == digest(snapshot(result)))
