from datetime import date
from pathlib import Path

import pytest

from ledgerline.banking import service
from ledgerline.models import InvoiceStatus, PurchaseInvoice, SalesInvoice

FIXTURES = Path(__file__).parent / "fixtures"
IBAN = "NL91ABNA0417164300"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def bank(session, admin):
    account = service.create_bank_account(session, admin, IBAN, "Zakelijk")
    session.commit()
    return account


def sales_invoice(
    session, admin, contact, number, total_cents, paid_cents=0, due=date(2026, 6, 30)
):
    invoice = SalesInvoice(
        administration_id=admin.id,
        contact_id=contact.id,
        number=number,
        status=InvoiceStatus.OPEN,
        issue_date=date(2026, 6, 1),
        due_date=due,
        total_excl_cents=total_cents,
        total_vat_cents=0,
        total_incl_cents=total_cents,
        paid_cents=paid_cents,
    )
    session.add(invoice)
    session.flush()
    return invoice


def purchase_invoice(session, admin, contact, reference, total_cents, due=date(2026, 6, 30)):
    invoice = PurchaseInvoice(
        administration_id=admin.id,
        contact_id=contact.id,
        supplier_reference=reference,
        status=InvoiceStatus.OPEN,
        invoice_date=date(2026, 6, 1),
        due_date=due,
        total_excl_cents=total_cents,
        total_vat_cents=0,
        total_incl_cents=total_cents,
    )
    session.add(invoice)
    session.flush()
    return invoice
