import re

import pytest
from sqlalchemy import select

from ledgerline.inbox import forwarding
from ledgerline.models import AuditEvent, ForwardingNotice, InboxDocument
from tests.conftest import tenant_db
from tests.inbox.factories import eml
from tests.inbox.test_email_intake import FakeMailbox, poll, settings  # noqa: F401

LINK = "https://mail-settings.google.com/mail/vf-%5BANGjdJ%5D-abc123?x=1"
GOOGLE = "forwarding-noreply@google.com"
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


PASS = "mx.jouwbedrijf.example; dkim=pass header.d=google.com header.s=20230601; spf=pass"


def confirmation(token, body=None, sender=GOOGLE, message_id="<c1@google.com>", auth=PASS):
    body = body if body is not None else f"Confirm forwarding: {LINK}\nIgnore otherwise."
    headers = {"Authentication-Results": auth} if auth else None
    return eml(sender=sender, to=f"invoices+{token}@jouwbedrijf.example", body=body,
               subject="Gmail Forwarding Confirmation", message_id=message_id,
               extra_headers=headers)


def notices(session):
    return list(session.scalars(select(ForwardingNotice)))


def test_confirmation_to_a_valid_address_creates_a_notice(session, admin, settings):  # noqa: F811
    raw = confirmation(admin.inbox_token)
    mailbox = FakeMailbox({"1": raw})
    result = poll(session, mailbox, settings)
    (notice,) = notices(session)
    assert (notice.administration_id, notice.kind, notice.link) == (admin.id, "gmail", LINK)
    assert (result.documents, result.rejected) == (0, 0)
    assert mailbox.folders == {"Ledgerline/Processed": [raw]}
    assert session.scalars(select(InboxDocument)).all() == []
    assert session.scalars(select(AuditEvent.action).where(
        AuditEvent.action == "inbox.forwarding_notice.created")).one()


def test_same_link_twice_makes_one_notice(session, admin, settings):  # noqa: F811
    mailbox = FakeMailbox({"1": confirmation(admin.inbox_token),
                           "2": confirmation(admin.inbox_token, message_id="<c2@google.com>")})
    poll(session, mailbox, settings)
    assert len(notices(session)) == 1


def test_invalid_token_is_rejected_as_before(session, admin, other_admin, settings):  # noqa: F811
    raw = confirmation("0123456789abcdef0123456789")
    mailbox = FakeMailbox({"1": raw})
    result = poll(session, mailbox, settings)
    assert result.rejected == 1 and mailbox.folders == {"Ledgerline/Rejected": [raw]}
    with tenant_db(admin.tenant_id) as s, tenant_db(other_admin.tenant_id) as t:
        assert notices(s) == [] and notices(t) == []


def test_invalid_token_on_a_single_tenant_gets_no_notice(session, admin, settings):  # noqa: F811
    mailbox = FakeMailbox({"1": confirmation("0123456789abcdef0123456789")})
    poll(session, mailbox, settings)
    assert notices(session) == []


def test_other_senders_do_not_create_notices(session, admin, settings):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token, sender="x@evil.example")}),
         settings)
    assert notices(session) == []


@pytest.mark.parametrize("body", [
    "Open http://mail-settings.google.com/mail/vf-1",
    "Open https://evil.example/google.com/vf-1",
    "Open https://google.com.evil.example/vf-1",
    "Open https://mail-settings.google.com@evil.example/vf-1",
    "Open https://notgoogle.com/vf-1",
    "Open https://evil.example\\.google.com/vf-1",
    "Open https://evil.example%5C.google.com/vf-1",
    "Open https://sites.google.com/vf-1",
    "Open https://docs.google.com/vf-1",
    "Open https://google.com/vf-1",
    "Open https://mail-settings.google.com:8443/vf-1",
    "Open https://mail-settings.google.com.evil.example/vf-1",
    "No link at all",
])
def test_non_google_links_are_not_surfaced(session, admin, settings, body):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token, body=body)}), settings)
    assert notices(session) == []


