import re

import pytest
from fastapi.testclient import TestClient

from ledgerline import auth
from ledgerline.main import create_app
from tests.helpers import contact


@pytest.fixture(autouse=True)
def _reset_lockout():
    auth._failures.clear()
    yield
    auth._failures.clear()


@pytest.mark.parametrize("slug", ["privacy", "terms", "cookies", "disclaimer"])
def test_legal_pages_are_public(database_url, slug):
    r = TestClient(create_app()).get(f"/legal/{slug}")
    assert r.status_code == 200
    assert "TEMPLATE" in r.text


def test_cookie_notice_names_only_the_session_cookie(database_url):
    text = TestClient(create_app()).get("/legal/cookies").text
    assert "ledgerline_session" in text and "No tracking" in text


def test_legal_pages_render_for_signed_in_user(client):
    assert client.get("/legal/privacy").status_code == 200


def test_security_headers_present(database_url):
    r = TestClient(create_app()).get("/legal/privacy")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"


def test_legal_pages_have_no_inline_script_or_style(database_url):
    for slug in ("privacy", "terms", "cookies", "disclaimer"):
        html = TestClient(create_app()).get(f"/legal/{slug}").text
        assert "<script>" not in html and "style=" not in html


def test_login_lockout_after_five_failures(owner):
    client = TestClient(create_app())
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)

    def attempt(password):
        return client.post("/login", data={"email": owner.email, "password": password,
                                           "csrf_token": token}, follow_redirects=False)

    assert [attempt("wrong-password-1").status_code for _ in range(5)] == [401] * 5
    assert attempt("correct horse battery staple").status_code == 429


def test_post_without_csrf_token_is_403(client, session, admin):
    r = client.post("/contacts/new", data={"name": "Zonder token"})
    assert r.status_code == 403


def test_post_with_wrong_csrf_token_is_403(client):
    assert client.post("/contacts/new", data={"name": "x", "csrf_token": "nope"}).status_code == 403


def test_anonymous_user_is_redirected_from_contacts(database_url):
    r = TestClient(create_app()).get("/contacts", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/contacts/new", "/settings", "/settings/administrations",
                                  "/settings/users"])
def test_accountant_post_is_403(readonly_client, admin, path):
    r = readonly_client.post(path, data={"name": "x", "csrf_token": readonly_client.csrf})
    assert r.status_code == 403


def test_accountant_cannot_anonymise_or_delete(readonly_client, session, admin):
    c = contact(session, admin, name="Beschermd")
    session.commit()
    for action in ("anonymise", "delete"):
        r = readonly_client.post(f"/contacts/{c.id}/{action}",
                                 data={"confirm": "yes", "csrf_token": readonly_client.csrf})
        assert r.status_code == 403
    session.expire_all()
    assert session.get(type(c), c.id).name == "Beschermd"


def test_accountant_can_read_contacts_and_settings(readonly_client, session, admin):
    contact(session, admin, name="Leesbaar")
    session.commit()
    assert "Leesbaar" in readonly_client.get("/contacts").text
    assert "Read-only account" in readonly_client.get("/settings").text


def test_every_page_links_to_its_source_code(client):
    # AGPL-3.0 section 13: network users must be offered the source.
    page = client.get("/settings")
    assert 'href="https://github.com/RAJUSHANIGARAPU/ledgerline"' in page.text
    assert ">Source code<" in page.text
