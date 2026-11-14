from datetime import date

import pytest

from ledgerline.banking import csv_import
from ledgerline.banking.csv_import import CsvMapping, parse_amount, parse_csv, parse_date
from ledgerline.banking.parsed import StatementError

from .csv_samples import (
    OTHER_RABO_IBAN,
    RABO_IBAN,
    generic_csv,
    rabo_csv,
    rabo_row,
    rabo_standard,
)


@pytest.mark.parametrize(
    ("raw", "cents"),
    [
        ("1.234,56", 123_456), ("-1.234,56", -123_456), ("+99,00", 9_900), ("12,5", 1_250),
        ("€ 12,50", 1_250), ("EUR 12,50", 1_250), ("12,50-", -1_250), ("(12,50)", -1_250),
        ("1,234.56", 123_456), ("1.234.567,89", 123_456_789), ("1,234,567.89", 123_456_789),
        ("1234.56", 123_456), ("0,05", 5), (",50", 50), ("1.234.567", 123_456_700),
        ("1 234,56", 123_456), ("1 234,56", 123_456), ("-0,01", -1), ("100", 10_000),
        ("12,50 ", 1_250),
    ],
)
def test_amounts_in_dutch_and_english_notation(raw, cents):
    assert parse_amount(raw) == cents


@pytest.mark.parametrize(
    "raw", ["", "abc", "--5", "12,", "1.2.3", "1,2345", "1.234,5,6", "12,50 EUR EUR 1", "1,23.45,6"]
)
def test_invalid_amounts_are_refused(raw):
    with pytest.raises(StatementError):
        parse_amount(raw)


@pytest.mark.parametrize("raw", ["1.234", "1,234", "12,345", "0.123"])
def test_a_lone_separator_before_three_digits_is_ambiguous_so_it_is_refused(raw):
    with pytest.raises(StatementError, match="ambiguous"):
        parse_amount(raw)


def test_an_explicit_decimal_separator_settles_the_ambiguity():
    assert parse_amount("1.234", ",") == 123_400
    assert parse_amount("1,234", ".") == 123_400
    with pytest.raises(StatementError, match="two decimals"):
        parse_amount("1.234", ".")
    with pytest.raises(StatementError):
        parse_amount("1,2,3", ",")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-06-02", date(2026, 6, 2)), ("20260602", date(2026, 6, 2)),
        ("02-06-2026", date(2026, 6, 2)), ("02/06/2026", date(2026, 6, 2)),
        ("02.06.2026", date(2026, 6, 2)), ("02-06-26", date(2026, 6, 2)),
        ("2026-06-02T10:15:00", date(2026, 6, 2)), ("02-06-2026 10:15", date(2026, 6, 2)),
    ],
)
def test_dates_in_common_formats(raw, expected):
    assert parse_date(raw) == expected


def test_dates_follow_an_explicit_format_and_refuse_nonsense():
    assert parse_date("06/02/2026", "%m/%d/%Y") == date(2026, 6, 2)
    with pytest.raises(StatementError):
        parse_date("31-02-2026")
    with pytest.raises(StatementError, match="does not match"):
        parse_date("2026-06-02", "%d-%m-%Y")


def test_utf8_bom_and_cp1252_are_both_decoded():
    header, row = ["Datum", "Bedrag", "Naam"], [["02-06-2026", "1,00", "Café Zoë €"]]
    mapping = CsvMapping(date="Datum", amount="Bedrag", counterparty="Naam")
    for bom, encoding in ((True, "utf-8"), (False, "cp1252")):
        data = generic_csv(row, header, bom=bom, encoding=encoding)
        parsed = parse_csv(data, mapping, RABO_IBAN)
        assert parsed.transactions[0].counterparty_name == "Café Zoë €"


@pytest.mark.parametrize(("delimiter", "name"), [(";", "semicolon"), (",", "comma"), ("\t", "tab")])
def test_delimiter_is_detected(delimiter, name):
    data = generic_csv([["02-06-2026", "1.234,56", "Piet, Jan"]], ["Datum", "Bedrag", "Naam"],
                       delimiter=delimiter)
    parsed = parse_csv(data, CsvMapping(date="Datum", amount="Bedrag", counterparty="Naam"),
                       RABO_IBAN)
    assert parsed.transactions[0].amount_cents == 123_456, name
    assert parsed.transactions[0].counterparty_name == "Piet, Jan"


