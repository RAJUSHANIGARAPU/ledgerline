# ruff: noqa: E501
"""Scenario 5: an administration under the small business scheme (KOR)."""
from datetime import date

import pytest

from tests.oracle.conftest import check

Q = (date(2026, 1, 1), date(2026, 3, 31))
D = date(2026, 2, 10)


@pytest.fixture
def kor_world(world):
    world.admin.kor = True
    world.session.commit()
    return world


def test_kor_sales_carry_no_vat_whatever_code_is_chosen(kor_world):
    w = kor_world
    lines = [(2, 12345, "V21", "8000"), ("1.5", 4995, "V9", "8010"), (1, 100, "V0", "8020")]
    expected = [(q, p, "VKOR", a) for q, p, _, a in lines]
    invoice = w.sale("K1", "Alfa BV", D, lines, expect_lines=expected)
    check("every line VKOR", {line.vat_code for line in invoice.lines}, {"VKOR"})
    check("no VAT", invoice.total_vat_cents, 0)
    check("incl = excl", invoice.total_incl_cents, 2 * 12345 + 7493 + 100)  # 1.5 x 4995 = 7492.5
    w.sale("K2", "EU Klant GmbH", D, [(1, 50000, "VEU", "8030")],
           expect_lines=[(1, 50000, "VKOR", "8030")])
    w.verify_documents()
    w.verify_ledger(Q[1])
    w.verify_vat(*Q)
    w.verify_icp(*Q)
    check("return is empty", w.o.vat_return(*Q)["owed"], 0)


def test_kor_credit_note(kor_world):
    w = kor_world
    w.sale("K1", "Alfa BV", D, [(1, 10000, "V21", "8000")], expect_lines=[(1, 10000, "VKOR", "8000")])
    w.credit("KC", "K1", date(2026, 2, 20))
    w.verify_documents()
    w.verify_ledger(Q[1])
    w.verify_vat(*Q)


@pytest.mark.xfail(strict=True, reason="BUG-4: under the KOR, input VAT is booked as deductible "
                   "(1400 / rubriek 5b) instead of being part of the cost")
def test_kor_purchase_vat_is_cost_not_deductible(kor_world):
    w = kor_world
    w.purchase("KP", "Leverancier NL", D, [("4000", 10000, "I21", None)])
    expected = w.o
    expected.docs.pop("KP")
    expected.postings.clear()
    expected.facts.clear()
    expected.purchase("KP", "Leverancier NL", D, date(2026, 3, 12), [("4000", 12100, "I0", None)])
    check("5b is zero", w.o.vat_return(*Q)["deductible"], 0)
    w.session.expire_all()
    w.verify_ledger(Q[1])
    w.verify_vat(*Q)
