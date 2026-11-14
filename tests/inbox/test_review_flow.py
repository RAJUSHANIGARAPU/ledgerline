import pytest
from sqlalchemy import select

from ledgerline.inbox import review
from ledgerline.inbox.extract import ExtractedInvoice, ExtractedLine
from ledgerline.invoicing.service import PurchaseLineInput, create_purchase_invoice
from ledgerline.ledger.chart import create_administration
from ledgerline.models import (
    ContactKind,
    InboxDocument,
    InboxStatus,
    InvoiceStatus,
    JournalEntry,
    PurchaseInvoice,
)
from tests.helpers import account, contact
from tests.inbox.factories import (
    SUPPLIER_IBAN,
    invoice_html,
    jpeg_bytes,
    pdf_from_html,
    pdf_without_text,
    ubl,
)


def upload(client, session, *files):
    response = client.post(
        "/inbox/upload",
        data={"csrf_token": client.csrf},
        files=[("files", (name, content, ctype)) for name, content, ctype in files],
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    session.expire_all()
    return response


def upload_ubl(client, session, lines=None, **kwargs):
    lines = lines or [("Hosting", 50000, "S", 21), ("Licence", 12345, "S", 21)]
    upload(client, session, ("inv.xml", ubl(lines, **kwargs), "application/xml"))
    return session.scalars(select(InboxDocument).order_by(InboxDocument.id.desc())).first()


@pytest.fixture
def supplier(session, admin):
    c = contact(
        session,
        admin,
        name="Bakker Software",
        kind=ContactKind.SUPPLIER,
        vat_number="NL 8533.45678 B01",
        iban="NL02 ABNA 0123 4567 89",
    )
    session.commit()
    return c


def create_data(client, document, contact_id, session, admin, **extra):
    data = {
        "csrf_token": client.csrf,
        "contact_id": str(contact_id),
        "supplier_reference": "INV-2026-17",
        "invoice_date": "2026-11-03",
        "due_date": "2026-12-03",
        "description": "",
        "line_description": ["Hosting", "Licence"],
        "line_account": [str(account(session, admin, "4300").id)] * 2,
        "line_amount": ["500.00", "123.45"],
        "line_vat_code": ["I21", "I21"],
        "line_vat": ["105.00", "25.92"],
        "action": "draft",
    }
    data.update(extra)
    return data


def test_upload_is_read_in_the_background_and_listed(client, session, admin):
    doc = upload_ubl(client, session)
    assert doc.status == InboxStatus.REVIEW and doc.extraction_method == "ubl"
    page = client.get("/inbox")
    assert page.status_code == 200 and "inv.xml" in page.text and "e-invoice" in page.text
    assert "To review" in page.text and "Rejected" in page.text
    assert "inv.xml" not in client.get("/inbox?tab=booked").text


def test_detail_is_prefilled_with_the_proposal(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    page = client.get(f"/inbox/{doc.id}")
    assert page.status_code == 200
    assert "Read from e-invoice (exact)" in page.text
    assert 'value="INV-2026-17"' in page.text and 'value="2026-11-03"' in page.text
    assert f'<option value="{supplier.id}" selected>' in page.text
    assert "Matched on VAT number" in page.text
    assert 'value="500.00"' in page.text and 'value="105.00"' in page.text
    assert "Create purchase draft" in page.text and "Create and book" in page.text


def test_unknown_supplier_offers_creation_from_extracted_data(client, session, admin):
    doc = upload_ubl(client, session)
    page = client.get(f"/inbox/{doc.id}")
    assert "Create supplier from the extracted data" in page.text
    assert 'value="NL853345678B01"' in page.text and 'value="Bakker Software B.V."' in page.text


def test_create_draft_links_the_attachment_and_totals(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, supplier.id, session, admin),
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    invoice = session.scalars(select(PurchaseInvoice)).one()
    assert r.headers["location"] == f"/purchases/{invoice.id}"
    assert invoice.status == InvoiceStatus.DRAFT and invoice.contact_id == supplier.id
    assert (invoice.total_excl_cents, invoice.total_vat_cents, invoice.total_incl_cents) == (
        62345,
        13092,
        75437,
    )
    assert invoice.attachment_id == doc.attachment_id and invoice.journal_entry_id is None
    session.refresh(doc)
    assert doc.status == InboxStatus.BOOKED and doc.purchase_invoice_id == invoice.id
    assert client.get(f"/purchases/{invoice.id}/attachment").status_code == 200
    assert "was created from this document" in client.get(f"/inbox/{doc.id}").text
    assert "inv.xml" in client.get("/inbox?tab=booked").text


def test_create_and_book_posts_a_balanced_entry(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, supplier.id, session, admin, action="book"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    invoice = session.scalars(select(PurchaseInvoice)).one()
    assert invoice.status == InvoiceStatus.OPEN and invoice.journal_entry_id
    entry = session.get(JournalEntry, invoice.journal_entry_id)
    assert sum(ln.debit_cents for ln in entry.lines) == sum(ln.credit_cents for ln in entry.lines)
    creditors = account(session, admin, "1600").id
    assert [ln.credit_cents for ln in entry.lines if ln.account_id == creditors] == [75437]
    vat = account(session, admin, "1400").id
    assert [ln.debit_cents for ln in entry.lines if ln.account_id == vat] == [13092]


def test_create_supplier_from_extracted_data(client, session, admin):
    doc = upload_ubl(client, session)
    data = create_data(
        client,
        doc,
        "new",
        session,
        admin,
        new_name="Bakker Software B.V.",
        new_vat="NL853345678B01",
        new_kvk="87654321",
        new_iban=SUPPLIER_IBAN,
        new_country="NL",
    )
    assert (
        client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False).status_code == 303
    )
    session.expire_all()
    invoice = session.scalars(select(PurchaseInvoice)).one()
    assert invoice.contact.name == "Bakker Software B.V." and invoice.contact.kind == "supplier"
    assert (invoice.contact.vat_number, invoice.contact.kvk_number) == (
        "NL853345678B01",
        "87654321",
    )
    assert invoice.contact.administration_id == admin.id


def test_new_supplier_with_a_bad_vat_number_is_refused(client, session, admin):
    doc = upload_ubl(client, session)
    data = create_data(client, doc, "new", session, admin, new_name="X", new_vat="NL12")
    r = client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False)
    assert r.status_code == 422 and "VAT number" in r.text
    session.expire_all()
    assert session.scalars(select(PurchaseInvoice)).all() == []
    assert session.get(InboxDocument, doc.id).status == InboxStatus.REVIEW


def test_reverse_charge_invoice_is_proposed_with_the_reverse_vat_code(
    client, session, admin, supplier
):
    doc = upload_ubl(client, session, [("Onderaanneming", 40000, "AE", 0)])
    page = client.get(f"/inbox/{doc.id}")
    assert '<option value="IRV21" selected>' in page.text
    data = create_data(
        client,
        doc,
        supplier.id,
        session,
        admin,
        action="book",
        supplier_reference="INV-2026-17",
        line_description=["Onderaanneming"],
        line_account=[str(account(session, admin, "4900").id)],
        line_amount=["400.00"],
        line_vat_code=["IRV21"],
        line_vat=[""],
    )
    assert (
        client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False).status_code == 303
    )
    session.expire_all()
    invoice = session.scalars(select(PurchaseInvoice)).one()
    assert (invoice.total_excl_cents, invoice.total_vat_cents, invoice.total_incl_cents) == (
        40000,
        0,
        40000,
    )
    assert invoice.lines[0].vat_cents == 8400 and invoice.status == InvoiceStatus.OPEN


