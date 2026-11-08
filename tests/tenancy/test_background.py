"""Work that spans tenants: the worker, email routing and phone tokens."""
import threading

from sqlalchemy import select

from ledgerline.config import get_settings
from ledgerline.db import for_each_tenant
from ledgerline.inbox import email_intake, service, worker
from ledgerline.models import InboxDocument, InboxStatus
from tests.conftest import tenant_db
from tests.inbox.factories import eml, ubl
from tests.inbox.test_email_intake import FakeMailbox


def _receive(tenant_id, administration_id, number):
    with tenant_db(tenant_id) as s:
        doc, _ = service.receive(s, administration_id, f"{number}.xml",
                                 ubl([("Hosting", 1000, "S", 21)], number=number), "upload")
        s.commit()
        return doc.id


def _statuses(tenant_id):
    with tenant_db(tenant_id) as s:
        return {d.id: d.status for d in s.scalars(select(InboxDocument))}


def test_worker_processes_every_tenant_in_its_own_context(tenant, other_tenant, admin,
                                                          other_admin, tmp_path):
    a = _receive(tenant.id, admin.id, "A-1")
    b = _receive(other_tenant.id, other_admin.id, "B-1")
    stop = threading.Event()

    class Mailbox:
        def unseen(self):
            stop.set()
            return []

        def close(self):
            pass

    worker.run_forever(stop, mailbox_factory=lambda _s: Mailbox(), heartbeat=tmp_path / "beat")
    assert _statuses(tenant.id) == {a: InboxStatus.REVIEW}
    assert _statuses(other_tenant.id) == {b: InboxStatus.REVIEW}


def test_for_each_tenant_visits_each_tenant_with_its_own_rows(tenant, other_tenant, admin,
                                                              other_admin):
    _receive(tenant.id, admin.id, "A-1")
    _receive(other_tenant.id, other_admin.id, "B-1")
    seen = {}
    for tenant_id, session in for_each_tenant():
        seen[tenant_id] = session.scalars(select(InboxDocument.tenant_id)).all()
    assert seen == {tenant.id: [tenant.id], other_tenant.id: [other_tenant.id]}


def test_mail_is_routed_to_the_tenant_of_the_addressed_administration(
    session, tenant, other_tenant, admin, other_admin
):
    settings = get_settings().model_copy(update={
        "imap_host": "mail.example", "imap_user": "invoices@jouwbedrijf.example",
        "imap_default_administration_id": admin.id, "imap_allowed_senders": ""})
    pdf = ("a.xml", ubl([("Hosting", 1000, "S", 21)]), "application", "xml")
    to_b = eml(to=f"invoices+{other_admin.inbox_token}@jouwbedrijf.example", attachments=[pdf],
               message_id="<b@x>")
    to_a = eml(to=f"invoices+{admin.inbox_token}@jouwbedrijf.example", attachments=[pdf],
               message_id="<a@x>")
    mailbox = FakeMailbox({"1": to_b, "2": to_a})
    result = email_intake.poll_once(session, lambda _s: mailbox, settings)
    assert result.documents == 2
    with tenant_db(other_tenant.id) as s:
        assert [d.administration_id for d in s.scalars(select(InboxDocument))] == [other_admin.id]
    with tenant_db(tenant.id) as s:
        assert [d.administration_id for d in s.scalars(select(InboxDocument))] == [admin.id]


def test_with_several_tenants_unrouted_mail_is_rejected(session, tenant, other_tenant, admin,
                                                        other_admin):
    settings = get_settings().model_copy(update={
        "imap_host": "mail.example", "imap_user": "invoices@jouwbedrijf.example",
        "imap_default_administration_id": admin.id, "imap_allowed_senders": ""})
    pdf = ("a.xml", ubl([("Hosting", 1000, "S", 21)]), "application", "xml")
    stranger = eml(to=f"invoices+{admin.id}@jouwbedrijf.example", attachments=[pdf])
    mailbox = FakeMailbox({"1": stranger})
    result = email_intake.poll_once(session, lambda _s: mailbox, settings)
    assert (result.rejected, result.documents) == (1, 0)
    assert mailbox.folders == {"Ledgerline/Rejected": [stranger]}


def test_unknown_administration_is_skipped(session, admin):
    settings = get_settings().model_copy(update={
        "imap_host": "mail.example", "imap_user": "invoices@jouwbedrijf.example",
        "imap_default_administration_id": 9999, "imap_allowed_senders": ""})
    pdf = ("a.xml", ubl([("Hosting", 1000, "S", 21)]), "application", "xml")
    mailbox = FakeMailbox({"1": eml(attachments=[pdf])})
    assert email_intake.poll_once(session, lambda _s: mailbox, settings).skipped == 1


def test_phone_token_resolves_its_tenant(client, tenant, other_tenant, seeded_a, seeded_b):
    for seeded, label in ((seeded_a, "a"), (seeded_b, "b")):
        response = client.post(
            "/api/v1/inbox?filename=x.xml",
            content=ubl([("Hosting", 1000, "S", 21)], number=f"T-{label}"),
            headers={"Authorization": f"Bearer {seeded['token']}",
                     "Content-Type": "application/xml"})
        assert response.status_code == 201
    for tenant_ref, seeded in ((tenant, seeded_a), (other_tenant, seeded_b)):
        with tenant_db(tenant_ref.id) as s:
            owners = set(s.scalars(select(InboxDocument.administration_id)))
        assert owners == {seeded["administration_id"]}
    bad = client.post("/api/v1/inbox?filename=x.xml", content=b"x",
                      headers={"Authorization": "Bearer ll_unknown"})
    assert bad.status_code == 401
