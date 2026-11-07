from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline.banking import matching, service
from ledgerline.banking.service import Allocation, BankingError
from ledgerline.ledger.service import account_balance_cents
from ledgerline.models import (
    BankStatement,
    BankTransaction,
    ContactKind,
    InvoiceStatus,
    JournalEntry,
    PaymentAllocation,
    TxStatus,
)
from tests.helpers import account, contact

from .conftest import fixture_bytes, purchase_invoice, sales_invoice

SUPPLIER_IBAN = "NL86INGB0002445588"


def load(session, bank, name="camt053_02.xml"):
    result = service.import_statement(session, bank, name, fixture_bytes(name), None)
    session.commit()
    return result


def tx_by_amount(session, cents):
    return session.scalar(select(BankTransaction).where(BankTransaction.amount_cents == cents))


def entry_totals(entry):
    debit = sum(line.debit_cents for line in entry.lines)
    return debit, sum(line.credit_cents for line in entry.lines)


def test_import_creates_unmatched_transactions_and_posts_nothing(session, bank):
    result = load(session, bank)
    assert (result.created, result.skipped, result.warnings) == (2, 0, [])
    txs = session.scalars(select(BankTransaction).order_by(BankTransaction.id)).all()
    assert [(t.amount_cents, t.status) for t in txs] == [
        (12_100, TxStatus.UNMATCHED), (-5_000, TxStatus.UNMATCHED),
    ]
    assert session.scalar(select(func.count()).select_from(JournalEntry)) == 0


def test_same_file_cannot_be_imported_twice(session, bank):
    load(session, bank)
    with pytest.raises(BankingError, match="already been imported"):
        service.import_statement(session, bank, "again.xml", fixture_bytes("camt053_02.xml"))


def test_overlapping_files_do_not_duplicate_transactions(session, bank):
    load(session, bank)
    result = load(session, bank, "overlap.xml")
    assert (result.created, result.skipped) == (1, 1)
    assert session.scalar(select(func.count()).select_from(BankTransaction)) == 3
    assert session.scalar(select(func.count()).select_from(BankStatement)) == 2


def test_identical_lines_within_one_file_are_kept(session, bank):
    row = (
        b'<Ntry><Amt Ccy="EUR">5.00</Amt><CdtDbtInd>DBIT</CdtDbtInd>'
        b"<BookgDt><Dt>2026-06-03</Dt></BookgDt><NtryDtls><TxDtls>"
        b"<RmtInf><Ustrd>Parkeren</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>"
    )
    content = fixture_bytes("camt053_02.xml").replace(b"</Stmt>", row + row + b"</Stmt>")
    result = service.import_statement(session, bank, "dup.xml", content)
    assert result.created == 4
    again = service.import_statement(session, bank, "dup2.xml", content + b"\n")
    assert (again.created, again.skipped) == (0, 4)


def test_statement_for_another_iban_is_refused(session, admin, bank):
    other = service.create_bank_account(session, admin, SUPPLIER_IBAN, "Spaar")
    with pytest.raises(BankingError, match="not for"):
        service.import_statement(session, other, "x.xml", fixture_bytes("camt053_02.xml"))


def test_balance_mismatch_is_reported_as_warning(session, bank):
    content = fixture_bytes("camt053_02.xml").replace(b"1071.00", b"1099.00")
    result = service.import_statement(session, bank, "x.xml", content)
    assert result.created == 2
    assert len(result.warnings) == 1


def test_oversized_upload_is_refused(session, bank, monkeypatch):
    from ledgerline.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 10)
    with pytest.raises(BankingError, match="too large"):
        service.import_statement(session, bank, "x.xml", fixture_bytes("camt053_02.xml"))


def test_bank_account_validates_iban_and_ledger_type(session, admin, bank):
    with pytest.raises(BankingError, match="invalid IBAN"):
        service.create_bank_account(session, admin, "NL00NOPE0000000000", "x")
    with pytest.raises(BankingError, match="already registered"):
        service.create_bank_account(session, admin, "nl91 abna 0417 1643 00", "dup")
    with pytest.raises(BankingError, match="asset"):
        service.create_bank_account(
            session, admin, SUPPLIER_IBAN, "x", account(session, admin, "8000").id
        )


def test_match_posts_balanced_entry_and_marks_invoice_paid(session, admin, bank):
    klant = contact(session, admin, iban="NL02ABNA0123456789")
    invoice = sales_invoice(session, admin, klant, "2026-0001", 12_100)
    session.commit()
    load(session, bank)
    tx = tx_by_amount(session, 12_100)
    [top] = service.suggest(session, tx)
    assert top.candidate.invoice_id == invoice.id and top.exact_amount and top.number_hit

    entry = service.match(session, tx, [Allocation("sales", invoice.id, 12_100)], user_id=None)
    session.commit()

    assert entry_totals(entry) == (12_100, 12_100)
    by_account = {line.account_id: line for line in entry.lines}
    assert by_account[bank.ledger_account_id].debit_cents == 12_100
    debtors = by_account[account(session, admin, "1300").id]
    assert (debtors.credit_cents, debtors.contact_id) == (12_100, klant.id)
    session.refresh(invoice)
    assert (invoice.paid_cents, invoice.status) == (12_100, InvoiceStatus.PAID)
    assert (tx.status, tx.journal_entry_id) == (TxStatus.MATCHED, entry.id)
    assert entry.entry_date == date(2026, 6, 2)
    assert account_balance_cents(session, bank.ledger_account_id) == 12_100