def test_duplicate_supplier_invoice_is_flagged_blocked_and_linkable(
    client, session, admin, supplier
):
    existing = create_purchase_invoice(
        session,
        admin,
        supplier,
        [PurchaseLineInput("Old", account(session, admin, "4300").id, 1000, "I21")],
        supplier_reference="inv-2026-17",
        invoice_date=__import__("datetime").date(2026, 11, 1),
    )
    session.commit()
    doc = upload_ubl(client, session)
    page = client.get(f"/inbox/{doc.id}")
    assert "already registered" in page.text and f"/purchases/{existing.id}" in page.text
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, supplier.id, session, admin),
        follow_redirects=False,
    )
    assert r.status_code == 422 and "already registered" in r.text
    session.expire_all()
    assert len(session.scalars(select(PurchaseInvoice)).all()) == 1
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, supplier.id, session, admin, action="link"),
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == f"/purchases/{existing.id}"
    session.expire_all()
    assert session.get(InboxDocument, doc.id).purchase_invoice_id == existing.id


def test_a_booked_document_cannot_be_created_twice(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    data = create_data(client, doc, supplier.id, session, admin)
    assert (
        client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False).status_code == 303
    )
    data["supplier_reference"] = "OTHER-1"
    assert (
        client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False).status_code == 422
    )
    session.expire_all()
    assert len(session.scalars(select(PurchaseInvoice)).all()) == 1


