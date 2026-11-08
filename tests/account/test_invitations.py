import hashlib
import re
from datetime import timedelta

import pytest

from tests.account.conftest import body, link_in, password_login, path_of

PASSWORD = "a-fresh-passphrase-1"


def invite(client, email, role="bookkeeper"):
    return client.post("/settings/users", data={"email": email, "role": role,
                                                "csrf_token": client.csrf},
                       follow_redirects=False)


def flash_of(client, response):
    page = client.get(response.headers["location"]).text
    return re.search(r'class="flash flash-(\w+)" role="status">([^<]+)<', page).groups()


def accept(_unused, path, password=PASSWORD, confirm=None):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    anon = TestClient(create_app())
    token = re.search(r'name="csrf_token" value="([^"]+)"', anon.get("/login").text).group(1)
    return anon, anon.post(path, data={"password": password, "confirm": confirm or password,
                                       "csrf_token": token}, follow_redirects=False)


def test_invite_new_user_creates_pending_login_and_emails_one_link(client, admin, outbox, q):
    r = invite(client, "Nieuw@Example.com")
    assert r.status_code == 303
    user = q("SELECT id, is_active, password_hash FROM users WHERE email = 'nieuw@example.com'")[0]
    assert user.is_active is False and not user.password_hash.startswith("$argon2")
    membership = q("SELECT role, status, tenant_id FROM memberships WHERE user_id = :u",
                   u=user.id)[0]
    assert (membership.role, membership.status, membership.tenant_id) == (
        "bookkeeper", "pending", admin.tenant_id)
    assert len(outbox) == 1 and outbox[0]["To"] == "nieuw@example.com"
    link = link_in(outbox[0])
    assert "/invite/" in link
    stored = q("SELECT token_hash, purpose, used_at, expires_at - created_at AS ttl "
               "FROM auth_tokens WHERE user_id = :u", u=user.id)[0]
    token = link.rsplit("/", 1)[1]
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored.token_hash and stored.purpose == "invite"
    assert stored.used_at is None
    assert timedelta(days=6, hours=23) < stored.ttl <= timedelta(days=7, seconds=5)
    assert len(token) >= 43


def test_invite_existing_user_adds_pending_membership_without_password_or_token(
    client, admin, other_owner, outbox, q
):
    invite(client, other_owner.email, "accountant")
    rows = q("SELECT tenant_id, status FROM memberships WHERE user_id = :u ORDER BY id",
             u=other_owner.id)
    assert [r.status for r in rows] == ["active", "pending"]
    assert q("SELECT count(*) FROM auth_tokens")[0][0] == 0
    assert q("SELECT count(*) FROM users WHERE email = :e", e=other_owner.email)[0][0] == 1
    text = body(outbox[0])
    assert "You were added to Testbedrijf" not in text  # tenant name, not administration
    assert "You were added to Tenant A" in text and "/invite/" not in text


def test_response_is_identical_whether_or_not_the_email_exists(client, other_owner, outbox):
    known = invite(client, other_owner.email)
    k = flash_of(client, known)
    unknown = invite(client, "nobody-here@example.com")
    u = flash_of(client, unknown)
    assert known.status_code == unknown.status_code == 303
    assert k[0] == u[0] == "success"
    assert k[1].replace(other_owner.email, "X") == u[1].replace("nobody-here@example.com", "X")
    assert len(outbox) == 2


def test_inviting_a_current_member_is_a_quiet_noop(client, owner, outbox, q):
    r = invite(client, owner.email)
    assert flash_of(client, r)[0] == "success"
    assert outbox == []
    assert q("SELECT count(*) FROM memberships WHERE user_id = :u", u=owner.id)[0][0] == 1


def test_accept_sets_password_activates_membership_and_allows_login(client, admin, outbox, q):
    invite(client, "nieuw@example.com")
    path = path_of(link_in(outbox[0]))
    _, response = accept(None, path)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    user = q("SELECT is_active FROM users WHERE email = 'nieuw@example.com'")[0]
    assert user.is_active is True
    assert q("SELECT status FROM memberships m JOIN users u ON u.id = m.user_id "
             "WHERE u.email = 'nieuw@example.com'")[0][0] == "active"
    logged_in, login = password_login("nieuw@example.com", PASSWORD)
    assert login.status_code == 303
    assert logged_in.get("/sales").status_code == 200


def test_weak_passwords_are_rejected_on_accept(client, outbox, q):
    invite(client, "nieuw@example.com")
    path = path_of(link_in(outbox[0]))
    for password, confirm in (("short", "short"), (PASSWORD, "different-confirm-1"),
                              ("a" * 20, "a" * 20), ("nieuw@example.com", "nieuw@example.com")):
        _, r = accept(None, path, password, confirm)
        assert r.status_code == 422, password
    assert q("SELECT is_active FROM users WHERE email = 'nieuw@example.com'")[0][0] is False


