"""Turning a received document into proposed purchase-invoice fields.

Extractors are tried in order of trust: structured UBL first (exact), then the PDF
text layer, then the optional vision provider. Every result is a proposal that the
owner reviews before anything is booked.
"""
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class ExtractedLine:
    description: str
    amount_excl_cents: int
    vat_rate_percent: int | None = None  # 21, 9, 0 or None when unknown
    vat_cents: int | None = None


@dataclass
class ExtractedInvoice:
    supplier_name: str | None = None
    supplier_vat_number: str | None = None
    supplier_kvk: str | None = None
    supplier_iban: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = "EUR"
    total_excl_cents: int | None = None
    total_vat_cents: int | None = None
    total_incl_cents: int | None = None
    reverse_charge: bool = False
    # invoice | statement | reminder | contract | own_sales; only "invoice" is a purchase
    document_type: str = "invoice"
    lines: list[ExtractedLine] = field(default_factory=list)
    # Problems the reviewer should look at, e.g. "totals do not add up".
    warnings: list[str] = field(default_factory=list)
    # Set when the document is a delivery-platform payout statement, not a supplier invoice.
    platform_statement: dict | None = None

    def to_json(self) -> dict:
        data = asdict(self)
        for key in ("invoice_date", "due_date"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        return data

    @classmethod
    def from_json(cls, data: dict) -> "ExtractedInvoice":
        data = dict(data)
        for key in ("invoice_date", "due_date"):
            if data.get(key):
                data[key] = date.fromisoformat(data[key])
        data["lines"] = [ExtractedLine(**line) for line in data.get("lines", [])]
        return cls(**data)


class ExtractionError(Exception):
    """The document could not be read; the message is shown to the reviewer."""


class Extractor(Protocol):
    method: str  # stored in InboxDocument.extraction_method

    def can_handle(self, content_type: str, content: bytes) -> bool: ...

    def extract(self, content_type: str, content: bytes) -> ExtractedInvoice: ...
