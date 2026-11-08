"""UBL 2.1 Invoice / CreditNote parser (Peppol BIS 3, NLCIUS). XML goes through defusedxml only."""
from datetime import date
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from ledgerline.inbox.extract import ExtractedInvoice, ExtractedLine, ExtractionError
from ledgerline.money import MoneyError, percent_of, to_cents

UBL_MARKERS = (b"urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
               b"urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2")


REVERSE_CATEGORIES = {"AE", "K"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(el: Element | None, name: str) -> list[Element]:
    return [] if el is None else [c for c in el if _local(c.tag) == name]


def _first(el: Element | None, *path: str) -> Element | None:
    for name in path:
        found = _kids(el, name)
        el = found[0] if found else None
        if el is None:
            return None
    return el


def _text(el: Element | None, *path: str) -> str | None:
    node = _first(el, *path)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _cents(el: Element | None, *path: str) -> int | None:
    value = _text(el, *path)
    if value is None:
        return None
    try:
        return to_cents(value.replace(",", "."))
    except MoneyError as exc:
        raise ExtractionError(f"invalid amount {value!r} in the e-invoice") from exc


def _date(el: Element | None, *path: str) -> date | None:
    value = _text(el, *path)
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ExtractionError(f"invalid date {value!r} in the e-invoice") from exc


def _compact(value: str | None) -> str | None:
    return value.replace(" ", "").replace(".", "").upper() if value else None


def _supplier(party: Element | None) -> dict:
    vat = None
    for scheme in _kids(party, "PartyTaxScheme"):
        company = _text(scheme, "CompanyID")
        if company and (_text(scheme, "TaxScheme", "ID") or "VAT").upper() == "VAT":
            vat = company
            break
    kvk = None
    candidates = [
        _first(e, "CompanyID") for e in _kids(party, "PartyLegalEntity")
    ] + [_first(e, "ID") for e in _kids(party, "PartyIdentification")]
    for node in candidates:
        value = (node.text or "").strip() if node is not None else ""
        if value and (node.get("schemeID") == "0106" or value.isdigit() and len(value) == 8):
            kvk = value
            break
    name = _text(party, "PartyLegalEntity", "RegistrationName") or _text(
        party, "PartyName", "Name")
    return {"name": name, "vat": _compact(vat), "kvk": kvk}


def _tax_total(root: Element, currency: str) -> tuple[int | None, dict[str, int]]:
    """(total VAT, {percent text: VAT}) from the TaxTotal in the document currency."""
    total, per_percent = None, {}
    for tax in _kids(root, "TaxTotal"):
        amount = _first(tax, "TaxAmount")
        if amount is not None and amount.get("currencyID", currency) != currency:
            continue
        total = _cents(tax, "TaxAmount")
        for sub in _kids(tax, "TaxSubtotal"):
            percent = _text(sub, "TaxCategory", "Percent")
            tax_amount = _cents(sub, "TaxAmount")
            if percent is not None and tax_amount is not None:
                key = _percent_key(percent)
                per_percent[key] = per_percent.get(key, 0) + tax_amount
        break
    return total, per_percent


def _percent_key(raw: str) -> str:
    try:
        return str(int(float(raw)))
    except ValueError:
        return raw


def _parse_lines(root: Element, credit: bool, sign: int) -> list[tuple[ExtractedLine, bool]]:
    parsed = []
    for line in _kids(root, "CreditNoteLine" if credit else "InvoiceLine"):
        amount = _cents(line, "LineExtensionAmount")
        if amount is None:
            continue
        category = _first(line, "Item", "ClassifiedTaxCategory")
        code = (_text(category, "ID") or "").upper()
        percent = _text(category, "Percent")
        reverse = code in REVERSE_CATEGORIES
        rate = None if reverse else (int(_percent_key(percent)) if percent else None)
        description = (
            _text(line, "Item", "Name") or _text(line, "Item", "Description")
            or _text(line, "Note") or "Line"
        )
        parsed.append((ExtractedLine(description[:500], sign * amount, rate), reverse))
    return parsed


def _assign_vat(lines: list[ExtractedLine], per_percent: dict[str, int], sign: int) -> None:
    """Spread the stated VAT per rate over the lines so the sum equals the invoice's figure."""
    for percent, stated in per_percent.items():
        members = [ln for ln in lines if ln.vat_rate_percent is not None
                   and str(ln.vat_rate_percent) == percent]
        if not members:
            continue
        shares = [percent_of(ln.amount_excl_cents, ln.vat_rate_percent) for ln in members]
        shares[-1] = sign * stated - sum(shares[:-1])
        for ln, share in zip(members, shares, strict=True):
            ln.vat_cents = share


def parse_ubl(content: bytes) -> ExtractedInvoice:
    try:
        root = fromstring(content, forbid_dtd=True)
    except (DefusedXmlException, ParseError) as exc:
        raise ExtractionError("the XML file is not safe or not well-formed") from exc
    kind = _local(root.tag)
    if kind not in {"Invoice", "CreditNote"}:
        raise ExtractionError("the XML file is not a UBL invoice or credit note")
    credit = kind == "CreditNote"
    sign = -1 if credit else 1
    number = _text(root, "ID")
    if not number:
        raise ExtractionError("the e-invoice has no invoice number")
    currency = (_text(root, "DocumentCurrencyCode") or "EUR").upper()
    supplier = _supplier(_first(root, "AccountingSupplierParty", "Party"))
    iban = _compact(_text(root, "PaymentMeans", "PayeeFinancialAccount", "ID"))

    totals = _first(root, "LegalMonetaryTotal")
    excl = _cents(totals, "TaxExclusiveAmount")
    if excl is None:
        excl = _cents(totals, "LineExtensionAmount")
    incl = _cents(totals, "TaxInclusiveAmount")
    if incl is None:
        incl = _cents(totals, "PayableAmount")
    vat, per_percent = _tax_total(root, currency)

    parsed = _parse_lines(root, credit, sign)
    lines = [ln for ln, _ in parsed]
    reverse = any(flag for _, flag in parsed) or any(
        (_text(s, "TaxCategory", "ID") or "").upper() in REVERSE_CATEGORIES
        for t in _kids(root, "TaxTotal") for s in _kids(t, "TaxSubtotal")
    )
    _assign_vat(lines, per_percent, sign)
    warnings: list[str] = []
    if currency != "EUR":
        warnings.append(f"The invoice is in {currency}; only euro amounts can be booked.")
    if credit:
        warnings.append("This is a credit note: amounts are negative.")
    if reverse:
        warnings.append("VAT is reverse-charged (btw verlegd): the supplier charges no VAT.")
    if not lines and excl is not None:
        lines = [ExtractedLine(f"Invoice {number}", sign * excl, None)]
        warnings.append("The e-invoice has no lines; one line was made from the totals.")

    excl = None if excl is None else sign * excl
    incl = None if incl is None else sign * incl
    vat = None if vat is None else sign * vat
    if vat is None and excl is not None and incl is not None:
        vat = incl - excl
    if None not in (excl, vat, incl) and not reverse and abs(excl + vat - incl) > 1:
        warnings.append("Totals do not add up: excl. VAT + VAT is not the total payable.")
    if excl is not None and lines and abs(sum(ln.amount_excl_cents for ln in lines) - excl) > 1:
        warnings.append("The lines do not add up to the total excl. VAT.")
    return ExtractedInvoice(
        supplier_name=supplier["name"],
        supplier_vat_number=supplier["vat"],
        supplier_kvk=supplier["kvk"],
        supplier_iban=iban,
        invoice_number=number,
        invoice_date=_date(root, "IssueDate"),
        due_date=_date(root, "DueDate"),
        currency=currency,
        total_excl_cents=excl,
        total_vat_cents=vat,
        total_incl_cents=incl,
        reverse_charge=reverse,
        lines=lines,
        warnings=warnings,
    )


class UblExtractor:
    method = "ubl"

    def can_handle(self, content_type: str, content: bytes) -> bool:
        return content_type == "application/xml" and any(m in content[:4096] for m in UBL_MARKERS)

    def extract(self, content_type: str, content: bytes) -> ExtractedInvoice:
        return parse_ubl(content)
