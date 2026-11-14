from datetime import date

import pytest
from sqlalchemy import select

from ledgerline.banking import service as banking
from ledgerline.inbox import platform_booking
from ledgerline.inbox.extract import ExtractionError
from ledgerline.inbox.extract.pdf_text import PdfTextExtractor, read_text
from ledgerline.inbox.extract.platform import (
    THUISBEZORGD,
    UBER,
    PlatformStatement,
    parse_platform_text,
)
from ledgerline.ledger.service import account_balance_cents
from ledgerline.models import (
    BankStatement,
    BankTransaction,
    InboxDocument,
    InboxStatus,
    JournalEntry,
    TxStatus,
)
from tests.helpers import account
from tests.inbox.factories import (
    pdf_from_html,
    thuisbezorgd_statement_html,
    uber_statement_html,
)
from tests.inbox.test_review_flow import upload


def read(html) -> PlatformStatement:
    return parse_platform_text(read_text(pdf_from_html(html)))


def test_uber_week_with_promotions():
    st = read(uber_statement_html())
    assert (st.platform, st.orders) == (UBER, 14)
    assert (st.period_start, st.period_end) == (date(2026, 5, 4), date(2026, 5, 10))
    assert (st.gross_sales_cents, st.fees_cents, st.fee_vat_cents) == (40250, 6030, 1266)
    assert (st.adjustments_cents, st.tips_cents) == (-2000, 0)
    assert st.payout_cents == 40250 - 6030 - 1266 - 2000
    assert st.arithmetic_ok
    assert st.fee_vat_rate_percent == 21 and not st.fee_vat_rate_printed


def test_uber_week_with_tips_and_thousands_separator():
    st = read(uber_statement_html(sales=123456, fee=18000, fee_vat=3780, tips=1219,
                                  days=[("5/5/26", 40, 123456)]))
    assert st.tips_cents == 1219 and st.gross_sales_cents == 123456
    assert st.payout_cents == 123456 - 18000 - 3780 + 1219 - 2000 and st.arithmetic_ok


def test_uber_week_with_a_refund_line():
    st = read(uber_statement_html(adjustments=(
        ("Promoties voor items", -2000),
        ("Aanpassingen fout in bestelling - week van 5/4/26", -1125))))
    assert st.adjustments_cents == -3125 and len(st.adjustment_lines) == 2 and st.arithmetic_ok


def test_uber_negative_payout():
    refund = (("Terugbetaling", -5000),)
    st = read(uber_statement_html(sales=1000, fee=150, fee_vat=32, adjustments=refund,
                                  days=[("5/5/26", 1, 1000)]))
    assert st.payout_cents == 1000 - 150 - 32 - 5000 < 0 and st.arithmetic_ok


def test_uber_components_that_do_not_sum_are_flagged():
    st = read(uber_statement_html(payout=30000))
    assert not st.arithmetic_ok
    assert any("does not add up" in w for w in st.warnings)


def test_uber_daily_rows_that_disagree_with_the_total_are_flagged():
    st = read(uber_statement_html(days=[("5/5/26", 3, 10000)]))
    assert not st.arithmetic_ok


def test_uber_period_in_day_first_notation():
    st = read(uber_statement_html(start="25/5/26", end="31/5/26"))
    assert (st.period_start, st.period_end) == (date(2026, 5, 25), date(2026, 5, 31))


def test_thuisbezorgd_week_with_cash_and_tips():
    st = read(thuisbezorgd_statement_html())
    assert st.platform == THUISBEZORGD and st.reference == "900000001"
    assert (st.period_start, st.period_end) == (date(2026, 3, 1), date(2026, 3, 7))
    assert (st.gross_sales_cents, st.cash_collected_cents, st.tips_cents) == (100000, 4000, 600)
    assert st.fees_cents == 14000 + 2500 + 700 and st.fee_vat_rate_percent == 21
    assert st.fee_vat_rate_printed and st.arithmetic_ok
    assert st.payout_cents == 96000 + 600 - st.fees_cents - st.fee_vat_cents


def test_thuisbezorgd_week_with_a_refund():
    st = read(thuisbezorgd_statement_html(refund=-270, tips=0))
    assert st.adjustments_cents == -270 and st.adjustment_lines[0][0] == "terugboeking"
    assert st.arithmetic_ok


