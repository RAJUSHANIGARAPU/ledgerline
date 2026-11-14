from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline import cli
from ledgerline.banking import matching, service
from ledgerline.banking.service import Allocation
from ledgerline.imports import opening
from ledgerline.invoicing.service import InvoiceError, create_credit_note
from ledgerline.ledger.service import account_balance_cents
from ledgerline.models import (
    Account,
    AuditEvent,
    BankAccount,
    BankStatement,
    BankTransaction,
    ContactKind,
    InvoiceStatus,
    JournalEntry,
    PurchaseInvoice,
    SalesInvoice,
    TxStatus,
)
from ledgerline.reports import financial
from ledgerline.reports.aging import aging, totals
from ledgerline.vat.icp import icp_overview
from ledgerline.vat.vat_return import compute_vat_return
from tests.conftest import tenant_db
from tests.helpers import account, contact
from tests.vat.booking import book_purchase, book_sale

START = date(2026, 10, 1)
Q4 = (START, date(2026, 12, 31))
HEADER = "type;number;contact;contact_vat_number;invoice_date;due_date;total;open_amount"


@pytest.fixture
def customer(session, admin):
    c = contact(session, admin, name="Klant BV", vat_number="NL123456782B01",
                iban="NL02ABNA0123456789")
    session.commit()
    return c


@pytest.fixture
def supplier(session, admin):
    c = contact(session, admin, name="Leverancier BV", kind=ContactKind.SUPPLIER)
    session.commit()
    return c


def rows_of(session, admin, text, start=START):
    rows, _, _ = opening.plan_csv(session, admin, text.encode(), start)
    return rows


def add(session, admin, owner, text, start=START):
    rows = rows_of(session, admin, text, start)
    created = opening.apply_items(session, admin, rows, start, owner.id, "items.csv")
    session.commit()
    return created


SALE = "sales;2026-0412;Klant BV;;2026-09-14;2026-10-14;1.210,00;1.210,00"
PART = "purchase;INV-88231;Leverancier BV;;30-09-2026;30-10-2026;605,00;300,00"


def sale_of(session, number="2026-0412"):
    session.expire_all()
    return session.scalar(select(SalesInvoice).where(SalesInvoice.number == number))


def bank_tx(session, admin, cents, description, name=None, iban=None):
    bank = service.create_bank_account(session, admin, "NL91ABNA0417164300", "Zakelijk")
    statement = BankStatement(bank_account_id=bank.id, file_name="s.xml", file_sha256="0" * 64,
                              file_format="camt053")
    session.add(statement)
    session.flush()
    tx = BankTransaction(bank_account_id=bank.id, statement_id=statement.id,
                         booking_date=date(2026, 10, 20), amount_cents=cents,
                         description=description, counterparty_name=name, counterparty_iban=iban,
                         dedupe_key=f"k{cents}{description}", status=TxStatus.UNMATCHED)
    session.add(tx)
    session.flush()
    return bank, tx


def test_sales_item_posts_debtors_against_opening_equity_with_the_contact(
        session, admin, owner, customer):
    assert add(session, admin, owner, f"{HEADER}\n{SALE}\n") == 1
    invoice = sale_of(session)
    assert (invoice.is_opening, invoice.status, invoice.total_incl_cents, invoice.paid_cents,
            invoice.issue_date, invoice.due_date) == (
        True, InvoiceStatus.OPEN, 121000, 0, date(2026, 9, 14), date(2026, 10, 14))
    entry = session.get(JournalEntry, invoice.journal_entry_id)
    assert (entry.entry_date, entry.source_type, entry.source_id) == (
        START, "opening_item", invoice.id)
    got = {(line.account_id, line.debit_cents, line.credit_cents, line.contact_id,
            line.vat_code, line.vat_role) for line in entry.lines}
    counter = account(session, admin, "0580")
    assert counter.type == "equity" and counter.name == "Opening balance, open items"
    assert got == {(account(session, admin, "1300").id, 121000, 0, customer.id, None, None),
                   (counter.id, 0, 121000, None, None, None)}


