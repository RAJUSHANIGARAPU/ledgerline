from decimal import Decimal

import pytest
from defusedxml import ElementTree as ET

from ledgerline.invoicing.pdf import render_invoice_html, render_invoice_pdf
from ledgerline.invoicing.service import create_credit_note, issue_sales_invoice
from ledgerline.invoicing.ubl import CUSTOMIZATION, NS, PROFILE, build_ubl
from tests.invoicing.conftest import sline


def cbc(el, path):
    return el.find(path, NS)


def findall(el, path):
    return el.findall(path, NS)


def dec(el, path):
    return Decimal(cbc(el, path).text)


@pytest.fixture
def mixed_invoice(session, admin, owner, make_invoice):
    inv = make_invoice([
        sline(session, admin, "Consulting", "1.5", 8333, "V21", "8000"),
        sline(session, admin, "Book", "2", 1999, "V9", "8010"),
        sline(session, admin, "Subcontract", "1", 40000, "VRV", "8020"),
    ])
    issue_sales_invoice(session, admin, inv, owner.id)
    session.commit()
    return inv


def test_ubl_invoice_is_wellformed_and_consistent(session, admin, mixed_invoice):
    root = ET.fromstring(build_ubl(mixed_invoice, admin, None))
    assert root.tag == f"{{{NS['inv']}}}Invoice"
    assert cbc(root, "cbc:CustomizationID").text == CUSTOMIZATION
    assert cbc(root, "cbc:ProfileID").text == PROFILE
    assert cbc(root, "cbc:ID").text == "2026-0001"
    assert cbc(root, "cbc:InvoiceTypeCode").text == "380"

    lines = findall(root, "cac:InvoiceLine")
    line_sum = sum(dec(line, "cbc:LineExtensionAmount") for line in lines)
    totals = cbc(root, "cac:LegalMonetaryTotal")
    assert line_sum == dec(totals, "cbc:LineExtensionAmount") == Decimal("564.98")
    tax_total = cbc(root, "cac:TaxTotal")
    subtotals = findall(tax_total, "cac:TaxSubtotal")
    assert sum(dec(s, "cbc:TaxAmount") for s in subtotals) == dec(tax_total, "cbc:TaxAmount")
    assert dec(tax_total, "cbc:TaxAmount") == Decimal("26.25") + Decimal("3.60")
    assert dec(totals, "cbc:TaxInclusiveAmount") == dec(totals, "cbc:TaxExclusiveAmount") + dec(
        tax_total, "cbc:TaxAmount")
    assert dec(totals, "cbc:PayableAmount") == Decimal(mixed_invoice.total_incl_cents) / 100
    taxable = sum(dec(s, "cbc:TaxableAmount") for s in subtotals)
    assert taxable == dec(totals, "cbc:TaxExclusiveAmount")


def test_ubl_vat_categories(session, admin, mixed_invoice):
    root = ET.fromstring(build_ubl(mixed_invoice, admin, None))
    cats = {}
    for sub in findall(cbc(root, "cac:TaxTotal"), "cac:TaxSubtotal"):
        category = cbc(sub, "cac:TaxCategory")
        cats[cbc(category, "cbc:ID").text] = (
            cbc(category, "cbc:Percent").text, cbc(category, "cbc:TaxExemptionReason"))
    assert set(cats) == {"S", "AE"}
    assert cats["AE"][0] == "0.00" and "btw verlegd" in cats["AE"][1].text
    standard_rates = {
        cbc(cbc(line, "cac:Item/cac:ClassifiedTaxCategory"), "cbc:Percent").text
        for line in findall(root, "cac:InvoiceLine")
    }
    assert standard_rates == {"21.00", "9.00", "0.00"}
    endpoint = cbc(root, "cac:AccountingSupplierParty/cac:Party/cbc:EndpointID")
    assert endpoint.get("schemeID") == "9944"
    iban = cbc(root, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID")
    assert iban.text == "NL91ABNA0417164300"


def test_ubl_credit_note_uses_positive_amounts_and_references_original(
        session, admin, owner, mixed_invoice):
    note = create_credit_note(session, admin, mixed_invoice, owner.id)
    session.commit()
    root = ET.fromstring(build_ubl(note, admin, mixed_invoice.number))
    assert root.tag == f"{{{NS['cn']}}}CreditNote"
    assert cbc(root, "cbc:CreditNoteTypeCode").text == "381"
    assert cbc(root, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID").text == "2026-0001"
    totals = cbc(root, "cac:LegalMonetaryTotal")
    assert dec(totals, "cbc:PayableAmount") == Decimal(mixed_invoice.total_incl_cents) / 100
    credited = findall(root, "cac:CreditNoteLine")
    assert [Decimal(cbc(ln, "cbc:CreditedQuantity").text) for ln in credited] == [
        Decimal("1.5"), Decimal("2"), Decimal("1")]


def test_pdf_html_shows_required_invoice_content(session, admin, mixed_invoice):
    html = render_invoice_html(mixed_invoice, admin)
    for needle in ("Testbedrijf", "12345678", "NL123456789B01", "Klant BV", "Klantweg 5",
                   "2026-0001", "01-03-2026", "Leveringsdatum", "btw verlegd",
                   "€\xa0564,98", "€\xa0125,00"):
        assert needle in html, needle


def test_pdf_is_generated_without_network(session, admin, mixed_invoice):
    pdf = render_invoice_pdf(mixed_invoice, admin)
    assert pdf.startswith(b"%PDF-") and len(pdf) > 2000
