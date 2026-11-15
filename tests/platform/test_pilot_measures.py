"""Pilot report: platform statements, uploads by who, and edits made at review."""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from ledgerline import cli
from ledgerline.mobile import tokens
from ledgerline.models import AuditEvent, ContactKind, InboxDocument
from ledgerline.platform import pilot
from tests.helpers import account, contact
from tests.inbox.factories import jpeg_bytes, thuisbezorgd_statement_html
from tests.inbox.test_platform import received, split_form
from tests.inbox.test_review_flow import create_data, upload, upload_ubl
from tests.mobile.test_mobile import JPEG, bearer  # noqa: F401
from tests.platform.test_pilot import WIDE, _report


@pytest.fixture
def supplier(session, admin):
    c = contact(session, admin, name="Bakker Software", kind=ContactKind.SUPPLIER,
                vat_number="NL 8533.45678 B01", iban="NL02 ABNA 0123 4567 89")
    session.commit()
    return c


def _create(client, session, admin, doc, contact_id, **extra):
    r = client.post(f"/inbox/{doc.id}/create", follow_redirects=False,
                    data=create_data(client, doc, contact_id, session, admin, **extra))
    assert r.status_code == 303, r.text
    session.expire_all()


def _details(session, doc, action="inbox.create_purchase"):
    return session.scalars(select(AuditEvent.details).where(
        AuditEvent.action == action, AuditEvent.entity_id == doc.id)).one()


