from datetime import date

import pytest

from ledgerline.platform import validation as v

VALID = ["NL123456789B01", "nl 1234.56.789 b01", "DE123456789", "BE0123456789"]
INVALID = ["NL123456789B1", "NL12345678B01", "NL123456789X01", "DE12345", "123456789B01"]


@pytest.mark.parametrize("raw", VALID)
def test_valid_vat_numbers(raw):
    value, err = v.check_vat(raw)
    assert err is None
    assert value == value.upper().replace(" ", "").replace(".", "")


@pytest.mark.parametrize("raw", INVALID)
def test_invalid_vat_numbers(raw):
    assert v.check_vat(raw)[1] is not None


def test_empty_vat_is_allowed():
    assert v.check_vat("  ") == (None, None)


def test_kvk_needs_eight_digits():
    assert v.check_kvk("12345678") == ("12345678", None)
    assert v.check_kvk("1234567")[1] is not None
    assert v.check_kvk("1234567a")[1] is not None


def test_iban_is_checked_and_compacted():
    assert v.check_iban("NL91 ABNA 0417 1643 00") == ("NL91ABNA0417164300", None)
    assert v.check_iban("NL91ABNA0417164301")[1] is not None


def test_country_defaults_and_validates():
    assert v.check_country("") == ("NL", None)
    assert v.check_country("de") == ("DE", None)
    assert v.check_country("NLD")[1] is not None


def test_password_rules():
    assert v.check_password("short", "short") is not None
    assert v.check_password("a-long-enough-pass", "a-long-enough-other") is not None
    assert v.check_password("a-long-enough-pass", "a-long-enough-pass") is None


def test_retention_runs_to_end_of_seventh_year():
    assert v.retention_until(date(2024, 3, 5)) == date(2031, 12, 31)
    assert v.retention_until(date(2024, 12, 31), years=10) == date(2034, 12, 31)
    assert v.retention_until(None) is None
