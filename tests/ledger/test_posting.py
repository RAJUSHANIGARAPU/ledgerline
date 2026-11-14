from datetime import date

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ledgerline.ledger.service import (
    LedgerError,
    LineSpec,
    account_balance_cents,
    account_balances,
    post_entry,
    reverse_entry,
    validate_lines,
)
from tests.helpers import account


def _sale(session, admin, cents=12100, when=date(2026, 3, 1)):
    debtors, revenue, vat = (account(session, admin, c) for c in ("1300", "8000", "1700"))
    return post_entry(session, admin.id, when, "Sale", [
        LineSpec(debtors.id, debit_cents=cents),
        LineSpec(revenue.id, credit_cents=cents * 100 // 121, vat_code="V21", vat_role="base"),
        LineSpec(vat.id, credit_cents=cents - cents * 100 // 121, vat_code="V21", vat_role="vat"),
    ])


def test_post_entry_writes_balanced_numbered_entry(session, admin):
    entry = _sale(session, admin)
    session.commit()
    assert entry.number == "2026-00001"
    assert sum(line.debit_cents for line in entry.lines) == 12100
    assert sum(line.credit_cents for line in entry.lines) == 12100
    assert account_balance_cents(session, account(session, admin, "1300").id) == 12100
    assert account_balance_cents(session, account(session, admin, "8000").id) == -10000


def test_numbers_are_sequential_per_year(session, admin):
    numbers = [_sale(session, admin).number, _sale(session, admin).number,
               _sale(session, admin, when=date(2027, 1, 2)).number]
    assert numbers == ["2026-00001", "2026-00002", "2027-00001"]


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([LineSpec(1, debit_cents=100)], "at least two"),
        ([LineSpec(1, debit_cents=100), LineSpec(2, credit_cents=99)], "does not balance"),
        ([LineSpec(1, debit_cents=100, credit_cents=100), LineSpec(2)], "either a debit"),
        ([LineSpec(1, debit_cents=-5), LineSpec(2, credit_cents=-5)], "negative"),
    ],
)
def test_validate_lines_rejects(lines, message):
    with pytest.raises(LedgerError, match=message):
        validate_lines(lines)


def test_foreign_account_is_refused(session, admin):
    from ledgerline.ledger.chart import create_administration

    other = create_administration(session, name="Other")
    with pytest.raises(LedgerError, match="belongs elsewhere"):
        post_entry(session, admin.id, date(2026, 1, 1), "x", [
            LineSpec(account(session, admin, "1100").id, debit_cents=1),
            LineSpec(account(session, other, "8000").id, credit_cents=1),
        ])


def test_locked_period_is_refused(session, admin):
    admin.locked_until = date(2026, 3, 31)
    session.flush()
    with pytest.raises(LedgerError, match="closed"):
        _sale(session, admin, when=date(2026, 3, 31))
    assert _sale(session, admin, when=date(2026, 4, 1)).number == "2026-00001"


def test_reversal_nets_to_zero_and_only_once(session, admin):
    entry = _sale(session, admin)
    reversal = reverse_entry(session, entry, date(2026, 3, 5), "wrong customer")
    session.commit()
    assert reversal.reversal_of_id == entry.id
    assert all(b.debit_cents == b.credit_cents for b in account_balances(session, admin.id))
    with pytest.raises(LedgerError, match="already been reversed"):
        reverse_entry(session, entry, date(2026, 3, 6), "again")


# The database enforces the rules even when the service layer is bypassed.

