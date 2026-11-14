from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from ledgerline.banking import service as banking
from ledgerline.ledger.service import account_balance_cents
from ledgerline.models import (
    Account,
    AuditEvent,
    BankAccount,
    BankStatement,
    BankTransaction,
    DailyTakings,
    JournalEntry,
    SystemRole,
)
from ledgerline.money import vat_in_gross
from ledgerline.takings.service import (
    TakingsError,
    TakingsInput,
    post_takings,
    replace_takings,
    reverse_takings,
)
from ledgerline.vat.vat_return import compute_vat_return
from tests.helpers import account

DAY = date(2026, 10, 5)


def day(**amounts):
    return TakingsInput(amounts.pop("when", DAY), **amounts)


def lines_by_account(session, row):
    entry = session.get(JournalEntry, row.journal_entry_id)
    return {(session.get(Account, line.account_id).code): (line.debit_cents, line.credit_cents,
                                                          line.vat_code, line.vat_role)
            for line in entry.lines}


def test_each_rate_posts_revenue_and_vat(session, admin):
    row = post_takings(session, admin, day(
        gross_high_cents=12100, gross_low_cents=10900, gross_zero_cents=5000,
        cash_cents=8000, card_cents=19000, other_cents=1000))
    posted = lines_by_account(session, row)
    assert posted["8000"] == (0, 10000, "V21", "base")
    assert posted["1700"] == (0, 2100, "V21", "vat")
    assert posted["8010"] == (0, 10000, "V9", "base")
    assert posted["1710"] == (0, 900, "V9", "vat")
    assert posted["8020"] == (0, 5000, "V0", "base")
    assert posted["1000"][0] == 8000
    assert posted["1310"][0] == 19000
    assert posted["1320"][0] == 1000
    assert row.note is None


def test_vat_is_rounded_half_up_on_the_daily_gross(session, admin):
    # 10001 * 21 / 121 = 1735.70 -> 1736
    row = post_takings(session, admin, day(gross_high_cents=10001, cash_cents=10001))
    assert lines_by_account(session, row)["1700"][1] == 1736
    assert lines_by_account(session, row)["8000"][1] == 10001 - 1736
    # 6 * 9 / 109 = 0.49 -> 0: no VAT line, the whole amount stays revenue
    small = post_takings(session, admin, day(when=date(2026, 10, 6), gross_low_cents=6,
                                             cash_cents=6))
    assert "1710" not in lines_by_account(session, small)
    assert lines_by_account(session, small)["8010"][1] == 6
    # 11 * 9 / 109 = 0.908 -> 1; 109 * 9 / 109 sits on a whole cent
    near = post_takings(session, admin, day(when=date(2026, 10, 7), gross_low_cents=12,
                                            cash_cents=12))
    assert lines_by_account(session, near)["1710"][1] == 1  # 12 * 9 / 109 = 0.99


def test_half_cent_rounds_up(session, admin):
    # 21 * 21 / 121 = 3.645 -> 4; and the 0.5 boundary: 242 * 21 / 121 = 42 exactly
    assert vat_in_gross(21, Decimal(21)) == 4
    assert vat_in_gross(242, Decimal(21)) == 42
    assert vat_in_gross(109, Decimal(9)) == 9
    assert vat_in_gross(1, Decimal(100)) == 1  # 0.5 exactly rounds up


def test_mismatch_between_rates_and_methods_is_refused(session, admin):
    with pytest.raises(TakingsError, match="do not equal"):
        post_takings(session, admin, day(gross_high_cents=1000, cash_cents=900))
    assert session.scalar(select(DailyTakings.id)) is None


def test_empty_negative_and_future_days_are_refused(session, admin):
    for bad, message in (
        (day(), "at least one amount"),
        (day(gross_high_cents=-100, cash_cents=-100), "negative"),
        (day(when=date(2099, 1, 1), gross_high_cents=100, cash_cents=100), "future"),
    ):
        with pytest.raises(TakingsError, match=message):
            post_takings(session, admin, bad)


def test_period_lock_is_respected(session, admin):
    admin.locked_until = date(2026, 9, 30)
    with pytest.raises(TakingsError, match="closed up to 2026-09-30"):
        post_takings(session, admin, day(when=date(2026, 9, 30), gross_high_cents=100,
                                         cash_cents=100))
    post_takings(session, admin, day(when=date(2026, 10, 1), gross_high_cents=100,
                                     cash_cents=100))


