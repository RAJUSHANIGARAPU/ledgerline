from datetime import date

import pytest
from sqlalchemy import select

from ledgerline.ledger.service import account_balance_cents, reverse_entry
from ledgerline.models import AuditEvent, JournalEntry
from ledgerline.money import format_eur
from ledgerline.vat.vat_return import (
    Row,
    build_return,
    compute_vat_return,
    lines_for_bucket,
    period_bounds,
    whole_euros,
)
from tests.helpers import account
from tests.vat.booking import book_purchase, book_sale

D = date(2026, 2, 10)
Q1 = (date(2026, 1, 1), date(2026, 3, 31))
SETTLE_KEY = "vat_settlement:2026-Q1"


def _book_every_code(session, admin):
    for code, excl in [("V21", 10000), ("V9", 5000), ("V0", 2000), ("VRV", 3000),
                       ("VEU", 4000), ("VEX", 6000), ("VVR", 700)]:
        book_sale(session, admin, code, excl, D)
    for code, excl in [("I21", 8000), ("I9", 1000), ("IRV21", 2000), ("IEU21", 3000),
                       ("INEU21", 500)]:
        book_purchase(session, admin, code, excl, D)
    session.commit()


def test_every_code_lands_in_its_rubriek(session, admin):
    _book_every_code(session, admin)
    r = compute_vat_return(session, admin.id, *Q1)
    expected = {  # rubriek: (base, vat)
        "1a": (10000, 2100), "1b": (5000, 450), "1c": (0, 0), "1d": (0, 0),
        "1e": (5000, 0), "2a": (2000, 420), "3a": (6000, 0), "3b": (4000, 0), "3c": (0, 0),
        "4a": (500, 105), "4b": (3000, 630),
    }
    assert {line.rubriek: (line.base_cents, line.vat_cents) for line in r.lines} == expected


def test_totals_and_balance(session, admin):
    _book_every_code(session, admin)
    r = compute_vat_return(session, admin.id, *Q1)
    assert r.owed_cents == 2100 + 450 + 420 + 105 + 630 == 3705
    assert r.deductible_cents == 1680 + 90 + 420 + 630 + 105 == 2925
    assert r.balance_cents == 780


def test_refund_when_input_vat_exceeds_output(session, admin):
    book_sale(session, admin, "V21", 1000, D)
    book_purchase(session, admin, "I21", 10000, D)
    r = compute_vat_return(session, admin.id, *Q1)
    assert (r.owed_cents, r.deductible_cents, r.balance_cents) == (210, 2100, -1890)


def test_stated_supplier_vat_is_used_for_deduction(session, admin):
    book_purchase(session, admin, "I21", 1000, D, vat=211)
    assert compute_vat_return(session, admin.id, *Q1).deductible_cents == 211


def test_period_boundaries(session, admin):
    book_sale(session, admin, "V21", 10000, date(2026, 3, 31))
    book_sale(session, admin, "V21", 20000, date(2026, 4, 1))
    book_sale(session, admin, "V21", 40000, date(2025, 12, 31))
    q1 = compute_vat_return(session, admin.id, *Q1)
    q2 = compute_vat_return(session, admin.id, date(2026, 4, 1), date(2026, 6, 30))
    assert q1.line("1a").base_cents == 10000
    assert q2.line("1a").base_cents == 20000


def test_reversal_nets_to_zero(session, admin):
    sale = book_sale(session, admin, "V21", 10000, D)
    purchase = book_purchase(session, admin, "IEU21", 5000, D)
    reverse_entry(session, sale, date(2026, 3, 1), "wrong customer")
    reverse_entry(session, purchase, date(2026, 3, 2), "duplicate")
    session.commit()
    r = compute_vat_return(session, admin.id, *Q1)
    assert all(line.base_cents == 0 and line.vat_cents == 0 for line in r.lines)
    assert (r.owed_cents, r.deductible_cents, r.balance_cents) == (0, 0, 0)


def test_untagged_and_unknown_lines_are_ignored():
    rows = [
        Row("V21", "base", "debtors", 0, 100),
        Row("XXX", "base", None, 0, 999),
        Row("V21", "vat", "bank", 0, 21),
        Row("V0", "vat", "vat_payable_high", 0, 5),
    ]
    r = build_return(rows, *Q1)
    assert r.line("1a").base_cents == 100 and r.owed_cents == 0 and r.line("1e").base_cents == 0


def test_credit_note_reduces_base_and_vat():
    rows = [
        Row("V21", "base", "x", 0, 10000), Row("V21", "base", "x", 2000, 0),
        Row("V21", "vat", "vat_payable_high", 0, 2100),
        Row("V21", "vat", "vat_payable_high", 420, 0),
    ]
    r = build_return(rows, *Q1)
    assert (r.line("1a").base_cents, r.line("1a").vat_cents) == (8000, 1680)