def test_token_is_single_use(client, outbox):
    invite(client, "nieuw@example.com")
    path = path_of(link_in(outbox[0]))
    assert accept(None, path)[1].status_code == 303
    again_client, again = accept(None, path)
    assert again.status_code == 404
    assert again_client.get(path).status_code == 404


def test_expired_token_is_rejected(client, outbox, clock):
    invite(client, "nieuw@example.com")
    path = path_of(link_in(outbox[0]))
    clock.advance(days=7, seconds=1)
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    assert TestClient(create_app()).get(path).status_code == 404


def test_tampered_or_unknown_token_is_rejected(client, outbox):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    invite(client, "nieuw@example.com")
    path = path_of(link_in(outbox[0]))
    anon = TestClient(create_app())
    assert anon.get(path[:-1] + ("A" if path[-1] != "A" else "B")).status_code == 404
    assert anon.get("/invite/" + "x" * 43).status_code == 404
    assert anon.get(path).status_code == 200


def test_a_reset_token_cannot_be_used_as_an_invite(client, owner, outbox, q):
    from fastapi.testclient import TestClient

    from ledgerline.accounts import tokens
    from ledgerline.db import get_sessionmaker
    from ledgerline.main import create_app

    with get_sessionmaker()() as s:
        token = tokens.issue(s, owner.id, "reset")
        s.commit()
    assert TestClient(create_app()).get(f"/invite/{token}").status_code == 404


def test_invite_for_tenant_a_never_grants_tenant_b(client, admin, other_admin, other_tenant,
                                                   outbox, q):
    invite(client, "nieuw@example.com")
    accept(None, path_of(link_in(outbox[0])))
    rows = q("SELECT tenant_id FROM memberships m JOIN users u ON u.id = m.user_id "
             "WHERE u.email = 'nieuw@example.com'")
    assert [r.tenant_id for r in rows] == [admin.tenant_id]
    member, _ = password_login("nieuw@example.com", PASSWORD)
    denied = member.post("/tenants", data={"tenant_id": other_tenant.id,
                                           "csrf_token": member.csrf})
    assert denied.status_code == 403
    assert "Andere Zaak" not in member.get("/settings").text


def test_pending_membership_grants_no_access_until_accepted(client, other_owner, other_tenant,
                                                            admin, outbox, q):
    invite(client, other_owner.email, "owner")
    member, response = password_login(other_owner.email)
    assert response.status_code == 303
    assert member.post("/tenants", data={"tenant_id": admin.tenant_id,
                                         "csrf_token": member.csrf}).status_code == 403
    page = member.get("/account").text
    assert "Invitations" in page and "Tenant A" in page
    pending = q("SELECT id FROM memberships WHERE status = 'pending'")[0].id
    r = member.post(f"/account/invitations/{pending}/accept", data={"csrf_token": member.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    assert member.post("/tenants", data={"tenant_id": admin.tenant_id, "csrf_token": member.csrf},
                       follow_redirects=False).status_code == 303


def test_user_cannot_accept_someone_elses_invitation(client, other_owner, admin, outbox, q):
    invite(client, other_owner.email)
    pending = q("SELECT id FROM memberships WHERE status = 'pending'")[0].id
    attacker, _ = password_login("owner@example.com")
    r = attacker.post(f"/account/invitations/{pending}/accept", data={"csrf_token": attacker.csrf})
    assert r.status_code == 404
    assert q("SELECT status FROM memberships WHERE id = :i", i=pending)[0][0] == "pending"


@pytest.mark.parametrize("who", ["readonly_client", "bookkeeper_client"])
def test_only_owners_can_invite(request, who, outbox, q):
    other = request.getfixturevalue(who)
    r = invite(other, "pwn@example.com", "owner")
    assert r.status_code == 403
    assert q("SELECT count(*) FROM users WHERE email = 'pwn@example.com'")[0][0] == 0
    assert outbox == []


def test_invite_is_refused_when_email_is_not_set_up(client, monkeypatch, q):
    from ledgerline.config import get_settings

    monkeypatch.setenv("LEDGERLINE_SMTP_HOST", "")
    monkeypatch.setenv("LEDGERLINE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    r = invite(client, "nieuw@example.com")
    kind, message = flash_of(client, r)
    assert kind == "error" and "Email is not set up" in message
    assert q("SELECT count(*) FROM users WHERE email = 'nieuw@example.com'")[0][0] == 0


def test_invalid_role_and_address_are_refused(client, outbox):
    assert flash_of(client, invite(client, "nieuw@example.com", "root"))[0] == "error"
    assert flash_of(client, invite(client, "not-an-email"))[0] == "error"
    assert outbox == []


def test_audit_records_invite_events_without_secrets(client, outbox, q):
    invite(client, "nieuw@example.com")
    link = link_in(outbox[0])
    accept(None, path_of(link))
    rows = q("SELECT action, details::text AS d FROM audit_events WHERE action LIKE 'invite.%' "
             "ORDER BY id")
    assert [r.action for r in rows] == ["invite.sent", "invite.accepted"]
    token = link.rsplit("/", 1)[1]
    assert all(token not in (r.d or "") for r in rows)
