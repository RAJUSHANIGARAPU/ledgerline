import imaplib

import pytest
from sqlalchemy import select

from ledgerline.config import get_settings
from ledgerline.inbox import email_intake
from ledgerline.ledger.chart import create_administration
from ledgerline.models import Administration, Attachment, InboxDocument
from tests.inbox.factories import eml, invoice_html, jpeg_bytes, pdf_from_html, ubl


class FakeMailbox:
    def __init__(self, messages):
        self.messages = dict(messages)
        self.folders: dict[str, list[bytes]] = {}
        self.closed = False

    def unseen(self):
        return list(self.messages)

    def fetch(self, uid):
        return self.messages[uid]

    def move(self, uid, folder):
        self.folders.setdefault(folder, []).append(self.messages.pop(uid))

    def close(self):
        self.closed = True


@pytest.fixture
def settings():
    return get_settings().model_copy(
        update={
            "imap_host": "mail.example",
            "imap_user": "invoices@jouwbedrijf.example",
            "imap_default_administration_id": 1,
            "imap_allowed_senders": "",
            "imap_processed_folder": "Ledgerline/Processed",
        }
    )


def poll(session, mailbox, settings):
    return email_intake.poll_once(session, lambda _s: mailbox, settings)


PDF = ("inv.pdf", pdf_from_html(invoice_html()), "application", "pdf")


def test_attachments_are_stored_and_mail_is_moved_not_deleted(session, admin, settings):
    raw = eml(attachments=[PDF, ("foto.jpg", jpeg_bytes(), "image", "jpeg")])
    mailbox = FakeMailbox({"1": raw})
    result = poll(session, mailbox, settings)
    docs = session.scalars(select(InboxDocument).order_by(InboxDocument.id)).all()
    assert (result.messages, result.documents) == (1, 2)
    assert [d.source for d in docs] == ["email", "email"]
    assert docs[0].sender == "boekhouder@leverancier.example"
    assert (
        docs[0].subject == "Factuur 2026-0042"
        and docs[0].email_message_id == "<m1@leverancier.example>"
    )
    assert docs[0].administration_id == admin.id and docs[0].status == "new"
    assert mailbox.folders == {"Ledgerline/Processed": [raw]} and mailbox.messages == {}
    assert mailbox.closed


def test_same_attachment_twice_makes_one_document(session, admin, settings):
    mailbox = FakeMailbox(
        {
            "1": eml(attachments=[PDF], message_id="<a@x>"),
            "2": eml(attachments=[PDF, PDF], message_id="<b@x>"),
        }
    )
    result = poll(session, mailbox, settings)
    assert len(session.scalars(select(InboxDocument)).all()) == 1
    assert len(session.scalars(select(Attachment)).all()) == 1
    assert (result.documents, result.duplicates) == (1, 2)
    assert len(mailbox.folders["Ledgerline/Processed"]) == 2


def test_plus_address_routes_to_that_administration(session, admin, settings):
    other = create_administration(session, name="Tweede BV")
    session.commit()
    mailbox = FakeMailbox(
        {"1": eml(to=f"invoices+{other.id}@jouwbedrijf.example", attachments=[PDF])}
    )
    poll(session, mailbox, settings)
    assert session.scalars(select(InboxDocument)).one().administration_id == other.id


def test_unknown_plus_id_falls_back_to_the_default(session, admin, settings):
    mailbox = FakeMailbox({"1": eml(to="invoices+999@jouwbedrijf.example", attachments=[PDF])})
    poll(session, mailbox, settings)
    assert session.scalars(select(InboxDocument)).one().administration_id == admin.id


def test_plus_address_of_another_mailbox_is_not_a_routing_hint(session, admin, settings):
    other = create_administration(session, name="Tweede BV")
    session.commit()
    mailbox = FakeMailbox(
        {"1": eml(to=f"someone+{other.id}@jouwbedrijf.example", attachments=[PDF])}
    )
    poll(session, mailbox, settings)
    assert session.scalars(select(InboxDocument)).one().administration_id == admin.id


@pytest.mark.parametrize(
    "allowed, sender, accepted",
    [
        ("boekhouder@leverancier.example", "Boekhouder <BOEKHOUDER@leverancier.example>", True),
        ("@leverancier.example", "x@leverancier.example", True),
        ("@leverancier.example", "x@evil-leverancier.example", False),
        ("a@b.example, @c.example", "x@d.example", False),
        ("", "anyone@anywhere.example", True),
    ],
)
def test_sender_allow_list(session, admin, settings, allowed, sender, accepted):
    settings = settings.model_copy(update={"imap_allowed_senders": allowed})
    raw = eml(sender=sender, attachments=[PDF])
    mailbox = FakeMailbox({"1": raw})
    result = poll(session, mailbox, settings)
    assert len(session.scalars(select(InboxDocument)).all()) == (1 if accepted else 0)
    assert result.rejected == (0 if accepted else 1)
    folder = "Ledgerline/Processed" if accepted else "Ledgerline/Rejected"
    assert mailbox.folders == {folder: [raw]}


