# ruff: noqa: E501
"""Scenarios 6 and 7: intra-EU supplies (rubriek 3b + ICP) and private use (rubriek 1d)."""
from datetime import date

import pytest

from ledgerline.invoicing import service as inv
from ledgerline.invoicing.calc import InvoiceError
from tests.oracle.conftest import check

Q1 = (date(2026, 1, 1), date(2026, 3, 31))
Q2 = (date(2026, 4, 1), date(2026, 6, 30))
D = date(2026, 3, 10)


def test_eu_supplies_land_in_3b_and_the_icp_per_customer(world):
    world.contact("EU Klant SA", vat="FR12345678901", country="FR")
    world.sale("E1", "EU Klant GmbH", D, [(1, 100000, "VEU", "8030")])
    world.sale("E2", "EU Klant GmbH", D, [(3, 33333, "VEUG", "8030"), (1, 1, "VEU", "8030")])
    world.sale("E3", "EU Klant SA", D, [("2.5", 4000, "VEUG", "8030")])
    world.sale("E4", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    check("3b", world.o.vat_return(*Q1)["base"]["3b"], 100000 + 99999 + 1 + 10000)
    check("icp", world.o.icp(*Q1), {"EU Klant GmbH": 200000, "EU Klant SA": 10000})
    world.verify_all(*Q1)
    csv = world.client.get("/vat/icp?kind=quarter&year=2026&number=1&export=csv").text
    rows = [line.split(",") for line in csv.lstrip("﻿").strip().splitlines()[1:]]
    check("icp csv", sorted(rows), sorted([["DE123456789", "EU Klant GmbH", "2000.00"],
                                           ["FR12345678901", "EU Klant SA", "100.00"]]))


def test_icp_and_3b_net_a_credit_note_in_the_next_quarter(world):
    world.sale("E1", "EU Klant GmbH", D, [(1, 100000, "VEU", "8030")])
    world.credit("EC", "E1", date(2026, 4, 2))
    for period in (Q1, Q2):
        world.verify_vat(*period)
        world.verify_icp(*period)
    check("Q2 icp negative", world.o.icp(*Q2), {"EU Klant GmbH": -100000})


@pytest.mark.parametrize("vat_number", [None, "", "   "])
@pytest.mark.parametrize("code", ["VEU", "VEUG", "VRV"])
def test_missing_customer_vat_number_refuses_issue(world, vat_number, code):
    world.contact("Zonder BTW", vat=vat_number, country="DE")
    draft = world.sale("Z1", "Zonder BTW", D, [(1, 1000, code, "8030")], issue=False)
    with pytest.raises(InvoiceError) as caught:
        inv.issue_sales_invoice(world.session, world.admin, draft, None)
    world.session.rollback()
    assert any("VAT number" in p for p in caught.value.problems)
    world.session.refresh(draft)
    check("still a draft", (str(draft.status), draft.number), ("draft", None))
    world.verify_ledger(Q1[1])


def test_missing_vat_number_is_fine_for_a_consumer_at_21_percent(world):
    world.contact("Particulier", vat=None)
    world.sale("C1", "Particulier", D, [(1, 1000, "V21", "8000")])
    world.verify_all(*Q1)


@pytest.mark.xfail(strict=True, reason="BUG-5: a VAT-shifted EU supply to a customer in the "
                   "Netherlands is accepted and reported in 3b and the ICP")
def test_eu_supply_to_a_dutch_customer_is_refused(world):
    world.contact("Nederlandse Klant", vat="NL111111111B01", country="NL")
    draft = world.sale("N1", "Nederlandse Klant", D, [(1, 1000, "VEU", "8030")], issue=False)
    with pytest.raises(InvoiceError):
        inv.issue_sales_invoice(world.session, world.admin, draft, None)


@pytest.mark.xfail(strict=True, reason="BUG-7: a fully credited EU supply stays on the ICP "
                   "overview as a zero line")
def test_zero_net_icp_customer_is_not_listed(world):
    world.sale("E1", "EU Klant GmbH", D, [(1, 100000, "VEU", "8030")])
    world.credit("EC", "E1", date(2026, 3, 20))
    from ledgerline.vat.icp import icp_overview
    rows = icp_overview(world.session, world.admin.id, *Q1)
    check("no zero rows", [(r.name, r.amount_cents) for r in rows], [])


def journal_form(world, on, lines):
    return {
        "csrf_token": world.client.csrf, "date": on.isoformat(), "description": "private use",
        "account_id": [str(world.acct(a)) for a, *_ in lines],
        "debit": [x[1] for x in lines], "credit": [x[2] for x in lines],
        "line_description": [""] * len(lines), "vat_code": [x[3] for x in lines],
        "vat_role": [x[4] for x in lines],
    }


def test_private_use_goes_to_1d_without_a_base(world):
    lines = [("0510", "121,00", "", "", "base"), ("8900", "", "100,00", "VPRIV", "base"),
             ("1700", "", "21,00", "VPRIV", "vat")]
    r = world.client.post("/ledger/new", data=journal_form(world, date(2026, 3, 31), lines),
                          follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    world.o.manual(date(2026, 3, 31), [("0510", 12100), ("8900", -10000), ("1700", -2100)])
    from tests.oracle.oracle import Fact
    world.o.facts.append(Fact(date(2026, 3, 31), "1d", vat=2100))
    world.verify_ledger(Q1[1])
    world.verify_vat(*Q1)
    check("1d vat", world.o.vat_return(*Q1)["vat"]["1d"], 2100)
    check("1d form", world.o.vat_return(*Q1)["form_vat"]["1d"], 21)
    page = world.client.get("/vat?kind=quarter&year=2026&number=1").text
    assert "Private use" in page


def test_private_use_on_the_low_vat_account_also_counts_in_1d(world):
    lines = [("0510", "10,90", "", "", "base"), ("8900", "", "10,00", "VPRIV", "base"),
             ("1710", "", "0,90", "VPRIV", "vat")]
    r = world.client.post("/ledger/new", data=journal_form(world, date(2026, 3, 31), lines),
                          follow_redirects=False)
    assert r.status_code == 303
    from tests.oracle.oracle import Fact
    world.o.manual(date(2026, 3, 31), [("0510", 1090), ("8900", -1000), ("1710", -90)])
    world.o.facts.append(Fact(date(2026, 3, 31), "1d", vat=90))
    world.verify_vat(*Q1)
