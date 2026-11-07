"""SEPA credit transfer initiation (pain.001.001.03). Amounts come from integer cents."""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring

from schwifty import BIC, IBAN

from ledgerline.money import from_cents

NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
MAX_TRANSACTIONS = 99_999


class SepaError(ValueError):
    pass


@dataclass(frozen=True)
class SepaPayment:
    creditor_name: str
    creditor_iban: str
    amount_cents: int
    end_to_end_id: str
    remittance: str
    creditor_bic: str | None = None


def clean_text(value: str, limit: int) -> str:
    """Reduce to the SEPA Latin character set."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^A-Za-z0-9/\-?:().,'+ ]", " ", folded)
    return " ".join(folded.split())[:limit]


def _iban(value: str, label: str) -> str:
    try:
        return IBAN(value.replace(" ", "")).compact
    except ValueError as exc:
        raise SepaError(f"{label}: invalid IBAN {value!r}") from exc


def _bic(value: str | None, label: str) -> str | None:
    if not value:
        return None
    try:
        return BIC(value).compact
    except ValueError as exc:
        raise SepaError(f"{label}: invalid BIC {value!r}") from exc


def build_pain001(
    debtor_name: str,
    debtor_iban: str,
    payments: list[SepaPayment],
    execution_date: date,
    *,
    debtor_bic: str | None = None,
    message_id: str,
    created_at: datetime,
) -> bytes:
    if not payments:
        raise SepaError("no payments selected")
    if len(payments) > MAX_TRANSACTIONS:
        raise SepaError("too many payments for one file")
    dbtr_iban = _iban(debtor_iban, "debtor")
    dbtr_bic = _bic(debtor_bic, "debtor")
    total = 0
    for payment in payments:
        if payment.amount_cents <= 0:
            raise SepaError(f"{payment.end_to_end_id}: amount must be positive")
        total += payment.amount_cents
    ctrl_sum = str(from_cents(total))
    count = str(len(payments))
    name = clean_text(debtor_name, 70) or "Debtor"
    msg_id = clean_text(message_id, 35)

    register_namespace("", NAMESPACE)
    root = Element(f"{{{NAMESPACE}}}Document")
    init = SubElement(root, f"{{{NAMESPACE}}}CstmrCdtTrfInitn")

    def child(parent: Element, tag: str, text: str | None = None) -> Element:
        node = SubElement(parent, f"{{{NAMESPACE}}}{tag}")
        node.text = text
        return node

    header = child(init, "GrpHdr")
    child(header, "MsgId", msg_id)
    child(header, "CreDtTm", created_at.strftime("%Y-%m-%dT%H:%M:%S"))
    child(header, "NbOfTxs", count)
    child(header, "CtrlSum", ctrl_sum)
    child(child(header, "InitgPty"), "Nm", name)

    info = child(init, "PmtInf")
    child(info, "PmtInfId", clean_text(f"{msg_id}-1", 35))
    child(info, "PmtMtd", "TRF")
    child(info, "BtchBookg", "true")
    child(info, "NbOfTxs", count)
    child(info, "CtrlSum", ctrl_sum)
    child(child(child(info, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    child(info, "ReqdExctnDt", execution_date.isoformat())
    child(child(info, "Dbtr"), "Nm", name)
    child(child(child(info, "DbtrAcct"), "Id"), "IBAN", dbtr_iban)
    agent = child(child(info, "DbtrAgt"), "FinInstnId")
    if dbtr_bic:
        child(agent, "BIC", dbtr_bic)
    else:
        child(child(agent, "Othr"), "Id", "NOTPROVIDED")
    child(info, "ChrgBr", "SLEV")

    for payment in payments:
        tx = child(info, "CdtTrfTxInf")
        end_to_end = clean_text(payment.end_to_end_id, 35) or "NOTPROVIDED"
        child(child(tx, "PmtId"), "EndToEndId", end_to_end)
        amount = child(child(tx, "Amt"), "InstdAmt", str(from_cents(payment.amount_cents)))
        amount.set("Ccy", "EUR")
        bic = _bic(payment.creditor_bic, payment.creditor_name)
        if bic:
            child(child(child(tx, "CdtrAgt"), "FinInstnId"), "BIC", bic)
        child(child(tx, "Cdtr"), "Nm", clean_text(payment.creditor_name, 70) or "Creditor")
        creditor_iban = _iban(payment.creditor_iban, payment.creditor_name)
        child(child(child(tx, "CdtrAcct"), "Id"), "IBAN", creditor_iban)
        remittance = clean_text(payment.remittance, 140)
        if remittance:
            child(child(tx, "RmtInf"), "Ustrd", remittance)

    return tostring(root, encoding="utf-8", xml_declaration=True)

