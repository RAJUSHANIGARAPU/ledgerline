from datetime import date

from sqlalchemy import select

from ledgerline.ledger.chart import create_administration
from ledgerline.models import AuditEvent, Contact, InvoiceStatus, SalesInvoice
from tests.helpers import contact

GOOD = {"name": "Jansen BV", "kind": "customer", "email": "inkoop@jansen.example",
        "address_line": "Dorpsstraat 5", "postcode": "3511 AA", "city": "Utrecht",
        "country": "NL", "vat_number": "NL123456789B01", "kvk_number": "87654321",
        "iban": "NL91 ABNA 0417 1643 00"}


def _invoice(session, admin, c):
    inv = SalesInvoice(administration_id=admin.id, contact_id=c.id, number="2026-0001",
                       status=InvoiceStatus.OPEN, issue_date=date(2026, 3, 1),
                       due_date=date(2026, 3, 31), total_excl_cents=10000,
                       total_vat_cents=2100, total_incl_cents=12100)
    session.add(inv)
    session.commit()
    return inv


def test_create_contact_normalises_fields(client, session, admin):
    r = client.post("/contacts/new", data={**GOOD, "csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    c = session.scalar(select(Contact).where(Contact.name == "Jansen BV"))
    assert c.administration_id == admin.id
    assert c.iban == "NL91ABNA0417164300"
    assert c.vat_number == "NL123456789B01"
    assert r.headers["location"] == f"/contacts/{c.id}"


def test_create_rejects_bad_vat_kvk_iban(client, session, admin):
    bad = {**GOOD, "vat_number": "NL123", "kvk_number": "12", "iban": "NL00BAD"}
    r = client.post("/contacts/new", data={**bad, "csrf_token": client.csrf})
    assert r.status_code == 422
    assert "VAT number does not match" in r.text
    assert "KvK number must be 8 digits" in r.text
    assert "IBAN is not valid" in r.text
    assert session.scalar(select(Contact)) is None


def test_list_search_and_kind_filter(client, session, admin):
    contact(session, admin, name="Alpha Klant", kind="customer", city="Zwolle")
    contact(session, admin, name="Beta Leverancier", kind="supplier")
    session.commit()
    both = client.get("/contacts").text
    assert "Alpha Klant" in both and "Beta Leverancier" in both
    only = client.get("/contacts", params={"kind": "supplier"}).text
    assert "Beta Leverancier" in only and "Alpha Klant" not in only
    found = client.get("/contacts", params={"q": "zwolle"}).text
    assert "Alpha Klant" in found and "Beta Leverancier" not in found


def test_edit_updates_contact(client, session, admin):
    c = contact(session, admin, name="Oud", email=None)
    session.commit()
    r = client.post(f"/contacts/{c.id}/edit",
                    data={**GOOD, "name": "Nieuw", "csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    session.refresh(c)
    assert c.name == "Nieuw" and c.city == "Utrecht"


def test_detail_shows_invoices(client, session, admin):
    c = contact(session, admin, name="Met Factuur")
    inv = _invoice(session, admin, c)
    text = client.get(f"/contacts/{c.id}").text
    assert inv.number in text
    assert "€\xa0121,00" in text


def test_contact_of_other_administration_is_404(client, session, admin):
    other = create_administration(session, name="Andere BV")
    foreign = contact(session, other, name="Vreemd")
    session.commit()
    assert client.get(f"/contacts/{foreign.id}").status_code == 404
    r = client.post(f"/contacts/{foreign.id}/delete", data={"csrf_token": client.csrf})
    assert r.status_code == 404


def test_delete_without_records(client, session, admin):
    c = contact(session, admin, name="Weg ermee")
    session.commit()
    cid = c.id
    r = client.post(f"/contacts/{cid}/delete", data={"csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.get(Contact, cid) is None
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "contact.delete"))


def test_delete_blocked_when_contact_has_invoices(client, session, admin):
    c = contact(session, admin, name="Behouden")
    _invoice(session, admin, c)
    r = client.post(f"/contacts/{c.id}/delete", data={"csrf_token": client.csrf},
                    follow_redirects=True)
    assert "cannot be deleted" in r.text
    session.expire_all()
    assert session.get(Contact, c.id) is not None
