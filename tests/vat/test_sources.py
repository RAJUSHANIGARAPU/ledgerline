"""VAT return rules checked against belastingdienst.nl on 2026-11-08 (see vat/sources.py)."""
from datetime import date

import pytest

from ledgerline.models import SystemRole
from ledgerline.vat import codes
from ledgerline.vat.sources import SOURCES
from ledgerline.vat.vat_return import (
    RUBRIEKEN,
    VERIFY,
    Row,
    build_return,
    euros_down,
    euros_up,
    filing_deadline,
)

PERIOD = (date(2026, 1, 1), date(2026, 3, 31))


def _return(*rows):
    return build_return(list(rows), *PERIOD)


def test_every_rubriek_and_rule_has_a_primary_source():
    needed = {r[0] for r in RUBRIEKEN} | {"5b", "rounding", "deadline", "kor"}
    assert needed <= SOURCES.keys()
    urls = [url for _, url in SOURCES.values()]
    assert all(url.startswith("https://") and "belastingdienst.nl" in url for url in urls)


def test_only_rubrieken_without_a_current_source_keep_the_verify_mark():
    assert {"1b", "1c", "2a", "3c"} == VERIFY


@pytest.mark.parametrize(("cents", "down", "up"), [
    (123456, 1234, 1235), (100, 1, 1), (99, 0, 1), (0, 0, 0), (-1050, -11, -10),
])
def test_whole_euro_rounding(cents, down, up):
    assert (euros_down(cents), euros_up(cents)) == (down, up)


def test_form_rounds_vat_owed_down_and_deductible_vat_up():
    result = _return(
        Row("V21", "vat", SystemRole.VAT_PAYABLE_HIGH, 0, 21099),
        Row("V9", "vat", SystemRole.VAT_PAYABLE_LOW, 0, 4550),
        Row("I21", "vat", SystemRole.VAT_RECEIVABLE, 3001, 0),
    )
    assert (result.line("1a").form_vat_euros, result.line("1b").form_vat_euros) == (210, 45)
    assert result.form_owed_euros == 255
    assert result.form_deductible_euros == 31
    assert result.form_balance_euros == 224


@pytest.mark.parametrize(("kind", "end", "deadline"), [
    ("quarter", date(2026, 3, 31), date(2026, 4, 30)),
    ("quarter", date(2026, 6, 30), date(2026, 7, 31)),
    ("quarter", date(2026, 9, 30), date(2026, 10, 31)),
    ("quarter", date(2026, 12, 31), date(2027, 1, 31)),
    ("month", date(2026, 1, 31), date(2026, 2, 28)),
    ("year", date(2026, 12, 31), None),
])
def test_filing_deadline_is_the_end_of_the_following_month(kind, end, deadline):
    assert filing_deadline(kind, end) == deadline


def test_eu_goods_and_services_both_land_in_3b_and_on_the_icp():
    result = _return(Row("VEU", "base", None, 0, 10000), Row("VEUG", "base", None, 0, 25000))
    assert result.line("3b").base_cents == 35000
    assert codes.get("VEU").icp and codes.get("VEUG").icp
    assert codes.get("VEUG").invoice_note == "intracommunautaire levering"
    assert codes.get("VEU").invoice_note == "btw verlegd"


def test_services_to_non_eu_customers_are_not_reported():
    result = _return(Row("VNEU", "base", None, 0, 50000))
    assert all(line.base_cents == 0 for line in result.lines)


def test_export_of_goods_goes_to_3a_and_domestic_reverse_charge_sales_to_1e():
    result = _return(Row("VEX", "base", None, 0, 40000), Row("VRV", "base", None, 0, 15000))
    assert (result.line("3a").base_cents, result.line("1e").base_cents) == (40000, 15000)


def test_private_use_correction_fills_1d_vat_only():
    result = _return(Row("VPRIV", "vat", SystemRole.VAT_PAYABLE_HIGH, 0, 31550))
    line = result.line("1d")
    assert (line.base_cents, line.vat_cents, line.has_base) == (0, 31550, False)
    assert result.form_owed_euros == 315
    assert codes.get("VPRIV") not in codes.for_direction(codes.Direction.SALES)


def test_vat_page_shows_deadline_sources_and_kor_notice(client, session, admin):
    page = client.get("/vat?kind=quarter&year=2026&number=1")
    assert "30-04-2026" in page.text
    assert SOURCES["rounding"][1] in page.text
    assert 'data-testid="kor-banner"' not in page.text
    admin.kor = True
    session.commit()
    page = client.get("/vat?kind=quarter&year=2026&number=1")
    assert 'data-testid="kor-banner"' in page.text