def test_a_preamble_line_before_the_header_is_skipped():
    data = b"EXPORT 2026\n" + generic_csv([["02-06-2026", "1,00"]], ["Datum", "Bedrag"])
    assert csv_import.read_table(data).headers == ["Datum", "Bedrag"]


def test_file_without_transactions_or_header_is_refused():
    with pytest.raises(StatementError, match="no transactions"):
        csv_import.read_table(generic_csv([], ["Datum", "Bedrag"]))
    with pytest.raises(StatementError, match="not a CSV"):
        csv_import.read_table(b"just some words\n")


def test_rabobank_preset_is_detected_and_parsed():
    table = csv_import.read_table(rabo_standard())
    preset = csv_import.detect_preset(table.headers)
    assert preset and preset.key == "rabobank"
    assert preset.accessed == "2026-11-14" and preset.source_url.startswith("https://")
    parsed = parse_csv(rabo_standard(), preset.mapping, RABO_IBAN)
    assert parsed.file_format == "csv" and parsed.iban == RABO_IBAN
    first, second = parsed.transactions
    assert (first.booking_date, first.amount_cents) == (date(2026, 6, 2), 12_100)
    assert (first.counterparty_name, first.counterparty_iban) == (
        "Klant BV", "NL02ABNA0123456789")
    assert (first.description, first.bank_reference) == ("Factuur 2026-0001", "101")
    assert second.amount_cents == -5_000 and second.value_date == date(2026, 6, 3)


def test_rabobank_description_joins_the_three_fields():
    row = rabo_row(1, "2026-06-02", "+1,00", "A", "", "eerste")
    row[-6], row[-5] = "tweede", "derde"
    parsed = parse_csv(rabo_csv([row]), csv_import.PRESETS["rabobank"].mapping, RABO_IBAN)
    assert parsed.transactions[0].description == "eerste tweede derde"


def test_rabobank_file_with_several_accounts_keeps_only_the_chosen_one():
    data = rabo_csv([
        rabo_row(1, "2026-06-02", "+10,00", "A", "", "mine"),
        rabo_row(1, "2026-06-02", "+20,00", "B", "", "theirs", own=OTHER_RABO_IBAN),
    ])
    parsed = parse_csv(data, csv_import.PRESETS["rabobank"].mapping, RABO_IBAN)
    assert [t.amount_cents for t in parsed.transactions] == [1_000]
    assert OTHER_RABO_IBAN in parsed.notes[0]


def test_rabobank_file_without_rows_for_the_account_is_refused():
    data = rabo_csv([rabo_row(1, "2026-06-02", "+20,00", "B", "", "x", own=OTHER_RABO_IBAN)])
    with pytest.raises(StatementError, match="no transactions for"):
        parse_csv(data, csv_import.PRESETS["rabobank"].mapping, RABO_IBAN)


def test_non_euro_rows_are_refused():
    data = rabo_csv([rabo_row(1, "2026-06-02", "+20,00", "B", "", "x", currency="USD")])
    with pytest.raises(StatementError, match="only EUR"):
        parse_csv(data, csv_import.PRESETS["rabobank"].mapping, RABO_IBAN)


def test_a_non_iban_counterparty_account_is_dropped_not_fatal():
    data = rabo_csv([rabo_row(1, "2026-06-02", "+20,00", "B", "1234567", "x")])
    parsed = parse_csv(data, csv_import.PRESETS["rabobank"].mapping, RABO_IBAN)
    assert parsed.transactions[0].counterparty_iban is None


def test_other_headers_do_not_match_the_preset():
    assert csv_import.detect_preset(["Date", "Amount", "Description"]) is None
    english = ["IBAN/BBAN", "Currency", "Seq No", "Date", "Value Date", "Amount"]
    assert csv_import.detect_preset(english) is None


def test_generic_signed_amount_column():
    data = generic_csv([["02-06-2026", "-1.234,56", "Bakker", "NL86INGB0002445588", "Brood"]],
                       ["Datum", "Bedrag", "Naam", "IBAN", "Omschrijving"])
    mapping = CsvMapping(date="datum", amount="BEDRAG", counterparty="Naam", iban="IBAN",
                         description=["Omschrijving"])
    tx = parse_csv(data, mapping, RABO_IBAN).transactions[0]
    assert (tx.amount_cents, tx.counterparty_iban, tx.description) == (
        -123_456, "NL86INGB0002445588", "Brood")