def test_mail_without_attachment_creates_nothing_but_is_moved(session, admin, settings):
    mailbox = FakeMailbox({"1": eml(body="Factuur staat in deze mail: 100 euro")})
    result = poll(session, mailbox, settings)
    assert result.documents == 0 and session.scalars(select(InboxDocument)).all() == []
    assert len(mailbox.folders["Ledgerline/Processed"]) == 1


def test_unwanted_attachments_are_ignored(session, admin, settings):
    mailbox = FakeMailbox(
        {
            "1": eml(
                attachments=[
                    ("virus.exe", b"MZ\x90\x00", "application", "octet-stream"),
                    ("notes.txt", b"hello", "text", "plain"),
                    ("fake.pdf", b"not a pdf at all", "application", "pdf"),
                ]
            )
        }
    )
    result = poll(session, mailbox, settings)
    assert result.documents == 0 and session.scalars(select(InboxDocument)).all() == []


def test_ubl_and_pdf_in_one_mail_are_both_stored(session, admin, settings):
    xml = ("inv.xml", ubl([("Hosting", 10000, "S", 21)]), "application", "xml")
    poll(session, FakeMailbox({"1": eml(attachments=[PDF, xml])}), settings)
    docs = session.scalars(select(InboxDocument)).all()
    assert len(docs) == 2 and len({d.email_message_id for d in docs}) == 1


def test_failure_leaves_the_mail_in_place(session, admin, settings, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(email_intake.service, "receive", boom)
    mailbox = FakeMailbox({"1": eml(attachments=[PDF])})
    result = poll(session, mailbox, settings)
    assert result.skipped == 1 and mailbox.folders == {} and "1" in mailbox.messages


def test_no_administration_leaves_the_mail_in_place(session, settings):
    assert session.scalars(select(Administration)).all() == []
    mailbox = FakeMailbox({"1": eml(attachments=[PDF])})
    result = poll(session, mailbox, settings)
    assert result.skipped == 1 and mailbox.folders == {}


def test_rejected_folder_name():
    s = get_settings().model_copy(update={"imap_processed_folder": "Done"})
    assert email_intake.rejected_folder(s) == "Done-Rejected"


class FakeImap:
    """Records imaplib calls so the adapter's command sequence can be checked."""

    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.calls = []

    def select(self, folder):
        self.calls.append(("select", folder))
        return "OK", [b"1"]

    def uid(self, *args):
        self.calls.append(("uid", *args))
        if args[0] == "SEARCH":
            return "OK", [b"4 7"]
        if args[0] == "FETCH":
            return "OK", [(b"1 (BODY[] {3}", b"raw"), b")"]
        return "OK", [b""]

    def create(self, folder):
        self.calls.append(("create", folder))
        return "OK", [b""]

    def expunge(self):
        self.calls.append(("expunge",))

    def logout(self):
        raise imaplib.IMAP4.error("already gone")


def test_imap_adapter_uses_move_when_supported():
    client = FakeImap(("IMAP4REV1", "MOVE"))
    box = email_intake.ImapMailbox(client, "INBOX")
    assert box.unseen() == ["4", "7"] and box.fetch("4") == b"raw"
    box.move("4", "Ledgerline/Processed")
    assert ("uid", "MOVE", "4", '"Ledgerline/Processed"') in client.calls
    assert not any(c[0] == "uid" and c[1] in ("STORE", "COPY") for c in client.calls)
    assert ("uid", "FETCH", "4", "(BODY.PEEK[])") in client.calls
    box.close()


def test_imap_adapter_copy_then_flag_without_move_capability():
    client = FakeImap(("IMAP4REV1",))
    box = email_intake.ImapMailbox(client, "INBOX")
    box.move("4", "Ledgerline/Processed")
    box.move("7", "Ledgerline/Processed")
    verbs = [c[1] for c in client.calls if c[0] == "uid"]
    assert verbs == ["COPY", "STORE", "COPY", "STORE"]
    assert [c for c in client.calls if c[0] == "create"] == [("create", '"Ledgerline/Processed"')]
