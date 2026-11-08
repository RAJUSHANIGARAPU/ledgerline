"""UBL 2.1 Invoice / CreditNote following Peppol BIS Billing 3.0, built with the stdlib."""
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

from ledgerline.invoicing.calc import vat_by_code
from ledgerline.models import Administration, Contact, SalesInvoice
from ledgerline.money import from_cents
from ledgerline.vat import codes

CUSTOMIZATION = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
PROFILE = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

# our VAT code -> (EN 16931 category, exemption reason)
CATEGORY = {
    "V21": ("S", None),
    "V9": ("S", None),
    "V0": ("Z", None),
    "VRV": ("AE", "Reverse charge (btw verlegd)"),
    "VEU": ("K", "Intra-community supply (btw verlegd)"),
    "VEUG": ("K", "Intra-community supply of goods"),
    "VEX": ("G", "Export outside the EU"),
    "VNEU": ("G", "Service to a customer outside the EU"),
    "VVR": ("E", "Exempt from VAT"),
    "VKOR": ("E", "Exempt from VAT under the small business scheme (KOR)"),
}


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    prefix, name = tag.split(":")
    el = ET.SubElement(parent, _q(prefix, name), attrs)
    if text is not None:
        el.text = text
    return el


def _amount(parent: ET.Element, tag: str, cents: int) -> ET.Element:
    return _sub(parent, tag, f"{from_cents(cents):.2f}", currencyID="EUR")


def _endpoint(vat: str | None, kvk: str | None, email: str | None) -> tuple[str, str]:
    if vat:
        return "9944", re.sub(r"\s", "", vat)
    if kvk:
        return "0106", re.sub(r"\s", "", kvk)
    return "EM", email or "unknown"


def _party(parent: ET.Element, tag: str, name: str, source: Administration | Contact) -> None:
    wrapper = _sub(parent, tag)
    party = _sub(wrapper, "cac:Party")
    scheme, value = _endpoint(source.vat_number, source.kvk_number, source.email)
    _sub(party, "cbc:EndpointID", value, schemeID=scheme)
    _sub(_sub(party, "cac:PartyName"), "cbc:Name", name)
    address = _sub(party, "cac:PostalAddress")
    if source.address_line:
        _sub(address, "cbc:StreetName", source.address_line)
    if source.city:
        _sub(address, "cbc:CityName", source.city)
    if source.postcode:
        _sub(address, "cbc:PostalZone", source.postcode)
    _sub(_sub(address, "cac:Country"), "cbc:IdentificationCode", source.country or "NL")
    if source.vat_number:
        tax = _sub(party, "cac:PartyTaxScheme")
        _sub(tax, "cbc:CompanyID", re.sub(r"\s", "", source.vat_number))
        _sub(_sub(tax, "cac:TaxScheme"), "cbc:ID", "VAT")
    legal = _sub(party, "cac:PartyLegalEntity")
    _sub(legal, "cbc:RegistrationName", name)
    if source.kvk_number:
        _sub(legal, "cbc:CompanyID", source.kvk_number, schemeID="0106")


def _tax_category(
    parent: ET.Element, tag: str, category: str, percent: Decimal, reason: str | None
) -> None:
    cat = _sub(parent, tag)
    _sub(cat, "cbc:ID", category)
    _sub(cat, "cbc:Percent", f"{percent:.2f}")
    if reason and category not in ("S", "Z"):
        _sub(cat, "cbc:TaxExemptionReason", reason)
    _sub(_sub(cat, "cac:TaxScheme"), "cbc:ID", "VAT")