def test_generic_debit_and_credit_columns():
    data = generic_csv(
        [["02-06-2026", "", "10,00"], ["03-06-2026", "4,50", ""], ["04-06-2026", "", "3,00"]],
        ["Datum", "Debet", "Credit"],
    )
    mapping = CsvMapping(date="Datum", debit="Debet", credit="Credit")
    amounts = [t.amount_cents for t in parse_csv(data, mapping, RABO_IBAN).transactions]
    assert amounts == [1_000, -450, 300]


def test_generic_debit_and_credit_both_filled_is_refused():
    data = generic_csv([["02-06-2026", "1,00", "2,00"]], ["Datum", "Debet", "Credit"])
    with pytest.raises(StatementError, match="row 2.*both"):
        parse_csv(data, CsvMapping(date="Datum", debit="Debet", credit="Credit"), RABO_IBAN)


@pytest.mark.parametrize(("word", "sign"), [("Af", -1), ("Bij", 1), ("D", -1), ("C", 1),
                                            ("Debit", -1), ("Credit", 1)])
def test_generic_amount_with_a_direction_column(word, sign):
    data = generic_csv([["20260602", "12,50", word]], ["Datum", "Bedrag", "Af Bij"])
    mapping = CsvMapping(date="Datum", amount="Bedrag", direction="Af Bij")
    assert parse_csv(data, mapping, RABO_IBAN).transactions[0].amount_cents == sign * 1_250


def test_unknown_direction_word_is_refused():
    data = generic_csv([["20260602", "12,50", "??"]], ["Datum", "Bedrag", "Af Bij"])
    with pytest.raises(StatementError, match="indicator"):
        parse_csv(data, CsvMapping(date="Datum", amount="Bedrag", direction="Af Bij"), RABO_IBAN)


def test_missing_mapped_column_is_reported():
    data = generic_csv([["02-06-2026", "1,00"]], ["Datum", "Bedrag"])
    with pytest.raises(csv_import.MissingColumns, match="Saldo"):
        parse_csv(data, CsvMapping(date="Datum", amount="Bedrag", reference="Saldo"), RABO_IBAN)


def test_errors_name_the_row_and_stop_after_a_few():
    rows = [["02-06-2026", "x"]] * 9
    data = generic_csv(rows, ["Datum", "Bedrag"])
    with pytest.raises(StatementError) as err:
        parse_csv(data, CsvMapping(date="Datum", amount="Bedrag"), RABO_IBAN)
    assert "row 2" in str(err.value) and str(err.value).count("row ") == csv_import.MAX_ERRORS


def test_zero_amounts_are_refused():
    data = generic_csv([["02-06-2026", "0,00"]], ["Datum", "Bedrag"])
    with pytest.raises(StatementError, match="zero"):
        parse_csv(data, CsvMapping(date="Datum", amount="Bedrag"), RABO_IBAN)


@pytest.mark.parametrize(
    "mapping",
    [
        CsvMapping(date=""), CsvMapping(date="d"), CsvMapping(date="d", amount="a", debit="x"),
        CsvMapping(date="d", debit="x", direction="y"),
        CsvMapping(date="d", amount="a", decimal=";"),
        CsvMapping(date="d", amount="a", date_format="nonsense"),
    ],
)
def test_invalid_mappings_are_refused(mapping):
    with pytest.raises(StatementError):
        mapping.validate()


def test_mapping_survives_a_json_round_trip():
    mapping = CsvMapping(date="Datum", amount="Bedrag", description=["a", "b"], decimal=",")
    assert CsvMapping.from_json(mapping.to_json()) == mapping
    assert "iban" not in mapping.to_json()


def test_column_suggestions_use_common_header_words():
    found = csv_import.suggest_mapping(["Datum", "Naam / Omschrijving", "Bedrag", "Mededelingen"])
    assert found == {"date": "Datum", "amount": "Bedrag", "counterparty": "Naam / Omschrijving",
                     "description": ["Mededelingen"]}
