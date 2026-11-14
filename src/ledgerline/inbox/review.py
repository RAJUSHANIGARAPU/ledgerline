"""Turning an extracted document into a purchase invoice: supplier match, VAT mapping, creation."""
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.inbox.extract import ExtractedInvoice, ExtractedLine
from ledgerline.invoicing import forms
from ledgerline.invoicing.calc import InvoiceError
from ledgerline.invoicing.service import (
    book_purchase_invoice,
    create_purchase_invoice,
    find_duplicate_purchase,
)
from ledgerline.models import (
    Account,
    AccountType,
    Administration,
    Attachment,
    Contact,
    ContactKind,
    InboxDocument,
    InboxStatus,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    User,
)
from ledgerline.money import percent_of
from ledgerline.platform.validation import check_iban, check_kvk, check_vat, normalise_vat

EU_NON_NL = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR", "HU", "IE",
    "IT", "LT", "LU", "LV", "MT", "PL", "PT", "RO", "SE", "SI", "SK",
}
FALLBACK_ACCOUNT = "4900"
METHODS = {
    "ubl": ("Read from e-invoice (exact)", "good"),
    "pdf_text": ("Read from PDF text — check", "warn"),
    "vision": ("Read from photo — check every figure", "bad"),
    "none": ("Could not be read — enter it by hand", "bad"),
}
DOCUMENT_TYPES = {
    "statement": "Payout statement, not an invoice",
    "reminder": "Payment reminder, not an invoice",
    "contract": "Contract or order form, not an invoice",
    "own_sales": "Looks like your own sales invoice",
}
IDENTIFIERS = ("supplier_vat_number", "supplier_kvk", "supplier_iban")
_LEGAL_SUFFIX = re.compile(r"(bv|nv|vof|cv|bvba|gmbh|ltd|inc)$")


@dataclass
class Proposal:
    extracted: ExtractedInvoice | None
    contact: Contact | None = None
    matched_by: str | None = None
    reference: str = ""
    invoice_date: str = ""
    due_date: str = ""
    rows: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate: PurchaseInvoice | None = None


def method_label(method: str | None) -> tuple[str, str]:
    return METHODS.get(method or "none", METHODS["none"])


def _name_key(name: str | None) -> str:
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return _LEGAL_SUFFIX.sub("", key)


def _suppliers(session: Session, admin_id: int) -> list[Contact]:
    return list(session.scalars(
        select(Contact).where(
            Contact.administration_id == admin_id,
            Contact.kind.in_((ContactKind.SUPPLIER, ContactKind.BOTH)),
            Contact.anonymised_at.is_(None),
        ).order_by(Contact.name)
    ))


def match_supplier(
    session: Session, admin_id: int, extracted: ExtractedInvoice
) -> tuple[Contact | None, str | None]:
    suppliers = _suppliers(session, admin_id)
    usable = {
        f: getattr(extracted, f) or "" for f in IDENTIFIERS if f not in extracted.flagged_fields
    }
    vat = normalise_vat(usable.get("supplier_vat_number", ""))
    if vat:
        for contact in suppliers:
            if contact.vat_number and normalise_vat(contact.vat_number) == vat:
                return contact, "VAT number"
    kvk = re.sub(r"\s", "", usable.get("supplier_kvk", ""))
    if kvk:
        for contact in suppliers:
            if contact.kvk_number and re.sub(r"\s", "", contact.kvk_number) == kvk:
                return contact, "KvK number"
    iban = usable.get("supplier_iban", "").replace(" ", "").upper()
    if iban:
        for contact in suppliers:
            if contact.iban and contact.iban.replace(" ", "").upper() == iban:
                return contact, "IBAN"
    key = _name_key(extracted.supplier_name)
    if key:
        for contact in suppliers:
            if _name_key(contact.name) == key:
                return contact, "name"
    return None, None


def default_account(session: Session, admin_id: int, contact: Contact | None) -> Account | None:
    if contact is not None:
        last = session.scalar(
            select(PurchaseInvoiceLine.expense_account_id)
            .join(PurchaseInvoice, PurchaseInvoice.id == PurchaseInvoiceLine.invoice_id)
            .where(PurchaseInvoice.administration_id == admin_id,
                   PurchaseInvoice.contact_id == contact.id)
            .order_by(PurchaseInvoice.id.desc(), PurchaseInvoiceLine.position)
            .limit(1)
        )
        account = session.get(Account, last) if last else None
        if account and account.administration_id == admin_id and account.is_active:
            return account
    accounts = list(session.scalars(
        select(Account).where(Account.administration_id == admin_id,
                              Account.type == AccountType.EXPENSE, Account.is_active.is_(True))
        .order_by(Account.code)
    ))
    preferred = next((a for a in accounts if a.code == FALLBACK_ACCOUNT), None)
    return preferred or (accounts[0] if accounts else None)


