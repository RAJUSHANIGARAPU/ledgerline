from datetime import date

from ledgerline.money import format_eur
from ledgerline.vat.icp import icp_overview
from tests.helpers import contact
from tests.vat.booking import book_sale

D = date(2026, 2, 10)
Q1 = (date(2026, 1, 1), date(2026, 3, 31))


def test_sums_icp_base_per_customer_vat_number(session, admin):
    a = contact(session, admin, name="Alpha GmbH", vat_number="DE123456789", country="DE")
    b = contact(session, admin, name="Bravo SA", vat_number="FR12345678901", country="FR")
    book_sale(session, admin, "VEU", 4000, D, contact_id=a.id)
    book_sale(session, admin, "VEU", 1500, date(2026, 3, 31), contact_id=a.id)
    book_sale(session, admin, "VEU", 700, D, contact_id=b.id)
    book_sale(session, admin, "VEU", 9999, date(2026, 4, 1), contact_id=b.id)
    book_sale(session, admin, "V21", 5000, D, contact_id=a.id)
    session.commit()
    rows = icp_overview(session, admin.id, *Q1)
    assert [(r.vat_number, r.amount_cents) for r in rows] == [
        ("DE123456789", 5500), ("FR12345678901", 700)]


def test_foreign_contact_is_not_exposed(session, admin):
    from ledgerline.ledger.chart import create_administration
    other = create_administration(session, name="Other BV")
    stranger = contact(session, other, name="Secret GmbH", vat_number="DE999999999")
    book_sale(session, admin, "VEU", 1000, D, contact_id=stranger.id)
    session.commit()
    (row,) = icp_overview(session, admin.id, *Q1)
    assert (row.name, row.vat_number, row.amount_cents) == ("Unknown customer", "", 1000)


def test_icp_page_and_csv(client, session, admin):
    c = contact(session, admin, name="=HYPERLINK(1)", vat_number="DE123456789")
    book_sale(session, admin, "VEU", 123456, D, contact_id=c.id)
    session.commit()
    page = client.get("/vat/icp?kind=quarter&year=2026&number=1")
    assert page.status_code == 200 and "DE123456789" in page.text
    assert format_eur(123456) in page.text
    plain = client.get("/vat/icp?kind=quarter&year=2026&number=1&export=csv")
    assert plain.headers["content-type"].startswith("text/csv")
    lines = plain.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "VAT number,Customer,Amount"
    assert lines[1] == "DE123456789,'=HYPERLINK(1),1234.56"
    dutch = client.get("/vat/icp?kind=quarter&year=2026&number=1&export=csv&comma=1")
    assert dutch.content.decode("utf-8-sig").splitlines()[1] == "DE123456789;'=HYPERLINK(1);1234,56"
