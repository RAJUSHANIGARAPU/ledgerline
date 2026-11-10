# ruff: noqa: E501
"""Scenario 2: rounding torture. VAT is rounded per invoice per code (documented rule)."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from ledgerline.models import SalesInvoice, SalesInvoiceLine
from tests.oracle import oracle as model
from tests.oracle.conftest import check

Q = (date(2026, 4, 1), date(2026, 6, 30))
D = date(2026, 4, 15)


def post_form(world, customer, on, rows):
    """Create a draft through the HTML form (Dutch number parsing) and issue it."""
    data = {
        "csrf_token": world.client.csrf, "contact_id": world.contacts[customer].id,
        "issue_date": on.isoformat(), "due_date": date(2026, 5, 15).isoformat(),
        "line_description": [f"r{i}" for i in range(len(rows))],
        "line_qty": [r[0] for r in rows], "line_price": [r[1] for r in rows],
        "line_vat_code": [r[2] for r in rows],
        "line_account": [str(world.acct(r[3])) for r in rows],
    }
    r = world.client.post("/sales/new", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:400]
    invoice_id = int(r.headers["location"].rsplit("/", 1)[1])
    r = world.client.post(f"/sales/{invoice_id}/issue", data={"csrf_token": world.client.csrf},
                          follow_redirects=False)
    assert r.status_code == 303
    world.session.expire_all()
    return world.session.get(SalesInvoice, invoice_id)


def test_vat_rounded_per_invoice_per_code_not_per_line(world):
    lines = [(1, 2, "V21", "8000")] * 3 + [(1, 2, "V9", "8010")] * 3
    invoice = world.sale("R1", "Alfa BV", D, lines)
    # 6 cents at 21% = 1.26 -> 1 ; 6 cents at 9% = 0.54 -> 1 (per line each would be 0)
    check("vat per invoice", invoice.total_vat_cents, 2)
    check("incl", invoice.total_incl_cents, 14)
    world.verify_documents()
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)


def test_half_cent_boundaries_round_up_and_credit_note_mirrors(world):
    a = world.sale("H1", "Alfa BV", D, [(1, 50, "V21", "8000")])   # 10.5 -> 11
    b = world.sale("H2", "Alfa BV", D, [(1, 50, "V9", "8010")])    # 4.5 -> 5
    c = world.sale("H3", "Alfa BV", D, [(1, 150, "V9", "8010")])   # 13.5 -> 14
    check("21% of 50", a.total_vat_cents, 11)
    check("9% of 50", b.total_vat_cents, 5)
    check("9% of 150", c.total_vat_cents, 14)
    note = world.credit("HC", "H1", date(2026, 4, 20))
    check("credit vat mirrors", note.total_vat_cents, -11)
    check("credit total", note.total_incl_cents, -61)
    world.verify_documents()
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)


def test_one_third_hour_quantities(world):
    lines = [("0.333", 7500, "V21", "8000"), ("0.667", 7500, "V21", "8000"),
             ("1.5", 4995, "V9", "8010"), ("0.25", 1, "V21", "8000")]
    world.sale("T1", "Beta BV", D, lines)
    check("line 1", world.docs["T1"].lines[0].line_excl_cents, 2498)  # 2497.5 half-up
    check("line 2", world.docs["T1"].lines[1].line_excl_cents, 5003)  # 5002.5
    world.verify_documents()
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)


def test_negative_discount_lines_and_negative_total_invoice(world):
    lines = [(1, 10000, "V21", "8000"), (1, -1000, "V21", "8000"),
             (2, 5000, "V9", "8010"), (1, -555, "V9", "8010")]
    inv = world.sale("N1", "Gamma BV", D, lines)
    check("net", inv.total_excl_cents, 18445)
    check("vat", inv.total_vat_cents, 1890 + 850)  # 9000*21% + 9445*9% (850.05)
    world.sale("N2", "Gamma BV", D, [(1, -2500, "V21", "8000")])  # a pure discount invoice
    world.verify_documents()
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)


def test_form_number_parsing_half_cents_and_dutch_notation(world):
    inv = post_form(world, "Delta VOF", D, [
        ("1", "0.005", "V21", "8000"),        # 0.5 cent -> 1 cent
        ("1,5", "1.234,56", "V21", "8000"),   # Dutch notation: 1,5 x 1234.56
        ("3", "0.004", "V21", "8000"),        # 0.4 cent -> 0 per unit price
    ])
    nets = [line.line_excl_cents for line in inv.lines]
    check("form line nets", nets, [1, 185184, 0])
    world.o.sale("F1", "Delta VOF", D, date(2026, 5, 15),
                 [(1, 1, "V21", "8000"), ("1.5", 123456, "V21", "8000"), (3, 0, "V21", "8000")])
    world.docs["F1"] = inv
    world.verify_documents()
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)


def test_form_values_round_down_owed_and_up_deductible(world):
    world.sale("W1", "Epsilon NV", D, [(1, 20948, "V21", "8000")])        # VAT 4399.08 -> 4399
    world.purchase("W2", "Leverancier NL", D, [("4000", 47620, "I21", 10001)])
    want = world.o.vat_return(*Q)
    check("owed cents", want["owed"], 4399)
    check("form owed", want["form_owed"], 43)
    check("form 5b", want["form_deductible"], 101)
    check("form base 1a", want["form_base"]["1a"], 209)
    world.verify_vat(*Q)


def test_purchase_vat_rules_differ_from_sales_rule(world):
    # Normal purchase VAT is the supplier's stated amount, rounded per line (default); the
    # sales rule (once per code on the sum) would give 1 cent here. Reverse charge uses the sum.
    lines = [("4900", 2, "I21", None)] * 3
    inv = world.purchase("D1", "Leverancier NL", D, lines)
    check("per-line default VAT", inv.total_vat_cents, 0)
    rc = world.purchase("D2", "Onderaannemer NL", D, [("4900", 2, "IRV21", None)] * 3)
    check("reverse charge VAT on the sum", [x.vat_cents for x in rc.lines], [0, 0, 1])
    world.verify_documents()
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)


@pytest.mark.xfail(strict=True, reason="BUG-2: quantity is stored with 3 decimals but the line "
                   "total is computed from the unrounded quantity")
def test_quantity_precision_is_consistent_between_line_total_and_stored_quantity(world):
    world.sale("Q1", "Alfa BV", D, [(Decimal("0.3333"), 7500, "V21", "8000")])
    world.session.expire_all()
    line = world.session.scalars(select(SalesInvoiceLine)).one()
    recomputed = model.line_net(line.quantity, line.unit_price_cents)
    check("stored qty x price = stored line total", line.line_excl_cents, recomputed)


def test_a_dot_in_a_form_amount_is_a_decimal_point(world):
    inv = post_form(world, "Alfa BV", D, [("1", "1.234", "V21", "8000")])
    check("1.234 is read as 1.234 euro, not 1234", inv.lines[0].line_excl_cents, 123)