def test_first_google_link_wins_over_earlier_foreign_links(session, admin, settings):  # noqa: F811
    body = f"https://evil.example/x then {LINK}"
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token, body=body)}), settings)
    assert notices(session)[0].link == LINK


def test_notice_is_only_for_the_addressed_administration(
        session, admin, other_admin, other_tenant, settings):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(other_admin.inbox_token)}), settings)
    with tenant_db(other_tenant.id) as s:
        assert [n.administration_id for n in notices(s)] == [other_admin.id]
    with tenant_db(admin.tenant_id) as s:
        assert notices(s) == []


def test_expired_and_dismissed_notices_are_hidden(session, admin, settings):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token)}), settings)
    (notice,) = notices(session)
    assert forwarding.active_notices(session, admin.id) == [notice]
    notice.expires_at = notice.received_at
    session.commit()
    assert forwarding.active_notices(session, admin.id) == []


def test_owner_sees_and_dismisses_the_notice(client, session, admin, settings):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token)}), settings)
    page = client.get("/inbox").text
    assert LINK.replace("&", "&amp;") in page or LINK in page
    notice = notices(session)[0]
    response = client.post(f"/inbox/forwarding-notices/{notice.id}/dismiss",
                           data={"csrf_token": client.csrf}, follow_redirects=False)
    assert response.status_code == 303
    assert LINK not in client.get("/inbox").text
    session.expire_all()
    assert notices(session)[0].dismissed_at is not None


def test_bookkeeper_does_not_see_the_notice(bookkeeper_client, session, admin, settings):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token)}), settings)
    assert LINK not in bookkeeper_client.get("/inbox").text


def test_another_tenant_cannot_dismiss_the_notice(
        session, admin, other_admin, other_owner, settings):  # noqa: F811
    from tests.conftest import _login

    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token)}), settings)
    notice_id = notices(session)[0].id
    stranger = _login(other_owner.email)
    response = stranger.post(f"/inbox/forwarding-notices/{notice_id}/dismiss",
                             data={"csrf_token": stranger.csrf}, follow_redirects=False)
    assert response.status_code == 404
    session.expire_all()
    assert notices(session)[0].dismissed_at is None


def test_confirmation_is_not_subject_to_the_sender_allow_list(session, admin, settings):  # noqa: F811
    strict = settings.model_copy(update={"imap_allowed_senders": "@leverancier.example"})
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token)}), strict)
    assert len(notices(session)) == 1


def test_mail_without_a_passing_google_dkim_is_labelled_unverified(session, admin, settings):  # noqa: F811
    for auth in (None, "mx.x.example; dkim=pass header.d=evil.example",
                 "mx.x.example; dkim=fail header.d=google.com"):
        poll(session, FakeMailbox({"1": confirmation(admin.inbox_token, auth=auth)}), settings)
        for n in notices(session):
            session.delete(n)
        session.commit()
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token, auth=None)}), settings)
    assert [n.kind for n in notices(session)] == ["unverified"]


def test_verified_mail_is_marked_gmail(session, admin, settings):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token)}), settings)
    assert [n.kind for n in notices(session)] == ["gmail"]


def test_unverified_notice_is_not_styled_as_gmail(client, session, admin, settings):  # noqa: F811
    poll(session, FakeMailbox({"1": confirmation(admin.inbox_token, auth=None)}), settings)
    page = client.get("/inbox").text
    assert "Unverified sender" in page and "Gmail asks you to confirm" not in page


def test_active_notices_are_capped_per_administration(session, admin, settings):  # noqa: F811
    for n in range(5):
        body = f"https://mail-settings.google.com/mail/vf-{n}"
        poll(session, FakeMailbox({"1": confirmation(
            admin.inbox_token, body=body, message_id=f"<c{n}@google.com>")}), settings)
    assert len(notices(session)) == forwarding.MAX_ACTIVE_NOTICES
