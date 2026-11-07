import io
import json
import zipfile
from datetime import date

from sqlalchemy import select

from ledgerline.ledger.chart import create_administration
from ledgerline.models import Attachment, AuditEvent, InvoiceStatus, SalesInvoice
from tests.helpers import contact


def _full_contact(session, admin, name="Pieter Persoon"):
    c = contact(session, admin, name=name, email="pieter@example.com",
                address_line="Lindenlaan 9", postcode="1234 AB", city="Haarlem",
                iban="NL91ABNA0417164300", kvk_number="11223344", vat_number="NL123456789B01")
    session.commit()
    return c


def _invoice(session, admin, c, year=2024):
    inv = SalesInvoice(administration_id=admin.id, contact_id=c.id, number=f"{year}-0001",
                       status=InvoiceStatus.OPEN, issue_date=date(year, 5, 1),
                       due_date=date(year, 5, 31), total_excl_cents=50000,
                       total_vat_cents=10500, total_incl_cents=60500)
    session.add(inv)
    session.commit()
    return inv


def test_anonymise_blanks_personal_fields_and_keeps_invoices(client, session, admin):
    c = _full_contact(session, admin)
    inv = _invoice(session, admin, c)
    r = client.post(f"/contacts/{c.id}/anonymise",
                    data={"confirm": "yes", "csrf_token": client.csrf}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    c = session.get(type(c), c.id)
    assert c.name == f"Anonymised contact #{c.id}"
    assert (c.email, c.address_line, c.postcode, c.city, c.iban, c.kvk_number) == (None,) * 6
    assert c.vat_number == "NL123456789B01" and c.anonymised_at is not None
    kept = session.get(SalesInvoice, inv.id)
    assert kept.contact_id == c.id and kept.total_incl_cents == 60500
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "contact.anonymise"))
    assert event.entity_id == c.id
    assert event.details["retention_until"] == "2031-12-31"


def test_anonymise_requires_confirmation(client, session, admin):
    c = _full_contact(session, admin)
    client.post(f"/contacts/{c.id}/anonymise", data={"csrf_token": client.csrf})
    session.expire_all()
    assert session.get(type(c), c.id).anonymised_at is None


def test_anonymised_contact_cannot_be_edited(client, session, admin):
    c = _full_contact(session, admin)
    client.post(f"/contacts/{c.id}/anonymise", data={"confirm": "yes", "csrf_token": client.csrf})
    r = client.post(f"/contacts/{c.id}/edit",
                    data={"name": "Terug", "csrf_token": client.csrf}, follow_redirects=True)
    assert "cannot be edited" in r.text
    session.expire_all()
    assert session.get(type(c), c.id).name.startswith("Anonymised contact")


def test_contact_export_has_contact_and_invoices_only(client, session, admin):
    c = _full_contact(session, admin)
    _invoice(session, admin, c)
    other = create_administration(session, name="Andere BV")
    stranger = contact(session, other, name="Vreemde Vogel", email="vogel@example.com")
    _invoice(session, other, stranger)
    session.commit()
    r = client.get(f"/settings/privacy/contacts/{c.id}/export.json")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["contact"]["email"] == "pieter@example.com"
    assert data["contact"]["iban"] == "NL91ABNA0417164300"
    assert [i["number"] for i in data["sales_invoices"]] == ["2024-0001"]
    assert "Vreemde Vogel" not in r.text and "vogel@example.com" not in r.text


def test_contact_export_of_foreign_contact_is_404(client, session, admin):
    other = create_administration(session, name="Andere BV")
    stranger = contact(session, other, name="Vreemd")
    session.commit()
    assert client.get(f"/settings/privacy/contacts/{stranger.id}/export.json").status_code == 404


def test_administration_export_zip_scoped_to_current_administration(client, session, admin):
    c = _full_contact(session, admin)
    _invoice(session, admin, c)
    session.add(Attachment(administration_id=admin.id, filename="bon.pdf",
                           content_type="application/pdf", sha256="0" * 64, size_bytes=4,
                           content=b"%PDF"))
    other = create_administration(session, name="Andere BV")
    contact(session, other, name="Vreemde Vogel")
    session.commit()
    r = client.get("/settings/privacy/export.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "data/contacts.json" in names and "data/sales_invoices.json" in names
    contacts = json.loads(zf.read("data/contacts.json"))
    assert [row["name"] for row in contacts] == ["Pieter Persoon"]
    assert json.loads(zf.read("data/administration.json"))[0]["name"] == "Testbedrijf"
    assert any(n.endswith("-bon.pdf") and zf.read(n) == b"%PDF" for n in names)
    assert all("Vreemde Vogel" not in zf.read(n).decode("latin-1") for n in names)


def test_exports_are_owner_only(readonly_client, session, admin):
    c = _full_contact(session, admin)
    assert readonly_client.get("/settings/privacy/export.zip").status_code == 403
    assert readonly_client.get(
        f"/settings/privacy/contacts/{c.id}/export.json").status_code == 403


def test_privacy_page_shows_retention_date(client, session, admin):
    c = _full_contact(session, admin)
    _invoice(session, admin, c, year=2024)
    text = client.get("/settings/privacy").text
    assert "31-12-2031" in text and c.name in text
