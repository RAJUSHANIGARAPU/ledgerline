# ruff: noqa: E501
"""Scenario 1: a realistic sole-trader quarter, every report checked against the oracle."""
import csv
import io
import random
from datetime import date

import pytest

from tests.oracle.conftest import check, eur

Q1 = (date(2026, 1, 1), date(2026, 3, 31))
CUSTOMERS = ["Alfa BV", "Beta BV", "Gamma BV", "Delta VOF", "Epsilon NV"]
QUANTITIES = ["1", "2", "1.5", "0.25", "3.75", "7.5", "12", "0.333"]
PRICES = [12345, 9999, 8750, 15000, 4995, 31250, 777, 1]


def build_quarter(w, credit=True):
    rng = random.Random(20260331)
    for n in range(1, 26):
        on = date(2026, 1 + (n - 1) // 9, 1 + (n * 3) % 28)
        shape = n % 5
        lines = [(rng.choice(QUANTITIES), rng.choice(PRICES), "V21", "8000")]
        if shape in (1, 3):
            lines.append((rng.choice(QUANTITIES), rng.choice(PRICES), "V9", "8010"))
        if shape == 2:
            lines += [(rng.choice(QUANTITIES), rng.choice(PRICES), "V21", "8000") for _ in range(3)]
        if shape == 4:
            lines = [(rng.choice(QUANTITIES), rng.choice(PRICES), "V9", "8010"),
                     (rng.choice(QUANTITIES), rng.choice(PRICES), "V0", "8020")]
        w.sale(f"S{n}", CUSTOMERS[n % 5], on, lines)
    purchases = [
        ("Leverancier NL", [("4000", 125000, "I21", None)]),
        ("Leverancier NL", [("4200", 4999, "I21", None), ("4300", 1234, "I21", None)]),
        ("Leverancier NL", [("4500", 9950, "I9", None)]),
        ("Leverancier NL", [("4900", 5000, "I0", None)]),
        ("Leverancier EU", [("4300", 23456, "IEU21", None)]),
        ("Leverancier EU", [("4300", 1111, "IEU21", None), ("4600", 2222, "IEU21", None)]),
        ("Leverancier US", [("4300", 7777, "INEU21", None)]),
        ("Onderaannemer NL", [("7000", 80000, "IRV21", None)]),
        ("Onderaannemer NL", [("7000", 333, "IRV21", None), ("4900", 333, "IRV21", None)]),
        ("Leverancier NL", [("4400", 3999, "I21", 840)]),
        ("Leverancier NL", [("4100", 2500, "I9", None), ("4100", 2501, "I9", None)]),
        ("Leverancier NL", [("4000", 99999, "I21", None)]),
        ("Leverancier NL", [("4700", 45000, "I21", None)]),
        ("Leverancier EU", [("4300", 999, "IEU9", None)]),
        ("Leverancier NL", [("4200", 1, "I21", None)]),
    ]
    for n, (sup, lines) in enumerate(purchases, 1):
        w.purchase(f"P{n}", sup, date(2026, 1 + (n - 1) // 5, 2 + n), lines)
    if credit:
        w.credit("C1", "S6", date(2026, 3, 20))


def test_quarter_ledger_vat_icp_match_oracle(world):
    build_quarter(world)
    world.verify_documents()
    world.verify_ledger(Q1[1])
    world.verify_vat(*Q1)
    world.verify_icp(*Q1)


def test_quarter_aging_and_dashboard_without_credit_note(world):
    build_quarter(world, credit=False)
    world.verify_aging(Q1[1])
    world.verify_dashboard(Q1[1])


@pytest.mark.xfail(strict=True, reason="BUG-1: aging and dashboard receivables go negative "
                   "for a credited unpaid invoice (see FINDINGS.md)")
def test_quarter_aging_and_dashboard_with_credit_note(world):
    build_quarter(world)
    world.verify_aging(Q1[1])
    world.verify_dashboard(Q1[1])


def test_quarter_with_payments_and_bank_costs(world):
    build_quarter(world, credit=False)
    world.receive(date(2026, 2, 10), [("S1", world.docs["S1"].total_incl_cents)])
    world.receive(date(2026, 2, 20), [("S2", 1000)])
    world.pay(date(2026, 2, 25), [("P1", world.docs["P1"].total_incl_cents)])
    world.book_bank(date(2026, 3, 5), -1250, "4800", "I0")
    world.book_bank(date(2026, 3, 6), -2500, "4800", "I0")
    world.verify_all(*Q1)
    world.verify_aging(date(2026, 5, 15))


def test_quarter_http_views_match_oracle(world):
    build_quarter(world)
    world.book_bank(date(2026, 3, 5), -1250, "4800", "I0")
    c = world.client
    want = world.o.trial(Q1[1])
    body = c.get("/reports/trial?end=2026-03-31&export=csv").text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(body)))[1:]
    got = {r[0]: (round(float(r[2]) * 100), round(float(r[3]) * 100)) for r in rows if r[0]}
    check("http trial csv", {a: v for a, v in got.items() if v != (0, 0)}, want)
    pnl = world.o.pnl(*Q1)
    body = c.get("/reports/pnl?start=2026-01-01&end=2026-03-31&export=csv").text
    result = [r for r in csv.reader(io.StringIO(body.lstrip("﻿"))) if r[0] == "Result"][0]
    check("http pnl result", round(float(result[-1]) * 100), pnl["result"])

    page = c.get("/vat?kind=quarter&year=2026&number=1").text
    want_vat = world.o.vat_return(*Q1)
    for rubriek in model_rubrieken():
        row = _vat_row(page, rubriek)
        check(f"http {rubriek} base", eur(row[0]), want_vat["base"][rubriek])
        check(f"http {rubriek} vat", eur(row[1]), want_vat["vat"][rubriek])
        check(f"http {rubriek} form base", int(row[2].replace("-", "-") or 0),
              want_vat["form_base"][rubriek])
        check(f"http {rubriek} form vat", int(row[3] or 0), want_vat["form_vat"][rubriek])


def model_rubrieken():
    from tests.oracle.oracle import RUBRIEKEN
    return [r for r in RUBRIEKEN if r not in ("1d",)]


def _vat_row(page: str, rubriek: str) -> list[str]:
    import re
    for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        if re.search(rf">{rubriek}</a>", tr):
            cells = re.findall(r'<td class="num">(.*?)</td>', tr, re.S)
            return [c.strip() for c in cells]
    raise AssertionError(rubriek)


@pytest.mark.xfail(strict=True, reason="BUG-8: the aging report ignores its as-of date for "
                   "invoices issued and payments made after it")
def test_aging_as_of_an_earlier_date_only_counts_what_existed_then(world):
    build_quarter(world, credit=False)
    world.receive(date(2026, 3, 25), [("S1", world.docs["S1"].total_incl_cents)])
    world.verify_aging(date(2026, 2, 15))
