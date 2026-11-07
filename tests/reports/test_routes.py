import json
import re
from datetime import date, timedelta

import pytest

from ledgerline.models import (
    BankAccount,
    BankStatement,
    BankTransaction,
    ContactKind,
    InvoiceStatus,
    SalesInvoice,
)
from ledgerline.money import format_eur
from ledgerline.reports.dashboard import last_months, monthly_series
from tests.helpers import account, contact
from tests.vat.booking import book_purchase, book_sale

TODAY = date.today()


def _chart(page):
    block = re.search(r'<script type="application/json" id="chart-data">(.*?)</script>', page.text)
    return json.loads(block.group(1))


def test_empty_administration_sees_onboarding_checklist(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'data-testid="onboarding"' in page.text
    for step in ("Add your business details", "Add a bank account", "Create your first invoice",
                 "Import a bank statement"):
        assert step in page.text
    for href in ("/settings", "/bank", "/sales"):
        assert f'href="{href}"' in page.text
    assert "chart-data" not in page.text


def test_dashboard_with_data(client, session, admin):
    customer = contact(session, admin, name="Klant BV")
    book_sale(session, admin, "V21", 100000, TODAY)
    book_purchase(session, admin, "I21", 30000, TODAY)
    bank = account(session, admin, "1100")
    from ledgerline.ledger.service import LineSpec, post_entry
    post_entry(session, admin.id, TODAY, "Receipt", [
        LineSpec(bank.id, debit_cents=50000),
        LineSpec(account(session, admin, "1300").id, credit_cents=50000)])
    session.add(SalesInvoice(administration_id=admin.id, contact_id=customer.id,
                             status=InvoiceStatus.OPEN, issue_date=TODAY - timedelta(days=60),
                             due_date=TODAY - timedelta(days=30), total_incl_cents=12100))
    session.add(SalesInvoice(administration_id=admin.id, contact_id=customer.id,
                             status=InvoiceStatus.DRAFT, issue_date=TODAY, due_date=TODAY))
    session.commit()
    page = client.get("/")
    assert page.status_code == 200 and "onboarding" not in page.text
    assert format_eur(50000) in page.text           # bank balance
    assert format_eur(12100) in page.text           # receivables open
    assert "1 overdue" in page.text and "1 draft sales invoice" in page.text
    assert format_eur(70000) in page.text           # result YTD: 100000 - 30000
    data = _chart(page)
    assert len(data["labels"]) == 12 == len(data["revenue"]) == len(data["expenses"])
    assert data["labels"][-1] == f"{TODAY.year}-{TODAY.month:02d}"
    assert (data["revenue"][-1], data["expenses"][-1]) == (100000, 30000)


def test_dashboard_has_no_inline_script_or_style(client, session, admin):
    book_sale(session, admin, "V21", 1000, TODAY)
    session.commit()
    html = client.get("/").text
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert all('src="' in s or 'type="application/json"' in s for s in scripts)
    assert " style=" not in html


def test_dashboard_counts_unmatched_bank_transactions(client, session, admin):
    book_sale(session, admin, "V21", 1000, TODAY)
    ledger = account(session, admin, "1100")
    bank = BankAccount(administration_id=admin.id, iban="NL91ABNA0417164300", name="Zakelijk",
                       ledger_account_id=ledger.id)
    session.add(bank)
    session.flush()
    statement = BankStatement(bank_account_id=bank.id, file_name="a.xml", file_sha256="0" * 64,
                              file_format="camt053")
    session.add(statement)
    session.flush()
    for n, status in enumerate(("unmatched", "unmatched", "matched")):
        session.add(BankTransaction(bank_account_id=bank.id, statement_id=statement.id,
                                    booking_date=TODAY, amount_cents=100, dedupe_key=str(n),
                                    status=status))
    session.commit()
    assert "2 unmatched bank transactions" in client.get("/").text


def test_last_months_spans_year_boundary():
    assert last_months(date(2026, 2, 15), 4) == [(2025, 10), (2025, 11), (2026, 0), (2026, 1)]


def test_monthly_series_buckets_by_month(session, admin):
    book_sale(session, admin, "V21", 1000, date(2026, 5, 31))
    book_sale(session, admin, "V21", 2000, date(2026, 6, 1))
    book_purchase(session, admin, "I21", 400, date(2026, 6, 15))
    book_sale(session, admin, "V21", 9000, date(2025, 5, 31))   # outside the 12 months
    session.commit()
    series = monthly_series(session, admin.id, date(2026, 6, 30))
    assert series[0]["label"] == "2025-07" and series[-1]["label"] == "2026-06"
    by_label = {m["label"]: (m["revenue"], m["expenses"]) for m in series}
    assert by_label["2026-05"] == (1000, 0) and by_label["2026-06"] == (2000, 400)
    assert sum(m["revenue"] for m in series) == 3000


# ---- report pages

@pytest.mark.parametrize("url", [
    "/reports", "/reports/pnl", "/reports/balance", "/reports/trial",
    "/reports/aging/receivables", "/reports/aging/payables",
])
def test_report_pages_render(client, url):
    assert client.get(url).status_code == 200


def test_unknown_aging_kind_and_bad_date(client):
    assert client.get("/reports/aging/other").status_code == 404
    assert client.get("/reports/pnl?start=nope").status_code == 422


def test_pnl_page_and_csv(client, session, admin):
    book_sale(session, admin, "V21", 123456, date(2026, 2, 1))
    book_purchase(session, admin, "I21", 3000, date(2026, 2, 5))
    session.commit()
    url = "/reports/pnl?start=2026-01-01&end=2026-12-31"
    page = client.get(url).text
    assert format_eur(120456) in page and "Revenue 21%" in page
    csv = client.get(url + "&export=csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert 'filename="profit-and-loss-2026-01-01-2026-12-31.csv"' in csv.headers[
        "content-disposition"]
    lines = csv.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "Section,Code,Account,Amount"
    assert "Revenue,8000,Revenue 21%,1234.56" in lines
    assert "Expenses,4200,Office supplies,30.00" in lines
    assert lines[-1] == "Result,,,1204.56"
    dutch = client.get(url + "&export=csv&comma=1").content.decode("utf-8-sig").splitlines()
    assert dutch[0] == "Section;Code;Account;Amount" and dutch[-1] == "Result;;;1204,56"


def test_balance_page_shows_check_and_csv_totals(client, session, admin):
    book_sale(session, admin, "V21", 10000, date(2026, 2, 1))
    session.commit()
    page = client.get("/reports/balance?end=2026-06-30").text
    assert "&#10003; Assets = Liabilities + Equity" in page
    assert "Result of the year to date" in page
    lines = client.get("/reports/balance?end=2026-06-30&export=csv").content.decode(
        "utf-8-sig").splitlines()
    assert "Assets,,Total,121.00" in lines
    assert "Liabilities,,Total,21.00" in lines
    assert "Equity,,Total,100.00" in lines


def test_trial_balance_csv_has_equal_totals(client, session, admin):
    book_sale(session, admin, "V21", 10000, date(2026, 2, 1))
    session.commit()
    lines = client.get("/reports/trial?end=2026-12-31&export=csv").content.decode(
        "utf-8-sig").splitlines()
    assert lines[-1] == ",Total,121.00,121.00"


def test_aging_page_and_csv(client, session, admin):
    customer = contact(session, admin, name="Late Payer", kind=ContactKind.CUSTOMER)
    session.add(SalesInvoice(administration_id=admin.id, contact_id=customer.id,
                             status=InvoiceStatus.OPEN, issue_date=date(2026, 1, 1),
                             due_date=date(2026, 4, 1), total_incl_cents=12100,
                             paid_cents=2100))
    session.commit()
    url = "/reports/aging/receivables?end=2026-06-30"
    assert "Late Payer" in client.get(url).text
    lines = client.get(url + "&export=csv").content.decode("utf-8-sig").splitlines()
    assert lines[0] == "Contact,Not yet due,1-30,31-60,61-90,Over 90,Total"
    assert lines[1] == "Late Payer,0.00,0.00,0.00,100.00,0.00,100.00"
    assert lines[-1] == "Total,0.00,0.00,0.00,100.00,0.00,100.00"


def test_reports_are_scoped_to_the_current_administration(client, session, admin):
    from ledgerline.ledger.chart import create_administration
    other = create_administration(session, name="Ander BV")
    book_sale(session, admin, "V21", 10000, date(2026, 2, 1))
    book_sale(session, other, "V21", 77700, date(2026, 2, 1))
    session.commit()
    page = client.get("/reports/pnl?start=2026-01-01&end=2026-12-31").text
    assert format_eur(10000) in page and format_eur(77700) not in page
