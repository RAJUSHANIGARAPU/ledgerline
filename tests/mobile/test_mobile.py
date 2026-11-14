import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ledgerline.config import get_settings
from ledgerline.ledger.chart import create_administration
from ledgerline.main import create_app
from ledgerline.mobile import tokens
from ledgerline.models import ApiToken, AuditEvent, InboxDocument

STATIC = Path(__file__).resolve().parents[2] / "src" / "ledgerline" / "static"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def pdf(tag: str) -> bytes:
    return f"%PDF-1.4\n% {tag}\n%%EOF".encode()


@pytest.fixture(autouse=True)
def _fresh_limits():
    tokens.reset_rate_limits()
    yield
    tokens.reset_rate_limits()


@pytest.fixture
def anon(database_url):
    return TestClient(create_app())


@pytest.fixture
def token(session, owner, admin):
    row, value = tokens.create_token(session, owner, admin.id, "iPhone")
    session.commit()
    return row, value


def bearer(value):
    return {"Authorization": f"Bearer {value}"}


def docs(session):
    session.expire_all()
    return session.scalars(select(InboxDocument).order_by(InboxDocument.id)).all()


def test_capture_page_has_camera_and_file_inputs(client):
    html = client.get("/capture").text
    assert 'accept="image/*" capture="environment"' in html
    assert "multiple" in html and 'name="note"' in html


def test_capture_post_stores_phone_documents_and_dedupes(client, session, admin):
    files = [
        ("files", ("a.pdf", pdf("one"), "application/pdf")),
        ("files", ("b.jpg", JPEG, "image/jpeg")),
    ]
    response = client.post(
        "/capture", data={"csrf_token": client.csrf, "note": "Fuel"}, files=files
    )
    assert response.status_code == 200
    assert "In your inbox" in response.text
    stored = docs(session)
    assert [(d.source, d.subject) for d in stored] == [("phone", "Fuel"), ("phone", "Fuel")]
    assert f'href="/inbox/{stored[0].id}"' in response.text
    assert "Capture another" in response.text

    again = client.post("/capture", data={"csrf_token": client.csrf}, files=files[:1])
    assert "already in your inbox" in again.text
    assert len(docs(session)) == 2


def test_capture_rejects_unsupported_type_and_empty_submit(client, session):
    bad = client.post(
        "/capture",
        data={"csrf_token": client.csrf},
        files=[("files", ("x.gif", b"GIF89a....", "image/gif"))],
    )
    assert "not supported" in bad.text
    assert docs(session) == []
    empty = client.post("/capture", data={"csrf_token": client.csrf}, follow_redirects=False)
    assert empty.status_code == 303 and empty.headers["location"] == "/capture"


def test_capture_post_forbidden_for_readonly_and_needs_csrf(readonly_client, client):
    files = [("files", ("a.pdf", pdf("ro"), "application/pdf"))]
    assert (
        readonly_client.post(
            "/capture", data={"csrf_token": readonly_client.csrf}, files=files
        ).status_code
        == 403
    )
    assert client.post("/capture", data={"csrf_token": "wrong"}, files=files).status_code == 403


def test_capture_json_reply_for_the_offline_queue(client, session, admin):
    files = [("files", ("q.pdf", pdf("queued"), "application/pdf"))]
    data = {"csrf_token": client.csrf, "administration_id": str(admin.id)}
    first = client.post("/capture", data=data, files=files, headers={"Accept": "application/json"})
    assert first.status_code == 200
    [row] = first.json()["results"]
    assert row["duplicate"] is False and "error" not in row
    again = client.post("/capture", data=data, files=files, headers={"Accept": "application/json"})
    assert again.json()["results"][0] == {**row, "duplicate": True}
    assert len(docs(session)) == 1
    bad = client.post(
        "/capture",
        data=data,
        files=[("files", ("x.gif", b"GIF89a....", "image/gif"))],
        headers={"Accept": "application/json"},
    )
    assert "not supported" in bad.json()["results"][0]["error"]