def test_purchase_item_posts_creditors_and_records_what_was_already_paid(
        session, admin, owner, supplier):
    add(session, admin, owner, f"{HEADER}\n{PART}\n")
    invoice = session.scalar(select(PurchaseInvoice))
    assert (invoice.supplier_reference, invoice.total_incl_cents, invoice.paid_cents,
            invoice.status, invoice.is_opening) == ("INV-88231", 60500, 30500,
                                                    InvoiceStatus.OPEN, True)
    entry = session.get(JournalEntry, invoice.journal_entry_id)
    got = {(line.account_id, line.debit_cents, line.credit_cents, line.contact_id)
           for line in entry.lines}
    assert got == {(account(session, admin, "0580").id, 30000, 0, None),
                   (account(session, admin, "1600").id, 0, 30000, supplier.id)}


def test_no_revenue_expense_or_vat_is_created_in_the_new_period(
        session, admin, owner, customer, supplier):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n{PART}\n")
    book_sale(session, admin, "V21", 10000, date(2026, 11, 5), customer.id)
    book_purchase(session, admin, "I21", 5000, date(2026, 11, 6))
    session.commit()

    vat = compute_vat_return(session, admin.id, *Q4)
    assert vat.line("1a").base_cents == 10000 and vat.line("1a").vat_cents == 2100
    assert (vat.owed_cents, vat.deductible_cents) == (2100, 1050)
    assert sum(line.base_cents for line in vat.lines) == 10000, "no base from opening items"
    assert icp_overview(session, admin.id, *Q4) == []

    pnl = financial.profit_and_loss(session, admin.id, *Q4)
    assert (pnl.revenue.total_cents, pnl.expenses.total_cents) == (10000, 5000)
    on_start = financial.profit_and_loss(session, admin.id, START, START)
    assert on_start.revenue.total_cents == on_start.expenses.total_cents == 0
    before = compute_vat_return(session, admin.id, date(2026, 7, 1), date(2026, 9, 30))
    assert before.owed_cents == before.deductible_cents == 0


def test_vat_return_is_zero_when_only_opening_items_exist(session, admin, owner, customer,
                                                          supplier):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n{PART}\n")
    vat = compute_vat_return(session, admin.id, *Q4)
    assert (vat.owed_cents, vat.deductible_cents) == (0, 0)
    assert all(line.base_cents == line.vat_cents == 0 for line in vat.lines)


def test_aging_lists_opening_items_by_due_date_and_contact(session, admin, owner, customer,
                                                           supplier):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n{PART}\n")
    receivables = aging(session, admin.id, "receivables", date(2026, 11, 5))
    assert [(r.name, r.buckets, r.invoices) for r in receivables] == [
        ("Klant BV", [0, 121000, 0, 0, 0], 1)]
    payables = aging(session, admin.id, "payables", date(2026, 10, 1))
    assert totals(payables) == [30000, 0, 0, 0, 0], "only the open part, not the 605,00 total"
    assert totals(aging(session, admin.id, "payables", date(2027, 3, 1))) == [0, 0, 0, 0, 30000]


