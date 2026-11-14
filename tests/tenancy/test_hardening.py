"""Composite foreign keys, fail-closed grants, inbox address and the raw-SQL guard."""

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from ledgerline import cli
from ledgerline.config import get_settings
from tests.conftest import APP_ROLE_PASSWORD
from tests.tenancy.conftest import scoped_tables

SRC = Path(__file__).resolve().parents[2] / "src" / "ledgerline"


def test_no_single_column_foreign_key_between_tenant_tables(owner_engine):
    tenant_tables = scoped_tables(owner_engine)
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conrelid::regclass::text, confrelid::regclass::text, conname, "
                "array_length(conkey, 1), confdeltype FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
            )
        ).all()
    between = [r for r in rows if r[0] in tenant_tables and r[1] in tenant_tables]
    assert len(between) > 20
    single = [r[2] for r in between if r[3] == 1]
    assert single == [], single
    assert all(r[3] == 2 for r in between)
    users = [r for r in rows if r[1] == "users"]
    assert users and all(r[3] == 1 for r in users)


def test_referenced_tenant_tables_have_unique_tenant_and_id(owner_engine):
    with owner_engine.connect() as conn:
        referenced = set(
            conn.execute(
                text(
                    "SELECT DISTINCT confrelid::regclass::text FROM pg_constraint "
                    "WHERE contype = 'f' AND array_length(conkey, 1) = 2"
                )
            ).scalars()
        )
        unique = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE contype = 'u'"
                )
            )
            if r[1] == "UNIQUE (tenant_id, id)"
        }
    assert referenced and referenced <= unique


def test_nullable_reference_stays_optional(database_url, tenant, admin):
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)}
            )
            conn.execute(text("INSERT INTO audit_events (action) VALUES ('x')"))
    finally:
        engine.dispose()


@pytest.fixture
def owner_conn(owner_engine):
    with owner_engine.begin() as conn:
        yield conn


@pytest.fixture
def scratch_tables(owner_engine):
    names = ["zz_nopolicy", "zz_notforced", "zz_protected"]
    yield names
    with owner_engine.begin() as conn:
        for name in names:
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))


def _denied(database_url, table):
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', '1', true)"))
            try:
                conn.execute(text(f"SELECT count(*) FROM {table}"))
            except DBAPIError as err:
                return err.orig.sqlstate == "42501" and "permission denied" in str(err.orig)
            return False
    finally:
        engine.dispose()


def test_ensure_app_role_grants_only_protected_tables(
    owner_engine, database_url, monkeypatch, scratch_tables
):
    with owner_engine.begin() as conn:
        conn.execute(text("CREATE TABLE zz_nopolicy (id serial primary key, tenant_id int)"))
        conn.execute(text("CREATE TABLE zz_notforced (id serial primary key, tenant_id int)"))
        conn.execute(text("ALTER TABLE zz_notforced ENABLE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "CREATE POLICY tenant_isolation ON zz_notforced "
                "USING (tenant_id = ledgerline_current_tenant())"
            )
        )
        conn.execute(text("CREATE TABLE zz_protected (id serial primary key, tenant_id int)"))
        conn.execute(text("ALTER TABLE zz_protected ENABLE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE zz_protected FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "CREATE POLICY tenant_isolation ON zz_protected "
                "USING (tenant_id = ledgerline_current_tenant())"
            )
        )
    # created by the owner: nothing is granted by default any more
    assert all(_denied(database_url, t) for t in scratch_tables)

    monkeypatch.setenv("LEDGERLINE_APP_DB_PASSWORD", APP_ROLE_PASSWORD)
    get_settings.cache_clear()
    assert cli.main(["ensure-app-role"]) == 0
    get_settings.cache_clear()
    assert _denied(database_url, "zz_nopolicy")
    assert _denied(database_url, "zz_notforced")
    assert not _denied(database_url, "zz_protected")
    assert _denied(database_url, "alembic_version")
    assert _no_update(owner_engine, "feedback_entries")
    # the real tables keep working
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', '1', true)"))
            conn.execute(text("SELECT count(*) FROM contacts"))
            conn.execute(text("SELECT count(*) FROM memberships"))
    finally:
        engine.dispose()


def _no_update(owner_engine, table):
    with owner_engine.connect() as conn:
        return not conn.execute(text(
            "SELECT has_table_privilege('ledgerline_app', :t, 'UPDATE')"), {"t": table}).scalar()


def test_app_role_has_no_default_privileges(owner_engine):
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_default_acl "
                    "WHERE defaclacl::text LIKE '%ledgerline_app=%'"
                )
            ).scalar()
            == 0
        )


def test_new_administrations_get_distinct_url_safe_tokens(session, admin):
    from ledgerline.ledger.chart import create_administration

    second = create_administration(session, name="Tweede")
    session.commit()
    assert admin.inbox_token != second.inbox_token
    for token in (admin.inbox_token, second.inbox_token):
        assert re.fullmatch(r"[a-z0-9]{20,64}", token)


def test_inbox_page_shows_the_address_and_owner_can_regenerate(client, admin, monkeypatch):
    from ledgerline.config import get_settings as settings_factory

    monkeypatch.setenv("LEDGERLINE_IMAP_USER", "invoices@jouwbedrijf.example")
    settings_factory.cache_clear()
    old = admin.inbox_token
    assert f"invoices+{old}@jouwbedrijf.example" in client.get("/inbox").text
    assert f"invoices+{old}@jouwbedrijf.example" in client.get("/settings").text
    response = client.post(
        "/settings/inbox-address", data={"csrf_token": client.csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    page = client.get("/inbox").text
    assert old not in page
    match = re.search(r"invoices\+([a-z0-9]+)@jouwbedrijf\.example", page)
    assert match and match.group(1) != old
    settings_factory.cache_clear()


def test_readonly_user_does_not_see_the_inbox_address(readonly_client, admin, monkeypatch):
    monkeypatch.setenv("LEDGERLINE_IMAP_USER", "invoices@jouwbedrijf.example")
    get_settings.cache_clear()
    assert admin.inbox_token not in readonly_client.get("/inbox").text
    get_settings.cache_clear()


def _dynamic(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return not (isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "format" and not _psycopg_composition(node.func.value)
    return False


def _psycopg_composition(node: ast.expr) -> bool:
    """sql.SQL("...").format(sql.Identifier(...)) quotes its parts; the only allowed form."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "SQL") or (
        isinstance(func, ast.Name) and func.id == "SQL"
    )


def test_no_sql_text_is_built_from_variables():
    offenders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = (
                node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            )
            if name in {"text", "execute", "exec_driver_sql"} and _dynamic(node.args[0]):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == [], offenders