def test_capture_lands_in_the_administration_it_was_captured_for(client, session, admin):
    from ledgerline.ledger.chart import create_administration

    second = create_administration(session, name="Tweede Zaak", kvk_number="11112222")
    session.commit()
    response = client.post(
        "/capture",
        data={"csrf_token": client.csrf, "administration_id": str(second.id)},
        files=[("files", ("late.pdf", pdf("late"), "application/pdf"))],
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200 and response.json()["administration"] == "Tweede Zaak"
    [doc] = docs(session)
    assert doc.administration_id == second.id != admin.id


def test_capture_refuses_an_administration_it_cannot_resolve(
    client, session, other_admin, admin
):
    files = [("files", ("n.pdf", pdf("nope"), "application/pdf"))]
    for bogus in (str(other_admin.id), "999999", "abc"):
        response = client.post(
            "/capture",
            data={"csrf_token": client.csrf, "administration_id": bogus},
            files=files,
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 409, bogus
    assert docs(session) == []


def test_api_upload_multipart_then_duplicate(anon, token, session, admin):
    row, value = token
    first = anon.post(
        "/api/v1/inbox",
        headers=bearer(value),
        files={"file": ("r.pdf", pdf("api"), "application/pdf")},
    )
    assert first.status_code == 201
    body = first.json()
    assert body["status"] == "new" and body["duplicate"] is False
    again = anon.post(
        "/api/v1/inbox",
        headers=bearer(value),
        files={"file": ("r.pdf", pdf("api"), "application/pdf")},
    )
    assert again.status_code == 200
    assert again.json() == {"id": body["id"], "status": "new", "duplicate": True}
    [doc] = docs(session)
    assert (doc.source, doc.administration_id) == ("phone", admin.id)


def test_api_upload_raw_bytes_with_filename(anon, token, session):
    _, value = token
    response = anon.post(
        "/api/v1/inbox?filename=photo.jpg",
        headers={**bearer(value), "Content-Type": "image/jpeg"},
        content=JPEG,
    )
    assert response.status_code == 201
    session.expire_all()
    doc = session.scalar(select(InboxDocument))
    assert doc.attachment_id and doc.id == response.json()["id"]


def test_api_rejects_missing_invalid_and_revoked_tokens_identically(
    anon, token, session, owner, admin
):
    row, value = token
    files = {"file": ("r.pdf", pdf("auth"), "application/pdf")}
    missing = anon.post("/api/v1/inbox", files=files)
    invalid = anon.post("/api/v1/inbox", headers=bearer("ll_nope"), files=files)
    assert tokens.revoke_token(session, owner, admin.id, row.id)
    session.commit()
    revoked = anon.post("/api/v1/inbox", headers=bearer(value), files=files)
    assert {r.status_code for r in (missing, invalid, revoked)} == {401}
    assert missing.json() == invalid.json() == revoked.json()
    assert docs(session) == []


def test_api_rate_limit(anon, token, monkeypatch):
    _, value = token
    monkeypatch.setattr(tokens, "RATE_LIMIT", 2)
    codes = [
        anon.post(
            "/api/v1/inbox",
            headers=bearer(value),
            files={"file": (f"{i}.pdf", pdf(f"rl{i}"), "application/pdf")},
        ).status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]


def test_api_oversize_and_unsupported_type(anon, token, monkeypatch, session):
    _, value = token
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 200)
    big = anon.post(
        "/api/v1/inbox",
        headers=bearer(value),
        files={"file": ("big.pdf", b"%PDF-" + b"x" * 500, "application/pdf")},
    )
    assert big.status_code == 413
    raw_big = anon.post("/api/v1/inbox", headers=bearer(value), content=b"%PDF-" + b"x" * 500)
    assert raw_big.status_code == 413
    gif = anon.post(
        "/api/v1/inbox",
        headers=bearer(value),
        files={"file": ("a.gif", b"GIF89a....", "image/gif")},
    )
    assert gif.status_code == 415
    assert docs(session) == []


def test_token_is_stored_hashed_only(session, token):
    row, value = token
    assert value.startswith("ll_") and len(value) > 40
    stored = session.scalar(select(ApiToken))
    assert stored.token_sha256 == tokens.hash_token(value)
    assert stored.token_prefix == value[:8]
    assert value not in " ".join(str(getattr(stored, c.name)) for c in ApiToken.__table__.columns)


def test_token_cannot_upload_into_another_administration(anon, session, owner, admin):
    other = create_administration(session, name="Andere BV")
    session.commit()
    row, value = tokens.create_token(session, owner, admin.id, "iPhone")
    session.commit()
    assert (
        anon.post(
            "/api/v1/inbox",
            headers=bearer(value),
            files={"file": ("a.pdf", pdf("scope"), "application/pdf")},
        ).status_code
        == 201
    )
    [doc] = docs(session)
    assert doc.administration_id == admin.id != other.id


def test_last_used_updated_and_upload_audited(anon, token, session):
    row, value = token
    assert row.last_used_at is None
    anon.post(
        "/api/v1/inbox",
        headers=bearer(value),
        files={"file": ("a.pdf", pdf("lu"), "application/pdf")},
    )
    session.expire_all()
    assert session.get(ApiToken, row.id).last_used_at is not None
    actions = set(session.scalars(select(AuditEvent.action)))
    assert {"api_token.create", "api_token.upload", "inbox.receive"} <= actions


def test_devices_page_create_shows_token_once_and_revoke(client, session):
    created = client.post(
        "/settings/devices", data={"csrf_token": client.csrf, "name": "My iPhone"}
    )
    assert created.status_code == 200
    shown = re.search(r'id="new-token"[^>]*value="(ll_[^"]+)"', created.text).group(1)
    assert created.headers["cache-control"] == "no-store"
    listing = client.get("/settings/devices")
    assert shown not in listing.text
    assert "My iPhone" in listing.text and shown[:8] in listing.text
    session.expire_all()
    row = session.scalar(select(ApiToken))
    assert row.token_sha256 == tokens.hash_token(shown)

    client.csrf = re.search(r'name="csrf_token" value="([^"]+)"', listing.text).group(1)
    done = client.post(
        f"/settings/devices/{row.id}/revoke",
        data={"csrf_token": client.csrf},
        follow_redirects=False,
    )
    assert done.status_code == 303
    session.expire_all()
    assert session.get(ApiToken, row.id).revoked_at is not None
    assert {"api_token.create", "api_token.revoke"} <= set(
        session.scalars(select(AuditEvent.action))
    )
    assert (
        client.post("/settings/devices/9999/revoke", data={"csrf_token": client.csrf}).status_code
        == 404
    )


def test_devices_owner_only(readonly_client):
    assert readonly_client.get("/settings/devices").status_code == 403
    assert (
        readonly_client.post(
            "/settings/devices", data={"csrf_token": readonly_client.csrf, "name": "x"}
        ).status_code
        == 403
    )


def test_devices_nav_is_settings(client):
    assert re.search(
        r'class="nav-link is-active"[^>]*>Settings<|href="/settings" class="nav-link is-active"',
        client.get("/settings/devices").text,
    )


def test_manifest_and_service_worker_headers(anon):
    manifest = anon.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["start_url"] == "/capture" and data["display"] == "standalone"
    assert {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
    sw = anon.get("/sw.js")
    assert sw.headers["content-type"].startswith("text/javascript")
    assert sw.headers["service-worker-allowed"] == "/"
    assert "Content-Security-Policy" in sw.headers


def test_service_worker_precaches_static_only():
    source = (STATIC / "sw.js").read_text()
    listed = re.search(r"const PRECACHE = \[(.*?)\];", source, re.S).group(1)
    paths = re.findall(r'"([^"]+)"|OFFLINE_URL', listed)
    entries = [p for p in paths if p] + ["/static/offline.html"]
    assert entries and all(p.startswith("/static/") for p in entries)
    assert 'url.pathname.startsWith("/static/")' in source
    assert "/capture" not in entries and not any(p.startswith("/api") for p in entries)
    assert "cache.put" in source and source.count("cache.put") == 1
    assert (
        "You're offline — photos can't be uploaded right now"
        in (STATIC / "offline.html").read_text()
    )
    for path in entries:
        assert (STATIC.parent / path.lstrip("/")).exists(), path


def test_icons_are_valid_pngs():
    for name, size in (("icon-192", 192), ("icon-512", 512), ("icon-180", 180)):
        raw = (STATIC / "img" / f"{name}.png").read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        assert int.from_bytes(raw[16:20], "big") == size == int.from_bytes(raw[20:24], "big")


def test_base_head_links(client):
    html = client.get("/capture").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<meta name="theme-color" content="#0f3d3e">' in html
    assert 'rel="apple-touch-icon"' in html and "icon-180.png" in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
