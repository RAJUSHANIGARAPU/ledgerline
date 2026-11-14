"""Feedback and pilot metrics never cross a tenant boundary and go away with the tenant."""
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from ledgerline import cli
from ledgerline.models import FeedbackEntry
from ledgerline.platform import pilot
from tests.conftest import tenant_db
from tests.platform.test_pilot import WIDE
from tests.tenancy.conftest import app_connection


def test_a_tenant_sees_only_its_own_feedback(tenant, other_tenant, seeded_a, seeded_b):
    with tenant_db(tenant.id) as s:
        mine = [f"seed feedback of tenant {tenant.id}"]
        assert s.scalars(select(FeedbackEntry.body)).all() == mine
        assert [r["text"] for r in pilot.feedback_rows(s, *WIDE)] == mine
        assert {r.administration_id for r in pilot.administration_reports(s, *WIDE)} == {
            seeded_a["administration_id"]}


def test_a_tenant_cannot_write_or_edit_another_tenants_feedback(
    database_url, tenant, other_tenant, seeded_a, seeded_b
):
    with app_connection(database_url) as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
        assert conn.execute(text("SELECT count(*) FROM feedback_entries WHERE tenant_id = :t"),
                            {"t": other_tenant.id}).scalar() == 0
        updated = conn.execute(text("UPDATE feedback_entries SET body = 'x' "
                                    "WHERE tenant_id = :t"), {"t": other_tenant.id})
        assert updated.rowcount == 0
        deleted = conn.execute(text("DELETE FROM feedback_entries WHERE tenant_id = :t"),
                               {"t": other_tenant.id})
        assert deleted.rowcount == 0
    with app_connection(database_url) as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
        conn.execute(text(
            "INSERT INTO feedback_entries (tenant_id, administration_id, user_id, body) "
            "VALUES (:t, :a, :u, 'planted')"),
            {"t": other_tenant.id, "a": seeded_b["administration_id"],
             "u": seeded_b["ids"]["user"]})


def test_feedback_cannot_point_at_another_tenants_administration(
    tenant, other_tenant, seeded_a, seeded_b
):
    with tenant_db(tenant.id) as s:
        s.add(FeedbackEntry(administration_id=seeded_b["administration_id"],
                            user_id=seeded_a["ids"]["user"], body="cross"))
        with pytest.raises(DBAPIError):
            s.flush()


def test_cli_report_keeps_tenants_apart(tenant, other_tenant, seeded_a, seeded_b, capsys):
    args = ["--from", WIDE[0].isoformat(), "--to", WIDE[1].isoformat()]
    assert cli.main(["feedback-export", "--tenant", str(tenant.id), *args]) == 0
    out = capsys.readouterr().out
    assert f"tenant {tenant.id}" in out and f"tenant {other_tenant.id}" not in out
    assert cli.main(["pilot-report", "--tenant", str(other_tenant.id), *args]) == 0
    report = capsys.readouterr().out
    assert f"Tenant {other_tenant.id}," in report and f"Tenant {tenant.id}," not in report
    assert f"seed feedback of tenant {tenant.id}" not in report


def test_offboarding_deletes_the_feedback(owner_engine, tenant, other_tenant, owner, other_owner,
                                          seeded_a, seeded_b, tmp_path):
    assert cli.main(["delete-tenant", "--tenant", str(tenant.id), "--confirm", "Tenant A",
                     "--export-dir", str(tmp_path)]) == 0
    with owner_engine.connect() as conn:
        counts = dict(conn.execute(text(
            "SELECT tenant_id, count(*) FROM feedback_entries GROUP BY tenant_id")).all())
    assert counts == {other_tenant.id: 1}
    with tenant_db(other_tenant.id) as s:
        assert s.scalar(select(func.count()).select_from(FeedbackEntry)) == 1
