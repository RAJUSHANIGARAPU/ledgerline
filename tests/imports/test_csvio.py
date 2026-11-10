from datetime import date

import pytest

from ledgerline.imports import csvio
from ledgerline.money import MoneyError

ALIASES = {"name": ("naam",), "city": ("plaats",), "note": ()}


def table(text, **kw):
    return csvio.read_table(text, ALIASES, kw.get("required", ("name",)))


def test_semicolon_and_comma_files_give_the_same_rows():
    semi = table("name;city\nA BV;Utrecht\nB;Delft\n")
    comma = table("name,city\nA BV,Utrecht\nB,Delft\n")
    assert semi.delimiter == ";" and comma.delimiter == ","
    assert semi.rows == comma.rows == [(2, {"name": "A BV", "city": "Utrecht"}),
                                       (3, {"name": "B", "city": "Delft"})]


def test_tab_separated_and_single_column_files():
    assert table("name\tcity\nA\tX\n").delimiter == "\t"
    assert table("name\nA\nB\n").rows[1] == (3, {"name": "B"})


def test_utf8_with_and_without_bom_decode_identically():
    plain, warn_plain = csvio.decode("name;city\nCafé Zoë;Gouda\n".encode())
    bom, warn_bom = csvio.decode(b"\xef\xbb\xbf" + "name;city\nCafé Zoë;Gouda\n".encode())
    assert plain == bom and warn_plain == warn_bom == []
    assert table(bom).rows[0][1]["name"] == "Café Zoë"


def test_bom_does_not_corrupt_the_first_header():
    text, _ = csvio.decode(b"\xef\xbb\xbfname;city\nA;B\n")
    assert table(text).rows[0][1]["name"] == "A"


def test_windows_1252_file_is_read_with_a_warning():
    text, warnings = csvio.decode("name;city\nZoë Café;Den Haag\n".encode("cp1252"))
    assert "Zoë Café" in text and len(warnings) == 1 and "Windows-1252" in warnings[0]


def test_quotes_embedded_separators_and_line_breaks():
    t = table('name;city\n"Jansen; Zn ""Het Anker"" BV";"Den\nBosch"\nPlain;X\n')
    assert t.rows[0] == (2, {"name": 'Jansen; Zn "Het Anker" BV', "city": "Den\nBosch"})
    assert t.rows[1][1]["name"] == "Plain"
    assert t.rows[1][0] == 3  # numbered by record, like a spreadsheet row


def test_aliases_case_and_spacing_of_headers_and_ignored_columns():
    t = table("  Naam ; PLAATS ;Extra\nA;B;C\n")
    assert t.rows == [(2, {"name": "A", "city": "B"})]
    assert t.warnings == ["Ignored columns: Extra."]


def test_blank_rows_are_skipped_and_numbers_follow_the_file():
    t = table("name\nA\n\n;\nB\n")
    assert [n for n, _ in t.rows] == [2, 5]


def test_more_cells_than_header_is_a_row_error_not_a_silent_drop():
    t = table("name;city\nA;B;oops\nC;D\n")
    assert 2 in t.row_errors and 3 not in t.row_errors


@pytest.mark.parametrize("raw, message", [
    (b"", "empty"), (b"   \n", "empty"), (b"name;city\n", "no rows"),
    (b"city\nX\n", "Missing column: name"), (b"name;name\nA;B\n", "twice"),
])
def test_unreadable_files_raise_a_readable_error(raw, message):
    with pytest.raises(csvio.CsvError, match=message):
        table(csvio.decode(raw)[0])


def test_file_and_row_limits(monkeypatch):
    with pytest.raises(csvio.CsvError, match="larger"):
        csvio.decode(b"x" * (csvio.MAX_BYTES + 1))
    monkeypatch.setattr(csvio, "MAX_ROWS", 2)
    with pytest.raises(csvio.CsvError, match="More than 2 rows"):
        table("name\nA\nB\nC\n")


@pytest.mark.parametrize("raw, cents", [
    ("1.234,56", 123456), ("1234,56", 123456), ("1234.56", 123456), ("1,234.56", 123456),
    ("1.234.567,89", 123456789), ("12", 1200), ("0,5", 50), ("€ 1.210,00", 121000),
    (" 605,00 EUR", 60500), ("-10,50", -1050), ("0,00", 0), ("1.000,00", 100000),
])
def test_amounts_in_dutch_and_english_notation(raw, cents):
    assert csvio.parse_amount(raw) == cents


@pytest.mark.parametrize("raw", ["", "abc", "1.234", "12.345.678", "1,234,56", "1,234",
                                 "10,555", "1e3", "--5", "1 2 3,45,6"])
def test_amounts_that_would_be_guesses_are_refused(raw):
    with pytest.raises(MoneyError):
        csvio.parse_amount(raw)


def test_ambiguous_thousands_message_tells_what_to_write():
    with pytest.raises(MoneyError, match="1234,00 or 1.234,00"):
        csvio.parse_amount("1.234")


@pytest.mark.parametrize("raw, day", [
    ("2026-10-01", date(2026, 10, 1)), ("2026-1-5", date(2026, 1, 5)),
    ("01-10-2026", date(2026, 10, 1)), ("1/10/2026", date(2026, 10, 1)),
    ("31.12.2025", date(2025, 12, 31)),
])
def test_dates_iso_and_day_first(raw, day):
    assert csvio.parse_date(raw) == day


@pytest.mark.parametrize("raw", ["", "2026-13-01", "13/13/2026", "31-02-2026", "1 oct 2026",
                                 "26-10-01"])
def test_invalid_dates(raw):
    with pytest.raises(ValueError, match="not a valid date"):
        csvio.parse_date(raw)
