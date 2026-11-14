import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ledgerline.models import DailyTakings, JournalEntry
from tests.conftest import tenant_db

INSERT = text(
    "INSERT INTO daily_takings (administration_id, takings_date, gross_high_cents, cash_cents, "
    "journal_entry_id) VALUES (:a, :d, 100, :cash, :e)")


def test_takings_are_invisible_and_unchangeable_across_tenants(seeded_a, seeded_b, tenant,
                                                               other_tenant):
    mine = seeded_a["ids"][DailyTakings]
    with tenant_db(tenant.id) as s:
        assert [r.id for r in s.scalars(select(DailyTakings))] == [mine]
    with tenant_db(other_tenant.id) as s:
        assert s.get(DailyTakings, mine) is None
        changed = s.execute(text("UPDATE daily_takings SET note = 'x' WHERE id = :i"),
                            {"i": mine}).rowcount
        assert changed == 0


def test_database_refuses_unbalanced_rows(seeded_a, tenant):
    with tenant_db(tenant.id) as s, pytest.raises(IntegrityError, match="ck_takings_balanced"):
        s.execute(INSERT, {"a": seeded_a["administration_id"], "d": "2026-02-01", "cash": 90,
                           "e": seeded_a["ids"][JournalEntry]})


def test_database_refuses_a_journal_entry_of_another_tenant(seeded_a, seeded_b, tenant):
    with tenant_db(tenant.id) as s, pytest.raises((IntegrityError, DBAPIError)):
        s.execute(INSERT, {"a": seeded_a["administration_id"], "d": "2026-02-02", "cash": 100,
                           "e": seeded_b["ids"][JournalEntry]})
