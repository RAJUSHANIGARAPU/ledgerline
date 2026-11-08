"""Login, tenant selection and the role carried by the membership."""
import re

from sqlalchemy import select

from ledgerline.models import AuditEvent, Membership, Role, User
from tests.conftest import OWNER_PASSWORD, _user, tenant_db

CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def _raw_login(client_factory, email):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    client = TestClient(create_app())
    token = CSRF.search(client.get("/login").text).group(1)
    response = client.post("/login", data={"email": email, "password": OWNER_PASSWORD,
                                           "csrf_token": token}, follow_redirects=False)
    return client, response


def _csrf(client):
    return CSRF.search(client.get("/tenants").text).group(1)


def test_single_membership_is_selected_automatically(owner, admin):
    client, response = _raw_login(None, owner.email)
    assert response.status_code == 303 and response.headers["location"] == "/"
    page = client.get("/")
    assert page.status_code == 200 and "Tenant A" in page.text
    assert ">switch<" not in page.text


def test_login_is_audited_inside_the_tenant(owner, admin, session):
    _raw_login(None, owner.email)
    events = session.scalars(select(AuditEvent).where(AuditEvent.action == "login")).all()
    assert [e.user_id for e in events] == [owner.id]


def test_user_without_membership_cannot_enter(session, admin):
    user = User(email="loner@example.com", password_hash=_user(session, "x@example.com",
                                                               Role.OWNER).password_hash)
    session.add(user)
    session.commit()
    client, response = _raw_login(None, user.email)
    assert response.status_code == 403 and "not a member" in response.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/tenants", follow_redirects=False).headers["location"] == "/login"


def test_two_memberships_choose_a_tenant(session, tenant, other_tenant, other_admin, admin):
    user = _user(session, "office@example.com", Role.OWNER)
    with tenant_db(other_tenant.id) as s:
        s.add(Membership(user_id=user.id, tenant_id=other_tenant.id, role=Role.ACCOUNTANT))
        s.commit()
    client, response = _raw_login(None, user.email)
    assert response.headers["location"] == "/tenants"
    assert client.get("/", follow_redirects=False).headers["location"] == "/tenants"
    chooser = client.get("/tenants").text
    assert "Tenant A" in chooser and "Tenant B" in chooser
    token = _csrf(client)

    client.post("/tenants", data={"tenant_id": other_tenant.id, "csrf_token": token})
    page = client.get("/")
    assert "Tenant B" in page.text and ">switch<" in page.text
    assert client.post("/contacts/new", data={"name": "n", "csrf_token": token},
                       follow_redirects=False).status_code == 403

    client.post("/tenants", data={"tenant_id": tenant.id, "csrf_token": token})
    assert client.post("/contacts/new", data={"name": "n", "kind": "customer",
                                              "csrf_token": token},
                       follow_redirects=False).status_code == 303
    assert "Andere Zaak" not in client.get("/contacts").text


def test_cannot_select_a_tenant_without_membership(session, tenant, other_tenant, admin,
                                                   other_admin):
    user = _user(session, "office@example.com", Role.OWNER)
    other = _user(session, "second@example.com", Role.OWNER)
    with tenant_db(other_tenant.id) as s:
        s.add(Membership(user_id=other.id, tenant_id=other_tenant.id, role=Role.OWNER))
        s.commit()
    assert other.id
    client, _ = _raw_login(None, user.email)
    response = client.post("/tenants", data={"tenant_id": other_tenant.id,
                                             "csrf_token": _csrf(client)})
    assert response.status_code == 403
    assert "Tenant B" not in client.get("/", follow_redirects=True).text


def test_role_change_applies_to_the_running_session(client, session, owner):
    assert client.post("/contacts/new", data={"name": "a", "kind": "customer",
                                              "csrf_token": client.csrf},
                       follow_redirects=False).status_code == 303
    session.execute(Membership.__table__.update().where(Membership.user_id == owner.id)
                    .values(role=Role.ACCOUNTANT))
    session.commit()
    assert client.post("/contacts/new", data={"name": "b", "kind": "customer",
                                              "csrf_token": client.csrf},
                       follow_redirects=False).status_code == 403
