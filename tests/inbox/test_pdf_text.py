import pytest

from ledgerline.inbox.extract import ExtractionError
from ledgerline.inbox.extract.pdf_text import (
    PdfTextExtractor,
    parse_amount,
    parse_date,
    parse_text,
    read_text,
)
from tests.inbox.factories import invoice_html, pdf_from_html, pdf_without_text


def extract(html, **kwargs):
    content = pdf_from_html(html)
    return PdfTextExtractor(**kwargs).extract("application/pdf", content)


def test_happy_path_reads_every_field():
    ex = extract(invoice_html(), ignore_vat=["NL123456789B01"])
    assert ex.invoice_number == "2026-0042"
    assert ex.invoice_date.isoformat() == "2026-11-03"
    assert ex.due_date.isoformat() == "2026-12-03"
    assert ex.supplier_vat_number == "NL853345678B01"
    assert ex.supplier_kvk == "87654321"
    assert ex.supplier_iban == "NL02ABNA0123456789"
    assert ex.supplier_name == "Bakker Software B.V."
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (100000, 21000, 121000)
    assert [(ln.amount_excl_cents, ln.vat_rate_percent, ln.vat_cents) for ln in ex.lines] == [
        (100000, 21, 21000)
    ]
    assert not any("add up" in w for w in ex.warnings)


def test_own_vat_number_is_not_taken_as_the_supplier():
    ex = extract(invoice_html(vat="NL853345678B01"), ignore_vat=["NL853345678B01"])
    assert ex.supplier_vat_number == "NL123456789B01"


def test_two_vat_rates():
    ex = extract(
        invoice_html(
            excl="1.100,00",
            vat_rows=(("21", "1.000,00", "210,00"), ("9", "100,00", "9,00")),
            incl="1.319,00",
        )
    )
    assert (ex.total_vat_cents, ex.total_incl_cents) == (21900, 131900)
    assert {(ln.vat_rate_percent, ln.amount_excl_cents, ln.vat_cents) for ln in ex.lines} == {
        (21, 100000, 21000),
        (9, 10000, 900),
    }


def test_totals_that_do_not_reconcile_warn():
    ex = extract(invoice_html(incl="1.250,00"))
    assert ex.total_incl_cents == 125000
    assert any("Totals do not add up" in w for w in ex.warnings)


def test_reverse_charge_text():
    ex = extract(invoice_html(vat_rows=(), incl="1.000,00", extra="<p>BTW verlegd</p>"))
    assert ex.reverse_charge is True and ex.total_vat_cents == 0
    assert ex.lines[0].vat_rate_percent is None and ex.lines[0].amount_excl_cents == 100000


def test_no_text_layer_raises():
    with pytest.raises(ExtractionError, match="no text layer"):
        PdfTextExtractor().extract("application/pdf", pdf_without_text())


def test_garbage_pdf_raises_instead_of_crashing():
    with pytest.raises(ExtractionError):
        read_text(b"%PDF-1.4\nnot really a pdf")


def test_text_without_any_invoice_data_raises():
    with pytest.raises(ExtractionError, match="no invoice data"):
        parse_text("Dit is een brief over de vergadering van volgende week, geen factuur hier.")


def test_extractor_only_handles_pdf():
    assert PdfTextExtractor().can_handle("application/pdf", b"")
    assert not PdfTextExtractor().can_handle("image/png", b"")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1.234,56", 123456),
        ("12,50", 1250),
        ("100,00", 10000),
        ("-5,00", -500),
        ("12.50", 1250),
    ],
)
def test_amounts(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("03-12-2026", "2026-12-03"),
        ("3/12/2026", "2026-12-03"),
        ("7 november 2026", "2026-11-07"),
        ("1 mrt. 2026", "2026-03-01"),
        ("2026-02-28", "2026-02-28"),
    ],
)
def test_dates(raw, expected):
    assert parse_date(raw).isoformat() == expected


def test_impossible_dates_are_ignored():
    assert parse_date("31-02-2026") is None and parse_date("5 foo 2026") is None


def test_invalid_iban_is_not_used():
    ex = parse_text(
        "Acme\nFactuurnummer: 77\nIBAN NL02 ABNA 0123 4567 80\nSubtotaal € 10,00\n"
        "BTW 21% over € 10,00 € 2,10\nTotaal te betalen € 12,10"
    )
    assert ex.supplier_iban is None
    assert ex.total_incl_cents == 1210


def test_missing_due_date_uses_payment_term_with_warning():
    ex = parse_text(
        "Acme\nFactuurnummer: 77\nFactuurdatum: 01-11-2026\nBetaling binnen 14 dagen\n"
        "Subtotaal € 10,00\nBTW 21% over € 10,00 € 2,10\nTotaal te betalen € 12,10"
    )
    assert ex.due_date.isoformat() == "2026-11-15"
    assert any("payment term" in w for w in ex.warnings)
