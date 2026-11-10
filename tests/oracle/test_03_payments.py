# ruff: noqa: E501
"""Scenario 3: partial payments, overpayment, one transaction for several invoices, undo."""
from datetime import date

import pytest

from ledgerline.banking import service as bank
from ledgerline.banking.service import BankingError
from ledgerline.models import TxStatus
from tests.oracle.conftest import check

Q = (date(2026, 4, 1), date(2026, 6, 30))
D = date(2026, 4, 10)


def everything(world, end=Q[1]):
    world.verify_documents()
    world.verify_ledger(end)
    world.verify_subledger(end)
    world.verify_vat(Q[0], end)
    world.verify_aging(end)
    world.verify_dashboard(end)


def test_partial_payments_then_settlement(world):
    inv = world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])  # 12100
    world.receive(date(2026, 4, 20), [("A1", 5000)])
    check("open after partial", (str(inv.status), inv.paid_cents), ("open", 5000))
    everything(world)
    world.receive(date(2026, 4, 25), [("A1", 7100)])
    world.session.refresh(inv)
    check("paid after remainder", (str(inv.status), inv.paid_cents), ("paid", 12100))
    everything(world)


def test_overpayment_is_refused_and_changes_nothing(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    t = world.tx(date(2026, 4, 20), 13000)
    before = world.raw_balances()
    with pytest.raises(BankingError):  # allocation above the remaining amount
        bank.match(world.session, t, [bank.Allocation("sales", world.docs["A1"].id, 13000)], None)
    world.session.rollback()
    with pytest.raises(BankingError):  # allocations below the transaction amount
        bank.match(world.session, t, [bank.Allocation("sales", world.docs["A1"].id, 12100)], None)
    world.session.rollback()
    check("ledger untouched", world.raw_balances(), before)
    world.session.refresh(t)
    check("tx still unmatched", str(t.status), str(TxStatus.UNMATCHED))
    everything(world)


def test_paid_invoice_cannot_be_paid_twice(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    world.receive(date(2026, 4, 20), [("A1", 12100)])
    t = world.tx(date(2026, 4, 21), 12100)
    with pytest.raises(BankingError):
        bank.match(world.session, t, [bank.Allocation("sales", world.docs["A1"].id, 12100)], None)
    world.session.rollback()
    everything(world)


def test_one_transaction_pays_invoices_of_two_customers(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    world.sale("A2", "Alfa BV", D, [(2, 4995, "V9", "8010")])
    world.sale("B1", "Beta BV", D, [(1, 33333, "V21", "8000")])
    world.receive(date(2026, 4, 30), [("A1", 12100), ("A2", 5000), ("B1", 40333)])
    everything(world)
    world.verify_subledger()
    entry_lines = world.session.execute(__import__("sqlalchemy").text(
        "SELECT count(*) FROM journal_lines l JOIN accounts a ON a.id = l.account_id "
        "WHERE a.code = '1300' AND l.credit_cents > 0 AND l.entry_id = "
        "(SELECT max(id) FROM journal_entries)")).scalar_one()
    check("one debtor line per customer in the receipt", entry_lines, 2)


def test_one_transaction_pays_two_supplier_invoices(world):
    world.purchase("P1", "Leverancier NL", D, [("4000", 10000, "I21", None)])
    world.purchase("P2", "Leverancier NL", D, [("4500", 5000, "I9", None)])
    world.pay(date(2026, 4, 30), [("P1", 12100), ("P2", 5450)])
    everything(world)


def test_undo_restores_every_figure_and_allows_rematch(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    world.sale("B1", "Beta BV", D, [(1, 5000, "V9", "8010")])
    before = world.raw_balances(date(2026, 6, 30))
    t = world.receive(date(2026, 4, 30), [("A1", 12100), ("B1", 5450)])
    undo_day = date(2026, 5, 2)
    bank.undo(world.session, t, None, undo_day)
    world.session.commit()
    world.o.unpay(undo_day, [("A1", 12100), ("B1", 5450)], sales=True, total_sign=1)
    # the receipt and its reversal net to zero on every account
    check("net effect of match + undo", world.raw_balances(date(2026, 6, 30)), before)
    check("tx unmatched again", str(world.session.get(type(t), t.id).status), "unmatched")
    everything(world)
    bank.match(world.session, t, [bank.Allocation("sales", world.docs["A1"].id, 12100),
                                  bank.Allocation("sales", world.docs["B1"].id, 5450)], None)
    world.session.commit()
    world.o.receipt(date(2026, 4, 30), [("A1", 12100), ("B1", 5450)])
    everything(world)


def test_undo_of_a_supplier_payment(world):
    world.purchase("P1", "Leverancier NL", D, [("4000", 10000, "I21", None)])
    t = world.pay(date(2026, 4, 30), [("P1", 12100)])
    bank.undo(world.session, t, None, date(2026, 5, 2))
    world.session.commit()
    world.o.unpay(date(2026, 5, 2), [("P1", 12100)], sales=False, total_sign=-1)
    everything(world)


def test_bank_costs_go_to_4800_without_vat(world):
    world.book_bank(date(2026, 4, 5), -1250, "4800", "I0")
    world.book_bank(date(2026, 4, 6), -2500, "4800", "I0")
    world.book_bank(date(2026, 4, 7), -990, "4800")
    check("4800 balance", world.raw_balances()["4800"], 4740)
    everything(world)


def test_bank_booking_splits_vat_out_of_the_gross(world):
    world.book_bank(date(2026, 4, 5), -1000, "4200", "I21")      # vat 173.55 -> 174
    world.book_bank(date(2026, 4, 6), -12100, "4300", "I21")     # vat 2100
    world.book_bank(date(2026, 4, 7), 10900, "8010", "V9")       # vat 900 owed in 1b
    world.book_bank(date(2026, 4, 8), 12100, "8000", "V21")
    world.book_bank(date(2026, 4, 9), 5000, "8040", "VEX")       # export, 3a, no VAT
    check("vat in 1000 at 21%", world.o.vat_return(*Q)["deductible"], 174 + 2100)
    everything(world)


def test_credit_note_for_a_paid_invoice_leaves_a_refund_due(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    world.receive(date(2026, 4, 20), [("A1", 12100)])
    world.credit("C1", "A1", date(2026, 4, 25))
    everything(world)


@pytest.mark.xfail(strict=True, reason="BUG-3: a credit note can never be settled; the refund "
                   "cannot be matched and the note stays open forever")
def test_refund_of_a_credit_note_settles_it(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    world.receive(date(2026, 4, 20), [("A1", 12100)])
    note = world.credit("C1", "A1", date(2026, 4, 25))
    refund = world.tx(date(2026, 4, 28), -12100)
    bank.match(world.session, refund, [bank.Allocation("sales", note.id, 12100)], None)
    world.session.commit()
    world.session.refresh(note)
    check("credit note settled", str(note.status), "paid")
