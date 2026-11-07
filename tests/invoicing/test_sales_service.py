from datetime import date
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from ledgerline.invoicing.calc import InvoiceError
from ledgerline.invoicing.service import (
    create_credit_note,
    create_sales_invoice,
    issue_sales_invoice,
    update_sales_invoice,
)
from ledgerline.ledger.service import account_balance_cents
from ledgerline.models import AuditEvent, InvoiceStatus, JournalEntry
from tests.helpers import account
from tests.invoicing.conftest import ISSUE, sline


def entry_lines(session, invoice):
    entry = session.get(JournalEntry, invoice.journal_entry_id)
    return [
        (ln.account_id, ln.debit_cents, ln.credit_cents, ln.vat_code, ln.vat_role, ln.contact_id)
        for ln in entry.lines
    ]


def test_issue_posts_exact_balanced_entry(session, admin, owner, customer, make_invoice):
    lines = [
        sline(session, admin, "Consulting", "1", 10000, "V21", "8000"),
        sline(session, admin, "Training", "3", 1000, "V9", "8010"),
    ]
    invoice = make_invoice(lines)
    issue_sales_invoice(session, admin, invoice, owner.id)
    session.commit()

    assert invoice.number == "2026-0001"
    assert invoice.status == InvoiceStatus.OPEN
    assert (invoice.total_excl_cents, invoice.total_vat_cents, invoice.total_incl_cents) == (
        13000, 2370, 15370)
    assert invoice.issued_at is not None
    codes = ("1300", "8000", "8010", "1700", "1710")
    a = {code: account(session, admin, code).id for code in codes}
    assert sorted(entry_lines(session, invoice)) == sorted([
        (a["1300"], 15370, 0, None, None, customer.id),
        (a["8000"], 0, 10000, "V21", "base", customer.id),
        (a["8010"], 0, 3000, "V9", "base", customer.id),
        (a["1700"], 0, 2100, "V21", "vat", None),
        (a["1710"], 0, 270, "V9", "vat", None),
    ])
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "sales_invoice.issue"))
    assert event.entity_id == invoice.id and event.user_id == owner.id


def test_numbers_are_sequential_and_restart_per_year(session, admin, owner, make_invoice):
    numbers = []
    for issue_date in (ISSUE, ISSUE, date(2026, 12, 31), date(2027, 1, 2), date(2027, 1, 3)):
        inv = make_invoice(issue_date=issue_date)
        issue_sales_invoice(session, admin, inv, owner.id)
        session.commit()
        numbers.append(inv.number)
    assert numbers == ["2026-0001", "2026-0002", "2026-0003", "2027-0001", "2027-0002"]


def test_prefix_is_used(session, admin, owner, make_invoice):
    admin.invoice_prefix = "F"
    session.commit()
    inv = make_invoice()
    issue_sales_invoice(session, admin, inv, owner.id)
    assert inv.number == "F2026-0001"


def test_refused_issue_lists_every_gap_and_burns_no_number(
    session, admin, owner, customer, make_invoice
):
    admin.kvk_number = None
    admin.vat_number = ""
    customer.address_line = None
    session.commit()
    inv = make_invoice()
    with pytest.raises(InvoiceError) as err:
        issue_sales_invoice(session, admin, inv, owner.id)
    text = " | ".join(err.value.problems)
    assert "KvK" in text and "VAT number" in text and "Customer address" in text
    assert inv.status == InvoiceStatus.DRAFT and inv.number is None

    admin.kvk_number = "12345678"
    admin.vat_number = "NL123456789B01"
    customer.address_line = "Weg 1"
    issue_sales_invoice(session, admin, inv, owner.id)
    assert inv.number == "2026-0001"


def test_locked_period_refuses_issue_without_consuming_number(session, admin, owner, make_invoice):
    admin.locked_until = date(2026, 3, 31)
    session.commit()
    inv = make_invoice()
    with pytest.raises(InvoiceError, match="closed"):
        issue_sales_invoice(session, admin, inv, owner.id)
    session.rollback()
    admin.locked_until = None
    inv2 = make_invoice()
    issue_sales_invoice(session, admin, inv2, owner.id)
    assert inv2.number == "2026-0001"


