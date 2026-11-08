import pytest

from ledgerline.inbox.extract import ExtractionError
from ledgerline.inbox.extract.ubl import UblExtractor, parse_ubl
from tests.inbox.factories import SUPPLIER_IBAN, ubl


def test_invoice_fields_and_totals():
    ex = parse_ubl(ubl([("Hosting", 50000, "S", 21), ("Licence", 12345, "S", 21)]))
    assert ex.supplier_name == "Bakker Software B.V."
    assert ex.supplier_vat_number == "NL853345678B01"
    assert ex.supplier_kvk == "87654321"
    assert ex.supplier_iban == SUPPLIER_IBAN
    assert ex.invoice_number == "INV-2026-17"
    assert (ex.invoice_date.isoformat(), ex.due_date.isoformat()) == ("2026-11-03", "2026-12-03")
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (62345, 13092, 75437)
    assert [(ln.description, ln.amount_excl_cents, ln.vat_rate_percent) for ln in ex.lines] == [
        ("Hosting", 50000, 21),
        ("Licence", 12345, 21),
    ]
    assert sum(ln.vat_cents for ln in ex.lines) == 13092
    assert not ex.reverse_charge and ex.warnings == []


def test_multiple_vat_rates_keep_the_stated_vat_per_rate():
    ex = parse_ubl(
        ubl([("Boek", 2000, "AA", 9), ("Advies", 10000, "S", 21), ("Vrij", 500, "Z", 0)])
    )
    by_rate = {ln.vat_rate_percent: ln for ln in ex.lines}
    assert (
        by_rate[9].vat_cents == 180 and by_rate[21].vat_cents == 2100 and by_rate[0].vat_cents == 0
    )
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (12500, 2280, 14780)


def test_credit_note_amounts_are_negative():
    ex = parse_ubl(ubl([("Retour", 10000, "S", 21)], credit=True, number="CN-9"))
    assert ex.invoice_number == "CN-9"
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (-10000, -2100, -12100)
    assert ex.lines[0].amount_excl_cents == -10000 and ex.lines[0].vat_cents == -2100
    assert any("credit note" in w for w in ex.warnings)


def test_reverse_charge_category_ae():
    ex = parse_ubl(ubl([("Onderaanneming", 40000, "AE", 0)]))
    assert ex.reverse_charge is True
    assert ex.lines[0].vat_rate_percent is None and ex.lines[0].vat_cents is None
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (40000, 0, 40000)
    assert any("reverse" in w.lower() for w in ex.warnings)


def test_foreign_currency_is_flagged():
    ex = parse_ubl(ubl([("Hosting", 1000, "S", 21)], currency="USD"))
    assert ex.currency == "USD" and any("USD" in w for w in ex.warnings)


def test_inconsistent_totals_warn():
    xml = ubl([("Hosting", 10000, "S", 21)]).replace(
        b'<cbc:TaxInclusiveAmount currencyID="EUR">121.00',
        b'<cbc:TaxInclusiveAmount currencyID="EUR">125.00',
    )
    ex = parse_ubl(xml)
    assert any("do not add up" in w for w in ex.warnings)


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">&b;</Invoice>',
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">&e;</Invoice>',
        b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"><broken>',
        b"<html/>",
    ],
)
def test_malicious_or_broken_xml_is_refused(payload):
    with pytest.raises(ExtractionError):
        parse_ubl(payload)


def test_missing_invoice_number_is_refused():
    with pytest.raises(ExtractionError, match="invoice number"):
        parse_ubl(ubl([("x", 100, "S", 21)]).replace(b"<cbc:ID>INV-2026-17</cbc:ID>", b""))


def test_extractor_only_claims_ubl_xml():
    extractor = UblExtractor()
    assert extractor.can_handle("application/xml", ubl([("x", 100, "S", 21)]))
    assert not extractor.can_handle("application/xml", b"<?xml version='1.0'?><camt/>")
    assert not extractor.can_handle("application/pdf", ubl([("x", 100, "S", 21)]))
