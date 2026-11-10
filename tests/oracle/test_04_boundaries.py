# ruff: noqa: E501
"""Scenario 4: period boundaries, credit notes across quarters, the period lock on every path."""
from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline.banking import service as bank
from ledgerline.invoicing import service as inv
from ledgerline.invoicing.calc import InvoiceError
from ledgerline.ledger.service import LedgerError
from ledgerline.models import InvoiceStatus, JournalEntry, SalesInvoice
from tests.oracle.conftest import check

Q1 = (date(2026, 1, 1), date(2026, 3, 31))
Q2 = (date(2026, 4, 1), date(2026, 6, 30))


def test_31_march_and_1_april_land_in_different_quarters(world):
    world.sale("M1", "Alfa BV", date(2026, 3, 31), [(1, 10000, "V21", "8000")])
    world.sale("A1", "Alfa BV", date(2026, 4, 1), [(1, 20000, "V21", "8000")])
    world.purchase("PM", "Leverancier NL", date(2026, 3, 31), [("4000", 5000, "I21", None)])
    world.purchase("PA", "Leverancier NL", date(2026, 4, 1), [("4000", 7000, "I21", None)])
    check("Q1 1a base", world.o.vat_return(*Q1)["base"]["1a"], 10000)
    check("Q2 1a base", world.o.vat_return(*Q2)["base"]["1a"], 20000)
    for period in (Q1, Q2):
        world.verify_vat(*period)
        world.verify_ledger(period[1])
    world.verify_aging(Q2[1])
    world.verify_documents()
    for start, end in ((date(2026, 3, 31), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 4, 1)),
                       (date(2026, 3, 31), date(2026, 4, 1))):
        world.verify_vat(start, end)


def test_credit_note_in_next_quarter_reduces_next_quarters_return(world):
    world.sale("A1", "Alfa BV", date(2026, 3, 20), [(1, 150, "V21", "8000"), (1, 9999, "V9", "8010")])
    world.credit("C1", "A1", date(2026, 4, 5))
    for period in (Q1, Q2):
        world.verify_vat(*period)
        world.verify_ledger(period[1])
    q2 = world.o.vat_return(*Q2)
    check("Q2 1a base is negative", q2["base"]["1a"], -150)
    check("Q2 1a vat is negative", q2["vat"]["1a"], -32)      # 31.5 -> 32 mirrored
    check("Q2 form 1a vat", q2["form_vat"]["1a"], -1)         # floor(-0.32 euro)
    check("Q2 owed", q2["owed"], -32 - 900)


def test_year_boundary_numbering_and_balance_sheet(world):
    a = world.sale("Y1", "Alfa BV", date(2026, 12, 31), [(1, 10000, "V21", "8000")])
    b = world.sale("Y2", "Alfa BV", date(2027, 1, 1), [(1, 5000, "V21", "8000")])
    c = world.sale("Y3", "Alfa BV", date(2027, 1, 2), [(1, 5000, "V9", "8010")])
    check("numbers", [a.number, b.number, c.number], ["2026-0001", "2027-0001", "2027-0002"])
    world.purchase("YP", "Leverancier NL", date(2026, 12, 15), [("4000", 3000, "I21", None)])
    for end in (date(2026, 12, 31), date(2027, 1, 31)):
        world.verify_ledger(end)
    world.verify_vat(date(2026, 10, 1), date(2026, 12, 31))
    world.verify_vat(date(2027, 1, 1), date(2027, 3, 31))


# ------------------------------------------------------------------ period lock

def lock(world, until: date):
    r = world.client.post("/ledger/lock", data={"csrf_token": world.client.csrf,
                                                "locked_until": until.isoformat(), "confirm": "1"},
                          follow_redirects=False)
    assert r.status_code == 303
    world.session.expire_all()
    world.session.refresh(world.admin)
    check("lock stored", world.admin.locked_until, until)


def entries(world):
    return world.session.scalar(select(func.count()).select_from(JournalEntry))


