"""The database enforces tenant isolation: catalog guard, no context, cross-tenant access."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.tenancy.conftest import app_connection, scoped_tables

TRIGGER_GUARDED = {"journal_entries", "journal_lines", "audit_events"}


def test_every_tenant_table_has_enabled_forced_policy(owner_engine):
    tables = scoped_tables(owner_engine)
    with owner_engine.connect() as conn:
        with_columns = set(conn.execute(text(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name IN ('tenant_id', 'administration_id')"
        )).scalars())
        flags = {r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in conn.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"))}
        policies = {r.tablename: r for r in conn.execute(text(
            "SELECT tablename, policyname, qual, with_check FROM pg_policies "
            "WHERE schemaname = 'public'"))}
    assert set(tables) == with_columns - {"memberships"}
    assert len(tables) == 18
    for table in tables:
        assert flags[table] == (True, True), table
        policy = policies[table]
        assert policy.policyname == "tenant_isolation"
        assert "ledgerline_current_tenant()" in policy.qual
        assert "ledgerline_current_tenant()" in policy.with_check


def test_app_role_cannot_bypass_rls(database_url):
    with app_connection(database_url) as conn:
        role = conn.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles "
            "WHERE rolname = current_user"
        )).one()
        assert conn.execute(text("SELECT current_user")).scalar() == "ledgerline_app"
    assert tuple(role) == (False, False, False)


def test_no_tenant_context_means_error(owner_engine, database_url, seeded_a, seeded_b):
    tables = scoped_tables(owner_engine)
    with owner_engine.connect() as conn:
        for table in tables:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() > 0, table
    for table in tables:
        with app_connection(database_url) as conn, pytest.raises(DBAPIError) as err:
            conn.execute(text(f"SELECT * FROM {table}"))
        assert err.value.orig.sqlstate == "42501", table


def test_empty_or_unknown_context_still_fails(database_url, seeded_a):
    with app_connection(database_url) as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        conn.execute(text("SELECT count(*) FROM accounts"))
    with app_connection(database_url) as conn:
        conn.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT count(*) FROM accounts"))


def test_only_own_rows_are_visible(owner_engine, database_url, tenant, other_tenant,
                                   seeded_a, seeded_b):
    for table in scoped_tables(owner_engine):
        with owner_engine.connect() as owner:
            mine = owner.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),
                                 {"t": tenant.id}).scalar()
        assert mine > 0, table
        with app_connection(database_url) as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                         {"t": str(tenant.id)})
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == mine, table
            assert conn.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id <> :t"),
                                {"t": tenant.id}).scalar() == 0, table


def test_other_tenants_rows_cannot_be_changed(owner_engine, database_url, tenant, other_tenant,
                                              seeded_a, seeded_b):
    for table in scoped_tables(owner_engine):
        with app_connection(database_url) as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                         {"t": str(tenant.id)})
            if table not in TRIGGER_GUARDED:
                hit = conn.execute(text(
                    f"UPDATE {table} SET tenant_id = tenant_id WHERE tenant_id = :b"),
                    {"b": other_tenant.id})
                assert hit.rowcount == 0, table
                gone = conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :b"),
                                    {"b": other_tenant.id})
                assert gone.rowcount == 0, table
            conn.rollback()


@pytest.mark.parametrize("move", ["update", "insert"])
def test_rows_cannot_be_written_into_another_tenant(owner_engine, database_url, tenant,
                                                    other_tenant, seeded_a, seeded_b, move):
    for table in scoped_tables(owner_engine):
        if move == "update":
            sql = f"UPDATE {table} SET tenant_id = :b WHERE tenant_id = :a"
        else:
            sql = (f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table}, "
                   f"to_jsonb(src) || jsonb_build_object('tenant_id', CAST(:b AS integer)))).* "
                   f"FROM {table} src WHERE src.tenant_id = :a LIMIT 1")
        with app_connection(database_url) as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                         {"t": str(tenant.id)})
            with pytest.raises(DBAPIError) as err:
                conn.execute(text(sql), {"a": tenant.id, "b": other_tenant.id})
            if table not in TRIGGER_GUARDED and table != "journal_lines":
                assert err.value.orig.sqlstate == "42501", (table, str(err.value.orig))


def test_new_rows_get_the_current_tenant(database_url, tenant, other_tenant):
    with app_connection(database_url) as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                     {"t": str(other_tenant.id)})
        got = conn.execute(text(
            "INSERT INTO administrations (name, legal_form, country, invoice_prefix, "
            "payment_terms_days, kor) VALUES ('x', 'bv', 'NL', '', 30, false) "
            "RETURNING tenant_id")).scalar()
        assert got == other_tenant.id
        with pytest.raises(DBAPIError):
            conn.execute(text(
                "INSERT INTO administrations (name, legal_form, country, invoice_prefix, "
                "payment_terms_days, kor, tenant_id) "
                "VALUES ('y', 'bv', 'NL', '', 30, false, :t)"), {"t": tenant.id})


def test_ensure_app_role_sets_a_working_login(database_url, monkeypatch):
    from ledgerline import cli
    from ledgerline.config import get_settings
    from tests.conftest import APP_ROLE_PASSWORD

    monkeypatch.setenv("LEDGERLINE_APP_DB_PASSWORD", "")
    get_settings.cache_clear()
    assert cli.main(["ensure-app-role"]) == 1
    monkeypatch.setenv("LEDGERLINE_APP_DB_PASSWORD", APP_ROLE_PASSWORD)
    get_settings.cache_clear()
    assert cli.main(["ensure-app-role"]) == 0
    with app_connection(database_url) as conn:
        assert conn.execute(text("SELECT current_user")).scalar() == "ledgerline_app"
    get_settings.cache_clear()
