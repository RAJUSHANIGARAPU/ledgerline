"""Sales and purchase invoice use cases. Callable without HTTP; callers commit."""
import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.config import get_settings
from ledgerline.invoicing.calc import (
    InvoiceError,
    allocate_vat,
    line_amount,
    side,
    vat_by_code,
)
from ledgerline.ledger.service import (
    LedgerError,
    LineSpec,
    next_number,
    post_entry,
    system_account,
)
from ledgerline.models import (
    Account,
    AccountType,
    Administration,
    Attachment,
    Contact,
    ContactKind,
    InvoiceStatus,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    SalesInvoice,
    SalesInvoiceLine,
    SystemRole,
)
from ledgerline.vat import codes
from ledgerline.vat.codes import Direction

DEFAULT_REVENUE_CODE = "8000"
KOR_CODE = "VKOR"
ALLOWED_UPLOADS = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


@dataclass
class SalesLineInput:
    description: str
    quantity: Decimal
    unit_price_cents: int
    vat_code: str | None = None
    revenue_account_id: int | None = None


@dataclass
class PurchaseLineInput:
    description: str
    expense_account_id: int
    amount_excl_cents: int
    vat_code: str | None = None
    vat_cents: int | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _account(
    session: Session, admin: Administration, account_id: int, kind: AccountType
) -> Account:
    account = session.get(Account, account_id)
    if (
        account is None
        or account.administration_id != admin.id
        or account.type != kind
        or not account.is_active
    ):
        raise InvoiceError(f"account {account_id} is not an active {kind} account")
    return account


def _contact(admin: Administration, contact: Contact, kinds: tuple[str, ...]) -> None:
    if contact.administration_id != admin.id:
        raise InvoiceError("contact belongs to another administration")
    if contact.kind not in kinds:
        raise InvoiceError(f"contact {contact.name} is not a {kinds[0]}")


# ---------------------------------------------------------------- sales


def _sales_codes() -> set[str]:
    return {c.code for c in codes.for_direction(Direction.SALES)}


def _build_sales_lines(
    session: Session, admin: Administration, inputs: list[SalesLineInput]
) -> list[SalesInvoiceLine]:
    if not inputs:
        raise InvoiceError("add at least one line")
    default_account = session.scalar(
        select(Account).where(
            Account.administration_id == admin.id, Account.code == DEFAULT_REVENUE_CODE
        )
    )
    lines = []
    for position, item in enumerate(inputs):
        if not item.description.strip():
            raise InvoiceError(f"line {position + 1}: description is required")
        if item.quantity <= 0:
            raise InvoiceError(f"line {position + 1}: quantity must be positive")
        account_id = item.revenue_account_id or (default_account.id if default_account else None)
        if account_id is None:
            raise InvoiceError(f"line {position + 1}: choose a revenue account")
        account = _account(session, admin, account_id, AccountType.REVENUE)
        code = KOR_CODE if admin.kor else (item.vat_code or account.default_vat_code or "V21")
        if code not in _sales_codes():
            raise InvoiceError(f"line {position + 1}: {code} is not a sales VAT code")
        lines.append(
            SalesInvoiceLine(
                position=position,
                description=item.description.strip()[:500],
                quantity=item.quantity,
                unit_price_cents=item.unit_price_cents,
                vat_code=code,
                revenue_account_id=account.id,
                line_excl_cents=line_amount(item.quantity, item.unit_price_cents),
            )
        )
    return lines


def recompute_sales_totals(invoice: SalesInvoice) -> None:
    per_code = vat_by_code((line.vat_code, line.line_excl_cents) for line in invoice.lines)
    invoice.total_excl_cents = sum(line.line_excl_cents for line in invoice.lines)
    invoice.total_vat_cents = sum(vat for _, vat in per_code.values())
    invoice.total_incl_cents = invoice.total_excl_cents + invoice.total_vat_cents


def create_sales_invoice(
    session: Session,
    admin: Administration,
    contact: Contact,
    lines: list[SalesLineInput],
    *,
    issue_date: date | None = None,
    due_date: date | None = None,
    supply_date: date | None = None,
    reference: str | None = None,
    notes: str | None = None,
) -> SalesInvoice:
    _contact(admin, contact, (ContactKind.CUSTOMER, ContactKind.BOTH))
    issue_date = issue_date or date.today()
    invoice = SalesInvoice(
        administration_id=admin.id,
        contact_id=contact.id,
        status=InvoiceStatus.DRAFT,
        issue_date=issue_date,
        supply_date=supply_date,
        due_date=due_date or issue_date + timedelta(days=admin.payment_terms_days),
        reference=(reference or None),
        notes=(notes or None),
    )
    invoice.lines = _build_sales_lines(session, admin, lines)
    recompute_sales_totals(invoice)
    session.add(invoice)
    session.flush()
    return invoice