def test_period_lock_blocks_every_posting_path_and_leaves_no_trace(world):
    d = date(2026, 3, 15)
    world.sale("A1", "Alfa BV", d, [(1, 10000, "V21", "8000")])
    world.sale("A2", "Alfa BV", d, [(1, 5000, "V21", "8000")])
    world.purchase("P1", "Leverancier NL", d, [("4000", 4000, "I21", None)])
    sale_tx = world.tx(date(2026, 3, 25), 12100)
    pay_tx = world.tx(date(2026, 3, 26), -4840)
    cost_tx = world.tx(date(2026, 3, 27), -500)
    matched = world.receive(date(2026, 3, 28), [("A2", 6050)])
    draft_sale = world.sale("D1", "Beta BV", d, [(1, 7777, "V21", "8000")], issue=False)
    purchase_draft = inv.create_purchase_invoice(
        world.session, world.admin, world.contacts["Leverancier NL"],
        [inv.PurchaseLineInput("x", world.acct("4000"), 1000, "I21")],
        supplier_reference="LOCKED", invoice_date=d)
    world.session.commit()
    lock(world, date(2026, 3, 31))
    count = entries(world)
    balances = world.raw_balances()

    with pytest.raises(InvoiceError):
        inv.issue_sales_invoice(world.session, world.admin, draft_sale, None)
    world.session.rollback()
    world.session.refresh(draft_sale)
    check("draft stays draft without number", (str(draft_sale.status), draft_sale.number),
          (str(InvoiceStatus.DRAFT), None))

    with pytest.raises(InvoiceError):
        inv.book_purchase_invoice(world.session, world.admin, purchase_draft, None)
    world.session.rollback()

    with pytest.raises(LedgerError):
        bank.match(world.session, sale_tx, [bank.Allocation("sales", world.docs["A1"].id, 12100)], None)
    world.session.rollback()
    with pytest.raises(LedgerError):
        bank.match(world.session, pay_tx, [bank.Allocation("purchase", world.docs["P1"].id, 4840)], None)
    world.session.rollback()
    with pytest.raises(LedgerError):
        bank.book_to_account(world.session, cost_tx, world.acct("4800"), "I0", None)
    world.session.rollback()
    with pytest.raises(LedgerError):
        bank.undo(world.session, matched, None, date(2026, 3, 30))
    world.session.rollback()
    with pytest.raises(InvoiceError):
        inv.create_credit_note(world.session, world.admin, world.docs["A1"], None, date(2026, 3, 31))
    world.session.rollback()

    manual = {"csrf_token": world.client.csrf, "date": "2026-03-10", "description": "late",
              "account_id": [str(world.acct("1000")), str(world.acct("0520"))],
              "debit": ["10,00", ""], "credit": ["", "10,00"], "line_description": ["", ""],
              "vat_code": ["", ""], "vat_role": ["base", "base"]}
    check("manual entry refused", world.client.post("/ledger/new", data=manual).status_code, 422)
    manual["date"] = "2026-03-31"  # exactly on the lock date is still closed
    check("manual entry on lock date refused",
          world.client.post("/ledger/new", data=manual).status_code, 422)

    r = world.client.post("/vat/settle", data={"csrf_token": world.client.csrf, "kind": "quarter",
                                               "year": "2026", "number": "1"})
    check("settlement refused", r.status_code, 200)
    last = world.session.scalars(select(JournalEntry).order_by(JournalEntry.id.desc())).first()
    reversal_of = world.client.post(f"/ledger/{last.id}/reverse",
                                    data={"csrf_token": world.client.csrf, "reason": "x",
                                          "date": "2026-03-20"}, follow_redirects=False)
    check("reversal redirect", reversal_of.status_code, 303)

    world.session.expire_all()
    check("no entries added by any path", entries(world), count)
    check("balances unchanged", world.raw_balances(), balances)
    for key in ("A1", "A2", "P1"):
        world.session.refresh(world.docs[key])
    check("A1 untouched", (str(world.docs["A1"].status), world.docs["A1"].paid_cents), ("open", 0))
    check("A2 still part paid", world.docs["A2"].paid_cents, 6050)
    check("tx still unmatched", str(sale_tx.status), "unmatched")
    check("matched tx still matched", str(matched.status), "matched")
    notes = world.session.scalar(select(func.count()).select_from(SalesInvoice)
                                 .where(SalesInvoice.credit_note_of_id.is_not(None)))
    check("no stray credit note drafts", notes, 0)
    world.verify_documents()
    world.verify_ledger(date(2026, 3, 31))
    world.verify_vat(*Q1)
    check("numbering gap free after refused issue",
          world.session.scalar(select(func.max(SalesInvoice.number))), "2026-0002")


def test_day_after_the_lock_posts_normally_and_numbering_has_no_gap(world):
    world.sale("A1", "Alfa BV", date(2026, 3, 15), [(1, 10000, "V21", "8000")])
    lock(world, date(2026, 3, 31))
    draft = world.sale("D1", "Beta BV", date(2026, 3, 31), [(1, 100, "V21", "8000")], issue=False)
    with pytest.raises(InvoiceError):
        inv.issue_sales_invoice(world.session, world.admin, draft, None)
    world.session.rollback()
    world.session.expire_all()
    draft = world.session.get(SalesInvoice, draft.id)
    draft.issue_date = date(2026, 4, 1)
    inv.issue_sales_invoice(world.session, world.admin, draft, None)
    world.session.commit()
    check("next number is 0002", draft.number, "2026-0002")
    world.o.sale("D1", "Beta BV", date(2026, 4, 1), date(2026, 5, 1), [(1, 100, "V21", "8000")])
    world.docs["D1"] = draft
    world.verify_documents()
    world.verify_ledger(date(2026, 4, 30))
    world.verify_vat(*Q2)


def test_settlement_clears_vat_accounts_and_equals_the_return_balance(world):
    world.sale("A1", "Alfa BV", date(2026, 2, 3), [(1, 33333, "V21", "8000"), (3, 4995, "V9", "8010")])
    world.purchase("P1", "Leverancier NL", date(2026, 2, 4), [("4000", 150000, "I21", None)])
    world.purchase("P2", "Leverancier EU", date(2026, 2, 5), [("4300", 10000, "IEU21", None)])
    r = world.client.post("/vat/settle", data={"csrf_token": world.client.csrf, "kind": "quarter",
                                               "year": "2026", "number": "1"},
                          follow_redirects=False)
    assert r.status_code == 303
    world.o.settle_vat(*Q1)
    check("settlement account", world.raw_balances(Q1[1])["1790"],
          world.o.vat_return(*Q1)["balance"] * -1)
    for account in ("1400", "1700", "1710", "1720"):
        check(f"{account} cleared", world.raw_balances(Q1[1]).get(account, 0), 0)
    world.verify_ledger(Q1[1])
    world.verify_vat(*Q1)
    again = world.client.post("/vat/settle", data={"csrf_token": world.client.csrf, "kind": "quarter",
                                                   "year": "2026", "number": "1"})
    assert again.status_code == 200
    world.session.expire_all()
    world.verify_ledger(Q1[1])
