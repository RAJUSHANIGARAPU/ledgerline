import json
from datetime import date
from pathlib import Path

import pytest

from ledgerline.inbox.extract import ExtractedInvoice
from ledgerline.inbox.extract.vision import SYSTEM_PROMPT, parse_result
from ledgerline.inbox.review import match_supplier, vat_code_for
from ledgerline.models import Contact, ContactKind

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 8)


def load(name, **over):
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    data.update(over)
    return parse_result(data, TODAY)


def clean(invoice):
    return [w for w in invoice.warnings if not w.startswith("Read from")]


def test_mixed_vat_rates_pass_all_checks():
    invoice = load("mixed_9_21")
    assert clean(invoice) == []
    assert [(r.rate_percent, r.base_cents, r.vat_cents) for r in invoice.vat_summary] == [
        (9, 20000, 1800),
        (21, 10000, 2100),
    ]
    assert invoice.flagged_fields == []


def test_credit_note_keeps_negative_amounts_without_warning():
    invoice = load("credit_note")
    assert invoice.credit_note and invoice.total_incl_cents == -5450
    assert clean(invoice) == []


def test_credit_note_flag_and_sign_disagreeing_warn():
    assert any("positive" in w for w in load("mixed_9_21", credit_note=True).warnings)
    assert any("negative" in w for w in load("credit_note", credit_note=False).warnings)


def test_deposit_and_paid_at_delivery_are_surfaced():
    invoice = load("deposit")
    assert invoice.lines[1].is_deposit and not invoice.lines[0].is_deposit
    assert invoice.paid_at_delivery
    text = " ".join(invoice.warnings)
    assert "deposit" in text and "already paid" in text


def test_summary_only_invoice_gets_one_line_per_rate():
    invoice = load("summary_only")
    assert [(ln.vat_rate_percent, ln.amount_excl_cents) for ln in invoice.lines] == [
        (9, 15000),
        (21, 8000),
    ]
    assert any("created from the summary" in w for w in invoice.warnings)
    assert vat_code_for(invoice.lines[0], False, "NL") == "I9"
    assert not any("add up" in w for w in invoice.warnings)


def test_arithmetic_mismatch_is_flagged():
    warnings = " ".join(load("arithmetic_mismatch").warnings)
    assert "Totals do not add up" in warnings
    assert "VAT at 21%" in warnings


def test_bad_iban_is_flagged_and_unusable():
    invoice = load("bad_iban")
    assert "supplier_iban" in invoice.flagged_fields
    assert any("IBAN" in w and "misread" in w for w in invoice.warnings)


def test_bad_vat_number_and_kvk_flagged():
    invoice = load("mixed_9_21", supplier_vat_number="NL12345678B01", supplier_kvk="1234")
    assert set(invoice.flagged_fields) == {"supplier_vat_number", "supplier_kvk"}


def test_valid_identifiers_are_normalised():
    invoice = load(
        "mixed_9_21",
        supplier_iban="nl02 abna 0123 4567 89",
        supplier_vat_number="NL1234.56.782.B01",
    )
    assert invoice.supplier_iban == "NL02ABNA0123456789"
    assert invoice.supplier_vat_number == "NL123456782B01"


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"invoice_date": "2023-01-01"}, "more than two years"),
        ({"due_date": "2026-10-01"}, "before the invoice date"),
        ({"due_date": "2028-01-01"}, "more than a year after"),
    ],
)
def test_date_sanity(over, needle):
    assert any(needle in w for w in load("mixed_9_21", **over).warnings)


def test_non_dutch_rate_in_summary_warns():
    over = {"vat_summary": [{"vat_rate_percent": 19, "base_excl": "300.00", "vat_amount": "57.00"}]}
    assert any("not a Dutch rate" in w for w in load("mixed_9_21", **over).warnings)


def test_summary_total_mismatch_warns():
    over = {"vat_summary": [{"vat_rate_percent": 9, "base_excl": "200.00", "vat_amount": "18.00"}]}
    assert any("VAT summary bases" in w for w in load("mixed_9_21", **over).warnings)


def test_old_stored_extraction_still_loads():
    old = {"supplier_name": "X", "currency": "EUR", "lines": [], "warnings": []}
    invoice = ExtractedInvoice.from_json(old)
    assert invoice.vat_summary == [] and invoice.flagged_fields == []


def test_roundtrip_keeps_new_fields():
    invoice = load("deposit")
    again = ExtractedInvoice.from_json(invoice.to_json())
    assert again == invoice


def test_prompt_covers_restaurant_fields():
    for needle in ("vat_summary", "is_deposit", "credit_note", "paid_at_delivery", "statiegeld"):
        assert needle in SYSTEM_PROMPT


def _supplier(session, admin, **fields):
    contact = Contact(
        administration_id=admin.id, kind=ContactKind.SUPPLIER, name="Oude Naam", **fields
    )
    session.add(contact)
    session.flush()
    return contact


def test_supplier_matched_by_kvk(session, admin):
    contact = _supplier(session, admin, kvk_number="87654321")
    found, how = match_supplier(session, admin.id, load("mixed_9_21", supplier_vat_number=None))
    assert (found, how) == (contact, "KvK number")


def test_supplier_matched_by_iban_when_valid(session, admin):
    contact = _supplier(session, admin, iban="NL02ABNA0123456789")
    invoice = load("mixed_9_21", supplier_vat_number=None, supplier_kvk=None)
    assert match_supplier(session, admin.id, invoice) == (contact, "IBAN")


def test_flagged_identifier_is_not_used_for_matching(session, admin):
    _supplier(session, admin, kvk_number="1234")
    invoice = load("mixed_9_21", supplier_vat_number=None, supplier_kvk="1234", supplier_name="Zzz")
    assert "supplier_kvk" in invoice.flagged_fields
    assert match_supplier(session, admin.id, invoice) == (None, None)
