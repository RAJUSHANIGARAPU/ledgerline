from datetime import date

from ledgerline.ledger.chart import create_administration
from ledgerline.models import ContactKind, InboxStatus, InvoiceStatus
from ledgerline.spend.service import Pending, SpendFilter, Totals, overview
from tests.helpers import contact

FLT = SpendFilter()


def test_empty_administration_returns_zero_totals(session, admin):
    result = overview(session, admin.id, FLT)
    assert result.rows == []
    assert result.totals == Totals()
    assert result.by_supplier == result.by_month == result.by_category == []
    assert result.pending == Pending()


def test_drafts_are_listed_on_request_but_never_totalled(session, admin, make_bill):
    make_bill([(10000, "4300")], on=date(2026, 3, 1))
    draft = make_bill([(5000, "4300")], on=date(2026, 3, 5), book=False)
    assert draft.status == InvoiceStatus.DRAFT

    hidden = overview(session, admin.id, FLT)
    assert [r.status for r in hidden.rows] == ["open"]

    shown = overview(session, admin.id, SpendFilter(include_drafts=True))
    assert [r.status for r in shown.rows] == ["draft", "open"]
    assert shown.rows[0].open_cents == 0
    assert shown.totals == hidden.totals == Totals(1, 10000, 2100, 12100, 12100)
    assert shown.by_month[0].totals.count == 1
    assert shown.by_supplier[0].totals.incl_cents == 12100
    assert shown.by_category[0].totals.excl_cents == 10000


def test_rows_order_by_date_then_id_descending(session, admin, make_bill):
    a = make_bill(on=date(2026, 3, 1))
    b = make_bill(on=date(2026, 3, 9))
    c = make_bill(on=date(2026, 3, 9))
    assert [r.invoice_id for r in overview(session, admin.id, FLT).rows] == [c.id, b.id, a.id]


def test_date_bounds_are_inclusive_and_optional(session, admin, make_bill):
    make_bill(on=date(2026, 2, 28))
    first = make_bill(on=date(2026, 3, 1))
    last = make_bill(on=date(2026, 3, 31))
    make_bill(on=date(2026, 4, 1))

    both = overview(session, admin.id, SpendFilter(date(2026, 3, 1), date(2026, 3, 31)))
    assert [r.invoice_id for r in both.rows] == [last.id, first.id]
    assert len(overview(session, admin.id, SpendFilter(date_from=date(2026, 3, 31))).rows) == 2
    assert len(overview(session, admin.id, SpendFilter(date_to=date(2026, 3, 1))).rows) == 2


def test_contact_filter(session, admin, make_bill, supplier):
    other = contact(session, admin, name="Andere Leverancier", kind=ContactKind.SUPPLIER)
    session.commit()
    make_bill()
    theirs = make_bill(who=other)
    result = overview(session, admin.id, SpendFilter(contact_id=other.id))
    assert [(r.invoice_id, r.contact_id, r.supplier) for r in result.rows] == [
        (theirs.id, other.id, "Andere Leverancier")]
    assert [g.key for g in result.by_supplier] == ["Andere Leverancier"]


def test_sales_invoices_never_appear(session, admin, make_bill):
    from decimal import Decimal

    from ledgerline.invoicing.service import SalesLineInput, create_sales_invoice
    from tests.helpers import account

    customer = contact(session, admin, name="Klant BV", address_line="Weg 1", postcode="1111 AA",
                       city="Utrecht")
    create_sales_invoice(
        session, admin, customer,
        [SalesLineInput("Werk", Decimal(1), 99900, "V21", account(session, admin, "8000").id)],
        issue_date=date(2026, 3, 1),
    )
    session.commit()
    make_bill()
    result = overview(session, admin.id, SpendFilter(include_drafts=True))
    assert len(result.rows) == 1
    assert result.totals.excl_cents == 10000


def test_other_administrations_bills_never_appear(session, admin, make_bill):
    other = create_administration(session, name="Ander BV")
    stranger = contact(session, other, name="Vreemd", kind=ContactKind.SUPPLIER)
    session.commit()
    make_bill(adm=other, who=stranger)
    mine = make_bill()
    result = overview(session, admin.id, SpendFilter(include_drafts=True))
    assert [r.invoice_id for r in result.rows] == [mine.id]
    assert "Vreemd" not in [g.key for g in result.by_supplier]


def test_source_comes_from_the_inbox_document(session, admin, make_bill, make_inbox_doc):
    bills = {}
    for source in ("email", "upload", "phone"):
        bills[source] = make_bill(on=date(2026, 3, 2))
        make_inbox_doc(source=source, status=InboxStatus.BOOKED, invoice=bills[source])
    bills["manual"] = make_bill()
    rows = {r.invoice_id: r.source for r in overview(session, admin.id, FLT).rows}
    assert {name: rows[inv.id] for name, inv in bills.items()} == {
        "email": "email", "upload": "upload", "phone": "phone", "manual": "manual"}


def test_attachment_and_opening_flags(session, admin, make_bill):
    from ledgerline.invoicing.service import store_attachment

    plain = make_bill()
    with_file = make_bill(attachment=store_attachment(session, admin, "a.png",
                                                      b"\x89PNG\r\n\x1a\nx"))
    opening = make_bill()
    opening.is_opening = True
    session.commit()
    rows = {r.invoice_id: r for r in overview(session, admin.id, FLT).rows}
    assert not rows[plain.id].has_attachment
    assert rows[with_file.id].has_attachment
    assert rows[opening.id].is_opening
    assert not rows[plain.id].is_opening