def supplier_country(extracted: ExtractedInvoice, contact: Contact | None) -> str:
    vat = normalise_vat(extracted.supplier_vat_number or "")
    if vat[:2].isalpha():
        return vat[:2]
    return (contact.country if contact else "NL") or "NL"


def vat_code_for(line: ExtractedLine, reverse_charge: bool, country: str) -> str | None:
    """The purchase VAT code for a line, or None when the rate cannot be told."""
    rate = line.vat_rate_percent
    if rate is None and line.vat_cents is not None and line.amount_excl_cents:
        rate = next((r for r in (21, 9, 0)
                     if abs(percent_of(line.amount_excl_cents, r) - line.vat_cents) <= 1), None)
    if rate is None:
        return ("IEU21" if country in EU_NON_NL else "IRV21") if reverse_charge else None
    return {21: "I21", 9: "I9", 0: "I0"}.get(rate)


def _rows(extracted: ExtractedInvoice, account: Account | None, country: str,
          warnings: list[str]) -> list[dict[str, str]]:
    rows = []
    for number, line in enumerate(extracted.lines, 1):
        code = vat_code_for(line, extracted.reverse_charge, country)
        if code is None:
            code = ""
            warnings.append(f"Line {number}: the VAT rate could not be read; choose the VAT code.")
        reverse = code in ("IRV21", "IEU21")
        stated = None if reverse or code == "I0" else line.vat_cents
        rows.append({
            "description": line.description, "account": str(account.id) if account else "",
            "amount": forms.money_text(line.amount_excl_cents), "vat_code": code,
            "vat": forms.money_text(stated) if stated is not None else "",
            "vat_auto": "0" if stated is not None else "1",
        })
    return rows


def build_proposal(session: Session, admin: Administration, document: InboxDocument) -> Proposal:
    if not document.extraction:
        account = default_account(session, admin.id, None)
        row = forms.blank_purchase_row(account.id if account else "")
        return Proposal(None, rows=[row])
    extracted = ExtractedInvoice.from_json(document.extraction)
    contact, how = match_supplier(session, admin.id, extracted)
    country = supplier_country(extracted, contact)
    warnings = list(extracted.warnings)
    account = default_account(session, admin.id, contact)
    proposal = Proposal(
        extracted, contact, how, extracted.invoice_number or "",
        extracted.invoice_date.isoformat() if extracted.invoice_date else "",
        extracted.due_date.isoformat() if extracted.due_date else "",
        _rows(extracted, account, country, warnings), warnings,
    )
    if not proposal.rows:
        proposal.rows = [forms.blank_purchase_row(account.id if account else "")]
    if contact and proposal.reference:
        proposal.duplicate = find_duplicate_purchase(session, admin, contact.id, proposal.reference)
    return proposal


@dataclass
class ReviewForm:
    contact_id: str
    supplier_reference: str
    invoice_date: str
    due_date: str
    description: str
    rows: list[dict[str, str]]
    new_name: str = ""
    new_vat: str = ""
    new_kvk: str = ""
    new_iban: str = ""
    new_country: str = "NL"


def _new_supplier(session: Session, admin: Administration, data: ReviewForm) -> Contact:
    name = data.new_name.strip()
    if not name:
        raise InvoiceError("enter a name for the new supplier")
    problems = []
    vat, error = check_vat(data.new_vat)
    problems.append(error)
    kvk, error = check_kvk(data.new_kvk)
    problems.append(error)
    iban, error = check_iban(data.new_iban)
    problems.append(error)
    if any(problems):
        raise InvoiceError(*problems)
    contact = Contact(
        administration_id=admin.id, kind=ContactKind.SUPPLIER, name=name[:200],
        vat_number=vat, kvk_number=kvk, iban=iban,
        country=(data.new_country or "NL").strip().upper()[:2] or "NL",
    )
    session.add(contact)
    session.flush()
    return contact


def _contact(session: Session, admin: Administration, data: ReviewForm) -> Contact:
    if data.contact_id == "new":
        return _new_supplier(session, admin, data)
    contact = session.get(Contact, int(data.contact_id)) if data.contact_id.isdigit() else None
    if contact is None or contact.administration_id != admin.id:
        raise InvoiceError("choose a supplier")
    return contact


