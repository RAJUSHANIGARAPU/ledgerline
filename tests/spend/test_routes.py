import re
from datetime import date

import pytest

from ledgerline.invoicing.service import (
    PurchaseLineInput,
    book_purchase_invoice,
    create_purchase_invoice,
)
from ledgerline.models import ContactKind
from ledgerline.money import format_eur
from tests.helpers import account, contact
from tests.vat.booking import book_sale

TODAY = date.today()
IDS = ("spend-filter", "spend-totals", "spend-rows", "spend-by-supplier", "spend-by-month",
       "spend-by-category", "spend-export")


def _bill(session, admin, supplier, ref, excl, when, *, book=True):
    invoice = create_purchase_invoice(
        session, admin, supplier,
        [PurchaseLineInput("Goods", account(session, admin, "4200").id, excl, "I21")],
        supplier_reference=ref, invoice_date=when)
    if book:
        book_purchase_invoice(session, admin, invoice, None)
    session.commit()
    return invoice


@pytest.fixture
def bills(session, admin):
    acme = contact(session, admin, name="Acme BV", kind=ContactKind.SUPPLIER)
    evil = contact(session, admin, name="=HYPERLINK(1)", kind=ContactKind.SUPPLIER)
    session.commit()
    booked = _bill(session, admin, acme, "A-1", 10000, TODAY)
    formula = _bill(session, admin, evil, "E-1", 5000, TODAY)
    draft = _bill(session, admin, acme, "A-DRAFT", 2000, TODAY, book=False)
    old = _bill(session, admin, acme, "A-OLD", 7000, date(TODAY.year - 1, 6, 1))
    return {"acme": acme, "evil": evil, "booked": booked, "formula": formula, "draft": draft,
            "old": old}


def _row_ids(page):
    return {int(i) for i in re.findall(r'data-invoice-id="(\d+)"', page.text)}


def test_page_renders_with_the_dom_contract(client, bills):
    page = client.get("/spend")
    assert page.status_code == 200
    for dom_id in IDS:
        assert f'id="{dom_id}"' in page.text, dom_id
    for preset in ("month", "quarter", "year", "last_year", "all"):
        assert f'data-preset="{preset}"' in page.text
    for total in ("count", "excl", "vat", "incl", "open"):
        assert f'data-total="{total}"' in page.text
    assert 'id="spend-upload"' in page.text and 'action="/inbox/upload"' in page.text
    assert 'name="csrf_token"' in page.text
    assert f'href="/purchases/{bills["booked"].id}"' in page.text
    assert '<a href="/spend"' in page.text  # nav entry


def test_default_preset_is_the_current_year_and_drafts_are_hidden(client, bills):
    page = client.get("/spend")
    assert _row_ids(page) == {bills["booked"].id, bills["formula"].id}
    assert f'name="date_from" value="{TODAY.year}-01-01"' in page.text
    assert 'data-preset="year" href="/spend?preset=year"' in page.text
    assert format_eur(10000 + 5000) in page.text


def test_drafts_are_listed_but_flagged(client, bills):
    page = client.get("/spend?drafts=1")
    assert bills["draft"].id in _row_ids(page)
    assert re.search(rf'data-invoice-id="{bills["draft"].id}" class="is-draft"', page.text)
    assert "not in the totals" in page.text


def test_filters_pass_through(client, bills):
    page = client.get(f"/spend?contact_id={bills['evil'].id}")
    assert _row_ids(page) == {bills["formula"].id}
    assert f'<option value="{bills["evil"].id}" selected>' in page.text
    last = date(TODAY.year - 1, 1, 1).isoformat()
    page = client.get(f"/spend?date_from={last}&date_to={TODAY.year - 1}-12-31")
    assert _row_ids(page) == {bills["old"].id}
    assert f"date_from={last}" in re.search(r'id="spend-export" href="([^"]+)"', page.text).group(1)


def test_explicit_dates_override_the_preset(client, bills):
    last = f"{TODAY.year - 1}-01-01"
    page = client.get(f"/spend?preset=month&date_from={last}&date_to={TODAY.year - 1}-12-31")
    assert _row_ids(page) == {bills["old"].id}


