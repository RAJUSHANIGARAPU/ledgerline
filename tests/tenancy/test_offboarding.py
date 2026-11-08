import zipfile

import pytest
from sqlalchemy import func, select, text

from ledgerline.cli import main
from ledgerline.models import JournalEntry, User
from ledgerline.platform.offboarding import TENANT_TABLES, OffboardingError, delete_tenant
from tests.conftest import tenant_db
from tests.tenancy.conftest import app_connection


def _counts(owner_engine, tenant_id: int) -> dict[str, int]:
    with owner_engine.connect() as conn:
        conn.execute(text("SET session_replication_role = replica"))
        return {
            table: conn.execute(
                text(f'SELECT count(*) FROM "{table}" WHERE tenant_id = :t'), {"t": tenant_id}
            ).scalar()
            for table in conn.execute(TENANT_TABLES).scalars()
        }


def _delete(tenant, export_dir, confirm=None) -> int:
    return main([
        "delete-tenant", "--tenant", str(tenant.id), "--confirm", confirm or "Tenant A",
        "--export-dir", str(export_dir),
    ])


def test_delete_tenant_exports_then_removes_only_that_tenant(
    owner_engine, tenant, other_tenant, owner, other_owner, seeded_a, seeded_b, tmp_path
):
    before_a = _counts(owner_engine, tenant.id)
    before_b = _counts(owner_engine, other_tenant.id)
    assert sum(before_a.values()) > 100

    assert _delete(tenant, tmp_path) == 0

    exports = sorted(tmp_path.glob("tenant-*.zip"))
    assert len(exports) == before_a["administrations"] >= 1
    with zipfile.ZipFile(exports[-1]) as zf:
        assert "data/journal_entries.json" in zf.namelist()
        assert any(name.startswith("attachments/") for name in zf.namelist())

    assert set(_counts(owner_engine, tenant.id).values()) == {0}
    assert _counts(owner_engine, other_tenant.id) == before_b
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tenants WHERE id = :t"),
                            {"t": tenant.id}).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM memberships WHERE tenant_id = :t"),
                            {"t": tenant.id}).scalar() == 0
        users = set(conn.execute(text("SELECT id FROM users")).scalars())
    assert owner.id not in users and other_owner.id in users
    with tenant_db(other_tenant.id) as s:
        entries = s.scalar(select(func.count()).select_from(JournalEntry))
    assert entries == before_b["journal_entries"]


def test_a_login_shared_with_another_tenant_survives(
    owner_engine, tenant, other_tenant, owner, other_owner, tmp_path
):
    with owner_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO memberships (user_id, tenant_id, role, status) "
            "VALUES (:u, :t, 'accountant', 'active')"), {"u": owner.id, "t": other_tenant.id})

    assert _delete(tenant, tmp_path) == 0

    with tenant_db(other_tenant.id) as s:
        assert s.get(User, owner.id) is not None
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT tenant_id FROM memberships WHERE user_id = :u"),
                            {"u": owner.id}).scalars().all() == [other_tenant.id]


def test_wrong_confirmation_deletes_nothing(owner_engine, tenant, owner, admin, tmp_path, capsys):
    before = _counts(owner_engine, tenant.id)
    assert _delete(tenant, tmp_path, confirm="tenant a") == 1
    assert "exact name" in capsys.readouterr().err
    assert _counts(owner_engine, tenant.id) == before
    assert not list(tmp_path.iterdir())


def test_unknown_tenant_is_refused(tenant, tmp_path, capsys):
    assert main(["delete-tenant", "--tenant", "999", "--confirm", "x",
                 "--export-dir", str(tmp_path)]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_the_app_role_cannot_delete_a_tenant(database_url, tenant):
    with app_connection(database_url) as conn, pytest.raises(OffboardingError, match="owner"):
        delete_tenant(conn, tenant.id)


def test_export_as_owner_contains_only_that_tenant(
    owner_url, tenant, other_tenant, owner, other_owner, seeded_a, seeded_b, tmp_path
):
    # The owner role bypasses row-level security, so the export must filter by tenant itself.
    import json

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from ledgerline.platform.offboarding import export_tenant

    engine = create_engine(owner_url)
    try:
        with Session(engine) as owner_session:
            paths = export_tenant(tenant.id, tmp_path, owner_session)
    finally:
        engine.dispose()
    assert paths and all(f"tenant-{tenant.id}-" in p.name for p in paths)
    for path in paths:
        with zipfile.ZipFile(path) as zf:
            admin = json.loads(zf.read("data/administration.json"))
            entries = json.loads(zf.read("data/journal_entries.json"))
        assert {row["tenant_id"] for row in admin} == {tenant.id}
        assert entries and {row["tenant_id"] for row in entries} == {tenant.id}