def update_sales_invoice(
    session: Session,
    admin: Administration,
    invoice: SalesInvoice,
    contact: Contact,
    lines: list[SalesLineInput],
    *,
    issue_date: date,
    due_date: date,
    supply_date: date | None = None,
    reference: str | None = None,
    notes: str | None = None,
) -> SalesInvoice:
    if invoice.status != InvoiceStatus.DRAFT:
        raise InvoiceError("an issued invoice cannot be edited; issue a credit note instead")
    _contact(admin, contact, (ContactKind.CUSTOMER, ContactKind.BOTH))
    new_lines = _build_sales_lines(session, admin, lines)
    invoice.contact_id = contact.id
    invoice.contact = contact
    invoice.issue_date = issue_date
    invoice.due_date = due_date
    invoice.supply_date = supply_date
    invoice.reference = reference or None
    invoice.notes = notes or None
    invoice.lines = new_lines
    recompute_sales_totals(invoice)
    session.flush()
    return invoice


def delete_draft_sales_invoice(session: Session, invoice: SalesInvoice) -> None:
    if invoice.status != InvoiceStatus.DRAFT or invoice.number:
        raise InvoiceError("only drafts can be deleted")
    session.delete(invoice)
    session.flush()


def sales_issue_problems(admin: Administration, invoice: SalesInvoice) -> list[str]:
    """Missing items under the Dutch invoice requirements (factuureisen)."""
    problems = []
    for label, value in (
        ("your business name", admin.name),
        ("your business address", admin.address_line),
        ("your postcode", admin.postcode),
        ("your city", admin.city),
        ("your VAT number", admin.vat_number),
        ("your KvK number", admin.kvk_number),
    ):
        if not (value or "").strip():
            problems.append(f"Settings: {label} is missing")
    customer = invoice.contact
    for label, value in (
        ("name", customer.name),
        ("address", customer.address_line),
        ("city", customer.city),
    ):
        if not (value or "").strip():
            problems.append(f"Customer {label} is missing")
    if not invoice.lines:
        problems.append("The invoice has no lines")
    for number, line in enumerate(invoice.lines, 1):
        if not line.description.strip():
            problems.append(f"Line {number}: description is missing")
        if line.quantity == 0:
            problems.append(f"Line {number}: quantity is missing")
    shifted = any(
        codes.get(line.vat_code).invoice_note == "btw verlegd" or codes.get(line.vat_code).icp
        for line in invoice.lines
    )
    if shifted and not (customer.vat_number or "").strip():
        problems.append("Customer VAT number is required for reverse-charge / EU supplies")
    return problems


def _sales_entry_lines(invoice: SalesInvoice, session: Session) -> list[LineSpec]:
    admin_id = invoice.administration_id
    debtors = system_account(session, admin_id, SystemRole.DEBTORS)
    specs: list[LineSpec] = []
    debit, credit = side(invoice.total_incl_cents, credit_normal=False)
    if invoice.total_incl_cents:
        specs.append(
            LineSpec(debtors.id, debit, credit, f"Invoice {invoice.number}", invoice.contact_id)
        )
    for line in invoice.lines:
        if line.line_excl_cents:
            debit, credit = side(line.line_excl_cents, credit_normal=True)
            specs.append(
                LineSpec(
                    line.revenue_account_id, debit, credit, line.description[:300],
                    contact_id=invoice.contact_id, vat_code=line.vat_code, vat_role="base",
                )
            )
    per_code = vat_by_code((line.vat_code, line.line_excl_cents) for line in invoice.lines)
    for code, (_, vat) in per_code.items():
        if not vat:
            continue
        rate = codes.get(code).rate
        role = SystemRole.VAT_PAYABLE_HIGH if rate == 21 else SystemRole.VAT_PAYABLE_LOW
        if rate not in (21, 9):
            raise InvoiceError(f"VAT rate {rate}% has no payable account")
        debit, credit = side(vat, credit_normal=True)
        specs.append(
            LineSpec(
                system_account(session, admin_id, role).id, debit, credit,
                f"VAT {code}", vat_code=code, vat_role="vat",
            )
        )
    return specs