def test_all_preset_covers_every_year(client, bills):
    page = client.get("/spend?preset=all")
    assert bills["old"].id in _row_ids(page) and bills["booked"].id in _row_ids(page)


def test_invalid_input_is_rejected(client):
    assert client.get("/spend?date_from=nope").status_code == 422
    assert client.get("/spend?contact_id=x").status_code == 422
    assert client.get("/spend.csv?date_to=nope").status_code == 422


def test_empty_state(client):
    page = client.get("/spend")
    assert page.status_code == 200 and "No bills in this period" in page.text


def test_pending_notice_is_absent_without_waiting_documents(client, session, admin, bills):
    assert 'id="spend-pending"' not in client.get("/spend").text


def test_read_only_accountant_sees_page_without_upload(readonly_client, bills):
    page = readonly_client.get("/spend")
    assert page.status_code == 200
    assert 'id="spend-upload"' not in page.text
    assert 'id="spend-rows"' in page.text
    assert readonly_client.get("/spend.csv").status_code == 200


def test_anonymous_is_sent_to_login():
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    for path in ("/spend", "/spend.csv"):
        response = TestClient(create_app()).get(path, follow_redirects=False)
        assert response.status_code in (302, 303) and response.headers["location"] == "/login"


def test_csv_has_header_rows_filename_and_no_sales(client, session, admin, bills):
    book_sale(session, admin, "V21", 99900, TODAY)
    session.commit()
    response = client.get("/spend.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="spend-{TODAY.year}-01-01_{TODAY.isoformat()}.csv"')
    lines = response.text.lstrip("﻿").strip().split("\r\n")
    assert lines[0] == "Date,Supplier,Invoice number,Source,Status,Excl. VAT,VAT,Incl. VAT,Open"
    assert len(lines) == 3  # two booked bills this year, no draft, no sale
    assert "999.00" not in response.text and "A-1" in response.text


def test_csv_semicolon_and_comma_option_and_drafts(client, bills):
    response = client.get("/spend.csv?comma=1&drafts=1")
    lines = response.text.lstrip("﻿").strip().split("\r\n")
    assert lines[0].startswith("Date;Supplier;")
    assert len(lines) == 4 and any("A-DRAFT" in line for line in lines)
    assert any(";100,00;" in line for line in lines)


def test_csv_neutralises_formula_supplier(client, bills):
    text = client.get("/spend.csv").text
    assert "'=HYPERLINK(1)" in text
    assert ",=HYPERLINK" not in text


def test_export_link_carries_current_filters(client, bills):
    page = client.get(f"/spend?contact_id={bills['acme'].id}&drafts=1")
    link = re.search(r'id="spend-export" href="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    assert link.startswith("/spend.csv?")
    assert f"contact_id={bills['acme'].id}" in link and "drafts=1" in link
    assert client.get(link).status_code == 200


def test_pending_notice_shows_unchecked_bills_outside_the_totals(client, bills, make_inbox_doc):
    before = client.get("/spend?preset=all").text
    total_before = re.search(r'data-total="incl"[^>]*>([^<]+)<', before).group(1)
    make_inbox_doc(extraction={"total_incl_cents": 12100})
    make_inbox_doc(extraction=None)
    page = client.get("/spend?preset=all").text
    notice = re.search(r'id="spend-pending".*?</div></div>', page, re.S).group(0)
    assert "2 bills waiting for review" in notice and "1 unreadable" in notice
    assert "121,00" in notice
    assert re.search(r'data-total="incl"[^>]*>([^<]+)<', page).group(1) == total_before


def test_all_time_export_link_exports_all_time(client, bills):
    page = client.get("/spend?preset=all")
    link = re.search(r'id="spend-export" href="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    text = client.get(link).text
    assert "A-OLD" in page.text and "A-OLD" in text
    assert len(text.strip().splitlines()) - 1 == page.text.count("data-invoice-id=")


def test_other_tenants_bills_never_show(client, bills, other_owner, other_admin):
    page = client.get("/spend")
    assert "Tenant B" not in page.text and other_admin.name not in page.text