def _link(document: InboxDocument, invoice: PurchaseInvoice, user: User) -> None:
    document.status = InboxStatus.BOOKED
    document.purchase_invoice_id = invoice.id
    document.reviewed_by_id = user.id


def create_purchase(
    session: Session, admin: Administration, user: User, document: InboxDocument,
    data: ReviewForm,
) -> PurchaseInvoice:
    """Create the draft with the document attached. The caller commits."""
    if document.status == InboxStatus.BOOKED:
        raise InvoiceError("this document already has a purchase invoice")
    invoice_date = forms.parse_date(data.invoice_date, "invoice date")
    due_date = forms.parse_optional_date(data.due_date, "due date")
    lines = forms.purchase_inputs(data.rows)
    stated = (document.extraction or {}).get("total_incl_cents") or (
        document.extraction or {}).get("total_excl_cents")
    if stated and not any(line.amount_excl_cents for line in lines):
        raise InvoiceError(
            f"the document shows a total of {forms.money_text(stated)} but no line has an "
            "amount; enter the lines before creating the invoice")
    unset = [str(n) for n, row in enumerate(data.rows, 1)
             if not row["vat_code"] and any(row[k].strip() for k in ("description", "amount"))]
    if unset:
        raise InvoiceError(f"line {', '.join(unset)}: choose a VAT code")
    attachment = session.get(Attachment, document.attachment_id)
    contact = _contact(session, admin, data)
    invoice = create_purchase_invoice(
        session, admin, contact, lines, supplier_reference=data.supplier_reference,
        invoice_date=invoice_date, due_date=due_date, description=data.description or None,
        attachment=attachment,
    )
    _link(document, invoice, user)
    audit.record(
        session, "inbox.create_purchase", user_id=user.id, administration_id=admin.id,
        entity_type="inbox_document", entity_id=document.id,
        details={"purchase_invoice_id": invoice.id},
    )
    return invoice


def book(session: Session, admin: Administration, user: User, invoice: PurchaseInvoice) -> None:
    book_purchase_invoice(session, admin, invoice, user.id)


def link_existing(
    session: Session, admin: Administration, user: User, document: InboxDocument, contact_id: str,
    reference: str,
) -> PurchaseInvoice:
    contact = session.get(Contact, int(contact_id)) if contact_id.isdigit() else None
    existing = (
        find_duplicate_purchase(session, admin, contact.id, reference)
        if contact and contact.administration_id == admin.id and reference.strip() else None
    )
    if existing is None:
        raise InvoiceError("no purchase invoice with that supplier and number exists")
    _link(document, existing, user)
    audit.record(
        session, "inbox.link_duplicate", user_id=user.id, administration_id=admin.id,
        entity_type="inbox_document", entity_id=document.id,
        details={"purchase_invoice_id": existing.id},
    )
    return existing


def reject(session: Session, admin: Administration, user: User, document: InboxDocument) -> None:
    document.status = InboxStatus.REJECTED
    document.reviewed_by_id = user.id
    audit.record(session, "inbox.reject", user_id=user.id, administration_id=admin.id,
                 entity_type="inbox_document", entity_id=document.id)


def _document_type():
    return func.coalesce(InboxDocument.extraction["document_type"].as_string(), "invoice")


def in_tab(tab: str, admin_id: int):
    """The where-clause for an inbox tab; documents that are not purchase invoices get their own."""
    status = InboxStatus(tab) if tab != "other" else InboxStatus.REVIEW
    clause = [InboxDocument.administration_id == admin_id, InboxDocument.status == status]
    if tab == "review":
        clause.append(_document_type() == "invoice")
    elif tab == "other":
        clause.append(_document_type() != "invoice")
    return clause


def status_counts(session: Session, admin_id: int) -> dict[str, int]:
    """Documents per tab key: the status values, plus 'other' for non-invoices waiting in review."""
    rows = session.execute(
        select(InboxDocument.status, func.count()).where(
            InboxDocument.administration_id == admin_id).group_by(InboxDocument.status)
    ).all()
    counts = dict(rows)
    other = session.scalar(select(func.count()).where(*in_tab("other", admin_id))) or 0
    counts[InboxStatus.REVIEW] = counts.get(InboxStatus.REVIEW, 0) - other
    counts["other"] = other
    return counts


def reject_non_invoices(session: Session, admin: Administration, user: User) -> int:
    """Reject every document in the 'Not invoices' tab; returns how many."""
    documents = list(session.scalars(select(InboxDocument).where(*in_tab("other", admin.id))))
    for document in documents:
        reject(session, admin, user, document)
    return len(documents)
