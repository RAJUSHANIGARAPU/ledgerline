# ruff: noqa: E501
"""Scenario 8: reversals net to zero everywhere. Plus aging bucket edges and credit-note pairs."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from ledgerline.banking import service as bank
from ledgerline.invoicing.calc import InvoiceError
from ledgerline.models import JournalEntry
from tests.oracle.conftest import check

Q = (date(2026, 1, 1), date(2026, 3, 31))
D = date(2026, 2, 10)


def test_credit_note_in_the_same_quarter_nets_every_figure_to_zero(world):
    world.sale("M1", "Alfa BV", D, [("1.5", 12345, "V21", "8000"), (2, 4995, "V9", "8010")])
    world.sale("M2", "EU Klant GmbH", D, [(1, 50000, "VEU", "8030")])
    world.credit("C1", "M1", date(2026, 2, 12))
    world.credit("C2", "M2", date(2026, 2, 12))
    o = world.o.vat_return(*Q)
    check("all rubrieken zero", (o["owed"], o["balance"], sum(o["base"].values())), (0, 0, 0))
    check("ledger nets to zero except debtors/revenue pairs",
          world.raw_balances(), {})
    world.verify_documents()
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)
    world.verify_icp(*Q)
    check("trial balance is empty", world.o.trial(Q[1]), {})


def test_reversal_of_a_manual_settlement_and_resettlement(world):
    world.sale("A1", "Alfa BV", D, [(1, 33333, "V21", "8000")])
    world.purchase("P1", "Leverancier EU", D, [("4300", 10000, "IEU21", None)])
    before = world.raw_balances()
    r = world.client.post("/vat/settle", data={"csrf_token": world.client.csrf, "kind": "quarter",
                                               "year": "2026", "number": "1"})
    assert r.status_code == 200
    world.session.expire_all()
    settlement = world.session.scalars(
        select(JournalEntry).where(JournalEntry.description.like("VAT settlement%"))).one()
    rev = world.client.post(f"/ledger/{settlement.id}/reverse", data={
        "csrf_token": world.client.csrf, "reason": "wrong period", "date": "2026-03-31"},
        follow_redirects=False)
    assert rev.status_code == 303
    check("back to the pre-settlement ledger", world.raw_balances(), before)
    world.verify_ledger(Q[1])
    world.verify_vat(*Q)
    again = world.client.post("/vat/settle", data={"csrf_token": world.client.csrf, "kind": "quarter",
                                                   "year": "2026", "number": "1"},
                              follow_redirects=False)
    check("settling again after the reversal is allowed", again.status_code, 303)
    world.session.expire_all()
    world.o.settle_vat(*Q)
    world.verify_ledger(Q[1])


def test_double_reversal_is_refused(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    entry = world.session.scalars(select(JournalEntry)).first()
    from ledgerline.ledger.service import LedgerError, reverse_entry
    reverse_entry(world.session, entry, date(2026, 2, 11), "x")
    world.session.commit()
    with pytest.raises(LedgerError):
        reverse_entry(world.session, entry, date(2026, 2, 12), "again")
    world.session.rollback()


@pytest.mark.xfail(strict=True, reason="BUG-6: reversing an invoice's journal entry from the "
                   "ledger leaves the invoice open and in the aging report")
def test_reversing_an_invoice_entry_keeps_invoice_and_ledger_in_step(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    entry = world.session.scalars(select(JournalEntry)).first()
    world.client.post(f"/ledger/{entry.id}/reverse", data={
        "csrf_token": world.client.csrf, "reason": "oops", "date": "2026-02-11"})
    world.session.expire_all()
    from ledgerline.reports.aging import aging
    listed = sum(row.total_cents for row in aging(world.session, world.admin.id, "receivables", Q[1]))
    check("open receivables listed = debtors in the ledger", listed,
          world.raw_balances().get("1300", 0))


@pytest.mark.xfail(strict=True, reason="BUG-6: reversing a bank match from the ledger leaves the "
                   "transaction matched and the invoice paid")
def test_reversing_a_bank_entry_keeps_invoice_and_transaction_in_step(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    tx = world.receive(date(2026, 2, 20), [("A1", 12100)])
    world.session.refresh(tx)
    entry = world.session.get(JournalEntry, tx.journal_entry_id)
    world.client.post(f"/ledger/{entry.id}/reverse", data={
        "csrf_token": world.client.csrf, "reason": "oops", "date": "2026-02-21"})
    world.session.expire_all()
    world.session.refresh(world.docs["A1"])
    check("invoice reopened (ledger says unpaid)", (world.raw_balances().get("1100", 0),
                                                    world.docs["A1"].paid_cents), (0, 0))


# ----------------------------------------------------------------- aging buckets

def test_aging_bucket_edges(world):
    as_of = date(2026, 6, 30)
    late = [0, 1, 30, 31, 60, 61, 90, 91]
    for n, days in enumerate(late):
        world.contact(f"Late{days}")
        world.sale(f"L{days}", f"Late{days}", date(2026, 1, 5), [(1, 1000 + n, "V0", "8020")],
                   due=as_of - timedelta(days=days))
    want = {f"Late{d}": [0] * 5 for d in late}
    for n, (days, index) in enumerate(zip(late, [0, 1, 1, 2, 2, 3, 3, 4], strict=True)):
        want[f"Late{days}"][index] = 1000 + n
    got = {r.name: r.buckets for r in __import__("ledgerline.reports.aging", fromlist=["x"]).aging(
        world.session, world.admin.id, "receivables", as_of)}
    check("hard-coded bucket edges", got, want)
    world.verify_aging(as_of)


def test_payables_aging_and_partial_payment(world):
    world.purchase("P1", "Leverancier NL", D, [("4000", 10000, "I21", None)], due=date(2026, 3, 1))
    world.purchase("P2", "Leverancier NL", D, [("4000", 5000, "I9", None)], due=date(2026, 4, 15))
    world.pay(date(2026, 2, 20), [("P1", 2000)])
    world.verify_aging(date(2026, 5, 1))
    world.verify_dashboard(date(2026, 5, 1))


@pytest.mark.xfail(strict=True, reason="BUG-1: aging counts the credit note without the "
                   "cancelled original it offsets")
def test_credit_note_pairs_leave_only_what_was_paid(world):
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    world.sale("B1", "Beta BV", D, [(1, 10000, "V21", "8000")])
    world.receive(date(2026, 2, 15), [("B1", 5000)])
    world.credit("AC", "A1", date(2026, 2, 20))
    world.credit("BC", "B1", date(2026, 2, 20))
    world.verify_subledger()
    world.verify_aging(Q[1])


# ----------------------------------------------------------------- input plausibility

@pytest.mark.xfail(strict=True, reason="BUG-9: stated purchase VAT is not checked against the "
                   "amount, so VAT far above the net is deducted in 5b")
def test_implausible_stated_purchase_vat_is_refused(world):
    with pytest.raises(InvoiceError):
        world.purchase("X1", "Leverancier NL", D, [("4000", 10000, "I21", 999999)])


@pytest.mark.xfail(strict=True, reason="BUG-1: the dashboard receivable total counts the credit "
                   "note without the original it offsets")
def test_dashboard_receivable_of_a_credited_unpaid_invoice(world):
    from ledgerline.reports.dashboard import build_dashboard
    world.sale("A1", "Alfa BV", D, [(1, 10000, "V21", "8000")])
    world.credit("AC", "A1", date(2026, 2, 12))
    check("ledger debtors", world.raw_balances().get("1300", 0), 0)
    check("dashboard receivable", build_dashboard(world.session, world.admin, Q[1]).receivable_cents, 0)


def test_pure_discount_invoice_has_no_way_to_be_settled(world):
    note = world.sale("N2", "Gamma BV", D, [(1, -2500, "V21", "8000")])
    t = world.tx(date(2026, 2, 20), -3025)
    with pytest.raises(bank.BankingError):
        bank.match(world.session, t, [bank.Allocation("sales", note.id, 3025)], None)
    world.session.rollback()
    world.session.refresh(note)
    check("stays open with a negative total", (str(note.status), note.total_incl_cents),
          ("open", -3025))
