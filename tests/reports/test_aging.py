from datetime import date

import pytest

from ledgerline.models import ContactKind, InvoiceStatus, PurchaseInvoice, SalesInvoice
from ledgerline.reports.aging import BUCKETS, aging, bucket_index, totals
from tests.helpers import contact

AS_OF = date(2026, 6, 30)


@pytest.mark.parametrize(("due", "bucket"), [
    (date(2026, 7, 1), 0), (date(2026, 6, 30), 0),
    (date(2026, 6, 29), 1), (date(2026, 5, 31), 1),
    (date(2026, 5, 30), 2), (date(2026, 5, 1), 2),
    (date(2026, 4, 30), 3), (date(2026, 4, 1), 3),
    (date(2026, 3, 31), 4), (date(2025, 1, 1), 4),
])
def test_bucket_edges(due, bucket):
    assert bucket_index(due, AS_OF) == bucket


def _sale(session, admin, customer, due, total, paid=0, status=InvoiceStatus.OPEN):
    session.add(SalesInvoice(administration_id=admin.id, contact_id=customer.id, number=None,
                             status=status, issue_date=date(2026, 1, 1), due_date=due,
                             total_incl_cents=total, paid_cents=paid))


def test_receivables_are_bucketed_per_customer(session, admin):
    a = contact(session, admin, name="Alpha")
    b = contact(session, admin, name="Bravo")
    _sale(session, admin, a, date(2026, 7, 15), 1000)                  # not yet due
    _sale(session, admin, a, date(2026, 6, 10), 2000, paid=500)       # 20 days, 1500 left
    _sale(session, admin, a, date(2026, 5, 15), 3000)                  # 46 days
    _sale(session, admin, b, date(2026, 4, 15), 4000)                  # 76 days
    _sale(session, admin, b, date(2026, 1, 15), 5000)                  # 166 days
    _sale(session, admin, b, date(2026, 1, 15), 9999, status=InvoiceStatus.PAID)
    _sale(session, admin, b, date(2026, 1, 15), 800, paid=800)         # fully paid, still open
    _sale(session, admin, b, date(2026, 1, 15), 7777, status=InvoiceStatus.DRAFT)
    session.commit()
    rows = aging(session, admin.id, "receivables", AS_OF)
    assert [(r.name, r.buckets, r.total_cents) for r in rows] == [
        ("Alpha", [1000, 1500, 3000, 0, 0], 5500),
        ("Bravo", [0, 0, 0, 4000, 5000], 9000),
    ]
    assert totals(rows) == [1000, 1500, 3000, 4000, 5000]
    assert len(BUCKETS) == 5


def test_payables_use_purchase_invoices_and_stay_per_administration(session, admin):
    from ledgerline.ledger.chart import create_administration
    supplier = contact(session, admin, name="Leverancier", kind=ContactKind.SUPPLIER)
    session.add(PurchaseInvoice(administration_id=admin.id, contact_id=supplier.id,
                                supplier_reference="F-1", status=InvoiceStatus.OPEN,
                                invoice_date=date(2026, 3, 1), due_date=date(2026, 4, 1),
                                total_incl_cents=2420, paid_cents=420))
    other = create_administration(session, name="Ander BV")
    their = contact(session, other, name="Theirs", kind=ContactKind.SUPPLIER)
    session.add(PurchaseInvoice(administration_id=other.id, contact_id=their.id,
                                supplier_reference="F-9", status=InvoiceStatus.OPEN,
                                invoice_date=date(2026, 3, 1), due_date=date(2026, 4, 1),
                                total_incl_cents=999999, paid_cents=0))
    session.commit()
    rows = aging(session, admin.id, "payables", AS_OF)
    assert [(r.name, r.buckets) for r in rows] == [("Leverancier", [0, 0, 0, 2000, 0])]
    assert aging(session, admin.id, "receivables", AS_OF) == []