def issue_sales_invoice(
    session: Session, admin: Administration, invoice: SalesInvoice, user_id: int | None
) -> SalesInvoice:
    if invoice.administration_id != admin.id:
        raise InvoiceError("invoice belongs to another administration")
    if invoice.status != InvoiceStatus.DRAFT:
        raise InvoiceError("only a draft can be issued")
    if admin.kor:
        for line in invoice.lines:
            line.vat_code = KOR_CODE
    recompute_sales_totals(invoice)
    if invoice.supply_date is None:
        invoice.supply_date = invoice.issue_date
    problems = sales_issue_problems(admin, invoice)
    if problems:
        raise InvoiceError(*problems)
    try:
        with session.begin_nested():
            n = next_number(session, admin.id, "sales_invoice", invoice.issue_date.year)
            invoice.number = f"{admin.invoice_prefix}{invoice.issue_date.year}-{n:04d}"
            entry = post_entry(
                session,
                admin.id,
                invoice.issue_date,
                (
                    f"Credit note {invoice.number}"
                    if invoice.credit_note_of_id
                    else f"Sales invoice {invoice.number}"
                )
                + f" {invoice.contact.name}",
                _sales_entry_lines(invoice, session),
                source_type="sales_invoice",
                source_id=invoice.id,
                user_id=user_id,
            )
    except LedgerError as exc:
        invoice.number = None
        raise InvoiceError(str(exc)) from exc
    invoice.journal_entry_id = entry.id
    invoice.status = InvoiceStatus.OPEN
    invoice.issued_at = _now()
    audit.record(
        session, "sales_invoice.issue", user_id=user_id, administration_id=admin.id,
        entity_type="sales_invoice", entity_id=invoice.id,
        details={"number": invoice.number, "total_incl_cents": invoice.total_incl_cents},
    )
    session.flush()
    return invoice


def create_credit_note(
    session: Session,
    admin: Administration,
    invoice: SalesInvoice,
    user_id: int | None,
    on_date: date | None = None,
) -> SalesInvoice:
    if invoice.administration_id != admin.id:
        raise InvoiceError("invoice belongs to another administration")
    if invoice.is_opening:
        raise InvoiceError("an opening item was reported in the previous system; "
                           "it cannot be credited here")
    if invoice.credit_note_of_id:
        raise InvoiceError("a credit note cannot be credited")
    if invoice.status not in (InvoiceStatus.OPEN, InvoiceStatus.PAID):
        raise InvoiceError("only an open or paid invoice can be credited")
    on_date = on_date or date.today()
    note = SalesInvoice(
        administration_id=admin.id,
        contact_id=invoice.contact_id,
        status=InvoiceStatus.DRAFT,
        issue_date=on_date,
        supply_date=on_date,
        due_date=on_date,
        reference=invoice.reference,
        notes=f"Credit note for invoice {invoice.number}",
        credit_note_of_id=invoice.id,
    )
    note.lines = [
        SalesInvoiceLine(
            position=line.position,
            description=line.description,
            quantity=-line.quantity,
            unit_price_cents=line.unit_price_cents,
            vat_code=line.vat_code,
            revenue_account_id=line.revenue_account_id,
            line_excl_cents=-line.line_excl_cents,
        )
        for line in invoice.lines
    ]
    note.contact = invoice.contact
    session.add(note)
    session.flush()
    recompute_sales_totals(note)
    issue_sales_invoice(session, admin, note, user_id)
    invoice.status = InvoiceStatus.CANCELLED
    audit.record(
        session, "sales_invoice.credit", user_id=user_id, administration_id=admin.id,
        entity_type="sales_invoice", entity_id=invoice.id, details={"credit_note": note.number},
    )
    session.flush()
    return note


# ------------------------------------------------------------ attachments


def store_attachment(
    session: Session, admin: Administration, filename: str, data: bytes
) -> Attachment:
    if not data:
        raise InvoiceError("the uploaded file is empty")
    if len(data) > get_settings().max_upload_bytes:
        raise InvoiceError("the uploaded file is too large")
    content_type = next((ct for magic, ct in ALLOWED_UPLOADS if data.startswith(magic)), None)
    if content_type is None:
        raise InvoiceError("only PDF, PNG and JPEG files can be attached")
    safe_name = "".join(ch for ch in filename.rsplit("/", 1)[-1] if ch.isprintable())[:255]
    attachment = Attachment(
        administration_id=admin.id,
        filename=safe_name or "attachment",
        content_type=content_type,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        content=data,
    )
    session.add(attachment)
    session.flush()
    return attachment