def test_bank_receipt_finds_the_opening_invoice_and_pays_it(session, admin, owner, customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    other = SalesInvoice(administration_id=admin.id, contact_id=customer.id, number="2026-9999",
                         status=InvoiceStatus.OPEN, issue_date=date(2026, 10, 2),
                         due_date=date(2026, 11, 1), total_incl_cents=50000)
    session.add(other)
    bank, tx = bank_tx(session, admin, 121000, "Betaling factuur 2026-0412", "Klant BV",
                       "NL02ABNA0123456789")
    best = service.suggest(session, tx)[0]
    assert (best.candidate.number, best.exact_amount, best.number_hit) == ("2026-0412", True, True)
    assert "IBAN matches contact" in best.reason

    entry = service.match(session, tx, [Allocation(matching.SALES, best.candidate.invoice_id,
                                                   121000)], owner.id)
    session.commit()
    invoice = sale_of(session)
    assert (invoice.status, invoice.paid_cents) == (InvoiceStatus.PAID, 121000)
    got = {(line.account_id, line.debit_cents, line.credit_cents, line.contact_id)
           for line in entry.lines}
    assert got == {(bank.ledger_account_id, 121000, 0, None),
                   (account(session, admin, "1300").id, 0, 121000, customer.id)}
    debtors = account(session, admin, "1300")
    assert account_balance_cents(session, debtors.id) == 0
    assert aging(session, admin.id, "receivables", date(2026, 11, 20))[0].total_cents == 50000


def test_partly_paid_purchase_item_is_matched_for_the_open_amount(session, admin, owner,
                                                                  supplier):
    add(session, admin, owner, f"{HEADER}\n{PART}\n")
    _, tx = bank_tx(session, admin, -30000, "INV-88231 Leverancier BV", "Leverancier BV")
    best = service.suggest(session, tx)[0]
    assert (best.candidate.number, best.candidate.remaining_cents, best.exact_amount) == (
        "INV-88231", 30000, True)
    service.match(session, tx, [Allocation(matching.PURCHASE, best.candidate.invoice_id, 30000)])
    session.commit()
    session.expire_all()
    invoice = session.scalar(select(PurchaseInvoice))
    assert (invoice.status, invoice.paid_cents) == (InvoiceStatus.PAID, 60500)


def test_unmatching_a_payment_reopens_the_opening_item(session, admin, owner, customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    _, tx = bank_tx(session, admin, 121000, "2026-0412")
    service.match(session, tx, [Allocation(matching.SALES, sale_of(session).id, 121000)])
    session.commit()
    service.undo(session, tx, owner.id)
    session.commit()
    invoice = sale_of(session)
    assert (invoice.status, invoice.paid_cents) == (InvoiceStatus.OPEN, 0)


def test_opening_items_cannot_go_through_the_normal_invoice_flow(client, session, admin, owner,
                                                                 customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    invoice = sale_of(session)
    with pytest.raises(InvoiceError, match="opening item"):
        create_credit_note(session, admin, invoice, owner.id)
    for url in (f"/sales/{invoice.id}/pdf", f"/sales/{invoice.id}/ubl"):
        assert client.get(url).status_code == 409
    page = client.get(f"/sales/{invoice.id}")
    assert page.status_code == 200 and 'data-testid="opening-note"' in page.text
    assert "/pdf" not in page.text and "Credit note</button>" not in page.text
    assert "2026-0412" in client.get("/sales").text and "opening" in client.get("/sales").text
    r = client.post(f"/sales/{invoice.id}/credit", data={"csrf_token": client.csrf},
                    follow_redirects=True)
    assert "cannot be credited" in r.text
    r = client.post(f"/sales/{invoice.id}/save", data={"csrf_token": client.csrf},
                    follow_redirects=True)
    assert "cannot be edited" in r.text
    assert sale_of(session).status == InvoiceStatus.OPEN


def test_purchase_detail_page_renders_without_lines(client, session, admin, owner, supplier):
    add(session, admin, owner, f"{HEADER}\n{PART}\n")
    invoice = session.scalar(select(PurchaseInvoice))
    page = client.get(f"/purchases/{invoice.id}")
    assert page.status_code == 200 and 'data-testid="opening-note"' in page.text
    assert "INV-88231" in page.text


def test_number_sequence_is_not_consumed_and_new_invoices_still_number_normally(
        session, admin, owner, customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    seq = session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "sales_invoice.issue"))
    assert seq == 0


@pytest.mark.parametrize("row, message", [
    ("loans;X1;Klant BV;;2026-09-01;2026-10-01;1,00;1,00", "type must be sales or purchase"),
    ("sales;;Klant BV;;2026-09-01;2026-10-01;1,00;1,00", "invoice number is empty"),
    ("sales;X1;Onbekend;;2026-09-01;2026-10-01;1,00;1,00", "unknown contact"),
    ("sales;X1;;NL999999999B99;2026-09-01;2026-10-01;1,00;1,00", "unknown contact"),
    ("sales;X1;Klant BV;NL1;2026-09-01;2026-10-01;1,00;1,00", "contact VAT number"),
    ("purchase;X1;Klant BV;;2026-09-01;2026-10-01;1,00;1,00", "not a supplier"),
    ("sales;X1;Klant BV;;2026-10-02;2026-11-01;1,00;1,00", "after the start date"),
    ("sales;X1;Klant BV;;2026-09-01;2026-08-01;1,00;1,00", "due date is before"),
    ("sales;X1;Klant BV;;01-13-2026;2026-10-01;1,00;1,00", "invoice_date: not a valid date"),
    ("sales;X1;Klant BV;;2026-09-01;;1,00;1,00", "due_date: not a valid date"),
    ("sales;X1;Klant BV;;2026-09-01;2026-10-01;0;0", "total must be more than zero"),
    ("sales;X1;Klant BV;;2026-09-01;2026-10-01;1.234;1.234", "ambiguous"),
    ("sales;X1;Klant BV;;2026-09-01;2026-10-01;10,00;0", "leave paid invoices out"),
    ("sales;X1;Klant BV;;2026-09-01;2026-10-01;10,00;10,01", "more than the total"),
    ("sales;" + "9" * 31 + ";Klant BV;;2026-09-01;2026-10-01;1,00;1,00", "longer than 30"),
])
def test_row_validation(session, admin, customer, supplier, row, message):
    (item,) = rows_of(session, admin, f"{HEADER}\n{row}\n")
    assert item.action == "error" and message in item.reason


def test_empty_open_amount_defaults_to_total_with_a_warning(session, admin, customer):
    (item,) = rows_of(session, admin, f"{HEADER}\nsales;X1;Klant BV;;2026-09-01;2026-10-01;"
                                      "10,00;\n")
    assert item.action == "create" and item.open_cents == 1000
    assert "full total is taken as open" in item.warnings[0]


def test_contact_is_found_by_vat_number_and_name_case_insensitively(session, admin, customer):
    a, b = rows_of(session, admin, f"{HEADER}\nsales;A1;;NL 123456782 B01;2026-09-01;"
                                   "2026-10-01;1,00;1,00\nsales;A2;klant bv;;2026-09-01;"
                                   "2026-10-01;1,00;1,00\n")
    assert a.contact.id == b.contact.id == customer.id and a.action == b.action == "create"


def test_ambiguous_contact_name_is_refused_unless_vat_number_picks_one(session, admin, customer):
    contact(session, admin, name="Klant BV", vat_number="NL000000000B01")
    session.commit()
    a, b = rows_of(session, admin, f"{HEADER}\nsales;A1;Klant BV;;2026-09-01;2026-10-01;1,00;"
                                   "1,00\nsales;A2;Klant BV;NL000000000B01;2026-09-01;"
                                   "2026-10-01;1,00;1,00\n")
    assert a.action == "error" and "more than one contact" in a.reason
    assert b.action == "create"


def test_anonymised_contacts_are_not_selectable(session, admin):
    from datetime import UTC, datetime

    contact(session, admin, name="Weg", anonymised_at=datetime.now(UTC))
    session.commit()
    (item,) = rows_of(session, admin, f"{HEADER}\nsales;A1;Weg;;2026-09-01;2026-10-01;1,00;1,00\n")
    assert item.action == "error"


def test_duplicates_in_file_and_in_books(session, admin, owner, customer, supplier):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    again, clash, twin_a, twin_b, other_supplier = rows_of(
        session, admin,
        f"{HEADER}\n{SALE}\n"
        "sales;2026-0412;Klant BV;;2026-09-14;2026-10-14;99,00;99,00\n"
        "sales;N-1;Klant BV;;2026-09-01;2026-10-01;1,00;1,00\n"
        "sales;n-1;Klant BV;;2026-09-01;2026-10-01;1,00;1,00\n"
        "purchase;N-1;Leverancier BV;;2026-09-01;2026-10-01;1,00;1,00\n")
    assert (again.action, again.reason) == ("skip", "already imported as an opening item")
    assert clash.action == "error" and "already exists in the books" in clash.reason
    assert twin_a.action == "create" and twin_b.action == "error"
    assert "same invoice number as row 4" in twin_b.reason
    assert other_supplier.action == "create"


def test_a_normal_invoice_with_the_same_number_blocks_the_row(session, admin, customer):
    session.add(SalesInvoice(administration_id=admin.id, contact_id=customer.id, number="2026-0412",
                             status=InvoiceStatus.OPEN, issue_date=date(2026, 10, 3),
                             due_date=date(2026, 11, 3), total_incl_cents=121000))
    session.commit()
    (item,) = rows_of(session, admin, f"{HEADER}\n{SALE}\n")
    assert item.action == "error"


def test_reimport_is_idempotent_and_adds_only_new_rows(session, admin, owner, customer, supplier):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    assert add(session, admin, owner, f"{HEADER}\n{SALE}\n{PART}\n") == 1
    assert session.scalar(select(func.count()).select_from(SalesInvoice)) == 1
    assert session.scalar(select(func.count()).select_from(JournalEntry).where(
        JournalEntry.source_type == "opening_item")) == 2


def test_any_error_blocks_the_whole_import(session, admin, owner, customer):
    rows = rows_of(session, admin, f"{HEADER}\n{SALE}\nsales;X;Nobody;;2026-09-01;"
                                   "2026-10-01;1,00;1,00\n")
    with pytest.raises(opening.OpeningError, match="all together or not at all"):
        opening.apply_items(session, admin, rows, START, owner.id)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(SalesInvoice)) == 0


def test_closed_period_refuses_the_item_and_leaves_nothing_behind(session, admin, owner, customer):
    admin.locked_until = date(2026, 10, 15)
    session.commit()
    rows = rows_of(session, admin, f"{HEADER}\n{SALE}\n")
    with pytest.raises(opening.OpeningError, match="period is closed"):
        opening.apply_items(session, admin, rows, START, owner.id)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(SalesInvoice)) == 0
    assert session.scalar(select(func.count()).select_from(Account).where(
        Account.code == "0580")) == 0