def test_thuisbezorgd_week_without_cash_orders():
    st = read(thuisbezorgd_statement_html(cash=0))
    assert st.cash_collected_cents == 0 and st.arithmetic_ok


def test_thuisbezorgd_negative_payout():
    st = read(thuisbezorgd_statement_html(total=3000, cash=0, tips=0, commission=420, extras=(),
                                          admin=63, payout=None))
    st2 = read(thuisbezorgd_statement_html(total=3000, cash=0, tips=0, commission=420, extras=(),
                                           admin=63, refund=-90000))
    assert st.arithmetic_ok and st2.payout_cents < 0 and st2.arithmetic_ok


def test_thuisbezorgd_components_that_do_not_sum_are_flagged():
    st = read(thuisbezorgd_statement_html(payout=1234))
    assert not st.arithmetic_ok and any("payout" in w for w in st.warnings)


def test_thuisbezorgd_without_the_invoice_part_is_not_guessed():
    html = thuisbezorgd_statement_html()
    start = html.index("<p>Datum:")
    end = html.index("<h1>Specificaties")
    with pytest.raises(ExtractionError, match="invoice part"):
        read(html[:start] + html[end:])


def test_other_documents_are_not_platform_statements():
    assert parse_platform_text("Factuur 123\nTotaal te betalen EUR 10,00") is None


def test_pdf_reader_returns_the_statement_with_its_warnings():
    ex = PdfTextExtractor().extract("application/pdf", pdf_from_html(uber_statement_html()))
    assert ex.supplier_name == "Uber Eats" and ex.platform_statement["payout_cents"] == 30954
    assert not ex.lines and any("VAT rate" in w for w in ex.warnings)


# Booking

def received(client, session, html, name="overzicht.pdf"):
    upload(client, session, (name, pdf_from_html(html), "application/pdf"))
    return session.scalars(select(InboxDocument).order_by(InboxDocument.id.desc())).first()


def split_form(client, code_amounts, **extra):
    data = {"csrf_token": client.csrf, "sales_code": [c for c, _ in code_amounts],
            "sales_amount": [a for _, a in code_amounts]}
    data.update(extra)
    return data


def balance(session, admin, code):
    return account_balance_cents(session, account(session, admin, code).id)


def test_statement_detail_asks_for_what_the_document_does_not_say(client, session, admin):
    doc = received(client, session, thuisbezorgd_statement_html())
    page = client.get(f"/inbox/{doc.id}")
    assert page.status_code == 200
    assert "Split sales by VAT rate" in page.text and "Confirm with your adviser" in page.text
    assert "Account for tips" in page.text and "Book statement" in page.text
    assert not session.scalars(select(JournalEntry)).all()


def test_nothing_is_booked_until_the_owner_submits(client, session, admin):
    received(client, session, uber_statement_html())
    assert session.scalars(select(JournalEntry)).all() == []


def test_booking_a_thuisbezorgd_week(client, session, admin):
    doc = received(client, session, thuisbezorgd_statement_html(refund=-270))
    tips = account(session, admin, "8900")
    other = account(session, admin, "4600")
    r = client.post(f"/inbox/{doc.id}/book-statement", data=split_form(
        client, [("V9", "900,00"), ("V21", "100,00")],
        tips_account=str(tips.id), adjustments_account=str(other.id)), follow_redirects=False)
    assert r.status_code == 303, r.text
    session.expire_all()
    st = platform_booking.statement_of(session.get(InboxDocument, doc.id))
    entry = session.scalars(select(JournalEntry)).one()
    assert (entry.entry_date, entry.description) == (date(2026, 3, 7), st.label)
    assert sum(ln.debit_cents for ln in entry.lines) == sum(ln.credit_cents for ln in entry.lines)
    assert session.get(InboxDocument, doc.id).status == InboxStatus.BOOKED
    assert balance(session, admin, "1340") == st.payout_cents
    assert balance(session, admin, "1000") == 4000
    assert balance(session, admin, "1400") == st.fee_vat_cents
    assert balance(session, admin, "4610") == st.fees_cents
    assert balance(session, admin, "1500") == 0
    assert balance(session, admin, "8900") == -600
    assert balance(session, admin, "4600") == 270
    assert balance(session, admin, "8010") == -82569
    assert balance(session, admin, "8000") == -8264
    assert balance(session, admin, "1710") == -7431 and balance(session, admin, "1700") == -1736


