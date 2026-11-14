from sqlalchemy import select, text

from ledgerline.models import AuditEvent
from tests.adversarial.conftest import forge_session
from tests.mobile.test_mobile import docs, pdf


def upload(client, tag="uploader"):
    files = [("files", (f"{tag}.pdf", pdf(tag), "application/pdf"))]
    return client.post("/capture", data={"csrf_token": client.csrf, "note": "Fish"}, files=files)


def test_uploader_lands_on_capture_after_login(uploader_client):
    response = uploader_client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/capture"


def test_capture_page_shows_only_capture_navigation(uploader_client):
    html = uploader_client.get("/capture").text
    assert 'href="/capture" class="nav-link is-active"' in html
    for hidden in ("/sales", "/inbox", "/bank", "/ledger", "/reports", "/settings", "/setup",
                   "/help", "/contacts", "/vat"):
        assert f'href="{hidden}"' not in html, hidden
    assert 'action="/logout"' in html and 'href="/account"' in html


def test_upload_is_stored_and_audited_with_the_uploader(uploader_client, uploader, session):
    response = upload(uploader_client)
    assert response.status_code == 200
    assert "/inbox" not in response.text
    [document] = docs(session)
    assert document.source == "phone" and document.subject == "Fish"
    event = session.scalars(select(AuditEvent).where(AuditEvent.action == "inbox.receive")).one()
    assert event.user_id == uploader.id


def test_duplicate_upload_reveals_no_link(uploader_client, client):
    upload(client, "same")
    response = upload(uploader_client, "same")
    assert "already in your inbox" in response.text and "/inbox/" not in response.text


def test_uploader_needs_csrf(uploader_client, session):
    files = [("files", ("a.pdf", pdf("x"), "application/pdf"))]
    response = uploader_client.post("/capture", data={"csrf_token": "wrong"}, files=files)
    assert response.status_code == 403 and docs(session) == []


def test_uploader_cannot_manage_devices_or_tokens(uploader_client):
    assert uploader_client.get("/settings/devices", follow_redirects=False).status_code == 303
    post = uploader_client.post(
        "/settings/devices", data={"name": "mine", "csrf_token": uploader_client.csrf})
    assert post.status_code == 403


def test_uploader_cannot_open_documents_uploaded_by_others(uploader_client, client, session):
    upload(client, "secret")
    [document] = docs(session)
    for path in (f"/inbox/{document.id}", f"/inbox/{document.id}/file", "/inbox"):
        assert uploader_client.get(path, follow_redirects=False).status_code == 303


def test_uploader_in_another_tenant_cannot_use_this_one(
    uploader_client, uploader, other_tenant, other_admin
):
    forge_session(uploader_client, user_id=uploader.id, tenant_id=other_tenant.id)
    response = uploader_client.get("/capture", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tenants"


def test_tenant_two_factor_requirement_applies(uploader_client, owner_engine, tenant):
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE tenants SET require_2fa = true WHERE id = :t"), {"t": tenant.id})
    response = uploader_client.get("/capture", follow_redirects=False)
    assert response.status_code == 303 and "/account/security" in response.headers["location"]


def test_membership_check_constraint_accepts_uploader_only(owner_engine, tenant, uploader):
    from sqlalchemy.exc import IntegrityError

    other = ("INSERT INTO users (email, password_hash, is_active) "
             "VALUES (:e, 'x', true) RETURNING id")
    insert = "INSERT INTO memberships (user_id, tenant_id, role) VALUES (:u, :t, :r)"
    with owner_engine.begin() as conn:
        uid = conn.execute(text(other), {"e": "c@example.com"}).scalar_one()
        conn.execute(text(insert), {"u": uid, "t": tenant.id, "r": "uploader"})
    with owner_engine.connect() as conn:
        try:
            conn.execute(text(insert), {"u": uid, "t": tenant.id, "r": "janitor"})
            raise AssertionError("janitor accepted")
        except IntegrityError:
            conn.rollback()