def test_same_day_twice_is_refused(session, admin):
    post_takings(session, admin, day(gross_high_cents=100, cash_cents=100))
    with pytest.raises(TakingsError, match="already recorded"):
        post_takings(session, admin, day(gross_low_cents=100, cash_cents=100))


def test_reversal_nets_the_books_to_zero_and_frees_the_day(session, admin, owner):
    row = post_takings(session, admin, day(gross_high_cents=12100, card_cents=12100),
                       owner.id)
    reversal = reverse_takings(session, row, owner.id)
    assert row.reversed_at is not None and row.reversal_entry_id == reversal.id
    assert account_balance_cents(session, account(session, admin, "1310").id) == 0
    assert account_balance_cents(session, account(session, admin, "1700").id) == 0
    with pytest.raises(TakingsError, match="already been reversed"):
        reverse_takings(session, row, owner.id)
    post_takings(session, admin, day(gross_high_cents=500, cash_cents=500), owner.id)
    actions = session.scalars(select(AuditEvent.action).order_by(AuditEvent.id)).all()
    assert actions == ["takings.post", "takings.reverse", "takings.post"]


def test_reversal_into_a_locked_period_is_refused(session, admin):
    row = post_takings(session, admin, day(gross_high_cents=100, cash_cents=100))
    admin.locked_until = DAY
    with pytest.raises(TakingsError, match="closed"):
        reverse_takings(session, row)
    assert row.reversed_at is None


def test_replace_reverses_and_reposts_once(session, admin):
    old = post_takings(session, admin, day(gross_high_cents=12100, cash_cents=12100))
    new = replace_takings(session, admin, old, day(gross_high_cents=24200, cash_cents=24200))
    assert old.reversed_at is not None and new.reversed_at is None
    assert account_balance_cents(session, account(session, admin, "8000").id) == -20000
    with pytest.raises(TakingsError, match="date of a recorded day"):
        replace_takings(session, admin, new, day(when=date(2026, 10, 6), gross_high_cents=1,
                                                 cash_cents=1))
    with pytest.raises(TakingsError, match="do not equal"):
        replace_takings(session, admin, new, day(gross_high_cents=1, cash_cents=2))
    assert new.reversed_at is None


def test_vat_return_picks_up_output_vat(session, admin):
    post_takings(session, admin, day(gross_high_cents=12100, gross_low_cents=10900,
                                     gross_zero_cents=5000, cash_cents=28000))
    result = compute_vat_return(session, admin.id, date(2026, 10, 1), date(2026, 12, 31))
    assert (result.line("1a").base_cents, result.line("1a").vat_cents) == (10000, 2100)
    assert (result.line("1b").base_cents, result.line("1b").vat_cents) == (10000, 900)
    assert result.line("1e").base_cents == 5000
    assert result.owed_cents == 3000


def test_card_payout_clears_through_existing_bank_booking(session, admin):
    post_takings(session, admin, day(gross_high_cents=12100, card_cents=12100))
    bank_ledger = account(session, admin, "1100")
    bank = BankAccount(administration_id=admin.id, name="Zakelijk",
                       iban="NL91ABNA0417164300", ledger_account_id=bank_ledger.id)
    session.add(bank)
    session.flush()
    statement = BankStatement(bank_account_id=bank.id, file_name="a.xml", file_sha256="0" * 64,
                              file_format="camt053")
    session.add(statement)
    session.flush()
    tx = BankTransaction(bank_account_id=bank.id, statement_id=statement.id,
                         booking_date=date(2026, 10, 8),
                         amount_cents=12100, description="Payout", dedupe_key="payout-1")
    session.add(tx)
    session.flush()
    banking.book_to_account(session, tx, account(session, admin, "1310").id)
    assert account_balance_cents(session, account(session, admin, "1310").id) == 0


def test_older_books_get_the_receipt_accounts_on_first_use(session, admin):
    for code in ("1310", "1320"):
        session.delete(account(session, admin, code))
    cash = account(session, admin, "1000")
    cash.system_role = None
    session.flush()
    post_takings(session, admin, day(gross_high_cents=300, cash_cents=100, card_cents=100,
                                     other_cents=100))
    roles = {a.code: a.system_role for a in session.scalars(select(Account).where(
        Account.administration_id == admin.id, Account.code.in_(["1000", "1310", "1320"])))}
    assert roles == {"1000": SystemRole.CASH, "1310": SystemRole.CARD_CLEARING,
                     "1320": SystemRole.TAKINGS_OTHER}
