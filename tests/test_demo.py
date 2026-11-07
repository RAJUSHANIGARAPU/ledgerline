from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline import demo
from ledgerline.ledger.service import account_balances
from ledgerline.models import BankTransaction, InvoiceStatus, SalesInvoice, TxStatus


def test_demo_seed_produces_balanced_books_with_work_left(session):
    admin = demo.seed(session, today=date(2026, 11, 7))
    session.commit()

    balances = account_balances(session, admin.id)
    assert sum(b.debit_cents for b in balances) == sum(b.credit_cents for b in balances)

    def count(model, *where):
        return session.scalar(select(func.count()).select_from(model).where(*where))

    assert count(SalesInvoice, SalesInvoice.status == InvoiceStatus.PAID) >= 15
    assert count(SalesInvoice, SalesInvoice.status == InvoiceStatus.OPEN) >= 2
    assert count(SalesInvoice, SalesInvoice.status == InvoiceStatus.DRAFT) == 1
    assert count(BankTransaction, BankTransaction.status == TxStatus.UNMATCHED) == 2

    with pytest.raises(demo.DemoExists):
        demo.seed(session)
