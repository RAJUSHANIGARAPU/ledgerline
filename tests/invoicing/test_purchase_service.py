from datetime import date

import pytest
from sqlalchemy import select

from ledgerline.config import get_settings
from ledgerline.invoicing.calc import InvoiceError
from ledgerline.invoicing.service import (
    PurchaseLineInput,
    book_purchase_invoice,
    create_purchase_invoice,
    store_attachment,
    update_purchase_invoice,
)
from ledgerline.models import Attachment, InvoiceStatus, JournalEntry
from tests.helpers import account

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def pline(session, admin, desc, cents, code=None, vat=None, account_code="4300"):
    return PurchaseLineInput(desc, account(session, admin, account_code).id, cents, code, vat)


def entry_of(session, invoice):
    return session.get(JournalEntry, invoice.journal_entry_id)


def test_normal_purchase_posts_expense_vat_and_creditor(session, admin, owner, supplier):
    inv = create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, "Hosting", 10000)],
        supplier_reference="INV-1", invoice_date=date(2026, 3, 2),
    )
    book_purchase_invoice(session, admin, inv, owner.id)
    session.commit()
    assert inv.status == InvoiceStatus.OPEN
    assert (inv.total_excl_cents, inv.total_vat_cents, inv.total_incl_cents) == (10000, 2100, 12100)
    a = {c: account(session, admin, c).id for c in ("4300", "1400", "1600")}
    got = sorted((ln.account_id, ln.debit_cents, ln.credit_cents, ln.vat_code, ln.vat_role)
                 for ln in entry_of(session, inv).lines)
    assert got == sorted([
        (a["4300"], 10000, 0, "I21", "base"),
        (a["1400"], 2100, 0, "I21", "vat"),
        (a["1600"], 0, 12100, None, None),
    ])


def test_stated_vat_may_differ_by_a_cent(session, admin, owner, supplier):
    inv = create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, "Item", 3333, "I21", vat=699)],
        supplier_reference="INV-2", invoice_date=date(2026, 3, 2),
    )
    assert inv.lines[0].vat_cents == 699  # computed would be 700
    book_purchase_invoice(session, admin, inv, owner.id)
    assert inv.total_incl_cents == 3333 + 699
    lines = entry_of(session, inv).lines
    assert sum(ln.debit_cents for ln in lines) == sum(ln.credit_cents for ln in lines)


def test_reverse_charge_purchase_posts_four_lines(session, admin, owner, supplier):
    inv = create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, "EU service", 10000, "IEU21")],
        supplier_reference="EU-1", invoice_date=date(2026, 3, 2),
    )
    book_purchase_invoice(session, admin, inv, owner.id)
    a = {c: account(session, admin, c).id for c in ("4300", "1400", "1720", "1600")}
    got = sorted((ln.account_id, ln.debit_cents, ln.credit_cents, ln.vat_code, ln.vat_role)
                 for ln in entry_of(session, inv).lines)
    assert got == sorted([
        (a["4300"], 10000, 0, "IEU21", "base"),
        (a["1400"], 2100, 0, "IEU21", "vat"),
        (a["1720"], 0, 2100, "IEU21", "vat"),
        (a["1600"], 0, 10000, None, None),
    ])
    assert inv.total_incl_cents == 10000  # nothing of the VAT is owed to the supplier


def test_reverse_charge_vat_ignores_a_stated_amount(session, admin, supplier):
    inv = create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, "x", 10000, "IEU21", vat=5)],
        supplier_reference="EU-2", invoice_date=date(2026, 3, 2),
    )
    assert inv.lines[0].vat_cents == 2100


def test_zero_rated_purchase_has_no_vat_line(session, admin, owner, supplier):
    inv = create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, "Fee", 500, account_code="4800")],
        supplier_reference="B-1", invoice_date=date(2026, 3, 2),
    )
    book_purchase_invoice(session, admin, inv, owner.id)
    assert len(entry_of(session, inv).lines) == 2


def test_duplicate_supplier_reference_is_refused(session, admin, supplier):
    args = dict(supplier_reference="DUP-7", invoice_date=date(2026, 3, 2))
    create_purchase_invoice(session, admin, supplier, [pline(session, admin, "a", 100)], **args)
    with pytest.raises(InvoiceError, match="already registered"):
        create_purchase_invoice(session, admin, supplier, [pline(session, admin, "b", 100)],
                                supplier_reference=" dup-7 ", invoice_date=date(2026, 3, 3))


def test_booked_purchase_is_immutable(session, admin, owner, supplier):
    inv = create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, "a", 100)],
        supplier_reference="X-1", invoice_date=date(2026, 3, 2),
    )
    book_purchase_invoice(session, admin, inv, owner.id)
    with pytest.raises(InvoiceError, match="cannot be edited"):
        update_purchase_invoice(
            session, admin, inv, supplier, [pline(session, admin, "b", 200)],
            supplier_reference="X-1", invoice_date=date(2026, 3, 2), due_date=date(2026, 4, 1),
        )
    with pytest.raises(InvoiceError, match="only a draft"):
        book_purchase_invoice(session, admin, inv, owner.id)


def test_negative_purchase_swaps_sides(session, admin, owner, supplier):
    inv = create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, "Credit", -10000)],
        supplier_reference="CR-1", invoice_date=date(2026, 3, 2),
    )
    book_purchase_invoice(session, admin, inv, owner.id)
    lines = entry_of(session, inv).lines
    by_account = {ln.account_id: (ln.debit_cents, ln.credit_cents) for ln in lines}
    assert by_account[account(session, admin, "4300").id] == (0, 10000)
    assert by_account[account(session, admin, "1600").id] == (12100, 0)


def test_attachment_accepts_only_real_pdf_png_jpeg(session, admin):
    stored = store_attachment(session, admin, "../../scan.png", PNG)
    assert stored.content_type == "image/png" and stored.filename == "scan.png"
    assert len(stored.sha256) == 64 and stored.size_bytes == len(PNG)
    for bad in (b"MZ\x90\x00 not an image", b"<html>%PDF-</html>", b""):
        with pytest.raises(InvoiceError):
            store_attachment(session, admin, "evil.pdf", bad)
    assert len(session.scalars(select(Attachment)).all()) == 1


def test_attachment_size_limit(session, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 20)
    with pytest.raises(InvoiceError, match="too large"):
        store_attachment(session, admin, "a.png", PNG)