def build_ubl(invoice: SalesInvoice, admin: Administration, original_number: str | None) -> bytes:
    is_credit = invoice.credit_note_of_id is not None
    root_name = "CreditNote" if is_credit else "Invoice"
    ET.register_namespace("cac", NS["cac"])
    ET.register_namespace("cbc", NS["cbc"])
    ET.register_namespace("", NS["cn" if is_credit else "inv"])
    root = ET.Element(_q("cn" if is_credit else "inv", root_name))

    _sub(root, "cbc:CustomizationID", CUSTOMIZATION)
    _sub(root, "cbc:ProfileID", PROFILE)
    _sub(root, "cbc:ID", invoice.number)
    _sub(root, "cbc:IssueDate", invoice.issue_date.isoformat())
    if not is_credit:
        _sub(root, "cbc:DueDate", invoice.due_date.isoformat())
    _sub(root, "cbc:CreditNoteTypeCode" if is_credit else "cbc:InvoiceTypeCode",
         "381" if is_credit else "380")
    if invoice.notes:
        _sub(root, "cbc:Note", invoice.notes)
    _sub(root, "cbc:DocumentCurrencyCode", "EUR")
    _sub(root, "cbc:BuyerReference", invoice.reference or invoice.number)
    if is_credit and original_number:
        ref = _sub(_sub(root, "cac:BillingReference"), "cac:InvoiceDocumentReference")
        _sub(ref, "cbc:ID", original_number)

    _party(root, "cac:AccountingSupplierParty", admin.name, admin)
    _party(root, "cac:AccountingCustomerParty", invoice.contact.name, invoice.contact)
    if invoice.supply_date:
        _sub(_sub(root, "cac:Delivery"), "cbc:ActualDeliveryDate", invoice.supply_date.isoformat())
    means = _sub(root, "cac:PaymentMeans")
    _sub(means, "cbc:PaymentMeansCode", "30")
    _sub(means, "cbc:PaymentID", invoice.number)
    if admin.iban:
        account = _sub(means, "cac:PayeeFinancialAccount")
        _sub(account, "cbc:ID", re.sub(r"\s", "", admin.iban))
        if admin.bic:
            _sub(_sub(account, "cac:FinancialInstitutionBranch"), "cbc:ID", admin.bic)
    if not is_credit:
        terms = (invoice.due_date - invoice.issue_date).days
        _sub(_sub(root, "cac:PaymentTerms"), "cbc:Note", f"Betaling binnen {terms} dagen")

    per_code = vat_by_code((line.vat_code, line.line_excl_cents) for line in invoice.lines)
    groups: dict[tuple[str, Decimal, str | None], list[int]] = {}
    for code, (base, vat) in per_code.items():
        category, reason = CATEGORY[code]
        key = (category, codes.get(code).rate, reason)
        entry = groups.setdefault(key, [0, 0])
        entry[0] += base
        entry[1] += vat
    tax_total = _sub(root, "cac:TaxTotal")
    _amount(tax_total, "cbc:TaxAmount", abs(invoice.total_vat_cents))
    for (category, percent, reason), (base, vat) in groups.items():
        sub = _sub(tax_total, "cac:TaxSubtotal")
        _amount(sub, "cbc:TaxableAmount", abs(base))
        _amount(sub, "cbc:TaxAmount", abs(vat))
        _tax_category(sub, "cac:TaxCategory", category, percent, reason)

    totals = _sub(root, "cac:LegalMonetaryTotal")
    _amount(totals, "cbc:LineExtensionAmount", abs(invoice.total_excl_cents))
    _amount(totals, "cbc:TaxExclusiveAmount", abs(invoice.total_excl_cents))
    _amount(totals, "cbc:TaxInclusiveAmount", abs(invoice.total_incl_cents))
    _amount(totals, "cbc:PayableAmount", abs(invoice.total_incl_cents))

    line_tag = "cac:CreditNoteLine" if is_credit else "cac:InvoiceLine"
    qty_tag = "cbc:CreditedQuantity" if is_credit else "cbc:InvoicedQuantity"
    for number, line in enumerate(invoice.lines, 1):
        el = _sub(root, line_tag)
        _sub(el, "cbc:ID", str(number))
        _sub(el, qty_tag, f"{abs(line.quantity).normalize():f}", unitCode="C62")
        _amount(el, "cbc:LineExtensionAmount", abs(line.line_excl_cents))
        item = _sub(el, "cac:Item")
        _sub(item, "cbc:Name", line.description[:200])
        category, _ = CATEGORY[line.vat_code]
        _tax_category(item, "cac:ClassifiedTaxCategory", category,
                      codes.get(line.vat_code).rate, None)
        _amount(_sub(el, "cac:Price"), "cbc:PriceAmount", abs(line.unit_price_cents))
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
