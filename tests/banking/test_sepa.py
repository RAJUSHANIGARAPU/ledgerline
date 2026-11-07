from datetime import UTC, date, datetime

import pytest
from defusedxml.ElementTree import fromstring

from ledgerline.banking.sepa import SepaError, SepaPayment, build_pain001, clean_text

NS = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}
NOW = datetime(2026, 6, 15, 9, 0, 0, tzinfo=UTC)


def build(payments, **kw):
    return build_pain001(
        "Testbedrijf BV", "NL91ABNA0417164300", payments, date(2026, 6, 16),
        message_id="LL-1", created_at=NOW, **kw,
    )


def payment(cents, e2e="INV1", iban="NL86INGB0002445588"):
    return SepaPayment("Leverancier BV", iban, cents, e2e, "Inkoop 77")


def test_amounts_and_control_sums_are_exact():
    xml = build([payment(10, "A"), payment(20, "B"), payment(123_456, "C")])
    root = fromstring(xml)
    assert root.findtext("p:CstmrCdtTrfInitn/p:GrpHdr/p:NbOfTxs", namespaces=NS) == "3"
    assert root.findtext("p:CstmrCdtTrfInitn/p:GrpHdr/p:CtrlSum", namespaces=NS) == "1234.86"
    info = root.find("p:CstmrCdtTrfInitn/p:PmtInf", NS)
    assert info.findtext("p:NbOfTxs", namespaces=NS) == "3"
    assert info.findtext("p:CtrlSum", namespaces=NS) == "1234.86"
    amounts = [a.text for a in info.findall("p:CdtTrfTxInf/p:Amt/p:InstdAmt", NS)]
    assert amounts == ["0.10", "0.20", "1234.56"]
    assert info.findtext("p:DbtrAcct/p:Id/p:IBAN", namespaces=NS) == "NL91ABNA0417164300"
    assert info.findtext("p:ReqdExctnDt", namespaces=NS) == "2026-06-16"
    assert info.findtext("p:CdtTrfTxInf/p:RmtInf/p:Ustrd", namespaces=NS) == "Inkoop 77"


def test_invalid_creditor_iban_is_refused():
    with pytest.raises(SepaError, match="invalid IBAN"):
        build([payment(100, iban="NL00BOGUS0000000000")])


def test_empty_and_non_positive_payments_are_refused():
    with pytest.raises(SepaError):
        build([])
    with pytest.raises(SepaError, match="positive"):
        build([payment(0)])


def test_text_is_reduced_to_the_sepa_character_set():
    assert clean_text("Café & Zoon <b>", 70) == "Cafe Zoon b"
    assert len(clean_text("x" * 300, 140)) == 140