def test_open_cents_for_partly_and_fully_paid_bills(session, admin, make_bill):
    partly = make_bill([(10000, "4300")], paid=4000)
    paid = make_bill([(5000, "4300")], paid=6050)
    paid.status = InvoiceStatus.PAID
    session.commit()
    result = overview(session, admin.id, FLT)
    rows = {r.invoice_id: r for r in result.rows}
    assert rows[partly.id].open_cents == 8100
    assert rows[paid.id].open_cents == 0
    assert result.totals.open_cents == 8100
    assert result.totals.incl_cents == 12100 + 6050


def test_cancelled_bills_are_excluded(session, admin, make_bill):
    gone = make_bill()
    gone.status = InvoiceStatus.CANCELLED
    session.commit()
    result = overview(session, admin.id, SpendFilter(include_drafts=True))
    assert result.rows == []
    assert result.totals == Totals()


def test_groups_are_ordered_and_summed(session, admin, make_bill):
    big = contact(session, admin, name="Groot", kind=ContactKind.SUPPLIER)
    small = contact(session, admin, name="Klein", kind=ContactKind.SUPPLIER)
    session.commit()
    make_bill([(1000, "4300")], on=date(2026, 5, 10), who=small)
    make_bill([(20000, "4300"), (3000, "4100")], on=date(2026, 3, 10), who=big)
    make_bill([(4000, "4100")], on=date(2026, 3, 20), who=small)
    result = overview(session, admin.id, FLT)

    assert [(g.key, g.totals.count, g.totals.incl_cents) for g in result.by_supplier] == [
        ("Groot", 1, 27830), ("Klein", 2, 6050)]
    assert [(g.key, g.totals.excl_cents) for g in result.by_month] == [
        ("2026-03", 27000), ("2026-05", 1000)]
    assert [(g.key, g.totals.count, g.totals.excl_cents, g.totals.vat_cents, g.totals.incl_cents)
            for g in result.by_category] == [
        ("4300 Software and subscriptions", 2, 21000, 4410, 25410),
        ("4100 Travel and vehicle", 2, 7000, 1470, 8470),
    ]
    assert sum(g.totals.incl_cents for g in result.by_category) == result.totals.incl_cents


def test_reverse_charge_vat_stays_out_of_category_totals(session, admin, owner, supplier):
    from ledgerline.invoicing.service import (
        PurchaseLineInput,
        book_purchase_invoice,
        create_purchase_invoice,
    )
    from tests.helpers import account

    invoice = create_purchase_invoice(
        session, admin, supplier,
        [PurchaseLineInput("Sub", account(session, admin, "4300").id, 10000, "IRV21", None)],
        supplier_reference="RC-1", invoice_date=date(2026, 3, 2),
    )
    book_purchase_invoice(session, admin, invoice, owner.id)
    session.commit()
    result = overview(session, admin.id, FLT)
    assert result.totals.incl_cents == 10000
    assert result.by_category[0].totals.incl_cents == 10000


def test_credit_notes_reduce_every_sum(session, admin, make_bill):
    make_bill([(10000, "4300")], on=date(2026, 3, 2))
    note = make_bill([(-4000, "4300")], on=date(2026, 3, 20))
    assert note.total_incl_cents == -4840
    result = overview(session, admin.id, FLT)
    assert result.totals == Totals(2, 6000, 1260, 7260, 7260)
    assert result.by_supplier[0].totals.incl_cents == 7260
    assert result.by_month[0].totals.excl_cents == 6000
    assert result.by_category[0].totals.excl_cents == 6000
    assert result.by_category[0].totals.count == 2


def test_pending_counts_review_documents_and_sums_extracted_totals(
        session, admin, make_bill, make_inbox_doc):
    make_inbox_doc(status=InboxStatus.REVIEW, extraction={"total_incl_cents": 12100})
    make_inbox_doc(status=InboxStatus.REVIEW, extraction={"total_incl_cents": -2000,
                                                          "credit_note": True})
    make_inbox_doc(status=InboxStatus.NEW)
    make_inbox_doc(status=InboxStatus.REVIEW, extraction={"supplier_name": "x",
                                                          "total_incl_cents": None})
    # not pending: finished, or not a purchase invoice, or someone else's
    make_inbox_doc(status=InboxStatus.BOOKED, extraction={"total_incl_cents": 999},
                   invoice=make_bill())
    make_inbox_doc(status=InboxStatus.REJECTED, extraction={"total_incl_cents": 888})
    make_inbox_doc(status=InboxStatus.REVIEW, extraction={"document_type": "statement",
                                                          "total_incl_cents": 777})
    other = create_administration(session, name="Ander BV")
    session.commit()
    make_inbox_doc(adm=other, extraction={"total_incl_cents": 555})

    result = overview(session, admin.id, SpendFilter(date_from=date(2030, 1, 1)))
    assert result.pending == Pending(count=4, extracted_incl_cents=10100, unreadable=2)
    assert result.rows == []