def test_reverse_charge_needs_customer_vat_number(session, admin, owner, customer, make_invoice):
    customer.vat_number = None
    session.commit()
    lines = [sline(session, admin, "Subcontracting", "1", 50000, "VRV", "8020")]
    inv = make_invoice(lines)
    with pytest.raises(InvoiceError, match="VAT number is required"):
        issue_sales_invoice(session, admin, inv, owner.id)
    customer.vat_number = "NL999999999B01"
    issue_sales_invoice(session, admin, inv, owner.id)
    rows = entry_lines(session, inv)
    assert len(rows) == 2  # debtors + base, no VAT line
    assert any(r[3] == "VRV" and r[4] == "base" and r[2] == 50000 for r in rows)


def test_kor_forces_vkor_and_no_vat(session, admin, owner, make_invoice):
    admin.kor = True
    session.commit()
    inv = make_invoice([sline(session, admin, "Work", "2", 2500, "V21")])
    assert [ln.vat_code for ln in inv.lines] == ["VKOR"]
    issue_sales_invoice(session, admin, inv, owner.id)
    assert (inv.total_vat_cents, inv.total_incl_cents) == (0, 5000)


def test_issued_invoice_cannot_be_edited_or_reissued(session, admin, owner, customer, make_invoice):
    inv = make_invoice()
    issue_sales_invoice(session, admin, inv, owner.id)
    with pytest.raises(InvoiceError, match="cannot be edited"):
        update_sales_invoice(
            session, admin, inv, customer, [sline(session, admin, "x", "1", 1, "V21")],
            issue_date=ISSUE, due_date=ISSUE,
        )
    with pytest.raises(InvoiceError, match="only a draft"):
        issue_sales_invoice(session, admin, inv, owner.id)


def test_credit_note_nets_everything_to_zero(session, admin, owner, make_invoice):
    inv = make_invoice([
        sline(session, admin, "Consulting", "1", 10000, "V21", "8000"),
        sline(session, admin, "Training", "3", 1000, "V9", "8010"),
    ])
    issue_sales_invoice(session, admin, inv, owner.id)
    note = create_credit_note(session, admin, inv, owner.id, on_date=date(2026, 3, 5))
    session.commit()

    assert note.number == "2026-0002" and note.credit_note_of_id == inv.id
    assert note.total_incl_cents == -15370 and note.lines[0].quantity == Decimal("-1")
    assert inv.status == InvoiceStatus.CANCELLED
    for code in ("1300", "8000", "8010", "1700", "1710"):
        assert account_balance_cents(session, account(session, admin, code).id) == 0, code
    with pytest.raises(InvoiceError):
        create_credit_note(session, admin, note, owner.id)


def test_cancelled_invoice_cannot_be_credited_again(session, admin, owner, make_invoice):
    inv = make_invoice()
    issue_sales_invoice(session, admin, inv, owner.id)
    create_credit_note(session, admin, inv, owner.id)
    with pytest.raises(InvoiceError, match="open or paid"):
        create_credit_note(session, admin, inv, owner.id)


def test_due_date_defaults_to_payment_terms(session, admin, make_invoice):
    admin.payment_terms_days = 14
    session.commit()
    inv = make_invoice()
    assert inv.due_date == date(2026, 3, 15)


def test_contact_of_another_administration_is_refused(session, admin, customer):
    from ledgerline.ledger.chart import create_administration
    from tests.helpers import contact

    other = create_administration(session, name="Ander BV")
    foreign = contact(session, other, name="Vreemd")
    with pytest.raises(InvoiceError, match="another administration"):
        create_sales_invoice(session, admin, foreign, [sline(session, admin, "x", "1", 100, "V21")])


money = st.integers(min_value=1, max_value=2_000_000)
line_strategy = st.tuples(
    st.sampled_from(["1", "2", "0.5", "3.25"]), money, st.sampled_from(["V21", "V9", "V0", "VEX"])
)


@settings(
    max_examples=30, deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.lists(line_strategy, min_size=1, max_size=6))
def test_issued_entry_always_balances_and_matches_totals(session, admin, owner, customer, spec):
    lines = [sline(session, admin, "item", q, p, code) for q, p, code in spec]
    inv = create_sales_invoice(session, admin, customer, lines, issue_date=ISSUE)
    issue_sales_invoice(session, admin, inv, owner.id)
    entry = session.get(JournalEntry, inv.journal_entry_id)
    assert sum(ln.debit_cents for ln in entry.lines) == sum(ln.credit_cents for ln in entry.lines)
    assert sum(ln.debit_cents for ln in entry.lines) == inv.total_incl_cents
    base = sum(ln.credit_cents for ln in entry.lines if ln.vat_role == "base")
    assert base == inv.total_excl_cents
    session.rollback()
