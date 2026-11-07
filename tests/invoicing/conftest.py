from datetime import date
from decimal import Decimal

import pytest

from ledgerline.invoicing.service import SalesLineInput, create_sales_invoice
from ledgerline.models import ContactKind
from tests.helpers import account, contact

ISSUE = date(2026, 3, 1)


@pytest.fixture
def customer(session, admin):
    c = contact(
        session, admin, name="Klant BV", kind=ContactKind.CUSTOMER,
        address_line="Klantweg 5", postcode="3511 AA", city="Utrecht",
        vat_number="NL999999999B01",
    )
    session.commit()
    return c


@pytest.fixture
def supplier(session, admin):
    c = contact(
        session, admin, name="Leverancier BV", kind=ContactKind.SUPPLIER,
        address_line="Havenstraat 9", postcode="3011 AB", city="Rotterdam",
        vat_number="NL888888888B01",
    )
    session.commit()
    return c


@pytest.fixture
def make_invoice(session, admin, customer):
    def _make(lines=None, issue_date=ISSUE, contact_=None, **kwargs):
        lines = lines or [
            SalesLineInput(
                "Consulting", Decimal("1"), 10000, "V21", account(session, admin, "8000").id
            )
        ]
        invoice = create_sales_invoice(
            session, admin, contact_ or customer, lines, issue_date=issue_date, **kwargs
        )
        session.commit()
        return invoice

    return _make


def sline(session, admin, description, qty, price_cents, code, account_code="8000"):
    return SalesLineInput(
        description, Decimal(qty), price_cents, code, account(session, admin, account_code).id
    )
