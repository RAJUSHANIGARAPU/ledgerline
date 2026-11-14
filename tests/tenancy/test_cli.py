import pytest
from sqlalchemy import select, text

from ledgerline import cli
from ledgerline.ledger.chart import create_administration
from ledgerline.models import Administration, Membership, Tenant, User
from tests.conftest import tenant_db


@pytest.fixture(autouse=True)
def password(monkeypatch):
    monkeypatch.setenv("LEDGERLINE_INITIAL_PASSWORD", "initial-password-123")


def test_create_tenant_and_user_in_it(session, tenant, capsys):
    assert cli.main(["create-tenant", "--name", "Kantoor Jansen"]) == 0
    created = session.scalar(select(Tenant).where(Tenant.name == "Kantoor Jansen"))
    assert created and f"tenant {created.id}" in capsys.readouterr().out
    assert cli.main(["create-user", "--email", "x@example.com", "--role", "bookkeeper",
                     "--tenant", str(created.id)]) == 0
    member = session.scalar(select(Membership).where(Membership.tenant_id == created.id))
    assert member.role == "bookkeeper"


def test_tenant_is_required_when_there_are_several(session, tenant, other_tenant, capsys):
    assert cli.main(["create-user", "--email", "x@example.com"]) == 1
    assert "--tenant" in capsys.readouterr().err
    assert cli.main(["create-user", "--email", "x@example.com", "--tenant", "999"]) == 1
    assert cli.main(["create-administration", "--name", "Zonder"]) == 1


def test_administration_is_created_inside_the_given_tenant(session, tenant, other_tenant):
    assert cli.main(["create-administration", "--name", "Voor B", "--tenant",
                     str(other_tenant.id)]) == 0
    with tenant_db(other_tenant.id) as s:
        assert s.scalar(select(Administration.name)) == "Voor B"
    assert session.scalar(select(Administration.id)) is None


def test_add_membership_for_an_existing_user(session, tenant, other_tenant, owner, capsys):
    argv = ["add-membership", "--email", owner.email, "--tenant", str(other_tenant.id),
            "--role", "accountant"]
    assert cli.main(argv) == 0
    assert cli.main(argv) == 1
    assert cli.main(["add-membership", "--email", "nobody@example.com", "--tenant",
                     str(other_tenant.id)]) == 1
    roles = session.scalars(select(Membership.role).where(Membership.user_id == owner.id)).all()
    assert sorted(roles) == ["accountant", "owner"]


def test_seed_demo_creates_its_own_tenant_for_the_given_user(session, tenant, owner, capsys):
    assert cli.main(["seed-demo", "--email", owner.email]) == 0
    demo = session.scalar(select(Tenant).where(Tenant.id != tenant.id))
    assert session.scalar(select(Membership.role).where(
        Membership.user_id == owner.id, Membership.tenant_id == demo.id)) == "owner"
    with tenant_db(demo.id) as s:
        assert s.scalar(select(Administration.name)).startswith("Studio Noordlicht")
    assert cli.main(["seed-demo", "--email", owner.email]) == 1


def test_seed_demo_without_user_says_how_to_get_access(session, tenant, capsys):
    assert cli.main(["seed-demo"]) == 0
    assert "add-membership" in capsys.readouterr().out
    assert cli.main(["seed-demo", "--email", "ghost@example.com"]) == 1
    assert session.scalar(select(User.id)) is None


def _default_tenants(owner_engine):
    with owner_engine.connect() as conn:
        return conn.execute(text("SELECT id FROM tenants WHERE name = 'Default'")).scalars().all()


def test_create_tenant_removes_the_empty_seeded_default_tenant(owner_engine, session, capsys):
    with owner_engine.begin() as conn:
        seeded = conn.execute(text(
            "INSERT INTO tenants (name) VALUES ('Default') RETURNING id")).scalar_one()
    assert cli.main(["create-tenant", "--name", "Kantoor Jansen"]) == 0
    assert _default_tenants(owner_engine) == []
    assert f"Removed the empty seeded tenant {seeded}" in capsys.readouterr().out
    assert session.scalar(select(Tenant.id).where(Tenant.name == "Kantoor Jansen"))


def test_create_tenant_keeps_a_default_tenant_that_has_a_login(
        owner_engine, session, owner, capsys):
    with owner_engine.begin() as conn:
        used = conn.execute(text(
            "INSERT INTO tenants (name) VALUES ('Default') RETURNING id")).scalar_one()
        conn.execute(text(
            "INSERT INTO memberships (user_id, tenant_id, role) VALUES (:u, :t, 'owner')"),
            {"u": owner.id, "t": used})
    assert cli.main(["create-tenant", "--name", "Kantoor Jansen"]) == 0
    assert _default_tenants(owner_engine) == [used]
    assert "Removed" not in capsys.readouterr().out


def test_create_tenant_keeps_a_default_tenant_that_has_an_administration(
        owner_engine, session, capsys):
    with owner_engine.begin() as conn:
        used = conn.execute(text(
            "INSERT INTO tenants (name) VALUES ('Default') RETURNING id")).scalar_one()
    with tenant_db(used) as s:
        create_administration(s, name="Bestaand")
        s.commit()
    assert cli.main(["create-tenant", "--name", "Kantoor Jansen"]) == 0
    assert _default_tenants(owner_engine) == [used]
