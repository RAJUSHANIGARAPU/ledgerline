import pytest

from ledgerline.inbox.extract import ExtractionError
from ledgerline.inbox.extract.pdf_text import (
    PdfTextExtractor,
    _invoice_number,
    parse_amount,
    parse_date,
    parse_text,
    parse_texts,
    read_text,
    read_texts,
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


# Synthetic layouts: made-up companies and amounts, one per pattern the rules handle.
def layout_text(*rows):
    return "\n".join(rows)


def test_default_reading_that_splits_labels_from_values_loses_to_the_layout_reading():
    split = layout_text(
        "Fictional Parts B.V.", "Factuur", "Datum:", "Factuurnummer:", "Klantnummer:",
        "Totaal excl btw", "Btw hoog 21% (EUR 100,00)", "Totaal te betalen",
        "03-11-2026", "FP-2026-9", "552", "100,00", "21,00", "121,00",
    )
    aligned = layout_text(
        "Fictional Parts B.V.", "Factuur", "Datum:      03-11-2026", "Factuurnummer:   FP-2026-9",
        "Klantnummer:      552", "Totaal excl btw     EUR     100,00",
        "Btw hoog 21% (EUR 100,00)     EUR     21,00", "Totaal te betalen    EUR   121,00",
    )
    ex = parse_texts([split, aligned])
    assert ex.invoice_number == "FP-2026-9"
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (10000, 2100, 12100)
    assert not any("could not be reconciled" in w for w in ex.warnings)


def test_warns_when_neither_reading_reconciles():
    text = layout_text("Fictional Parts B.V.", "Factuurnummer: FP-1", "Totaal excl btw 100,00",
                       "BTW 21% 21,00", "Totaal te betalen 150,00")
    ex = parse_texts([text, text])
    assert any("could not be reconciled" in w for w in ex.warnings)


def test_read_texts_returns_default_and_layout_readings():
    texts = read_texts(pdf_from_html(invoice_html()))
    assert len(texts) == 2 and all("2026-0042" in t for t in texts)


@pytest.mark.parametrize(
    "vat_row",
    [
        "BTW (21% over EUR 200,00)      EUR    42,00",
        "21,00% BTW over EUR 200,00     EUR    42,00",
        "21% btw     EUR 42,00",
        "Btw hoog 21%:      42,00",
    ],
)
def test_vat_line_forms_take_the_vat_not_the_base(vat_row):
    text = layout_text("Fictional Eats B.V.", "Factuurnummer 77001", "Subtotaal    EUR   200,00",
                       vat_row, "Totaalbedrag van deze factuur    EUR   242,00")
    ex = parse_text(text)
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (20000, 4200, 24200)


def test_bare_btw_line_without_a_rate_takes_the_amount_not_the_next_heading():
    text = layout_text("Factuurnummer 55-1", "Totaal excl. Btw   300.00 EUR", "Btw   63.00 EUR",
                       "Btw-specificatie   Product   Bedrag",
                       "Netto te betalen incl. Btw   363.00 EUR")
    assert parse_text(text).total_vat_cents == 6300


def test_multi_supplier_block_sums_the_rows_and_deposit_is_outside_the_totals():
    text = layout_text(
        "Factuurnummer   9001", "Totaal te betalen   EUR 376,20",
        "Subtotaal exclusief BTW en emballage",
        "Alpha Drinks B.V.     EUR   20,00", "Beta Wholesale B.V.     EUR   300,00",
        "Subtotaal BTW      EUR    31,20",
        "Alpha Drinks B.V.   9%   over   EUR 20,00   is   EUR 1,80",
        "Beta Wholesale B.V.   9%   over   EUR 280,00   is   EUR 25,20",
        "Beta Wholesale B.V.   21%   over   EUR 20,00   is   EUR 4,20",
        "Subtotaal emballage     EUR   25,00",
    )
    ex = parse_text(text)
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (32000, 3120, 37620)
    assert any("deposit" in w for w in ex.warnings) and not any("add up" in w for w in ex.warnings)
    assert {(ln.vat_rate_percent, ln.amount_excl_cents) for ln in ex.lines} == {
        (9, 30000), (21, 2000)}


def test_identical_vat_table_repeated_on_a_second_page_is_not_double_counted():
    rows = ["Factuurnummer 31", "Subtotaal 100,00", "BTW 21% over 100,00 21,00",
            "Totaal 121,00", "Overzicht", "BTW 21% over 100,00 21,00"]
    assert parse_text(layout_text(*rows)).total_vat_cents == 2100


def test_surcharge_subtotals_pick_the_one_that_reconciles():
    text = layout_text("Factuurnummer 8", "Subtotaal   33,00", "Milieutoeslag   7,00",
                       "Subtotaal   40,00", "21,00% BTW over EUR 40,00    8,40",
                       "Totaal te voldoen    48,40")
    ex = parse_text(text)
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (4000, 840, 4840)


def test_ambiguous_total_labels_are_resolved_by_the_sums():
    text = layout_text("Factuurnummer NL1", "Totaalbedrag   EUR 50,00", "BTW (21%)   EUR 10,50",
                       "Factuurbedrag   EUR 60,50")
    ex = parse_text(text)
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (5000, 1050, 6050)


def test_a_later_zero_total_does_not_beat_the_one_that_adds_up():
    text = layout_text("Factuurnummer FT1", "Subtotaal:   2,00", "BTW 21%:   0,42",
                       "Totaal:   2,42", "Totaal:   0,00")
    assert parse_text(text).total_incl_cents == 242


def test_vat_stated_inside_the_payable_amount():
    text = layout_text("Factuurnummer 5", "De kosten (excl. btw) EUR 11.000,00",
                       "U betaalt bij (waarvan EUR 210,00 btw)   EUR 1.210,00")
    ex = parse_text(text)
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (100000, 21000, 121000)


def test_vat_inside_the_invoice_amount_line():
    text = layout_text("Factuurnummer 6", "Factuurbedrag: EUR 1.210,00 (inclusief EUR 210,00 btw)",
                       "Totaal   EUR 1.000,00   EUR 210,00")
    ex = parse_text(text)
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (100000, 21000, 121000)


def test_totals_row_with_quantity_net_vat_gross():
    text = layout_text("F A C T U U R", "15-03-2026   9900112233445   KASSA1",
                       "Totaal     7     50,00     10,50     60,50")
    ex = parse_text(text)
    assert ex.invoice_number == "9900112233445"
    assert (ex.total_excl_cents, ex.total_vat_cents, ex.total_incl_cents) == (5000, 1050, 6050)


@pytest.mark.parametrize(
    "text, number",
    [
        ("Factuurnummer\nKlantnummer\n9990001", None),
        ("Klantnummer: 9990001\nRelatienummer 5544\nFactuurnummer: A-77", "A-77"),
        ("Factuur 2026-0999\nFactuurdatum: 23-06-2026", "2026-0999"),
        ("Factuur S20269999\nFactuurdatum: 28-04-2026", "S20269999"),
        ("FACTUUR - N° nl-I-000999001\nFactuurdatum: 13-4-2026", "nl-I-000999001"),
        ("Factuur\n99999999", None),
        ("Factuur 11 januari 2026", None),
        ("Debiteurnummer: 99881\nInvoice no. IN-5", "IN-5"),
    ],
)
def test_invoice_number_patterns(text, number):
    assert _invoice_number(text) == number


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Betaaloverzicht voor 3/30/26 - 4/5/26\nTotaal verkopen 100,00", "statement"),
        ("Bij niet betalen bij Fictional B.V.\nNog te betalen: EUR 50,00\nVerplichte rente 5,00",
         "reminder"),
        ("Orderformulier\nLooptijd verlengen 3 jaar\nBedrag excl. BTW 40,00", "contract"),
        ("Factuurnummer FP-12\nTotaal excl btw 10,00\nBTW 21% 2,10\nTotaal 12,10", "invoice"),
        ("Aanmaning\nFactuurnummer FP-12\nTotaal excl btw 10,00\nBTW 21% 2,10\nTotaal 12,10",
         "invoice"),
    ],
)
def test_non_invoice_documents_get_their_own_type(text, kind):
    ex = parse_text(text)
    assert ex.document_type == kind
    if kind != "invoice":
        assert ex.total_incl_cents is None and ex.warnings