def test_purchase_payment_debits_creditors(session, admin, bank):
    supplier = contact(session, admin, name="Leverancier BV", kind=ContactKind.SUPPLIER)
    invoice = purchase_invoice(session, admin, supplier, "INK-77", 5_000)
    session.commit()
    load(session, bank)
    tx = tx_by_amount(session, -5_000)
    entry = service.match(session, tx, [Allocation("purchase", invoice.id, 5_000)])
    session.commit()
    lines = {line.account_id: line for line in entry.lines}
    assert lines[account(session, admin, "1600").id].debit_cents == 5_000
    assert lines[account(session, admin, "1600").id].contact_id == supplier.id
    assert lines[bank.ledger_account_id].credit_cents == 5_000
    session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PAID


def test_partial_payment_keeps_invoice_open(session, admin, bank):
    klant = contact(session, admin)
    invoice = sales_invoice(session, admin, klant, "2026-0001", 20_000)
    session.commit()
    load(session, bank)
    tx = tx_by_amount(session, 12_100)
    service.match(session, tx, [Allocation("sales", invoice.id, 12_100)])
    session.commit()
    session.refresh(invoice)
    assert (invoice.paid_cents, invoice.status) == (12_100, InvoiceStatus.OPEN)
    [candidate] = matching.load_candidates(session, admin.id)
    assert candidate.remaining_cents == 7_900


def test_match_rejects_wrong_sums_and_overpayment(session, admin, bank):
    klant = contact(session, admin)
    small = sales_invoice(session, admin, klant, "2026-0001", 5_000)
    session.commit()
    load(session, bank)
    tx = tx_by_amount(session, 12_100)
    with pytest.raises(BankingError, match="add up"):
        service.match(session, tx, [Allocation("sales", small.id, 5_000)])
    with pytest.raises(BankingError, match="exceeds"):
        service.match(session, tx, [Allocation("sales", small.id, 12_100)])
    with pytest.raises(BankingError, match="purchase"):
        service.match(session, tx, [Allocation("purchase", small.id, 12_100)])


def test_match_splits_one_payment_over_two_invoices(session, admin, bank):
    klant = contact(session, admin)
    first = sales_invoice(session, admin, klant, "2026-0001", 10_000)
    second = sales_invoice(session, admin, klant, "2026-0002", 2_100)
    session.commit()
    load(session, bank)
    tx = tx_by_amount(session, 12_100)
    entry = service.match(
        session, tx, [Allocation("sales", first.id, 10_000), Allocation("sales", second.id, 2_100)]
    )
    session.commit()
    assert entry_totals(entry) == (12_100, 12_100)
    assert session.scalar(select(func.count()).select_from(PaymentAllocation)) == 2
    session.refresh(first)
    session.refresh(second)
    assert first.status == second.status == InvoiceStatus.PAID


def test_undo_reverses_entry_and_restores_invoice(session, admin, bank):
    klant = contact(session, admin)
    invoice = sales_invoice(session, admin, klant, "2026-0001", 12_100)
    session.commit()
    load(session, bank)
    tx = tx_by_amount(session, 12_100)
    entry = service.match(session, tx, [Allocation("sales", invoice.id, 12_100)])
    session.commit()

    service.undo(session, tx)
    session.commit()

    session.refresh(invoice)
    assert (invoice.paid_cents, invoice.status) == (0, InvoiceStatus.OPEN)
    assert (tx.status, tx.journal_entry_id) == (TxStatus.UNMATCHED, None)
    assert session.scalar(select(func.count()).select_from(PaymentAllocation)) == 0
    assert account_balance_cents(session, bank.ledger_account_id) == 0
    reversal = session.scalar(select(JournalEntry).where(JournalEntry.reversal_of_id == entry.id))
    assert reversal is not None
    service.match(session, tx, [Allocation("sales", invoice.id, 12_100)])
    session.commit()
    assert account_balance_cents(session, bank.ledger_account_id) == 12_100


def line_map(entry):
    return {(line.account_id, line.vat_role): line for line in entry.lines}


