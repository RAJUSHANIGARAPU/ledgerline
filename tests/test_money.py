from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledgerline.money import MoneyError, format_eur, from_cents, percent_of, to_cents


@pytest.mark.parametrize(
    ("text", "cents"),
    [("12.34", 1234), ("1.234,56", 123456), ("0,005", 1), ("-0,005", -1), ("10", 1000),
     (" 7,1 ", 710), (Decimal("2.675"), 268)],
)
def test_to_cents_rounds_half_up_and_reads_dutch_notation(text, cents):
    assert to_cents(text) == cents


@pytest.mark.parametrize("bad", ["abc", "", "NaN", "Infinity"])
def test_to_cents_rejects_garbage(bad):
    with pytest.raises(MoneyError):
        to_cents(bad)


def test_floats_are_refused():
    with pytest.raises(MoneyError):
        to_cents(0.1)


@pytest.mark.parametrize(
    ("cents", "text"),
    [(0, "€\u00a00,00"), (5, "€\u00a00,05"), (123456, "€\u00a01.234,56"),
     (-100000, "-€\u00a01.000,00")],
)
def test_format_eur(cents, text):
    assert format_eur(cents) == text


@pytest.mark.parametrize(
    ("base", "rate", "vat"), [(1000, 21, 210), (1, 21, 0), (3, 21, 1), (250, 9, 23), (50, 9, 5)]
)
def test_percent_of_rounds_half_up(base, rate, vat):
    assert percent_of(base, Decimal(rate)) == vat


@given(st.integers(min_value=-10**12, max_value=10**12))
def test_cents_round_trip(cents):
    assert to_cents(from_cents(cents)) == cents