def test_invalid_form_rerenders_with_the_entered_values(client, session, admin, supplier):
    doc = upload_ubl(client, session)
    data = create_data(
        client,
        doc,
        supplier.id,
        session,
        admin,
        invoice_date="not-a-date",
        supplier_reference="KEEP-ME",
    )
    r = client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False)
    assert r.status_code == 422 and "invoice date is not a valid date" in r.text
    assert 'value="KEEP-ME"' in r.text


def test_duplicate_upload_points_to_the_existing_document(client, session, admin):
    doc = upload_ubl(client, session)
    r = upload(
        client,
        session,
        (
            "again.xml",
            ubl([("Hosting", 50000, "S", 21), ("Licence", 12345, "S", 21)]),
            "application/xml",
        ),
    )
    assert r.headers["location"] == f"/inbox?dup={doc.id}"
    page = client.get(r.headers["location"])
    assert "is already in your inbox" in page.text and f'href="/inbox/{doc.id}"' in page.text
    assert len(session.scalars(select(InboxDocument)).all()) == 1


def test_upload_refuses_unsupported_files(client, session, admin):
    upload(
        client,
        session,
        ("notes.txt", b"hello", "text/plain"),
        ("empty.pdf", b"", "application/pdf"),
    )
    assert session.scalars(select(InboxDocument)).all() == []
    page = client.get("/inbox")
    assert "notes.txt" in page.text and "empty.pdf" in page.text


def test_multiple_files_in_one_upload(client, session, admin):
    upload(
        client,
        session,
        ("a.pdf", pdf_from_html(invoice_html()), "application/pdf"),
        ("b.jpg", jpeg_bytes(), "image/jpeg"),
    )
    docs = session.scalars(select(InboxDocument).order_by(InboxDocument.id)).all()
    assert [d.source for d in docs] == ["upload", "upload"]
    assert [d.extraction_method for d in docs] == ["pdf_text", "none"]


def test_photo_without_a_reader_is_flagged_for_manual_entry(client, session, admin):
    upload(client, session, ("b.jpg", jpeg_bytes(), "image/jpeg"))
    doc = session.scalars(select(InboxDocument)).one()
    page = client.get(f"/inbox/{doc.id}")
    assert "Could not be read" in page.text and "No reader could handle" in page.text
    assert f'<img src="/inbox/{doc.id}/file"' in page.text


def test_pdf_text_result_shows_check_label_and_warnings(client, session, admin):
    upload(
        client, session, ("a.pdf", pdf_from_html(invoice_html(incl="1.250,00")), "application/pdf")
    )
    doc = session.scalars(select(InboxDocument)).one()
    page = client.get(f"/inbox/{doc.id}")
    assert "Read from PDF text — check" in page.text and "Totals do not add up" in page.text