def test_book_purchase_with_vat_splits_base_and_vat(session, admin, bank):
    load(session, bank)
    tx = tx_by_amount(session, -5_000)
    office = account(session, admin, "4200")
    entry = service.book_to_account(session, tx, office.id, "I21")
    session.commit()
    lines = line_map(entry)
    base = lines[(office.id, "base")]
    vat = lines[(account(session, admin, "1400").id, "vat")]
    assert (base.debit_cents, base.vat_code) == (4_132, "I21")
    assert (vat.debit_cents, vat.vat_code) == (868, "I21")
    assert lines[(bank.ledger_account_id, None)].credit_cents == 5_000
    assert entry_totals(entry) == (5_000, 5_000)
    assert (tx.status, tx.journal_entry_id) == (TxStatus.MATCHED, entry.id)


def test_book_bank_charges_without_vat_line(session, admin, bank):
    content = fixture_bytes("overlap.xml")
    service.import_statement(session, bank, "o.xml", content)
    session.commit()
    tx = tx_by_amount(session, -2_900)
    costs = account(session, admin, "4800")
    entry = service.book_to_account(session, tx, costs.id, "I0")
    assert len(entry.lines) == 2
    assert line_map(entry)[(costs.id, "base")].debit_cents == 2_900
    assert entry_totals(entry) == (2_900, 2_900)


def test_book_sales_income_uses_vat_payable(session, admin, bank):
    load(session, bank)
    tx = tx_by_amount(session, 12_100)
    revenue = account(session, admin, "8000")
    entry = service.book_to_account(session, tx, revenue.id, "V21")
    lines = line_map(entry)
    assert lines[(revenue.id, "base")].credit_cents == 10_000
    assert lines[(account(session, admin, "1700").id, "vat")].credit_cents == 2_100
    assert lines[(bank.ledger_account_id, None)].debit_cents == 12_100


def test_book_rejects_wrong_direction_and_foreign_accounts(session, admin, bank):
    load(session, bank)
    out = tx_by_amount(session, -5_000)
    with pytest.raises(BankingError, match="direction"):
        service.book_to_account(session, out, account(session, admin, "8000").id, "V21")
    with pytest.raises(BankingError, match="reverse-charge"):
        service.book_to_account(session, out, account(session, admin, "4200").id, "IEU21")
    with pytest.raises(BankingError, match="unknown account"):
        service.book_to_account(session, out, 99_999, None)


def test_ignore_and_undo_ignore(session, bank):
    load(session, bank)
    tx = tx_by_amount(session, 12_100)
    service.ignore(session, tx)
    assert tx.status == TxStatus.IGNORED
    with pytest.raises(BankingError, match="already processed"):
        service.ignore(session, tx)
    service.undo(session, tx)
    assert tx.status == TxStatus.UNMATCHED


def test_accept_exact_matches_only_unique_number_and_amount_hits(session, admin, bank):
    klant = contact(session, admin)
    hit = sales_invoice(session, admin, klant, "2026-0001", 12_100)
    sales_invoice(session, admin, klant, "2026-0002", 12_100)
    session.commit()
    load(session, bank)
    tx_in = tx_by_amount(session, 12_100)
    assert service.accept_exact_matches(session, bank) == 1
    session.commit()
    session.refresh(hit)
    assert (hit.status, tx_in.status) == (InvoiceStatus.PAID, TxStatus.MATCHED)
    assert tx_by_amount(session, -5_000).status == TxStatus.UNMATCHED


def test_accept_exact_skips_ambiguous_amount_without_number(session, admin, bank):
    klant = contact(session, admin)
    sales_invoice(session, admin, klant, "A-1000", 12_100)
    sales_invoice(session, admin, klant, "A-2000", 12_100)
    session.commit()
    load(session, bank)
    assert service.accept_exact_matches(session, bank) == 0


def test_sepa_export_uses_remaining_amount_and_requires_iban(session, admin, bank):
    supplier = contact(session, admin, name="Leverancier BV", kind=ContactKind.SUPPLIER,
                       iban=SUPPLIER_IBAN)
    no_iban = contact(session, admin, name="Zonder IBAN", kind=ContactKind.SUPPLIER)
    first = purchase_invoice(session, admin, supplier, "INK-1", 12_345)
    first.paid_cents = 345
    second = purchase_invoice(session, admin, supplier, "INK-2", 1_005)
    bad = purchase_invoice(session, admin, no_iban, "X-1", 100)
    session.commit()
    name, xml = service.sepa_export(
        session, admin, bank, [first.id, second.id], date(2026, 6, 20)
    )
    assert name.startswith("sepa-") and name.endswith(".xml")
    assert b"<CtrlSum>130.05</CtrlSum>" in xml
    assert b'Ccy="EUR">120.00<' in xml and b'Ccy="EUR">10.05<' in xml
    with pytest.raises(BankingError, match="no IBAN"):
        service.sepa_export(session, admin, bank, [bad.id], date(2026, 6, 20))
    with pytest.raises(BankingError, match="not found"):
        service.sepa_export(session, admin, bank, [first.id, 99_999], date(2026, 6, 20))
