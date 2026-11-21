from datetime import date
from itertools import count

import pytest

from ledgerline.invoicing.service import (
    PurchaseLineInput,
    book_purchase_invoice,
    create_purchase_invoice,
    store_attachment,
)
from ledgerline.models import ContactKind, InboxDocument, InboxStatus
from tests.helpers import account, contact

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def supplier(session, admin):
    c = contact(session, admin, name="Leverancier BV", kind=ContactKind.SUPPLIER)
    session.commit()
    return c


@pytest.fixture
def make_bill(session, admin, owner, supplier):
    """Create a purchase bill through the real service; booked unless book=False.

    lines: (amount_excl_cents, account_code) pairs; VAT follows the account's default code.
    """
    serial = count(1)

    def _make(lines=((10000, "4300"),), *, on=date(2026, 3, 2), who=None, adm=None, book=True,
              paid=0, reference=None, attachment=None):
        adm = adm or admin
        inputs = [PurchaseLineInput("Item", account(session, adm, code).id, cents, None, None)
                  for cents, code in lines]
        invoice = create_purchase_invoice(
            session, adm, who or supplier, inputs,
            supplier_reference=reference or f"F-{next(serial)}", invoice_date=on,
            attachment=attachment,
        )
        if book:
            book_purchase_invoice(session, adm, invoice, owner.id)
        invoice.paid_cents = paid
        session.commit()
        return invoice

    return _make


@pytest.fixture
def make_inbox_doc(session, admin):
    serial = count(1)

    def _make(*, source="email", status=InboxStatus.REVIEW, extraction=None, invoice=None,
              adm=None):
        adm = adm or admin
        n = next(serial)
        attachment = store_attachment(session, adm, f"doc{n}.png", PNG + str(n).encode())
        doc = InboxDocument(
            administration_id=adm.id, source=source, status=status, attachment_id=attachment.id,
            content_sha256=attachment.sha256, extraction=extraction,
            purchase_invoice_id=invoice.id if invoice else None,
        )
        session.add(doc)
        session.commit()
        return doc

    return _make
