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
    assert "don't have access to any administration yet" in r.text
    assert "Ask the owner" in r.text
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
