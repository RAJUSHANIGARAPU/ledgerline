"""Intra-community supplies (opgaaf ICP): base amounts per customer VAT number."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import Contact
from ledgerline.vat.codes import VAT_CODES
from ledgerline.vat.vat_return import tagged_lines


@dataclass
class IcpRow:
    contact_id: int | None
    name: str
    vat_number: str
    amount_cents: int = 0


def icp_overview(session: Session, administration_id: int, start: date, end: date) -> list[IcpRow]:
    rows: dict[int | None, IcpRow] = {}
    contacts: dict[int, Contact] = {}
    for line, entry, _account in tagged_lines(session, administration_id, start, end):
        code = VAT_CODES.get(line.vat_code)
        if code is None or not code.icp or line.vat_role != "base":
            continue
        # Revenue lines may not carry the customer; the debtor line of the same entry does.
        contact_id = line.contact_id or next(
            (other.contact_id for other in entry.lines if other.contact_id), None
        )
        if contact_id is not None and contact_id not in contacts:
            contact = session.scalar(
                select(Contact).where(
                    Contact.id == contact_id, Contact.administration_id == administration_id
                )
            )
            if contact is None:
                contact_id = None
            else:
                contacts[contact_id] = contact
        row = rows.get(contact_id)
        if row is None:
            contact = contacts.get(contact_id) if contact_id is not None else None
            row = rows[contact_id] = IcpRow(
                contact_id,
                contact.name if contact else "Unknown customer",
                (contact.vat_number or "") if contact else "",
            )
        row.amount_cents += line.credit_cents - line.debit_cents
    return sorted(rows.values(), key=lambda r: (r.vat_number == "", r.name.lower()))