def test_an_unedited_proposal_records_no_changed_field(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    _create(client, session, admin, doc, supplier.id)
    details = _details(session, doc)
    assert details["method"] == "ubl" and details["changed"] == []
    assert set(details) == {"purchase_invoice_id", "method", "changed"}


def test_edited_fields_are_named_without_values(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    _create(client, session, admin, doc, supplier.id, supplier_reference="INV-2026-18",
            invoice_date="2026-11-04", line_amount=["550.00", "123.45"],
            line_vat=["115.50", "25.92"])
    changed = _details(session, doc)["changed"]
    assert changed == ["invoice_number", "date", "net", "vat_21", "total"]
    assert not any(ch.isdigit() for name in changed for ch in name.replace("vat_21", ""))


def test_another_supplier_and_a_changed_rate_are_recorded(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    other = contact(session, admin, name="Andere BV", kind=ContactKind.SUPPLIER)
    session.commit()
    _create(client, session, admin, doc, other.id, line_vat_code=["I9", "I9"],
            line_vat=["45.00", "11.11"])
    assert _details(session, doc)["changed"] == ["supplier", "vat_21", "vat_9", "total"]


def test_a_one_cent_rounding_difference_is_not_an_edit(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    _create(client, session, admin, doc, supplier.id, line_vat=["105.00", "25.93"])
    assert _details(session, doc)["changed"] == []


def test_review_table_per_method(client, session, admin, supplier):
    first = upload_ubl(client, session)
    _create(client, session, admin, first, supplier.id)
    second = upload_ubl(client, session, number="INV-2")
    _create(client, session, admin, second, supplier.id, supplier_reference="INV-2-X",
            line_amount=["510.00", "123.45"], line_vat=["107.10", "25.92"])
    upload(client, session, ("foto.jpg", jpeg_bytes(), "image/jpeg"))
    photo = session.scalars(select(InboxDocument).order_by(InboxDocument.id.desc())).first()
    assert photo.extraction_method in pilot.BY_HAND
    _create(client, session, admin, photo, supplier.id, supplier_reference="HAND-1")
    report = _report(session, admin)
    ubl = report.methods["ubl"]
    assert (ubl.documents, ubl.untouched, ubl.untouched_share) == (2, 1, 0.5)
    assert ubl.fields == {"invoice_number": 1, "net": 1, "vat_21": 1, "total": 1}
    hand = report.methods[photo.extraction_method]
    assert (hand.documents, hand.untouched) == (1, 0) and hand.fields == {}
    out = pilot.render_text(1, [report], [], *WIDE)
    assert "ubl" in out and "50%" in out and "typed by hand" in out


def test_events_before_edit_tracking_are_listed_apart(session, admin):
    now = datetime.now(UTC)
    for kind, doc_id, details in (("inbox.receive", 301, {"channel": "web"}),
                                  ("inbox.create_purchase", 301, {"purchase_invoice_id": 1})):
        session.add(AuditEvent(action=kind, administration_id=admin.id, entity_id=doc_id,
                               entity_type="inbox_document", created_at=now, details=details))
    session.commit()
    report = _report(session, admin)
    assert report.edit_untracked == 1 and report.methods == {}
    assert "not tracked" in pilot.render_text(1, [report], [], *WIDE)


def test_created_and_booked_purchases_are_counted_apart(client, session, admin, supplier):
    drafted = upload_ubl(client, session)
    _create(client, session, admin, drafted, supplier.id)
    booked = upload_ubl(client, session, number="INV-3")
    _create(client, session, admin, booked, supplier.id, supplier_reference="INV-3",
            action="book")
    report = _report(session, admin)
    assert (report.purchases_created, report.purchases_booked) == (2, 1)
    assert "created from inbox documents: 2, booked since: 1" in pilot.render_text(
        1, [report], [], *WIDE)


def _statement_event(session, admin, doc_id, platform):
    now = datetime.now(UTC)
    session.add(AuditEvent(action="inbox.receive", administration_id=admin.id, entity_id=doc_id,
                           entity_type="inbox_document", created_at=now,
                           details={"channel": "web"}))
    session.add(AuditEvent(action="inbox.book_platform_statement", administration_id=admin.id,
                           entity_id=doc_id, entity_type="inbox_document", created_at=now,
                           details={"platform": platform}))


def test_a_booked_statement_counts_as_processed_with_its_time(client, session, admin):
    doc = received(client, session, thuisbezorgd_statement_html())
    r = client.post(f"/inbox/{doc.id}/book-statement", follow_redirects=False, data=split_form(
        client, [("V9", "1000,00")], tips_account=str(account(session, admin, "8900").id),
        adjustments_account=str(account(session, admin, "4600").id)))
    assert r.status_code == 303, r.text
    session.expire_all()
    for n, platform in ((901, "uber_eats"), (902, "uber_eats"), (903, "something_new")):
        _statement_event(session, admin, n, platform)
    session.commit()
    report = _report(session, admin)
    assert report.unprocessed == 0 and len(report.seconds_to_processed) == 4
    assert report.statements == {"thuisbezorgd": 1, "uber_eats": 2, "other": 1}
    out = pilot.render_text(1, [report], [], *WIDE)
    assert "platform statements booked: 4 (Thuisbezorgd=1, Uber Eats=2, other=1)" in out
    assert "not yet processed=0" in out


def test_uploads_are_split_by_role_without_names(client, bookkeeper_client, uploader_client,
                                                 session, admin, owner, capsys):
    pdfs = [("files", (f"{n}.pdf", b"%PDF-1.4 " + n.encode(), "application/pdf"))
            for n in "abcdefg"]
    post = lambda c, url, files: c.post(url, data={"csrf_token": c.csrf}, files=files)  # noqa: E731
    post(client, "/inbox/upload", pdfs[:2])
    post(bookkeeper_client, "/inbox/upload", pdfs[2:3])
    post(uploader_client, "/capture", pdfs[3:5])
    _, secret = tokens.create_token(session, owner, admin.id, "iPhone")
    session.commit()
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app
    TestClient(create_app()).post("/api/v1/inbox", headers=bearer(secret),
                                  files={"file": ("d.pdf", pdfs[5][1][1], "application/pdf")})
    session.add(AuditEvent(action="inbox.receive", administration_id=admin.id, entity_id=999,
                           entity_type="inbox_document",
                           details={"channel": "email", "source": "email"}))
    session.commit()
    report = _report(session, admin)
    assert report.uploaders == {"owner #1": 2, "bookkeeper #1": 1, "uploader #1": 2,
                                "device #1": 1, "email": 1}
    assert report.uploader_count == 4
    out = pilot.render_text(1, [report], [], *WIDE)
    assert ("accepted uploads by who: owner #1=2, bookkeeper #1=1, uploader #1=2, "
            "device #1=1, email=1 (4 signed-in users or devices)") in out
    assert "@example.com" not in out
    args = ["--from", WIDE[0].isoformat(), "--to", WIDE[1].isoformat(), "--json"]
    capsys.readouterr()
    assert cli.main(["pilot-report", *args]) == 0
    raw = capsys.readouterr().out
    data = json.loads(raw)
    [admin_json] = next(iter(data["tenants"].values()))
    assert admin_json["uploads_by"]["uploader #1"] == 2 and "@" not in raw
    assert set(admin_json) >= {"platform_statements_booked", "review", "purchases_booked"}


def test_users_of_one_role_get_stable_anonymous_numbers(session, admin, owner, bookkeeper):
    from tests.conftest import _user

    second = _user(session, "bookkeeper2@example.com", "bookkeeper")
    now = datetime.now(UTC)
    for n, user in enumerate((second, bookkeeper, second), 1):
        session.add(AuditEvent(action="inbox.receive", administration_id=admin.id,
                               user_id=user.id, entity_id=n, entity_type="inbox_document",
                               created_at=now, details={"channel": "web", "source": "upload"}))
    session.commit()
    assert _report(session, admin).uploaders == {"bookkeeper #1": 1, "bookkeeper #2": 2}