def test_remove_reverses_the_entry_and_frees_matching_and_aging(session, admin, owner, customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    invoice = sale_of(session)
    opening.remove_item(session, admin, "sales", invoice.id, owner.id)
    session.commit()
    assert sale_of(session).status == InvoiceStatus.CANCELLED
    assert matching.load_candidates(session, admin.id) == []
    assert aging(session, admin.id, "receivables", date(2026, 11, 1)) == []
    assert account_balance_cents(session, account(session, admin, "1300").id) == 0
    assert account_balance_cents(session, account(session, admin, "0580").id) == 0
    with pytest.raises(opening.OpeningError, match="no longer open"):
        opening.remove_item(session, admin, "sales", invoice.id, owner.id)


def test_remove_is_refused_after_a_payment_was_matched(session, admin, owner, customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    _, tx = bank_tx(session, admin, 50000, "deel 2026-0412")
    service.match(session, tx, [Allocation(matching.SALES, sale_of(session).id, 50000)])
    session.commit()
    with pytest.raises(opening.OpeningError, match="unmatch"):
        opening.remove_item(session, admin, "sales", sale_of(session).id, owner.id)


def test_remove_never_touches_a_normal_invoice(session, admin, owner, customer):
    normal = SalesInvoice(administration_id=admin.id, contact_id=customer.id, number="N-1",
                          status=InvoiceStatus.OPEN, issue_date=START, due_date=START,
                          total_incl_cents=100)
    session.add(normal)
    session.commit()
    with pytest.raises(opening.OpeningError):
        opening.remove_item(session, admin, "sales", normal.id, owner.id)


def test_everything_is_audited(session, admin, owner, customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    events = {e.action: e for e in session.scalars(select(AuditEvent)).all()
              if e.action.startswith("opening_item")}
    assert events["opening_item.create"].details == {
        "kind": "sales", "number": "2026-0412", "total_cents": 121000, "open_cents": 121000}
    assert events["opening_item.import"].details["start_date"] == "2026-10-01"
    assert events["opening_item.import"].user_id == owner.id


def test_start_date_is_fixed_by_the_first_posting(session, admin, owner, customer):
    assert opening.fixed_start_date(session, admin) is None
    with pytest.raises(opening.OpeningError, match="Enter the start date"):
        opening.resolve_start_date(session, admin, "")
    assert opening.resolve_start_date(session, admin, "01-10-2026") == START
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    assert opening.resolve_start_date(session, admin, "") == START
    with pytest.raises(opening.OpeningError, match="already 2026-10-01"):
        opening.resolve_start_date(session, admin, "2026-07-01")


def test_default_start_is_the_first_day_of_the_quarter():
    assert opening.default_start_date(date(2026, 11, 10)) == date(2026, 10, 1)
    assert opening.default_start_date(date(2026, 1, 31)) == date(2026, 1, 1)
    assert opening.default_start_date(date(2026, 6, 30)) == date(2026, 4, 1)


def test_summary_and_balance_check_with_bank_opening_and_items(client, session, admin, owner,
                                                               customer, supplier):
    client.post("/setup/bank", data={"csrf_token": client.csrf, "name": "Zakelijk",
                                     "iban": "NL91ABNA0417164300"})
    bank = session.scalar(select(BankAccount))
    client.post("/setup/opening", data={"csrf_token": client.csrf, "entry_date": "2026-10-01",
                                        f"balance_{bank.id}": "5.000,00"})
    add(session, admin, owner, f"{HEADER}\n{SALE}\n{PART}\n")
    s = opening.summary(session, admin)
    assert (s.start, s.receivable_cents, s.payable_cents, s.net_cents) == (
        START, 121000, 30000, 91000)
    assert s.check["balanced"] and s.check["date"] == START
    assert (s.check["assets"], s.check["liabilities"], s.check["equity"]) == (
        500000 + 121000, 30000, 500000 + 91000)
    page = client.get("/setup/opening-items")
    assert page.status_code == 200
    check = " ".join(page.text.split()).replace("\xa0", " ")
    assert "Assets = Liabilities + Equity on 01-10-2026: € 6.210,00 = € 300,00 + € 5.910,00 " \
        "(balanced)" in check
    assert "receivables € 1.210,00, payables € 300,00" in check
    assert "2026-0412" in page.text and "INV-88231" in page.text
    sheet = financial.balance_sheet(session, admin.id, START)
    rows = {r.account.code: r.balance_cents for r in sheet.equity.rows}
    assert rows == {"0500": 500000, "0580": 91000}


def test_bank_opening_must_share_the_start_date_of_the_items(client, session, admin, owner,
                                                             customer):
    client.post("/setup/bank", data={"csrf_token": client.csrf, "name": "Zakelijk",
                                     "iban": "NL91ABNA0417164300"})
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    bank = session.scalar(select(BankAccount))
    r = client.post("/setup/opening", data={"csrf_token": client.csrf, "entry_date": "2026-07-01",
                                            f"balance_{bank.id}": "10,00"})
    assert r.status_code == 422 and "same start date" in r.text


def test_wizard_has_the_step_and_it_does_not_count_as_a_first_invoice(client, session, admin,
                                                                      owner, customer):
    page = client.get("/setup")
    assert "Open invoices" in page.text
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    from ledgerline.onboarding import service as wizard
    session.expire_all()
    steps = {s.key: s.done for s in wizard.steps(session, admin)}
    assert steps["opening-items"] is True and steps["first-invoice"] is False
    assert [s.key for s in wizard.steps(session, admin)][3:5] == ["opening", "opening-items"]


def test_manual_add_form_creates_an_item_and_shows_errors(client, session, admin, customer):
    form = {"csrf_token": client.csrf, "start_date": "2026-10-01", "type": "sales",
            "number": "M-1", "contact": "Klant BV", "invoice_date": "2026-09-01",
            "due_date": "2026-10-01", "total": "121,00", "open_amount": "100,00"}
    r = client.post("/setup/opening-items/add", data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup/opening-items"
    invoice = sale_of(session, "M-1")
    assert (invoice.total_incl_cents, invoice.paid_cents) == (12100, 2100)
    dup = client.post("/setup/opening-items/add", data=form)
    assert dup.status_code == 422 and "Already imported" in dup.text
    bad = client.post("/setup/opening-items/add", data={**form, "number": "M-2", "contact": "?"})
    assert bad.status_code == 422 and "unknown contact" in bad.text
    wrong_date = client.post("/setup/opening-items/add", data={**form, "number": "M-3",
                                                               "start_date": "2026-07-01"})
    assert wrong_date.status_code == 422 and "already 2026-10-01" in wrong_date.text
    assert session.scalar(select(func.count()).select_from(SalesInvoice)) == 1


def test_csv_import_through_the_ui_preview_then_confirm(client, session, admin, customer, supplier):
    text = f"﻿{HEADER}\n{SALE}\n{PART}\n"
    files = {"file": ("open.csv", text.encode("utf-8"), "text/csv")}
    pre = client.post("/setup/opening-items/preview", files=files,
                      data={"csrf_token": client.csrf, "start_date": "2026-10-01"})
    assert pre.status_code == 200 and pre.text.count('data-action="create"') == 2
    assert "Post 2 opening items" in pre.text
    assert session.scalar(select(func.count()).select_from(SalesInvoice)) == 0
    done = client.post("/setup/opening-items/confirm", follow_redirects=False, data={
        "csrf_token": client.csrf, "payload": text.lstrip("﻿"), "filename": "open.csv",
        "start_date": "2026-10-01"})
    assert done.status_code == 303
    assert sale_of(session).is_opening and session.scalar(select(func.count()).select_from(
        PurchaseInvoice)) == 1


def test_csv_preview_with_errors_offers_no_confirm_button(client, session, admin, customer):
    text = f"{HEADER}\n{SALE}\nsales;Z;Niemand;;2026-09-01;2026-10-01;1,00;1,00\n"
    pre = client.post("/setup/opening-items/preview", data={
        "csrf_token": client.csrf, "start_date": "2026-10-01"},
        files={"file": ("o.csv", text.encode(), "text/csv")})
    assert 'data-action="error"' in pre.text and "unknown contact" in pre.text
    assert 'data-testid="import-confirm"' not in pre.text
    forced = client.post("/setup/opening-items/confirm", data={
        "csrf_token": client.csrf, "payload": text, "start_date": "2026-10-01"},
        follow_redirects=True)
    assert "all together or not at all" in forced.text
    assert session.scalar(select(func.count()).select_from(SalesInvoice)) == 0


def test_csv_without_a_start_date_or_file_returns_to_the_step_with_a_message(client, admin):
    r = client.post("/setup/opening-items/preview", data={"csrf_token": client.csrf,
                                                          "start_date": ""})
    assert r.status_code == 422 and "Enter the start date" in r.text
    r = client.post("/setup/opening-items/preview", data={"csrf_token": client.csrf,
                                                          "start_date": "2026-10-01"})
    assert r.status_code == 422 and "Choose a CSV file" in r.text


def test_remove_route_only_accepts_opening_items(client, session, admin, owner, customer):
    add(session, admin, owner, f"{HEADER}\n{SALE}\n")
    invoice = sale_of(session)
    normal = SalesInvoice(administration_id=admin.id, contact_id=customer.id, number="N-1",
                          status=InvoiceStatus.OPEN, issue_date=START, due_date=START,
                          total_incl_cents=100)
    session.add(normal)
    session.commit()
    for url in (f"/sales/{normal.id}/opening-remove", "/sales/99999/opening-remove",
                f"/purchases/{invoice.id}/opening-remove"):
        assert client.post(url, data={"csrf_token": client.csrf}).status_code == 404, url
    r = client.post(f"/sales/{invoice.id}/opening-remove", data={"csrf_token": client.csrf},
                    follow_redirects=True)
    assert "Opening item removed" in r.text and sale_of(session).status == "cancelled"
    again = client.post(f"/sales/{invoice.id}/opening-remove", data={"csrf_token": client.csrf},
                        follow_redirects=True)
    assert "no longer open" in again.text


def test_templates_download_and_pass_their_own_validation(client, session, admin):
    for sep in ("semicolon", "comma"):
        body = client.get(f"/setup/opening-items/template.csv?sep={sep}").content
        text = body.decode("utf-8-sig")
        assert text.startswith("type" + (";" if sep == "semicolon" else ",") + "number")
    names = ["Voorbeeld Klant BV", "Voorbeeld Leverancier"]
    session.add_all([
        contact(session, admin, name=names[0]), contact(session, admin, name=names[1],
                                                        kind=ContactKind.SUPPLIER)])
    session.commit()
    rows, _, _ = opening.plan_csv(session, admin, opening.template_csv(), START)
    assert [r.action for r in rows] == ["create", "create"]


def test_write_role_csrf_and_read_only_access(readonly_client, client, session, admin, customer):
    posts = {
        "/setup/opening-items/add": {"start_date": "2026-10-01"},
        "/setup/opening-items/preview": {"start_date": "2026-10-01"},
        "/setup/opening-items/confirm": {"payload": HEADER, "start_date": "2026-10-01"},
        "/sales/1/opening-remove": {},
    }
    for url, data in posts.items():
        assert readonly_client.post(url, data={**data, "csrf_token": readonly_client.csrf}
                                    ).status_code == 403, url
        assert client.post(url, data=data).status_code == 403, f"{url} without CSRF"
    assert readonly_client.get("/setup/opening-items").status_code == 403


def test_bookkeeper_can_add_items(bookkeeper_client, session, admin, customer):
    r = bookkeeper_client.post("/setup/opening-items/add", data={
        "csrf_token": bookkeeper_client.csrf, "start_date": "2026-10-01", "type": "sales",
        "number": "B-1", "contact": "Klant BV", "invoice_date": "2026-09-01",
        "due_date": "2026-10-01", "total": "10,00", "open_amount": "10,00"},
        follow_redirects=False)
    assert r.status_code == 303 and sale_of(session, "B-1") is not None


def test_import_never_touches_or_reads_another_tenant(client, session, admin, owner, customer,
                                                      other_tenant, other_admin):
    with tenant_db(other_tenant.id) as other:
        theirs = contact(other, other_admin, name="Klant BV", vat_number="NL123456782B01")
        other.add(SalesInvoice(administration_id=other_admin.id, contact_id=theirs.id,
                               number="2026-0412", status=InvoiceStatus.OPEN,
                               issue_date=START, due_date=START, total_incl_cents=777))
        other.commit()
        their_invoice_id = other.scalar(select(SalesInvoice.id))
    assert add(session, admin, owner, f"{HEADER}\n{SALE}\n") == 1, (
        "an invoice number used by another tenant is no clash")
    with pytest.raises(opening.OpeningError):
        opening.remove_item(session, admin, "sales", their_invoice_id, owner.id)
    with tenant_db(other_tenant.id) as other:
        invoices = other.scalars(select(SalesInvoice)).all()
        assert [(i.number, i.total_incl_cents, i.is_opening) for i in invoices] == [
            ("2026-0412", 777, False)]
        assert other.scalar(select(func.count()).select_from(JournalEntry)) == 0
        assert other.scalar(select(func.count()).select_from(Account).where(
            Account.code == "0580")) == 0
        assert other.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action.like("opening_item%"))) == 0


def test_contact_of_another_tenant_cannot_be_used(session, admin, owner, other_tenant,
                                                  other_admin):
    with tenant_db(other_tenant.id) as other:
        contact(other, other_admin, name="Buurman BV", vat_number="NL000000000B01")
        other.commit()
    rows = rows_of(session, admin, f"{HEADER}\nsales;A1;Buurman BV;;2026-09-01;2026-10-01;1,00;"
                                   "1,00\nsales;A2;;NL000000000B01;2026-09-01;2026-10-01;1,00;"
                                   "1,00\n")
    assert [r.action for r in rows] == ["error", "error"]


def test_cli_dry_run_then_apply_and_error_exit(session, tenant, admin, customer, supplier, tmp_path,
                                               capsys):
    path = tmp_path / "open.csv"
    path.write_text(f"{HEADER}\n{SALE}\n{PART}\n", encoding="utf-8")
    base = ["import-opening-items", "--file", str(path), "--start-date", "2026-10-01"]
    assert cli.main(base) == 0
    out = capsys.readouterr().out
    assert "2 to create" in out and "Dry run" in out
    assert session.scalar(select(func.count()).select_from(SalesInvoice)) == 0
    assert cli.main([*base, "--apply"]) == 0
    assert "Posted 2 opening items dated 2026-10-01" in capsys.readouterr().out
    assert cli.main(base) == 0
    assert "2 already imported" in capsys.readouterr().out
    path.write_text(f"{HEADER}\nsales;Q;Niemand;;2026-09-01;2026-10-01;1,00;1,00\n")
    assert cli.main(base) == 1
    captured = capsys.readouterr()
    assert "unknown contact" in captured.out and "nothing was saved" in captured.err
    assert cli.main(["import-opening-items", "--file", str(path), "--start-date", "x"]) == 1
    assert "not a valid date" in capsys.readouterr().err


def test_documented_sample_files_import_cleanly_together(session, admin):
    from pathlib import Path

    from ledgerline.imports import contacts as contact_import

    samples = Path(__file__).resolve().parents[2] / "docs" / "samples"
    plans = [contact_import.plan_contacts(session, admin, (samples / name).read_bytes())
             for name in ("contacts-import.csv", "contacts-import-excel-nl.csv")]
    assert [p.delimiter for p in plans] == [";", ","]
    for plan in plans:
        assert all(r.action == "create" and not r.errors for r in plan.rows), [
            r.reason for r in plan.rows]
        contact_import.apply_plan(session, admin, plan, None)
    session.commit()
    names = {r.data["name"] for p in plans for r in p.rows}
    assert "Jansen, Zn. & Dochters BV" in names and "Café 't Hoekje" in names
    rows, _, delimiter = opening.plan_csv(
        session, admin, (samples / "opening-items.csv").read_bytes(), START)
    assert delimiter == ";" and [r.action for r in rows] == ["create"] * 5
    assert sum(r.open_cents for r in rows if r.kind == "sales") == 121000 + 40000 + 12100
