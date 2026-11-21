"""The same supplier invoice arriving twice as different files (re-sent, re-encoded, photo)."""

import pytest
from sqlalchemy import select

from ledgerline.invoicing.service import find_duplicate_purchase
from ledgerline.models import ContactKind, InboxDocument, PurchaseInvoice
from tests.helpers import account, contact
from tests.inbox.factories import invoice_html, pdf_from_html
from tests.inbox.test_review_flow import create_data, upload

NUMBER = "DV-2026-0412"


@pytest.fixture
def supplier(session, admin):
    c = contact(session, admin, name="Drukkerij Voorbeeld B.V.", kind=ContactKind.SUPPLIER,
                vat_number="NL853345678B01")
    session.commit()
    return c


def two_copies(client, session, number="DV-2026-0412", second_number=None):
    pdf = pdf_from_html(invoice_html(supplier="Drukkerij Voorbeeld B.V.", number=number))
    other = pdf_from_html(invoice_html(
        supplier="Drukkerij Voorbeeld B.V.", number=second_number or number)) + b"\n%%padding\n"
    upload(client, session, ("a.pdf", pdf, "application/pdf"))
    upload(client, session, ("b.pdf", other, "application/pdf"))
    docs = session.scalars(select(InboxDocument).order_by(InboxDocument.id)).all()
    assert len(docs) == 2 and docs[0].content_sha256 != docs[1].content_sha256
    return docs


def create(client, session, admin, doc, supplier, **extra):
    expense = str(account(session, admin, "4300").id)
    data = create_data(
        client, doc, supplier.id, session, admin, supplier_reference=NUMBER,
        line_description=["Drukwerk"], line_account=[expense], line_amount=["120.00"],
        line_vat_code=["I21"], line_vat=["25.20"], **extra)
    return client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False)


def test_second_file_of_the_same_invoice_is_warned_and_refused(client, session, admin, supplier):
    first, second = two_copies(client, session)
    assert create(client, session, admin, first, supplier).status_code == 303
    session.expire_all()
    booked = session.scalars(select(PurchaseInvoice)).one()

    page = client.get(f"/inbox/{second.id}")
    assert "already registered" in page.text and f"/purchases/{booked.id}" in page.text
    assert "Same invoice: link to the existing one" in page.text

    refused = create(client, session, admin, second, supplier)
    assert refused.status_code == 422 and "already registered" in refused.text
    session.expire_all()
    assert len(session.scalars(select(PurchaseInvoice)).all()) == 1
    assert session.get(InboxDocument, second.id).purchase_invoice_id is None


def test_the_second_file_can_be_linked_to_the_first_purchase(client, session, admin, supplier):
    first, second = two_copies(client, session)
    create(client, session, admin, first, supplier)
    session.expire_all()
    booked = session.scalars(select(PurchaseInvoice)).one()
    r = create(client, session, admin, second, supplier, action="link")
    assert r.status_code == 303 and r.headers["location"] == f"/purchases/{booked.id}"
    session.expire_all()
    assert session.get(InboxDocument, second.id).purchase_invoice_id == booked.id


def test_a_differently_written_number_on_the_second_file_is_still_caught(
    client, session, admin, supplier
):
    first, second = two_copies(client, session, second_number="dv-2026-412")
    create(client, session, admin, first, supplier)
    session.expire_all()
    booked = session.scalars(select(PurchaseInvoice)).one()
    assert find_duplicate_purchase(session, admin, supplier.id, "dv 2026 412") == booked
    page = client.get(f"/inbox/{second.id}")
    assert "already registered" in page.text and f"/purchases/{booked.id}" in page.text


def test_same_number_from_another_supplier_is_not_flagged(client, session, admin, supplier):
    first, second = two_copies(client, session)
    other = contact(session, admin, name="Totaal Andere Drukker", kind=ContactKind.SUPPLIER,
                    vat_number="NL000000001B01")
    session.commit()
    create(client, session, admin, first, supplier)
    page = client.get(f"/inbox/{second.id}")
    assert "A second copy cannot be created" in page.text  # same supplier: flagged
    assert create(client, session, admin, second, other).status_code == 303