def test_database_refuses_unbalanced_entry_at_commit(session, admin):
    entry = _sale(session, admin)
    session.commit()
    session.execute(text(
        "INSERT INTO journal_entries (administration_id, number, entry_date, description) "
        "VALUES (:a, 'X-1', '2026-01-01', 'raw')"), {"a": admin.id})
    new_id = session.execute(text("SELECT max(id) FROM journal_entries")).scalar_one()
    session.execute(text(
        "INSERT INTO journal_lines (entry_id, account_id, debit_cents, credit_cents) "
        "VALUES (:e, :acc, 100, 0)"), {"e": new_id, "acc": entry.lines[0].account_id})
    with pytest.raises(DBAPIError, match="does not balance"):
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE journal_lines SET debit_cents = debit_cents + 1",
        "DELETE FROM journal_lines",
        "DELETE FROM journal_entries",
        "UPDATE journal_entries SET description = 'edited'",
        "TRUNCATE journal_lines CASCADE",
    ],
)
def test_database_refuses_changes_to_posted_entries(session, admin, statement):
    _sale(session, admin)
    session.commit()
    with pytest.raises(DBAPIError, match="append-only|permission denied"):
        session.execute(text(statement))
    session.rollback()


def test_database_refuses_lines_added_to_an_old_entry(session, admin):
    entry = _sale(session, admin)
    session.commit()
    with pytest.raises(DBAPIError, match="already posted"):
        session.execute(text(
            "INSERT INTO journal_lines (entry_id, account_id, debit_cents, credit_cents) "
            "VALUES (:e, :a, 5, 0), (:e, :a, 0, 5)"),
            {"e": entry.id, "a": entry.lines[0].account_id})
    session.rollback()


def test_audit_events_are_append_only(session, admin):
    from ledgerline import audit

    audit.record(session, "test", administration_id=admin.id)
    session.commit()
    with pytest.raises(DBAPIError, match="append-only|permission denied"):
        session.execute(text("UPDATE audit_events SET action = 'x'"))
    session.rollback()


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(st.integers(min_value=1, max_value=10**9), min_size=1, max_size=6))
def test_trial_balance_always_balances(session, admin, amounts):
    bank, revenue, costs = (account(session, admin, c) for c in ("1100", "8000", "4900"))
    for i, cents in enumerate(amounts):
        income = i % 2 == 0
        post_entry(session, admin.id, date(2026, 1, 1 + i), "mixed", [
            LineSpec((bank if income else costs).id, debit_cents=cents),
            LineSpec((revenue if income else bank).id, credit_cents=cents),
        ])
    balances = account_balances(session, admin.id)
    assert sum(b.debit_cents for b in balances) == sum(b.credit_cents for b in balances)
    session.rollback()


def test_two_concurrent_reversals_post_only_one(session, admin, tenant):
    import threading
    import time

    from sqlalchemy import func, select

    from ledgerline.models import JournalEntry
    from tests.conftest import tenant_db

    entry = _sale(session, admin)
    session.commit()
    outcome = {}

    def second_request():
        with tenant_db(tenant.id) as other:
            try:
                reverse_entry(other, other.get(JournalEntry, entry.id), date(2026, 3, 5), "twice")
                other.commit()
                outcome["result"] = "posted"
            except LedgerError as exc:
                other.rollback()
                outcome["result"] = str(exc)

    reverse_entry(session, entry, date(2026, 3, 5), "first")
    worker = threading.Thread(target=second_request)
    worker.start()
    time.sleep(0.5)
    session.commit()
    worker.join(timeout=20)
    assert outcome["result"] == "entry has already been reversed"
    assert session.scalar(select(func.count()).select_from(JournalEntry).where(
        JournalEntry.reversal_of_id == entry.id)) == 1


def test_database_refuses_a_second_reversal_of_the_same_entry(session, admin):
    entry = _sale(session, admin)
    reverse_entry(session, entry, date(2026, 3, 5), "first")
    session.commit()
    with pytest.raises(DBAPIError, match="uq_journal_entries_reversal_of"):
        session.execute(text(
            "INSERT INTO journal_entries (administration_id, number, entry_date, description, "
            "reversal_of_id) VALUES (:a, 'X-1', '2026-03-06', 'dup', :e)"
        ), {"a": admin.id, "e": entry.id})
        session.flush()