def test_sales_split_must_add_up_to_the_gross_sales(client, session, admin):
    doc = received(client, session, uber_statement_html())
    adj = account(session, admin, "4600")
    r = client.post(f"/inbox/{doc.id}/book-statement", data=split_form(
        client, [("V9", "100,00")], adjustments_account=str(adj.id)))
    assert r.status_code == 422 and "must add up" in r.text
    assert session.scalars(select(JournalEntry)).all() == []


def test_tips_need_an_account_chosen_by_the_owner(client, session, admin):
    doc = received(client, session, uber_statement_html(tips=500))
    adj = account(session, admin, "4600")
    r = client.post(f"/inbox/{doc.id}/book-statement", data=split_form(
        client, [("V9", "402,50")], adjustments_account=str(adj.id)))
    assert r.status_code == 422 and "account for tips" in r.text


def test_a_statement_that_does_not_add_up_needs_an_explicit_override(client, session, admin):
    doc = received(client, session, uber_statement_html(payout=30000))
    adj = account(session, admin, "4600")
    data = split_form(client, [("V9", "402,50")], adjustments_account=str(adj.id))
    r = client.post(f"/inbox/{doc.id}/book-statement", data=data)
    assert r.status_code == 422 and "do not add up" in r.text
    data["accept_difference"] = "1"
    assert client.post(f"/inbox/{doc.id}/book-statement", data=data,
                       follow_redirects=False).status_code == 303
    session.expire_all()
    assert balance(session, admin, "1330") == 30000
    assert balance(session, admin, "1500") == 954


def test_the_same_week_cannot_be_booked_twice(client, session, admin):
    first = received(client, session, uber_statement_html())
    adj = account(session, admin, "4600")
    data = split_form(client, [("V9", "402,50")], adjustments_account=str(adj.id))
    assert client.post(f"/inbox/{first.id}/book-statement", data=data,
                       follow_redirects=False).status_code == 303
    second = received(client, session, uber_statement_html(orders=15), name="again.pdf")
    r = client.post(f"/inbox/{second.id}/book-statement", data=data)
    assert r.status_code == 422 and "already booked" in r.text


def test_reverse_charge_text_blocks_booking(client, session, admin):
    html = thuisbezorgd_statement_html() + "<p>BTW verlegd</p>"
    doc = received(client, session, html)
    r = client.post(f"/inbox/{doc.id}/book-statement", data=split_form(
        client, [("V9", "1000,00")], tips_account=str(account(session, admin, "8900").id)))
    assert r.status_code == 422 and "reverse-charged" in r.text


def test_payout_is_offered_on_the_bank_line_and_clears_the_account(client, session, admin):
    doc = received(client, session, uber_statement_html())
    adj = account(session, admin, "4600")
    client.post(f"/inbox/{doc.id}/book-statement", data=split_form(
        client, [("V9", "402,50")], adjustments_account=str(adj.id)))
    session.expire_all()
    st = platform_booking.statement_of(session.get(InboxDocument, doc.id))
    bank = banking.create_bank_account(session, admin, "NL91ABNA0417164300", "Zakelijk")
    stmt = BankStatement(bank_account_id=bank.id, file_name="x",
                         file_sha256="0" * 64, file_format="csv")
    session.add(stmt)
    session.flush()
    tx = BankTransaction(bank_account_id=bank.id,
                         statement_id=stmt.id, booking_date=date(2026, 5, 13),
                         amount_cents=st.payout_cents, dedupe_key="k1",
                         status=TxStatus.UNMATCHED)
    other = BankTransaction(bank_account_id=bank.id,
                            statement_id=stmt.id, booking_date=date(2026, 5, 13),
                            amount_cents=st.payout_cents + 1, dedupe_key="k2",
                            status=TxStatus.UNMATCHED)
    session.add_all([tx, other])
    session.commit()
    page = client.get(f"/bank/reconcile?account_id={bank.id}")
    assert page.text.count("Book against the clearing account") == 1
    assert st.label in page.text
    clearing = account(session, admin, "1330")
    r = client.post(f"/bank/transactions/{tx.id}/book", data={
        "csrf_token": client.csrf, "account_id": str(clearing.id), "description": "payout"},
        follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert balance(session, admin, "1330") == 0
    assert balance(session, admin, "1100") == st.payout_cents
