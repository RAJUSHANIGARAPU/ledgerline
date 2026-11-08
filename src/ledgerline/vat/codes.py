"""BTW codes and their place on the Dutch VAT return (btw-aangifte).

Each rubriek's source is listed in ledgerline.vat.vat_return.SOURCES (checked 2026-11-08).
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Direction(StrEnum):
    SALES = "sales"
    PURCHASE = "purchase"
    # Year-end corrections booked by hand in the journal, never on an invoice.
    CORRECTION = "correction"


@dataclass(frozen=True)
class VatCode:
    code: str
    label: str
    direction: Direction
    rate: Decimal
    # Rubriek where the net amount (grondslag) is reported, if any.
    base_rubriek: str | None
    # Rubriek where VAT owed is reported (sales VAT, or self-assessed reverse charge).
    vat_rubriek: str | None
    # Whether the VAT is deductible as voorbelasting (rubriek 5b).
    deductible: bool = False
    # Self-assessed: the buyer owes and deducts the VAT; nothing is paid to the supplier.
    reverse_charge: bool = False
    # Reported on the ICP declaration (opgaaf intracommunautaire prestaties).
    icp: bool = False
    # Text required on the invoice for this treatment.
    invoice_note: str | None = None


_CODES = [
    VatCode("V21", "Sales 21%", Direction.SALES, Decimal(21), "1a", "1a"),
    VatCode("V9", "Sales 9%", Direction.SALES, Decimal(9), "1b", "1b"),
    VatCode("V0", "Sales 0% / not taxed in NL", Direction.SALES, Decimal(0), "1e", None),
    VatCode("VRV", "Sales, domestic reverse charge", Direction.SALES, Decimal(0), "1e", None,
            invoice_note="btw verlegd"),
    VatCode("VEU", "Services to EU business (ICP)", Direction.SALES, Decimal(0), "3b", None,
            icp=True, invoice_note="btw verlegd"),
    VatCode("VEUG", "Goods to EU business (ICP)", Direction.SALES, Decimal(0), "3b", None,
            icp=True, invoice_note="intracommunautaire levering"),
    VatCode("VEX", "Export of goods outside the EU", Direction.SALES, Decimal(0), "3a", None),
    # Services taxed in the customer's non-EU country are not reported on the return.
    VatCode("VNEU", "Services to customers outside the EU", Direction.SALES, Decimal(0),
            None, None),
    VatCode("VVR", "Sales exempt from VAT", Direction.SALES, Decimal(0), None, None),
    VatCode("VKOR", "Sales under the KOR", Direction.SALES, Decimal(0), None, None,
            invoice_note="vrijgesteld van btw (KOR)"),
    VatCode("I21", "Purchase 21%", Direction.PURCHASE, Decimal(21), None, None, deductible=True),
    VatCode("I9", "Purchase 9%", Direction.PURCHASE, Decimal(9), None, None, deductible=True),
    VatCode("I0", "Purchase without VAT", Direction.PURCHASE, Decimal(0), None, None),
    VatCode("IRV21", "Purchase, domestic reverse charge 21%", Direction.PURCHASE, Decimal(21),
            "2a", "2a", deductible=True, reverse_charge=True),
    VatCode("IEU21", "Purchase from EU 21%", Direction.PURCHASE, Decimal(21), "4b", "4b",
            deductible=True, reverse_charge=True),
    VatCode("IEU9", "Purchase from EU 9%", Direction.PURCHASE, Decimal(9), "4b", "4b",
            deductible=True, reverse_charge=True),
    VatCode("INEU21", "Purchase from outside the EU 21%", Direction.PURCHASE, Decimal(21),
            "4a", "4a", deductible=True, reverse_charge=True),
    # BTW owed on private use of business assets; the turnover column of 1d stays empty.
    VatCode("VPRIV", "Private use correction", Direction.CORRECTION, Decimal(21), None, "1d"),
]

VAT_CODES: dict[str, VatCode] = {c.code: c for c in _CODES}


def get(code: str) -> VatCode:
    try:
        return VAT_CODES[code]
    except KeyError:
        raise ValueError(f"unknown VAT code {code!r}") from None


def for_direction(direction: Direction) -> list[VatCode]:
    return [c for c in _CODES if c.direction is direction]