def test_drill_down_lists_the_lines_behind_a_rubriek(session, admin):
    sale = book_sale(session, admin, "V21", 10000, D)
    book_sale(session, admin, "V9", 5000, D)
    session.commit()
    rows = lines_for_bucket(session, admin.id, *Q1, "1a")
    assert {r[1].id for r in rows} == {sale.id}
    assert sorted(r[3] for r in rows) == [2100, 10000]
    assert lines_for_bucket(session, admin.id, *Q1, "5b") == []


@pytest.mark.parametrize(("cents", "whole"),
                         [(12345, 123), (99, 0), (-12345, -123), (0, 0), (100, 1)])
def test_whole_euros_drops_cents(cents, whole):
    assert whole_euros(cents) == whole


@pytest.mark.parametrize(("args", "start", "end", "label"), [
    (("quarter", 2026, 4), date(2026, 10, 1), date(2026, 12, 31), "2026-Q4"),
    (("quarter", 2026, 1), date(2026, 1, 1), date(2026, 3, 31), "2026-Q1"),
    (("month", 2028, 2), date(2028, 2, 1), date(2028, 2, 29), "2028-02"),
    (("month", 2026, 12), date(2026, 12, 1), date(2026, 12, 31), "2026-12"),
    (("year", 2026, 1), date(2026, 1, 1), date(2026, 12, 31), "2026"),
])
def test_period_bounds(args, start, end, label):
    assert period_bounds(*args) == (start, end, label)


def test_period_bounds_rejects_bad_numbers():
    with pytest.raises(ValueError, match="invalid"):
        period_bounds("quarter", 2026, 5)


# ---- routes

def test_overview_shows_banner_rubrieken_and_source_links(client, session, admin):
    _book_every_code(session, admin)
    page = client.get("/vat?kind=quarter&year=2026&number=1")
    assert page.status_code == 200
    assert "Ledgerline does not submit to the Belastingdienst" in page.text
    assert "/vat/lines?bucket=1a&amp;kind=quarter" in page.text
    assert page.text.count(">verify<") == 0
    assert page.text.count(">source<") == 12
    assert format_eur(780) in page.text


def test_drill_down_page_links_to_entries(client, session, admin):
    sale = book_sale(session, admin, "V21", 10000, D)
    session.commit()
    page = client.get("/vat/lines?bucket=1a&kind=quarter&year=2026&number=1")
    assert page.status_code == 200 and f"/ledger/{sale.id}" in page.text
    assert client.get("/vat/lines?bucket=9z").status_code == 404


def test_invalid_period_is_422(client):
    assert client.get("/vat?kind=quarter&year=2026&number=7").status_code == 422
    assert client.get("/vat?kind=week").status_code == 422


def _settlements(session):
    return session.scalars(select(JournalEntry).where(JournalEntry.source_type == SETTLE_KEY)).all()


def test_settlement_clears_vat_accounts_and_is_posted_once(client, session, admin):
    _book_every_code(session, admin)
    form = {"kind": "quarter", "year": "2026", "number": "1", "csrf_token": client.csrf}
    response = client.post("/vat/settle", data=form, follow_redirects=False)
    assert response.status_code == 303
    session.expire_all()
    for code in ("1700", "1710", "1720", "1400"):
        assert account_balance_cents(session, account(session, admin, code).id) == 0
    assert account_balance_cents(session, account(session, admin, "1790").id) == -780
    (entry,) = _settlements(session)
    assert entry.entry_date == date(2026, 3, 31)
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "vat.settle")) is not None
    # the return is unchanged: settlement lines carry no VAT code
    assert compute_vat_return(session, admin.id, *Q1).balance_cents == 780
    client.get("/vat")  # shows the success message of the first post
    again = client.post("/vat/settle", data=form, follow_redirects=True)
    assert "already been settled" in again.text
    assert len(_settlements(session)) == 1


def test_settlement_of_a_refund_debits_the_settlement_account(client, session, admin):
    book_purchase(session, admin, "I21", 10000, D)
    session.commit()
    client.post("/vat/settle", data={"kind": "month", "year": "2026", "number": "2",
                                     "csrf_token": client.csrf})
    session.expire_all()
    assert account_balance_cents(session, account(session, admin, "1400").id) == 0
    assert account_balance_cents(session, account(session, admin, "1790").id) == 2100


def test_settlement_with_no_movement_is_refused(client, session, admin):
    response = client.post("/vat/settle", data={
        "kind": "quarter", "year": "2026", "number": "3", "csrf_token": client.csrf},
        follow_redirects=True)
    assert "no VAT movement" in response.text
    assert session.scalar(select(JournalEntry.id)) is None


def test_settlement_blocked_by_period_lock(client, session, admin):
    book_sale(session, admin, "V21", 10000, D)
    admin.locked_until = date(2026, 3, 31)
    session.commit()
    response = client.post("/vat/settle", data={
        "kind": "quarter", "year": "2026", "number": "1", "csrf_token": client.csrf},
        follow_redirects=True)
    assert "period is closed" in response.text
    assert _settlements(session) == []


def test_readonly_cannot_settle(readonly_client):
    response = readonly_client.post("/vat/settle", data={
        "kind": "quarter", "year": "2026", "number": "1", "csrf_token": readonly_client.csrf})
    assert response.status_code == 403
