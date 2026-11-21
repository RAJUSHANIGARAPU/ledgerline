from datetime import date

import pytest

from ledgerline.invoicing.service import (
    InvoiceError,
    book_purchase_invoice,
    create_purchase_invoice,
    find_duplicate_purchase,
    reference_key,
    update_purchase_invoice,
)
from ledgerline.ledger.chart import create_administration
from ledgerline.models import ContactKind
from tests.helpers import contact
from tests.invoicing.test_purchase_service import pline

REFERENCE = "DV-2026-0412"


def _create(session, admin, supplier, reference, desc="Drukwerk"):
    return create_purchase_invoice(
        session, admin, supplier, [pline(session, admin, desc, 12000)],
        supplier_reference=reference, invoice_date=date(2026, 11, 3),
    )


def test_exact_repeat_is_refused_while_the_first_is_still_a_draft(session, admin, supplier):
    first = _create(session, admin, supplier, REFERENCE)
    assert find_duplicate_purchase(session, admin, supplier.id, REFERENCE) == first
    with pytest.raises(InvoiceError, match="already registered"):
        _create(session, admin, supplier, REFERENCE)


@pytest.mark.parametrize("variant", [
    "dv-2026-0412", "  DV-2026-0412 ", "DV 2026 0412", "dv.2026.0412", "DV/2026/0412",
    "DV-2026-412", "DV--2026-0412",
])
def test_same_number_written_differently_is_the_same_invoice(session, admin, supplier, variant):
    _create(session, admin, supplier, REFERENCE)
    with pytest.raises(InvoiceError, match="already registered"):
        _create(session, admin, supplier, variant)


@pytest.mark.parametrize("other", ["DV-2026-0413", "DV-2026-4120", "DV-2025-0412", "DV20260412"])
def test_a_genuinely_different_number_is_allowed(session, admin, supplier, other):
    _create(session, admin, supplier, REFERENCE)
    assert _create(session, admin, supplier, other).id


def test_letters_outside_a_to_z_still_tell_numbers_apart():
    assert reference_key("Ä-1") != reference_key("Ö-1")
    assert reference_key("Ä-01") == reference_key("ä 1")


def test_same_number_from_another_supplier_is_allowed(session, admin, supplier):
    other = contact(session, admin, name="Andere Drukker", kind=ContactKind.SUPPLIER)
    _create(session, admin, supplier, REFERENCE)
    assert _create(session, admin, other, REFERENCE).id


def test_another_administration_does_not_influence_the_check(session, admin, supplier):
    second = create_administration(session, name="Tweede Zaak", kvk_number="87654321")
    twin = contact(session, second, name=supplier.name, kind=ContactKind.SUPPLIER)
    _create(session, admin, supplier, REFERENCE)
    assert find_duplicate_purchase(session, second, twin.id, REFERENCE) is None
    assert _create(session, second, twin, REFERENCE).administration_id == second.id


def test_editing_a_draft_into_a_duplicate_is_refused(session, admin, owner, supplier):
    _create(session, admin, supplier, REFERENCE)
    draft = _create(session, admin, supplier, "DV-2026-0500")
    with pytest.raises(InvoiceError, match="already registered"):
        update_purchase_invoice(
            session, admin, draft, supplier, [pline(session, admin, "x", 100)],
            supplier_reference="dv 2026 0412", invoice_date=date(2026, 11, 3), due_date=None,
        )


def test_booking_is_refused_when_a_twin_exists(session, admin, owner, supplier):
    """A twin that got in some other way (import, older data) still cannot be booked."""
    _create(session, admin, supplier, REFERENCE)
    twin = _create(session, admin, supplier, "DV-2026-0500")
    twin.supplier_reference = "dv 2026 0412"
    session.flush()
    with pytest.raises(InvoiceError, match="already registered"):
        book_purchase_invoice(session, admin, twin, owner.id)
