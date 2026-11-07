from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from ledgerline.invoicing.calc import allocate_vat, line_amount, side, vat_by_code
from ledgerline.money import percent_of


def test_line_amount_rounds_half_up():
    assert line_amount(Decimal("1.5"), 333) == 500  # 499.5
    assert line_amount(Decimal("0.333"), 1000) == 333
    assert line_amount(Decimal("-1.5"), 333) == -500


def test_vat_is_computed_once_per_code_on_the_sum():
    items = [("V21", 2)] * 10
    assert vat_by_code(items) == {"V21": (20, 4)}  # per-line rounding would give 0


def test_zero_rate_codes_carry_a_base_and_no_vat():
    result = vat_by_code([("VRV", 5000), ("V9", 1000), ("V9", 1000)])
    assert result["VRV"] == (5000, 0)
    assert result["V9"] == (2000, 180)


def test_side_places_signed_amounts():
    assert side(100, credit_normal=True) == (0, 100)
    assert side(-100, credit_normal=True) == (100, 0)
    assert side(100, credit_normal=False) == (100, 0)
    assert side(-100, credit_normal=False) == (0, 100)


amounts = st.lists(st.integers(min_value=-5_000_000, max_value=5_000_000), min_size=1, max_size=12)


@given(amounts, st.sampled_from(["V21", "V9", "V0", "VRV", "VEU", "VKOR"]))
def test_code_vat_equals_percent_of_summed_base(values, code):
    from ledgerline.vat import codes

    base, vat = vat_by_code([(code, v) for v in values])[code]
    assert base == sum(values)
    assert vat == percent_of(sum(values), codes.get(code).rate)


@given(amounts, st.sampled_from(["IRV21", "IEU21", "IEU9", "INEU21"]))
def test_allocated_reverse_charge_vat_adds_up_exactly(values, code):
    from ledgerline.vat import codes

    shares = allocate_vat(values, code)
    assert len(shares) == len(values)
    assert sum(shares) == percent_of(sum(values), codes.get(code).rate)
