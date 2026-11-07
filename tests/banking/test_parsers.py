import pytest

from ledgerline.banking import importer
from ledgerline.banking.parsed import StatementError

from .conftest import fixture_bytes


def test_camt053_v02_credit_debit_and_structured_reference():
    parsed = importer.parse_statement(fixture_bytes("camt053_02.xml"))
    assert parsed.file_format == "camt053"
    assert parsed.iban == "NL91ABNA0417164300"
    assert (parsed.opening_cents, parsed.closing_cents) == (100_000, 107_100)
    credit, debit = parsed.transactions
    assert credit.amount_cents == 12_100
    assert credit.counterparty_name == "Klant BV"
    assert credit.counterparty_iban == "NL02ABNA0123456789"
    assert credit.description == "Factuur 2026-0001"
    assert credit.end_to_end_id == "E2E-1"
    assert credit.bank_reference == "REF-A"
    assert debit.amount_cents == -5_000
    assert debit.counterparty_name == "Leverancier BV"
    assert debit.description == "INK-77"
    assert debit.end_to_end_id is None


def test_camt053_v08_batch_entry_splits_per_txdtls():
    parsed = importer.parse_statement(fixture_bytes("camt053_08_batch.xml"))
    assert [t.amount_cents for t in parsed.transactions] == [-10_000, -20_000]
    assert [t.counterparty_name for t in parsed.transactions] == ["Leverancier BV", "Kantoor BV"]
    assert [t.end_to_end_id for t in parsed.transactions] == ["PAY-1", "PAY-2"]
    assert len({t.bank_reference for t in parsed.transactions}) == 2
    assert parsed.transactions[0].booking_date.isoformat() == "2026-06-10"
    assert parsed.opening_cents + sum(t.amount_cents for t in parsed.transactions) == (
        parsed.closing_cents
    )


def test_mt940_maps_to_the_same_structure():
    parsed = importer.parse_statement(fixture_bytes("mt940_sample.sta"))
    assert parsed.file_format == "mt940"
    assert parsed.iban == "NL91ABNA0417164300"
    assert (parsed.opening_cents, parsed.closing_cents) == (100_000, 107_100)
    credit, debit = parsed.transactions
    assert credit.amount_cents == 12_100
    assert credit.counterparty_iban == "NL02ABNA0123456789"
    assert credit.counterparty_name == "Klant BV"
    assert credit.description == "Factuur 2026-0001"
    assert credit.end_to_end_id == "E2E-1"
    assert credit.bank_reference == "BANKREF001"
    assert debit.amount_cents == -5_000


@pytest.mark.parametrize("name", ["billion_laughs.xml", "external_entity.xml"])
def test_malicious_xml_is_rejected(name):
    with pytest.raises(StatementError, match="safe XML"):
        importer.parse_statement(fixture_bytes(name))


def test_non_eur_statement_is_rejected():
    content = fixture_bytes("camt053_02.xml").replace(b'Ccy="EUR">121.00', b'Ccy="USD">121.00')
    with pytest.raises(StatementError, match="only EUR"):
        importer.parse_statement(content)


def test_unknown_format_is_rejected():
    with pytest.raises(StatementError, match="unrecognised"):
        importer.parse_statement(b"date;amount\n2026-01-01;1.00\n")


def test_xml_that_is_not_camt_is_rejected():
    with pytest.raises(StatementError, match="not a CAMT.053"):
        importer.parse_statement(b"<root><a/></root>")