def test_rerun_and_reject(client, session, admin):
    doc = upload_ubl(client, session)
    r = client.post(
        f"/inbox/{doc.id}/rerun", data={"csrf_token": client.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    session.expire_all()
    assert session.get(InboxDocument, doc.id).attempts == 2
    r = client.post(
        f"/inbox/{doc.id}/reject", data={"csrf_token": client.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    session.expire_all()
    assert session.get(InboxDocument, doc.id).status == InboxStatus.REJECTED
    assert "inv.xml" in client.get("/inbox?tab=rejected").text


def test_pdf_preview_may_be_framed_but_normal_pages_may_not(client, session, admin):
    upload(client, session, ("a.pdf", pdf_from_html(invoice_html()), "application/pdf"))
    doc = session.scalars(select(InboxDocument)).one()
    preview = client.get(f"/inbox/{doc.id}/file")
    assert preview.headers["content-type"] == "application/pdf"
    assert preview.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert preview.headers["content-disposition"].startswith("inline")
    assert preview.content.startswith(b"%PDF")
    for path in ("/inbox", f"/inbox/{doc.id}"):
        page = client.get(path)
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
    assert f'<iframe src="/inbox/{doc.id}/file"' in client.get(f"/inbox/{doc.id}").text


def test_xml_is_never_served_inline_and_images_keep_the_strict_policy(client, session, admin):
    xml_doc = upload_ubl(client, session)
    download = client.get(f"/inbox/{xml_doc.id}/file")
    assert download.headers["content-disposition"].startswith("attachment")
    upload(client, session, ("b.jpg", jpeg_bytes(), "image/jpeg"))
    image = session.scalars(select(InboxDocument).order_by(InboxDocument.id.desc())).first()
    response = client.get(f"/inbox/{image.id}/file")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.fixture
def foreign_document(session, admin):
    from ledgerline.inbox import service

    other = create_administration(session, name="Andere BV")
    doc, _ = service.receive(session, other.id, "x.xml", ubl([("Y", 100, "S", 21)]), "upload")
    session.commit()
    return doc


def test_other_administrations_documents_are_404(client, session, admin, foreign_document):
    doc_id = foreign_document.id
    assert client.get(f"/inbox/{doc_id}").status_code == 404
    assert client.get(f"/inbox/{doc_id}/file").status_code == 404
    for action in ("create", "reject", "rerun"):
        r = client.post(
            f"/inbox/{doc_id}/{action}", data={"csrf_token": client.csrf}, follow_redirects=False
        )
        assert r.status_code == 404, action
    assert "x.xml" not in client.get("/inbox").text
    assert "x.xml" not in client.get(f"/inbox?dup={doc_id}").text


def test_contact_from_another_administration_is_refused(client, session, admin):
    other = create_administration(session, name="Andere BV")
    foreign = contact(session, other, name="Vreemd", kind=ContactKind.SUPPLIER)
    session.commit()
    doc = upload_ubl(client, session)
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, foreign.id, session, admin),
        follow_redirects=False,
    )
    assert r.status_code == 422 and "choose a supplier" in r.text
    session.expire_all()
    assert session.scalars(select(PurchaseInvoice)).all() == []


def test_readonly_users_can_look_but_not_change(readonly_client, client, session, admin):
    doc = upload_ubl(client, session)
    assert readonly_client.get("/inbox").status_code == 200
    assert readonly_client.get(f"/inbox/{doc.id}").status_code == 200
    token = readonly_client.csrf
    for path in (f"/inbox/{doc.id}/create", f"/inbox/{doc.id}/reject", f"/inbox/{doc.id}/rerun"):
        assert readonly_client.post(path, data={"csrf_token": token}).status_code == 403
    r = readonly_client.post(
        "/inbox/upload",
        data={"csrf_token": token},
        files=[("files", ("a.xml", ubl([("Z", 100, "S", 21)]), "application/xml"))],
    )
    assert r.status_code == 403


def test_post_without_csrf_token_is_refused(client, session, admin):
    doc = upload_ubl(client, session)
    assert client.post(f"/inbox/{doc.id}/reject", data={}).status_code == 403


def test_anonymous_users_are_sent_to_login(session, admin):
    from fastapi.testclient import TestClient

    from ledgerline.main import create_app

    r = TestClient(create_app()).get("/inbox", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


# ---- proposal logic without HTTP


def extracted(**kwargs):
    base = dict(
        invoice_number="1", total_excl_cents=1000, lines=[ExtractedLine("x", 1000, 21, 210)]
    )
    return ExtractedInvoice(**{**base, **kwargs})


def test_supplier_match_order_is_vat_then_iban_then_name(session, admin):
    by_name = contact(session, admin, name="Acme B.V.", kind=ContactKind.SUPPLIER)
    by_iban = contact(session, admin, name="Other", kind=ContactKind.BOTH, iban=SUPPLIER_IBAN)
    by_vat = contact(
        session, admin, name="Third", kind=ContactKind.SUPPLIER, vat_number="NL853345678B01"
    )
    customer = contact(
        session, admin, name="Cust", kind=ContactKind.CUSTOMER, vat_number="NL111111111B01"
    )
    session.commit()
    ex = extracted(
        supplier_name="ACME bv", supplier_iban=SUPPLIER_IBAN, supplier_vat_number="NL853345678B01"
    )
    assert review.match_supplier(session, admin.id, ex) == (by_vat, "VAT number")
    ex.supplier_vat_number = None
    assert review.match_supplier(session, admin.id, ex) == (by_iban, "IBAN")
    ex.supplier_iban = None
    assert review.match_supplier(session, admin.id, ex) == (by_name, "name")
    ex.supplier_name = "Nobody"
    assert review.match_supplier(session, admin.id, ex) == (None, None)
    assert review.match_supplier(
        session, admin.id, extracted(supplier_vat_number=customer.vat_number)
    ) == (None, None)


@pytest.mark.parametrize(
    "line, reverse, country, code",
    [
        (ExtractedLine("a", 100, 21), False, "NL", "I21"),
        (ExtractedLine("a", 100, 9), False, "NL", "I9"),
        (ExtractedLine("a", 100, 0), False, "NL", "I0"),
        (ExtractedLine("a", 100, None), True, "NL", "IRV21"),
        (ExtractedLine("a", 100, None), True, "DE", "IEU21"),
        (ExtractedLine("a", 100, None), True, "NL", "IRV21"),
        (ExtractedLine("a", 10000, None, 2100), False, "NL", "I21"),
        (ExtractedLine("a", 10000, None, 900), False, "NL", "I9"),
        (ExtractedLine("a", 10000, 7), False, "NL", None),
        (ExtractedLine("a", 10000, None), False, "NL", None),
    ],
)
def test_vat_code_mapping(line, reverse, country, code):
    assert review.vat_code_for(line, reverse, country) == code


def test_default_account_is_the_suppliers_last_purchase_else_4900(session, admin, supplier):
    assert review.default_account(session, admin.id, supplier).code == "4900"
    assert review.default_account(session, admin.id, None).code == "4900"
    create_purchase_invoice(
        session,
        admin,
        supplier,
        [PurchaseLineInput("Cloud", account(session, admin, "4300").id, 1000, "I21")],
        supplier_reference="A1",
        invoice_date=__import__("datetime").date(2026, 10, 1),
    )
    session.commit()
    assert review.default_account(session, admin.id, supplier).code == "4300"


def test_unreadable_document_gets_one_blank_line(session, admin):
    from ledgerline.inbox import service

    doc, _ = service.receive(session, admin.id, "s.pdf", pdf_without_text(), "upload")
    proposal = review.build_proposal(session, admin, doc)
    assert proposal.extracted is None and len(proposal.rows) == 1
    assert proposal.rows[0]["vat_code"] == ""


def test_unreadable_vat_rate_is_left_for_the_owner_never_filled_with_21(
    client, session, admin, supplier
):
    doc = upload_ubl(client, session)
    doc.extraction = ExtractedInvoice(
        invoice_number="X-9", total_excl_cents=10000, lines=[ExtractedLine("Parts", 10000)]
    ).to_json()
    session.commit()
    proposal = review.build_proposal(session, admin, doc)
    assert proposal.rows[0]["vat_code"] == ""
    assert any("choose the VAT code" in w for w in proposal.warnings)
    assert not any("21%" in w for w in proposal.warnings)
    page = client.get(f"/inbox/{doc.id}")
    assert '<option value="" selected>Choose a VAT code</option>' in page.text
    data = create_data(
        client, doc, supplier.id, session, admin, line_vat_code=["", "I21"]
    )
    r = client.post(f"/inbox/{doc.id}/create", data=data, follow_redirects=False)
    assert "line 1: choose a VAT code" in r.text
    session.expire_all()
    assert session.scalars(select(PurchaseInvoice)).first() is None


def test_non_invoices_have_their_own_tab_and_can_be_rejected_in_one_click(client, session, admin):
    invoice_doc = upload_ubl(client, session)
    statement = upload_ubl(client, session, number="INV-2")
    statement.extraction = ExtractedInvoice(
        document_type="statement", warnings=["This looks like a payout or account statement."]
    ).to_json()
    session.commit()
    assert review.status_counts(session, admin.id) == {
        InboxStatus.REVIEW: 1, "other": 1}
    review_tab = client.get("/inbox?tab=review").text
    other_tab = client.get("/inbox?tab=other").text
    assert f"/inbox/{invoice_doc.id}" in review_tab and f"/inbox/{statement.id}" not in review_tab
    assert f"/inbox/{statement.id}" in other_tab and "Reject all 1" in other_tab
    assert "Payout statement, not an invoice" in other_tab
    r = client.post(
        "/inbox/reject-non-invoices", data={"csrf_token": client.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    session.expire_all()
    assert session.get(InboxDocument, statement.id).status == InboxStatus.REJECTED
    assert session.get(InboxDocument, invoice_doc.id).status == InboxStatus.REVIEW


def zero_line_document(client, session, supplier):
    doc = upload_ubl(client, session)
    doc.extraction = ExtractedInvoice(
        supplier_name="Bakker Software B.V.", invoice_number="INV-2026-17",
        total_excl_cents=10000, total_vat_cents=2100, total_incl_cents=12100,
        warnings=["No lines could be read from this document; enter them by hand."],
    ).to_json()
    session.commit()
    return doc


def test_a_document_with_a_total_but_no_lines_cannot_be_created_empty(
        client, session, admin, supplier):
    doc = zero_line_document(client, session, supplier)
    page = client.get(f"/inbox/{doc.id}").text
    assert "No lines could be read" in page
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, supplier.id, session, admin, line_description=[""],
                         line_account=[str(account(session, admin, "4300").id)],
                         line_amount=[""], line_vat_code=[""], line_vat=[""]),
        follow_redirects=False)
    assert r.status_code == 422 and "no line has an amount" in r.text
    assert session.scalars(select(PurchaseInvoice)).all() == []


def test_zero_amount_lines_do_not_satisfy_a_nonzero_total(client, session, admin, supplier):
    doc = zero_line_document(client, session, supplier)
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, supplier.id, session, admin, line_description=["Item"],
                         line_account=[str(account(session, admin, "4300").id)],
                         line_amount=["0.00"], line_vat_code=["I21"], line_vat=[""]))
    assert r.status_code == 422 and "no line has an amount" in r.text


def test_a_hand_entered_line_unblocks_the_same_document(client, session, admin, supplier):
    doc = zero_line_document(client, session, supplier)
    r = client.post(
        f"/inbox/{doc.id}/create",
        data=create_data(client, doc, supplier.id, session, admin, line_description=["Goods"],
                         line_account=[str(account(session, admin, "4300").id)],
                         line_amount=["100.00"], line_vat_code=["I21"], line_vat=["21.00"]),
        follow_redirects=False)
    assert r.status_code == 303
