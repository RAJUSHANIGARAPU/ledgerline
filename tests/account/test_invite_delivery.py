"""Invitation mail outcome (sent or failed, audited, retryable) and the operator CLI."""
import smtplib

import pytest

from ledgerline import cli, mail
from tests.account.conftest import link_in, path_of
from tests.account.test_invitations import PASSWORD, accept, flash_of, invite


@pytest.fixture
def broken_smtp(outbox, monkeypatch):
    class Refusing:
        def __init__(self, *args, **kwargs):
            raise smtplib.SMTPConnectError(421, "refused")

    monkeypatch.setattr(mail.smtplib, "SMTP", Refusing)


def actions(q):
    return [r.action for r in q("SELECT action FROM audit_events WHERE action LIKE 'invite.%' "
                                "ORDER BY id")]


def test_a_failed_mail_is_reported_audited_and_marked(client, broken_smtp, q):
    r = invite(client, "nieuw@example.com")
    kind, message = flash_of(client, r)
    assert kind == "error" and "could not be sent" in message and "on its way" not in message
    assert actions(q) == ["invite.mail_failed"]
    page = client.get("/settings").text
    assert "invited, email not delivered" in page and "Resend invitation" in page


def test_the_success_flash_says_sent_not_on_its_way(client, outbox, q):
    kind, message = flash_of(client, invite(client, "nieuw@example.com"))
    assert kind == "success" and "was sent" in message and "on its way" not in message
    assert actions(q) == ["invite.sent"]
    assert "invited, email not delivered" not in client.get("/settings").text


def test_resend_after_a_failure_sends_a_fresh_working_link(client, outbox, monkeypatch, q):
    real = mail.smtplib.SMTP

    class Refusing:
        def __init__(self, *args, **kwargs):
            raise smtplib.SMTPConnectError(421, "refused")

    monkeypatch.setattr(mail.smtplib, "SMTP", Refusing)
    assert flash_of(client, invite(client, "nieuw@example.com"))[0] == "error"
    user_id = q("SELECT id FROM users WHERE email = 'nieuw@example.com'")[0].id
    first_token = q("SELECT token_hash FROM auth_tokens WHERE user_id = :u", u=user_id)[0][0]

    monkeypatch.setattr(mail.smtplib, "SMTP", real)
    r = client.post(f"/settings/users/{user_id}/resend", data={"csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303 and flash_of(client, r)[0] == "success"
    assert actions(q) == ["invite.mail_failed", "invite.sent"]
    assert len(outbox) == 1
    rows = q("SELECT token_hash, used_at FROM auth_tokens WHERE user_id = :u ORDER BY id",
             u=user_id)
    assert rows[0].token_hash == first_token and rows[0].used_at is not None
    assert rows[1].used_at is None
    assert accept(None, path_of(link_in(outbox[0])), PASSWORD)[1].status_code == 303
    assert "invited, email not delivered" not in client.get("/settings").text


def test_resend_is_owner_only_and_needs_a_pending_invitation(client, owner, bookkeeper_client,
                                                              outbox):
    assert client.post(f"/settings/users/{owner.id}/resend",
                       data={"csrf_token": client.csrf}).status_code == 404
    assert bookkeeper_client.post(f"/settings/users/{owner.id}/resend",
                                  data={"csrf_token": bookkeeper_client.csrf}
                                  ).status_code == 403
    assert outbox == []


def test_resend_cannot_reach_another_tenants_invitation(client, other_owner, other_tenant,
                                                         outbox, q):
    from ledgerline.db import tenant_session
    from ledgerline.models import Tenant

    with tenant_session(other_tenant.id) as s:
        from ledgerline.accounts import invitations

        invitations.prepare(s, s.get(Tenant, other_tenant.id), "elsewhere@example.com",
                            "bookkeeper")
        s.commit()
    foreign = q("SELECT id FROM users WHERE email = 'elsewhere@example.com'")[0].id
    assert client.post(f"/settings/users/{foreign}/resend",
                       data={"csrf_token": client.csrf}).status_code == 404
    assert outbox == []


# --- operator CLI ---------------------------------------------------------------------------

def test_invite_owner_sends_the_same_mail_token_and_audit(tenant, admin, outbox, q, capsys):
    assert cli.main(["invite-owner", "--tenant", str(tenant.id),
                     "--email", "Owner@Client.example"]) == 0
    assert "Invitation sent to owner@client.example" in capsys.readouterr().out
    assert len(outbox) == 1 and outbox[0]["To"] == "owner@client.example"
    link = link_in(outbox[0])
    assert "/invite/" in link
    membership = q("SELECT role, status, tenant_id FROM memberships")[0]
    assert (membership.role, membership.status, membership.tenant_id) == (
        "owner", "pending", tenant.id)
    token = q("SELECT purpose, used_at, expires_at - created_at AS ttl FROM auth_tokens")[0]
    assert token.purpose == "invite" and token.used_at is None
    from datetime import timedelta

    assert timedelta(days=6, hours=23) < token.ttl <= timedelta(days=7, seconds=5)
    event = q("SELECT user_id, details FROM audit_events WHERE action = 'invite.sent'")[0]
    assert event.user_id is None and event.details == {"role": "owner", "source": "cli"}
    anon, response = accept(None, path_of(link))
    assert response.status_code == 303


def test_invite_owner_reports_a_failed_mail(
    tenant, admin, broken_smtp, monkeypatch, q, capsys
):
    assert cli.main(["invite-owner", "--tenant", str(tenant.id),
                     "--email", "owner@client.example"]) == 1
    assert "could not be sent" in capsys.readouterr().err
    assert actions(q) == ["invite.mail_failed"]


def test_invite_owner_rerun_after_failure_works(tenant, admin, outbox, monkeypatch, q):
    real = mail.smtplib.SMTP
    monkeypatch.setattr(mail.smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(
        smtplib.SMTPConnectError(421, "refused")))
    args = ["invite-owner", "--tenant", str(tenant.id), "--email", "owner@client.example"]
    assert cli.main(args) == 1
    monkeypatch.setattr(mail.smtplib, "SMTP", real)
    assert cli.main(args) == 0
    assert actions(q) == ["invite.mail_failed", "invite.sent"]
    assert len(outbox) == 1
    assert q("SELECT count(*) FROM memberships")[0][0] == 1


def test_invite_owner_refuses_unknown_tenant_bad_address_and_missing_smtp(
    tenant, outbox, monkeypatch, capsys, q
):
    assert cli.main(["invite-owner", "--tenant", "9999", "--email", "a@b.example"]) == 1
    assert "does not exist" in capsys.readouterr().err
    assert cli.main(["invite-owner", "--tenant", str(tenant.id), "--email", "nope"]) == 1
    assert "not valid" in capsys.readouterr().err
    from ledgerline.config import get_settings

    monkeypatch.setenv("LEDGERLINE_SMTP_HOST", "")
    monkeypatch.setenv("LEDGERLINE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    assert cli.main(["invite-owner", "--tenant", str(tenant.id), "--email", "a@b.example"]) == 1
    assert "email is not set up" in capsys.readouterr().err
    assert q("SELECT count(*) FROM users")[0][0] == 0 and outbox == []


def test_invite_owner_for_an_existing_member_sends_nothing(tenant, admin, owner, outbox, capsys):
    assert cli.main(["invite-owner", "--tenant", str(tenant.id), "--email", owner.email]) == 0
    assert "already a member" in capsys.readouterr().out
    assert outbox == []
