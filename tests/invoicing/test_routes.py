from datetime import date, timedelta
from decimal import Decimal

from defusedxml import ElementTree as ET
from sqlalchemy import select

from ledgerline.invoicing.service import (
    PurchaseLineInput,
    create_purchase_invoice,
    create_sales_invoice,
)
from ledgerline.ledger.chart import create_administration
from ledgerline.models import InvoiceStatus, PurchaseInvoice, SalesInvoice
from tests.helpers import account, contact
from tests.invoicing.conftest import sline

PDF = b"%PDF-1.4\n" + b"x" * 64


def sales_form(client, customer, session, admin, **extra):
    return {
        "csrf_token": client.csrf,
        "contact_id": str(customer.id),
        "issue_date": "2026-03-01",
        "due_date": "2026-03-31",
        "supply_date": "",
        "reference": "PO-77",
        "notes": "",
        "line_description": ["Consulting", "Book"],
        "line_qty": ["2", "1"],
        "line_price": ["100,00", "20.00"],
        "line_account": [
            str(account(session, admin, "8000").id), str(account(session, admin, "8010").id)
        ],
        "line_vat_code": ["V21", "V9"],
        **extra,
    }


def test_sales_flow_create_issue_pdf_ubl(client, session, admin, customer):
    r = client.post("/sales/new", data=sales_form(client, customer, session, admin),
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    inv = session.scalars(select(SalesInvoice)).one()
    assert r.headers["location"] == f"/sales/{inv.id}"
    assert (inv.total_excl_cents, inv.total_vat_cents) == (22000, 4200 + 180)
    assert inv.status == InvoiceStatus.DRAFT and inv.number is None

    page = client.get(f"/sales/{inv.id}")
    assert page.status_code == 200 and "Save draft" in page.text and "PO-77" in page.text

    r = client.post(f"/sales/{inv.id}/issue", data={"csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    session.refresh(inv)
    assert inv.number == "2026-0001" and inv.status == InvoiceStatus.OPEN

    detail = client.get(f"/sales/{inv.id}")
    assert "2026-0001" in detail.text and "Credit note" in detail.text
    pdf = client.get(f"/sales/{inv.id}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"] == "application/pdf"
    ubl = client.get(f"/sales/{inv.id}/ubl")
    assert ubl.headers["content-disposition"].startswith("attachment")
    root = ET.fromstring(ubl.content)
    amount = root.find(".//cac:LegalMonetaryTotal/cbc:PayableAmount", {
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"})
    assert Decimal(amount.text) == Decimal("263.80")


def test_issued_invoice_rejects_edits_over_http(
    client, session, admin, customer, owner, make_invoice
):
    from ledgerline.invoicing.service import issue_sales_invoice

    inv = make_invoice()
    issue_sales_invoice(session, admin, inv, owner.id)
    session.commit()
    data = sales_form(client, customer, session, admin, reference="CHANGED")
    r = client.post(f"/sales/{inv.id}/save", data=data, follow_redirects=True)
    assert "cannot be edited" in r.text
    session.expire_all()
    assert session.get(SalesInvoice, inv.id).reference is None
    r = client.post(f"/sales/{inv.id}/delete", data={"csrf_token": client.csrf},
                    follow_redirects=True)
    assert "only drafts" in r.text
    assert session.get(SalesInvoice, inv.id) is not None


def test_issue_refusal_is_listed_to_the_user(client, session, admin, customer, make_invoice):
    admin.kvk_number = None
    session.commit()
    inv = make_invoice()
    r = client.post(f"/sales/{inv.id}/issue", data={"csrf_token": client.csrf},
                    follow_redirects=True)
    assert "Cannot issue yet" in r.text and "KvK number is missing" in r.text
    session.expire_all()
    assert session.get(SalesInvoice, inv.id).number is None


def test_draft_can_be_deleted(client, session, make_invoice):
    inv = make_invoice()
    r = client.post(f"/sales/{inv.id}/delete", data={"csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/sales"
    session.expunge_all()
    assert session.get(SalesInvoice, inv.id) is None


def test_credit_note_over_http(client, session, admin, owner, make_invoice):
    from ledgerline.invoicing.service import issue_sales_invoice

    inv = make_invoice()
    issue_sales_invoice(session, admin, inv, owner.id)
    session.commit()
    r = client.post(f"/sales/{inv.id}/credit", data={"csrf_token": client.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.get(SalesInvoice, inv.id).status == InvoiceStatus.CANCELLED
    note = session.scalars(
        select(SalesInvoice).where(SalesInvoice.credit_note_of_id == inv.id)
    ).one()
    assert note.total_incl_cents == -12100
    assert client.get(f"/sales/{note.id}/pdf").content.startswith(b"%PDF-")


def test_list_marks_overdue_and_filters(client, session, admin, owner, make_invoice):
    from ledgerline.invoicing.service import issue_sales_invoice

    old = make_invoice(issue_date=date.today() - timedelta(days=90),
                       due_date=date.today() - timedelta(days=60))
    issue_sales_invoice(session, admin, old, owner.id)
    fresh = make_invoice(issue_date=date.today())
    issue_sales_invoice(session, admin, fresh, owner.id)
    session.commit()
    html = client.get("/sales").text
    assert html.count("badge-overdue") == 1 and html.count("badge-open") == 1
    overdue = client.get("/sales?status=overdue").text
    assert old.number in overdue and fresh.number not in overdue


def test_preview_recalculates_totals_and_add_remove(client, session, admin):
    data = {
        "csrf_token": client.csrf,
        "line_description": ["a", "b"], "line_qty": ["1", "2"], "line_price": ["100.00", "50.00"],
        "line_account": [str(account(session, admin, "8000").id)] * 2,
        "line_vat_code": ["V21", "V21"],
    }
    html = client.post("/sales/preview", data=data).text
    assert "€\xa0200,00" in html and "€\xa042,00" in html and "€\xa0242,00" in html
    added = client.post("/sales/preview", data={**data, "op": "add"}).text
    assert added.count('name="line_description"') == 3
    removed = client.post("/sales/preview", data={**data, "remove": "0"}).text
    assert removed.count('name="line_description"') == 1 and "€\xa0121,00" in removed


def test_other_administration_invoice_is_404(client, session, admin, customer):
    other = create_administration(session, name="Ander BV", vat_number="NL1", kvk_number="1")
    foreign_customer = contact(session, other, name="Vreemd")
    foreign = create_sales_invoice(
        session, other, foreign_customer, [sline(session, other, "x", "1", 100, "V21")])
    supplier = contact(session, other, name="Lev", kind="supplier")
    foreign_purchase = create_purchase_invoice(
        session, other, supplier,
        [PurchaseLineInput("x", account(session, other, "4300").id, 100)],
        supplier_reference="A1", invoice_date=date(2026, 3, 1))
    session.commit()
    for path in (f"/sales/{foreign.id}", f"/sales/{foreign.id}/pdf", f"/sales/{foreign.id}/ubl",
                 f"/purchases/{foreign_purchase.id}",
                 f"/purchases/{foreign_purchase.id}/attachment"):
        assert client.get(path).status_code == 404, path
    for path in (f"/sales/{foreign.id}/issue", f"/sales/{foreign.id}/delete",
                 f"/sales/{foreign.id}/credit", f"/purchases/{foreign_purchase.id}/book",
                 f"/purchases/{foreign_purchase.id}/delete"):
        assert client.post(path, data={"csrf_token": client.csrf}).status_code == 404, path
    session.expire_all()
    assert session.get(SalesInvoice, foreign.id).status == InvoiceStatus.DRAFT


def test_customer_from_another_administration_is_refused(client, session, admin, customer):
    other = create_administration(session, name="Ander BV")
    foreign_customer = contact(session, other, name="Vreemd")
    session.commit()
    r = client.post("/sales/new", data=sales_form(client, foreign_customer, session, admin))
    assert r.status_code == 422 and "choose a contact" in r.text
    assert session.scalars(select(SalesInvoice)).all() == []


def test_readonly_user_can_read_but_not_write(
    readonly_client, session, admin, customer, make_invoice
):
    inv = make_invoice()
    assert readonly_client.get("/sales").status_code == 200
    assert readonly_client.get(f"/sales/{inv.id}").status_code == 200
    token = {"csrf_token": readonly_client.csrf}
    for path in ("/sales/new", f"/sales/{inv.id}/issue", f"/sales/{inv.id}/delete",
                 f"/sales/{inv.id}/credit", "/sales/preview", "/purchases/new",
                 "/purchases/1/book"):
        assert readonly_client.post(path, data=token).status_code == 403, path
    session.expire_all()
    assert session.get(SalesInvoice, inv.id).status == InvoiceStatus.DRAFT


def test_post_without_csrf_is_rejected(client, session, admin, customer):
    data = sales_form(client, customer, session, admin)
    data.pop("csrf_token")
    assert client.post("/sales/new", data=data).status_code == 403


def purchase_form(client, supplier, session, admin, **extra):
    return {
        "csrf_token": client.csrf,
        "contact_id": str(supplier.id),
        "supplier_reference": "S-100",
        "invoice_date": "2026-03-02",
        "due_date": "2026-04-01",
        "description": "",
        "line_description": ["Hosting"],
        "line_account": [str(account(session, admin, "4300").id)],
        "line_amount": ["100,00"],
        "line_vat_code": ["I21"],
        "line_vat": ["21.00"],
        "line_vat_auto": ["1"],
        **extra,
    }


def test_purchase_flow_with_attachment_and_download(client, session, admin, supplier):
    r = client.post(
        "/purchases/new", data=purchase_form(client, supplier, session, admin),
        files={"attachment": ("scan.pdf", PDF, "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    inv = session.scalars(select(PurchaseInvoice)).one()
    assert inv.total_incl_cents == 12100 and inv.attachment_id is not None

    assert client.post(f"/purchases/{inv.id}/book", data={"csrf_token": client.csrf},
                       follow_redirects=False).status_code == 303
    session.expire_all()
    assert session.get(PurchaseInvoice, inv.id).status == InvoiceStatus.OPEN
    assert "S-100" in client.get("/purchases").text

    download = client.get(f"/purchases/{inv.id}/attachment")
    assert download.content == PDF
    assert download.headers["content-disposition"] == 'attachment; filename="scan.pdf"'
    assert download.headers["content-type"] == "application/pdf"


def test_purchase_upload_with_wrong_magic_bytes_is_refused(client, session, admin, supplier):
    r = client.post(
        "/purchases/new", data=purchase_form(client, supplier, session, admin),
        files={"attachment": ("invoice.pdf", b"<script>alert(1)</script>", "application/pdf")})
    assert r.status_code == 422 and "only PDF, PNG and JPEG" in r.text
    assert session.scalars(select(PurchaseInvoice)).all() == []


def test_purchase_duplicate_reference_is_refused_over_http(client, session, admin, supplier):
    data = purchase_form(client, supplier, session, admin)
    assert client.post("/purchases/new", data=data, follow_redirects=False).status_code == 303
    r = client.post("/purchases/new", data=data)
    assert r.status_code == 422 and "already registered" in r.text


def test_purchase_preview_computes_reverse_charge_and_keeps_manual_vat(client, session, admin):
    acc = str(account(session, admin, "4300").id)
    data = {
        "csrf_token": client.csrf,
        "line_description": ["EU", "Local"], "line_account": [acc, acc],
        "line_amount": ["100.00", "33.33"], "line_vat_code": ["IEU21", "I21"],
        "line_vat": ["0", "6.99"], "line_vat_auto": ["1", "0"],
    }
    html = client.post("/purchases/preview", data=data).text
    assert 'value="21.00"' in html and 'value="6.99"' in html
    assert "€\xa0133,33" in html and "€\xa06,99" in html and "€\xa0140,32" in html


def test_editor_pages_render(client, session, admin, supplier, customer):
    new_sales = client.get("/sales/new").text
    assert "Klant BV" in new_sales and 'name="line_price"' in new_sales
    assert "8000 Revenue 21%" in new_sales
    assert "Leverancier BV" in client.get("/purchases/new").text

    client.post("/purchases/new", data=purchase_form(client, supplier, session, admin))
    session.expire_all()
    inv = session.scalars(select(PurchaseInvoice)).one()
    draft = client.get(f"/purchases/{inv.id}").text
    assert "S-100" in draft and "Book invoice" in draft and 'value="21.00"' in draft
