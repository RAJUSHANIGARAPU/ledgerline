from datetime import date

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledgerline.ledger.service import LineSpec, post_entry
from ledgerline.reports.financial import balance_sheet, profit_and_loss, trial_balance
from tests.helpers import account
from tests.vat.booking import book_purchase, book_sale


def _by_code(section):
    return {row.account.code: row.balance_cents for row in section.rows}


def test_profit_and_loss_values(session, admin):
    book_sale(session, admin, "V21", 10000, date(2026, 2, 1))
    book_sale(session, admin, "V9", 4000, date(2026, 3, 1))
    book_purchase(session, admin, "I21", 3000, date(2026, 2, 5))
    book_sale(session, admin, "V21", 99900, date(2025, 12, 31))
    session.commit()
    pnl = profit_and_loss(session, admin.id, date(2026, 1, 1), date(2026, 12, 31))
    assert _by_code(pnl.revenue) == {"8000": 10000, "8010": 4000}
    assert _by_code(pnl.expenses) == {"4200": 3000}
    assert (pnl.revenue.total_cents, pnl.expenses.total_cents, pnl.result_cents) == (
        14000, 3000, 11000)


def test_balance_sheet_includes_results_and_balances(session, admin):
    book_sale(session, admin, "V21", 10000, date(2025, 11, 1))
    book_purchase(session, admin, "I21", 2000, date(2026, 2, 5))
    book_sale(session, admin, "V21", 5000, date(2026, 3, 1))
    session.commit()
    sheet = balance_sheet(session, admin.id, date(2026, 6, 30))
    extra = dict(sheet.equity.extra)
    assert extra["Result of previous years (not yet closed)"] == 10000
    assert extra["Result of the year to date"] == 3000
    assert _by_code(sheet.assets) == {"1300": 12100 + 6050, "1400": 420}
    assert _by_code(sheet.liabilities) == {"1600": 2420, "1700": 2100 + 1050}
    assert sheet.balanced
    assert sheet.assets.total_cents == 18570


def test_balance_sheet_as_of_excludes_later_entries(session, admin):
    book_sale(session, admin, "V21", 10000, date(2026, 3, 1))
    book_sale(session, admin, "V21", 20000, date(2026, 5, 1))
    session.commit()
    sheet = balance_sheet(session, admin.id, date(2026, 3, 31))
    assert sheet.assets.total_cents == 12100 and sheet.balanced


def test_trial_balance_columns_match(session, admin):
    book_sale(session, admin, "V21", 10000, date(2026, 2, 1))
    book_purchase(session, admin, "I21", 3000, date(2026, 2, 5))
    session.commit()
    rows = trial_balance(session, admin.id, date(2026, 12, 31))
    expected = 12100 + 3000 + 630
    assert sum(r.debit_cents for r in rows) == sum(r.credit_cents for r in rows) == expected
    by_code = {r.account.code: (r.debit_cents, r.credit_cents) for r in rows}
    assert by_code["1300"] == (12100, 0) and by_code["8000"] == (0, 10000)
    assert "9999" not in by_code and "0200" not in by_code


CODES = ["1000", "1100", "1300", "1600", "0500", "0520", "4000", "4200", "8000", "8900"]


@st.composite
def balanced_entries(draw):
    n = draw(st.integers(min_value=1, max_value=4))
    debits = draw(st.lists(st.tuples(st.sampled_from(CODES), st.integers(1, 500_000)),
                           min_size=n, max_size=n))
    credit_account = draw(st.sampled_from(CODES))
    split = draw(st.lists(st.sampled_from(CODES), min_size=1, max_size=3))
    total = sum(c for _, c in debits)
    parts = [total // len(split)] * len(split)
    parts[0] += total - sum(parts)
    credits = [(code, c) for code, c in zip(split, parts, strict=True) if c > 0]
    day = draw(st.dates(min_value=date(2025, 1, 1), max_value=date(2026, 12, 31)))
    return day, debits, credits or [(credit_account, total)]


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(entries=st.lists(balanced_entries(), min_size=1, max_size=6))
def test_reports_balance_for_random_balanced_entries(session, admin, entries):
    for day, debits, credits in entries:
        post_entry(session, admin.id, day, "random", [
            *[LineSpec(account(session, admin, c).id, debit_cents=v) for c, v in debits],
            *[LineSpec(account(session, admin, c).id, credit_cents=v) for c, v in credits]])
    for as_of in (date(2025, 6, 30), date(2026, 12, 31)):
        sheet = balance_sheet(session, admin.id, as_of)
        assert sheet.balanced, (sheet.assets.total_cents, sheet.liabilities.total_cents,
                                sheet.equity.total_cents)
        rows = trial_balance(session, admin.id, as_of)
        assert sum(r.debit_cents for r in rows) == sum(r.credit_cents for r in rows)
    pnl = profit_and_loss(session, admin.id, date(2025, 1, 1), date(2026, 12, 31))
    sheet = balance_sheet(session, admin.id, date(2026, 12, 31))
    assert sum(v for _, v in sheet.equity.extra) == pnl.result_cents
    session.rollback()