def test_own_sales_invoice_is_told_apart_by_the_issuers_identifiers():
    sales = layout_text(
        "Garden Bistro", "Factuur 2026-777", "Totaal excl btw 10,00", "BTW 21% 2,10",
        "Totaal 12,10", "KvK 12345678   Btw NL853345678B01")
    own = {"ignore_vat": ["NL853345678B01"], "ignore_kvk": ["12345678"]}
    assert parse_text(sales, own["ignore_vat"], (), own["ignore_kvk"]).document_type == "own_sales"
    purchase = sales + "\nOther Supplier B.V. KvK 87654321 Btw NL123456789B01"
    assert parse_text(purchase, own["ignore_vat"], (), own["ignore_kvk"]).document_type == "invoice"
    assert parse_text(sales).document_type == "invoice"


def test_supplier_name_skips_the_own_business_block_and_prefers_a_company_form():
    text = layout_text(
        "Garden Bistro (Terrace)", "Zuidlaan 12", "1234 AB Teststad", "Factuurnummer 9",
        "Klantnummer: 5", "Totaal excl btw 10,00", "BTW 21% 2,10", "Totaal 12,10",
        "Northwind Supplies B.V.   KvK 87654321   Btw NL123456789B01")
    ex = parse_text(text, (), (), (), ["Garden Bistro"])
    assert ex.supplier_name == "Northwind Supplies B.V."
    assert not any("supplier name" in w for w in ex.warnings)


def test_supplier_name_warning_stays_when_the_name_cannot_be_corroborated():
    text = layout_text("Some Shop", "Factuurnummer 9", "Totaal excl btw 10,00", "BTW 21% 2,10",
                       "Totaal 12,10")
    ex = parse_text(text)
    assert ex.supplier_name == "Some Shop"
    assert any("supplier name" in w for w in ex.warnings)


def test_supplier_name_printed_twice_is_trusted():
    text = layout_text("Some Shop", "Factuurnummer 9", "Totaal excl btw 10,00", "BTW 21% 2,10",
                       "Totaal 12,10", "Betalen aan Some Shop")
    assert not any("supplier name" in w for w in parse_text(text).warnings)
