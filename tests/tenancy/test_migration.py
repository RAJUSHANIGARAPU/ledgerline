"""Migration 0004 on a database that already holds data: up, down, up."""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


@pytest.fixture
def old_database(owner_url):
    name = "ledgerline_migration"
    admin = create_engine(owner_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(owner_url).set(database=name).render_as_string(hide_password=False)
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0003")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO users (email, password_hash, role, is_active)
            VALUES ('boss@example.com', 'x', 'owner', true),
                   ('acc@example.com', 'x', 'accountant', true);
            INSERT INTO administrations (name, legal_form, country, invoice_prefix,
                                         payment_terms_days, kor)
            VALUES ('Oude Zaak', 'bv', 'NL', '', 30, false);
            INSERT INTO accounts (administration_id, code, name, type, is_active)
            VALUES (1, '1000', 'Bank', 'asset', true), (1, '8000', 'Omzet', 'revenue', true);
            INSERT INTO contacts (administration_id, kind, name, country)
            VALUES (1, 'customer', 'Klant', 'NL');
            INSERT INTO journal_entries (administration_id, number, entry_date, description)
            VALUES (1, 'J-1', '2026-01-05', 'Start');
            INSERT INTO journal_lines (entry_id, account_id, debit_cents, credit_cents)
            VALUES (1, 1, 1000, 0), (1, 2, 0, 1000);
            INSERT INTO audit_events (administration_id, action) VALUES (1, 'seed');
        """))
    yield cfg, engine
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
    admin.dispose()


def _facts(engine):
    with engine.connect() as conn:
        one = lambda sql: conn.execute(text(sql)).scalar()  # noqa: E731
        return {
            "entries": one("SELECT count(*) FROM journal_entries"),
            "lines": one("SELECT count(*) FROM journal_lines"),
            "audit": one("SELECT count(*) FROM audit_events"),
            "accounts": one("SELECT count(*) FROM accounts"),
            "users": one("SELECT count(*) FROM users"),
        }


def test_upgrade_downgrade_upgrade_keeps_the_data(old_database):
    cfg, engine = old_database
    before = _facts(engine)
    assert before["entries"] == 1 and before["lines"] == 2

    command.upgrade(cfg, "0004")
    assert _facts(engine) == before
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id, name FROM tenants")).all() == [(1, "Oude Zaak")]
        roles = dict(conn.execute(text(
            "SELECT u.email, m.role FROM memberships m JOIN users u ON u.id = m.user_id")).all())
        assert roles == {"boss@example.com": "owner", "acc@example.com": "accountant"}
        for table in ("administrations", "accounts", "contacts", "journal_entries",
                      "journal_lines", "audit_events"):
            assert conn.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id = 1")
                                ).scalar() > 0, table
        assert conn.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' "
            "AND column_name = 'role'")).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM pg_class WHERE relforcerowsecurity")).scalar() == 18
        assert conn.execute(text("SELECT nextval(pg_get_serial_sequence('tenants', 'id'))")
                            ).scalar() == 2

    command.downgrade(cfg, "0003")
    assert _facts(engine) == before
    with engine.connect() as conn:
        assert dict(conn.execute(text("SELECT email, role FROM users")).all()) == {
            "boss@example.com": "owner", "acc@example.com": "accountant"}
        assert conn.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE column_name = 'tenant_id'"
        )).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM pg_class WHERE relrowsecurity")
                            ).scalar() == 0
        assert conn.execute(text("SELECT to_regclass('tenants')")).scalar() is None
        assert conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname LIKE "
                                 "'ledgerline_%tenant%'")).scalar() == 0

    command.upgrade(cfg, "0004")
    assert _facts(engine) == before


def test_hardening_migration_up_and_down(old_database):
    cfg, engine = old_database
    command.upgrade(cfg, "0004")
    before = _facts(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO administrations (tenant_id, name, legal_form, country, "
                          "invoice_prefix, payment_terms_days, kor) "
                          "VALUES (1, 'Tweede', 'bv', 'NL', '', 30, false)"))
    command.upgrade(cfg, "0005")
    with engine.connect() as conn:
        tokens = conn.execute(text("SELECT inbox_token FROM administrations")).scalars().all()
        assert len(tokens) == 2 and len(set(tokens)) == 2
        assert all(len(t) >= 20 and t == t.lower() and t.isalnum() for t in tokens)
        assert conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname = "
                                 "'ledgerline_tenant_of_administration'")).scalar() == 0
        single = conn.execute(text(_SINGLE_COLUMN_TENANT_FKS)).all()
        assert single == [], single
    assert _facts(engine)["entries"] == before["entries"]

    command.downgrade(cfg, "0004")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM information_schema.columns WHERE "
                                 "column_name = 'inbox_token'")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM pg_proc WHERE proname = "
                                 "'ledgerline_tenant_of_administration'")).scalar() == 1
        assert len(conn.execute(text(_SINGLE_COLUMN_TENANT_FKS)).all()) > 20
        assert conn.execute(text("SELECT count(*) FROM pg_constraint WHERE conname LIKE "
                                 "'uq\\_%\\_tenant\\_id\\_id'")).scalar() == 0
    command.upgrade(cfg, "0005")
    assert _facts(engine)["entries"] == before["entries"]


_TENANT_TABLE_IDS = ("SELECT c.oid FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace "
                     "AND c.relkind = 'r' AND c.relrowsecurity")
_SINGLE_COLUMN_TENANT_FKS = f"""
    SELECT conname FROM pg_constraint
    WHERE contype = 'f' AND array_length(conkey, 1) = 1
      AND conrelid IN ({_TENANT_TABLE_IDS}) AND confrelid IN ({_TENANT_TABLE_IDS})
"""
