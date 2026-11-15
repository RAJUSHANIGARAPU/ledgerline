"""A member of a tenant without administrations gets a page, not raw JSON."""
import pytest

from tests.conftest import OWNER_PASSWORD, _login  # noqa: F401


@pytest.fixture
def lonely_client(tenant, session):
    from tests.conftest import _user

    _user(session, "lonely@example.com", "owner", tenant.id)
    return _login("lonely@example.com")


@pytest.mark.parametrize("path", ["/", "/settings", "/sales", "/bank", "/vat"])
def test_no_administration_renders_a_page_not_json(lonely_client, path):
    r = lonely_client.get(path)
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("text/html")
    assert "Create your books" in r.text and 'action="/settings/first-administration"' in r.text
    assert "no administration exists yet" not in r.text
    assert 'action="/logout"' in r.text and 'href="/sales"' not in r.text


def test_the_page_offers_the_chooser_only_with_more_than_one_workspace(
    lonely_client, other_tenant, session, owner_engine
):
    assert 'href="/tenants"' not in lonely_client.get("/").text
    from sqlalchemy import text

    with owner_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO memberships (user_id, tenant_id, role, status) "
            "SELECT id, :t, 'owner', 'active' FROM users WHERE email = 'lonely@example.com'"),
            {"t": other_tenant.id})
    assert 'href="/tenants"' in lonely_client.get("/").text


def test_the_tenant_switcher_renders_inside_the_shell(owner, admin, other_tenant, owner_engine):
    from sqlalchemy import text

    with owner_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO memberships (user_id, tenant_id, role, status) VALUES (:u, :t, 'owner', "
            "'active')"), {"u": owner.id, "t": other_tenant.id})
    client = _login(owner.email)
    client.post("/tenants", data={"tenant_id": admin.tenant_id, "csrf_token": client.csrf})
    page = client.get("/tenants", follow_redirects=True)
    assert page.status_code == 200
    assert "Choose a workspace" in page.text and "Tenant A" in page.text
    assert 'class="shell"' in page.text and "Choose the account you want to work in." in page.text


@pytest.fixture
def lonely_bookkeeper_client(tenant, session):
    from tests.conftest import _user

    _user(session, "lonely-bk@example.com", "bookkeeper", tenant.id)
    return _login("lonely-bk@example.com")


def test_a_non_owner_is_told_to_ask_the_owner(lonely_bookkeeper_client):
    r = lonely_bookkeeper_client.get("/")
    assert r.status_code == 409
    assert "don't have access to any administration yet" in r.text and "Ask the owner" in r.text
    assert "first-administration" not in r.text and "Create your books" not in r.text


def test_the_owner_creates_the_first_administration(lonely_client, tenant, session):
    from sqlalchemy import select

    from ledgerline.models import Administration, AuditEvent

    r = lonely_client.post("/settings/first-administration", follow_redirects=False, data={
        "name": "Bakkerij Jansen", "legal_form": "bv", "csrf_token": lonely_client.csrf})
    assert r.status_code == 303 and r.headers["location"] == "/"
    session.expire_all()
    created = session.scalars(select(Administration)).all()
    assert [(a.name, a.legal_form, a.tenant_id) for a in created] == [
        ("Bakkerij Jansen", "bv", tenant.id)]
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.action == "administration.create")).all()
    assert [e.administration_id for e in events] == [created[0].id]
    assert lonely_client.get("/").status_code == 200


def test_the_created_administration_is_invisible_to_another_tenant(
    lonely_client, other_tenant, owner_engine
):
    from sqlalchemy import text

    lonely_client.post("/settings/first-administration", data={
        "name": "Eigen Zaak", "legal_form": "vof", "csrf_token": lonely_client.csrf})
    with owner_engine.begin() as conn:
        rows = conn.execute(text("SELECT tenant_id FROM administrations WHERE name = 'Eigen Zaak'")
                            ).all()
    assert len(rows) == 1 and rows[0][0] != other_tenant.id


@pytest.mark.parametrize("data", [{"name": ""}, {"name": "X", "legal_form": "nonsense"}])
def test_invalid_input_creates_nothing(lonely_client, session, data):
    from sqlalchemy import func, select

    from ledgerline.models import Administration

    lonely_client.post("/settings/first-administration",
                       data={**data, "csrf_token": lonely_client.csrf})
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Administration)) == 0


def test_a_bookkeeper_cannot_create_it(lonely_bookkeeper_client, session):
    from sqlalchemy import func, select

    from ledgerline.models import Administration

    r = lonely_bookkeeper_client.post("/settings/first-administration", data={
        "name": "Nope", "csrf_token": lonely_bookkeeper_client.csrf})
    assert r.status_code == 403
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Administration)) == 0


def test_creating_needs_a_csrf_token(lonely_client):
    r = lonely_client.post("/settings/first-administration", data={"name": "Nope"})
    assert r.status_code == 403