# ------------------------------------------------------------- purchases


def _purchase_codes() -> set[str]:
    return {c.code for c in codes.for_direction(Direction.PURCHASE)}


def find_duplicate_purchase(
    session: Session, admin: Administration, contact_id: int, reference: str,
    exclude_id: int | None = None,
) -> PurchaseInvoice | None:
    stmt = select(PurchaseInvoice).where(
        PurchaseInvoice.administration_id == admin.id,
        PurchaseInvoice.contact_id == contact_id,
        func.lower(func.trim(PurchaseInvoice.supplier_reference)) == reference.strip().lower(),
    )
    if exclude_id:
        stmt = stmt.where(PurchaseInvoice.id != exclude_id)
    return session.scalars(stmt).first()


def _build_purchase_lines(
    session: Session, admin: Administration, inputs: list[PurchaseLineInput]
) -> list[PurchaseInvoiceLine]:
    if not inputs:
        raise InvoiceError("add at least one line")
    resolved = []
    for position, item in enumerate(inputs):
        if not item.description.strip():
            raise InvoiceError(f"line {position + 1}: description is required")
        account = _account(session, admin, item.expense_account_id, AccountType.EXPENSE)
        code = item.vat_code or account.default_vat_code or "I21"
        if code not in _purchase_codes():
            raise InvoiceError(f"line {position + 1}: {code} is not a purchase VAT code")
        resolved.append((item, account, code))
    vat: dict[int, int] = {}
    for code in {code for _, _, code in resolved}:
        members = [i for i, (_, _, c) in enumerate(resolved) if c == code]
        amounts = [resolved[i][0].amount_excl_cents for i in members]
        if codes.get(code).reverse_charge:
            shares = allocate_vat(amounts, code)
        elif codes.get(code).rate == 0:
            shares = [0] * len(members)
        else:
            shares = [
                item.vat_cents if item.vat_cents is not None
                else allocate_vat([item.amount_excl_cents], code)[0]
                for item in (resolved[i][0] for i in members)
            ]
        vat.update(zip(members, shares, strict=True))
    return [
        PurchaseInvoiceLine(
            position=i,
            description=item.description.strip()[:500],
            expense_account_id=account.id,
            amount_excl_cents=item.amount_excl_cents,
            vat_code=code,
            vat_cents=vat[i],
        )
        for i, (item, account, code) in enumerate(resolved)
    ]


def recompute_purchase_totals(invoice: PurchaseInvoice) -> None:
    """Reverse-charge VAT is self-assessed: it is not part of what the supplier is owed."""
    invoice.total_excl_cents = sum(line.amount_excl_cents for line in invoice.lines)
    invoice.total_vat_cents = sum(
        line.vat_cents for line in invoice.lines if not codes.get(line.vat_code).reverse_charge
    )
    invoice.total_incl_cents = invoice.total_excl_cents + invoice.total_vat_cents


def create_purchase_invoice(
    session: Session,
    admin: Administration,
    contact: Contact,
    lines: list[PurchaseLineInput],
    *,
    supplier_reference: str,
    invoice_date: date,
    due_date: date | None = None,
    description: str | None = None,
    attachment: Attachment | None = None,
) -> PurchaseInvoice:
    _contact(admin, contact, (ContactKind.SUPPLIER, ContactKind.BOTH))
    reference = supplier_reference.strip()
    if not reference:
        raise InvoiceError("the supplier's invoice number is required")
    if find_duplicate_purchase(session, admin, contact.id, reference):
        raise InvoiceError(f"invoice {reference} from {contact.name} is already registered")
    invoice = PurchaseInvoice(
        administration_id=admin.id,
        contact_id=contact.id,
        supplier_reference=reference[:100],
        status=InvoiceStatus.DRAFT,
        invoice_date=invoice_date,
        due_date=due_date or invoice_date + timedelta(days=admin.payment_terms_days),
        description=(description or None),
        attachment_id=attachment.id if attachment else None,
    )
    invoice.lines = _build_purchase_lines(session, admin, lines)
    recompute_purchase_totals(invoice)
    session.add(invoice)
    session.flush()
    return invoice


