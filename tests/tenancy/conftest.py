from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select, text

from ledgerline import demo
from ledgerline.inbox import service as inbox
from ledgerline.mobile import tokens
from ledgerline.models import (
    Account,
    ApiToken,
    BankAccount,
    BankTransaction,
    Contact,
    FeedbackEntry,
    InboxDocument,
    JournalEntry,
    Membership,
    PurchaseInvoice,
    SalesInvoice,
    User,
)
from tests.conftest import tenant_db
from tests.inbox.factories import ubl

SHARED_TABLES = {"users", "tenants", "memberships", "alembic_version", "auth_tokens",
                 "user_totp", "recovery_codes"}


def seed_tenant(tenant_id: int, owner_id: int | None = None) -> dict:
    """Fill a tenant with demo books, an inbox document and a phone token; return sample ids."""
    with tenant_db(tenant_id) as s:
        books = demo.seed(s)
        user_id = owner_id or s.scalar(select(Membership.user_id).where(
            Membership.tenant_id == tenant_id))
        inbox.receive(s, books.id, "bill.xml", ubl([("Hosting", 1000, "S", 21)]), "upload")
        s.add(FeedbackEntry(administration_id=books.id, user_id=user_id,
                            body=f"seed feedback of tenant {tenant_id}"))
        _, secret = tokens.create_token(s, s.get(User, user_id), books.id, "Phone")
        s.commit()
        first = {m: s.scalar(select(m.id).where(m.administration_id == books.id).order_by(m.id))
                 for m in (Account, BankAccount, Contact, InboxDocument, JournalEntry,
                           PurchaseInvoice, SalesInvoice, ApiToken)}
        first[BankTransaction] = s.scalar(select(BankTransaction.id).where(
            BankTransaction.bank_account_id == first[BankAccount]).order_by(BankTransaction.id))
        first["user"] = user_id
        return {"ids": first, "token": secret, "administration_id": books.id}


@pytest.fixture
def seeded_a(tenant, owner):
    return seed_tenant(tenant.id, owner.id)


@pytest.fixture
def seeded_b(other_tenant, other_owner):
    return seed_tenant(other_tenant.id, other_owner.id)


@contextmanager
def app_connection(database_url):
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def scoped_tables(owner_engine) -> list[str]:
    with owner_engine.connect() as conn:
        names = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )).scalars().all()
    return [n for n in names if n not in SHARED_TABLES]
