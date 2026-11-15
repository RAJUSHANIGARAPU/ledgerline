from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline.invoicing.service import (
    PurchaseLineInput,
    book_purchase_invoice,
    create_purchase_invoice,
)
from ledgerline.ledger.service import LineSpec, post_entry
from ledgerline.models import ContactKind, JournalEntry, VatApproval
from tests.helpers import account, contact
from tests.inbox.test_review_flow import upload_ubl

DOC_DATE = date(2026, 11, 3)


@pytest.fixture
def supplier(session, admin):
    c = contact(session, admin, name="Bakker Software", kind=ContactKind.SUPPLIER,
                vat_number="NL 8533.45678 B01", iban="NL02 ABNA 0123 4567 89")
    session.commit()
    return c


def book_history(session, admin, owner, supplier, code, count, cents=10000):
    expense = account(session, admin, "4300").id
    for n in range(count):
        invoice = create_purchase_invoice(
            session, admin, supplier, [PurchaseLineInput("Hosting", expense, cents, code)],
            supplier_reference=f"H-{code}-{n}", invoice_date=date(2026, 3, 1 + n))
        book_purchase_invoice(session, admin, invoice, owner.id)
    session.commit()


def entries(session):
    session.expire_all()
    return session.scalar(select(func.count()).select_from(JournalEntry))


def review_page(client, session, **kwargs):
    doc = upload_ubl(client, session, **kwargs)
    return client.get(f"/inbox/{doc.id}").text


def test_review_page_flags_unusual_vat_rate(client, session, admin, owner, supplier):
    book_history(session, admin, owner, supplier, "I9", 4)
    page = review_page(client, session)
    assert 'data-advice="vat_rate"' in page
    assert "usually booked with 9% VAT" in page and "says 21%" in page


def test_usual_rate_gets_no_flag(client, session, admin, owner, supplier):
    book_history(session, admin, owner, supplier, "I21", 4)
    assert "data-advice" not in review_page(client, session)


def test_amount_flag_on_review_page(client, session, admin, owner, supplier):
    book_history(session, admin, owner, supplier, "I21", 5, cents=1000)
    assert 'data-advice="amount"' in review_page(client, session)


def test_draft_history_is_not_counted(client, session, admin, owner, supplier):
    expense = account(session, admin, "4300").id
    for n in range(4):
        create_purchase_invoice(
            session, admin, supplier, [PurchaseLineInput("Hosting", expense, 10000, "I9")],
            supplier_reference=f"D-{n}", invoice_date=date(2026, 3, 1 + n))
    session.commit()
    assert "data-advice" not in review_page(client, session)


def test_locked_period_warning(client, session, admin, supplier):
    admin.locked_until = date(2026, 12, 31)
    session.commit()
    page = review_page(client, session)
    assert 'data-advice="period_locked"' in page and "closed up to 2026-12-31" in page


def test_approved_period_warning(client, session, admin, owner, supplier):
    session.add(VatApproval(administration_id=admin.id, period_start=date(2026, 10, 1),
                            period_end=date(2026, 12, 31), figures={}, approved_by_id=owner.id))
    session.commit()
    assert 'data-advice="period_approved"' in review_page(client, session)


def test_settled_period_warning(client, session, admin, supplier):
    post_entry(session, admin.id, date(2026, 12, 31), "VAT settlement", [
        LineSpec(account(session, admin, "1600").id, debit_cents=100),
        LineSpec(account(session, admin, "1400").id, credit_cents=100)],
        source_type="vat_settlement:2026-Q4")
    session.commit()
    page = review_page(client, session)
    assert 'data-advice="period_settled"' in page and "2026-Q4 is already settled" in page


def test_duplicate_invoice_is_still_surfaced_by_the_existing_check(
        client, session, admin, owner, supplier):
    expense = account(session, admin, "4300").id
    create_purchase_invoice(
        session, admin, supplier, [PurchaseLineInput("Hosting", expense, 10000, "I21")],
        supplier_reference="INV-2026-17", invoice_date=DOC_DATE)
    session.commit()
    assert "which is already registered" in review_page(client, session)


def test_advice_never_writes_to_the_ledger(client, session, admin, owner, supplier):
    book_history(session, admin, owner, supplier, "I9", 5, cents=1000)
    admin.locked_until = date(2026, 12, 31)
    session.commit()
    before = entries(session)
    page = review_page(client, session)
    assert page.count("data-advice") >= 3 and "Create purchase draft" in page
    assert entries(session) == before