def update_purchase_invoice(
    session: Session,
    admin: Administration,
    invoice: PurchaseInvoice,
    contact: Contact,
    lines: list[PurchaseLineInput],
    *,
    supplier_reference: str,
    invoice_date: date,
    due_date: date,
    description: str | None = None,
    attachment: Attachment | None = None,
) -> PurchaseInvoice:
    if invoice.status != InvoiceStatus.DRAFT:
        raise InvoiceError("a booked invoice cannot be edited")
    _contact(admin, contact, (ContactKind.SUPPLIER, ContactKind.BOTH))
    reference = supplier_reference.strip()
    if not reference:
        raise InvoiceError("the supplier's invoice number is required")
    if find_duplicate_purchase(session, admin, contact.id, reference, invoice.id):
        raise InvoiceError(f"invoice {reference} from {contact.name} is already registered")
    new_lines = _build_purchase_lines(session, admin, lines)
    invoice.contact_id = contact.id
    invoice.contact = contact
    invoice.supplier_reference = reference[:100]
    invoice.invoice_date = invoice_date
    invoice.due_date = due_date
    invoice.description = description or None
    if attachment:
        invoice.attachment_id = attachment.id
    invoice.lines = new_lines
    recompute_purchase_totals(invoice)
    session.flush()
    return invoice


def delete_draft_purchase_invoice(session: Session, invoice: PurchaseInvoice) -> None:
    if invoice.status != InvoiceStatus.DRAFT:
        raise InvoiceError("only drafts can be deleted")
    session.delete(invoice)
    session.flush()


def _purchase_entry_lines(invoice: PurchaseInvoice, session: Session) -> list[LineSpec]:
    admin_id = invoice.administration_id
    receivable = system_account(session, admin_id, SystemRole.VAT_RECEIVABLE)
    specs: list[LineSpec] = []
    for line in invoice.lines:
        if line.amount_excl_cents:
            debit, credit = side(line.amount_excl_cents, credit_normal=False)
            specs.append(
                LineSpec(
                    line.expense_account_id, debit, credit, line.description[:300],
                    vat_code=line.vat_code, vat_role="base",
                )
            )
    per_code: dict[str, int] = {}
    for line in invoice.lines:
        per_code[line.vat_code] = per_code.get(line.vat_code, 0) + line.vat_cents
    for code, vat in per_code.items():
        if not vat:
            continue
        debit, credit = side(vat, credit_normal=False)
        specs.append(
            LineSpec(receivable.id, debit, credit, f"VAT {code}", vat_code=code, vat_role="vat")
        )
        if codes.get(code).reverse_charge:
            reverse = system_account(session, admin_id, SystemRole.VAT_PAYABLE_REVERSE)
            debit, credit = side(vat, credit_normal=True)
            specs.append(
                LineSpec(reverse.id, debit, credit, f"VAT {code} reverse charge",
                         vat_code=code, vat_role="vat")
            )
    if invoice.total_incl_cents:
        creditors = system_account(session, admin_id, SystemRole.CREDITORS)
        debit, credit = side(invoice.total_incl_cents, credit_normal=True)
        specs.append(
            LineSpec(
                creditors.id, debit, credit,
                f"{invoice.contact.name} {invoice.supplier_reference}", invoice.contact_id,
            )
        )
    return specs


def book_purchase_invoice(
    session: Session, admin: Administration, invoice: PurchaseInvoice, user_id: int | None
) -> PurchaseInvoice:
    if invoice.administration_id != admin.id:
        raise InvoiceError("invoice belongs to another administration")
    if invoice.status != InvoiceStatus.DRAFT:
        raise InvoiceError("only a draft can be booked")
    if not invoice.lines:
        raise InvoiceError("the invoice has no lines")
    if find_duplicate_purchase(
        session, admin, invoice.contact_id, invoice.supplier_reference, invoice.id
    ):
        raise InvoiceError(
            f"invoice {invoice.supplier_reference} from {invoice.contact.name} "
            "is already registered"
        )
    try:
        with session.begin_nested():
            entry = post_entry(
                session,
                admin.id,
                invoice.invoice_date,
                f"Purchase {invoice.contact.name} {invoice.supplier_reference}",
                _purchase_entry_lines(invoice, session),
                source_type="purchase_invoice",
                source_id=invoice.id,
                user_id=user_id,
            )
    except LedgerError as exc:
        raise InvoiceError(str(exc)) from exc
    invoice.journal_entry_id = entry.id
    invoice.status = InvoiceStatus.OPEN
    audit.record(
        session, "purchase_invoice.book", user_id=user_id, administration_id=admin.id,
        entity_type="purchase_invoice", entity_id=invoice.id,
        details={"reference": invoice.supplier_reference,
                 "total_incl_cents": invoice.total_incl_cents},
    )
    session.flush()
    return invoice
