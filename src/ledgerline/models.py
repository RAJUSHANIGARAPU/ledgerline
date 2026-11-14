"""The full schema. Amounts are integer cents; quantities and rates are Numeric."""
import secrets
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ledgerline.db import Base


class Role(StrEnum):
    """A user's role inside one tenant (memberships.role)."""

    OWNER = "owner"
    BOOKKEEPER = "bookkeeper"  # writes the books, no user or device management
    ACCOUNTANT = "accountant"  # read-only
    UPLOADER = "uploader"  # capture only: uploads documents, sees nothing else


WRITE_ROLES = (Role.OWNER, Role.BOOKKEEPER)
CAPTURE_ROLES = (*WRITE_ROLES, Role.UPLOADER)


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class SystemRole(StrEnum):
    """Accounts the application posts to automatically."""

    BANK = "bank"
    DEBTORS = "debtors"
    CREDITORS = "creditors"
    VAT_PAYABLE_HIGH = "vat_payable_high"
    VAT_PAYABLE_LOW = "vat_payable_low"
    VAT_PAYABLE_REVERSE = "vat_payable_reverse"
    VAT_RECEIVABLE = "vat_receivable"
    VAT_SETTLEMENT = "vat_settlement"
    SUSPENSE = "suspense"
    BANK_COSTS = "bank_costs"
    RETAINED_EARNINGS = "retained_earnings"
    CASH = "cash"
    CARD_CLEARING = "card_clearing"
    TAKINGS_OTHER = "takings_other"


class ContactKind(StrEnum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    BOTH = "both"


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"  # sales: issued; purchase: booked
    PAID = "paid"
    CANCELLED = "cancelled"  # sales: credited by a credit note


class TxStatus(StrEnum):
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    IGNORED = "ignored"


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def new_inbox_token() -> str:
    return secrets.token_hex(16)


class TenantScoped:
    """Mixin for every table that holds one tenant's data; the database fills in the tenant."""

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), index=True, server_default=text("ledgerline_current_tenant()")
    )


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    require_2fa: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change, reset and "sign out everywhere"; sessions carry the value.
    session_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = _now()
    # Not a column: the request fills it in from the membership of the selected tenant.
    role = None


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_tenant"),
        CheckConstraint(
            "role IN ('owner', 'bookkeeper', 'accountant', 'uploader')",
            name="ck_memberships_role"),
        CheckConstraint("status IN ('active', 'pending')", name="ck_memberships_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    # 'pending' = invited, not yet accepted; grants no access.
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active")
    created_at: Mapped[datetime] = _now()
    tenant: Mapped[Tenant] = relationship(lazy="joined")


class AuthToken(Base):
    """Single-use invite or password-reset token. Only the sha256 of the token is stored."""

    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('invite', 'reset')", name="ck_auth_tokens_purpose"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    membership_id: Mapped[int | None] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class UserTotp(Base):
    """A user's authenticator secret (Fernet-encrypted). Active once enabled_at is set."""

    __tablename__ = "user_totp"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         primary_key=True)
    secret_enc: Mapped[str] = mapped_column(Text)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_step: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = _now()


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    __table_args__ = (UniqueConstraint("user_id", "code_hash", name="uq_recovery_codes_user_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Administration(TenantScoped, Base):
    """One set of books (one business)."""

    __tablename__ = "administrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_form: Mapped[str] = mapped_column(String(40), default="eenmanszaak")
    kvk_number: Mapped[str | None] = mapped_column(String(20))
    vat_number: Mapped[str | None] = mapped_column(String(20))
    iban: Mapped[str | None] = mapped_column(String(34))
    bic: Mapped[str | None] = mapped_column(String(11))
    email: Mapped[str | None] = mapped_column(String(254))
    address_line: Mapped[str | None] = mapped_column(String(200))
    postcode: Mapped[str | None] = mapped_column(String(12))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(2), default="NL")
    invoice_prefix: Mapped[str] = mapped_column(String(10), default="")
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    kor: Mapped[bool] = mapped_column(Boolean, default=False)
    # Entries dated on or before this date are refused (closed period).
    locked_until: Mapped[date | None] = mapped_column(Date)
    # Random part of the email address that routes mail to this administration.
    inbox_token: Mapped[str] = mapped_column(
        String(64), unique=True, default=new_inbox_token,
        server_default=text(
            "left(replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''), 32)"),
    )
    created_at: Mapped[datetime] = _now()


class Account(TenantScoped, Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("administration_id", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))
    system_role: Mapped[str | None] = mapped_column(String(40))
    default_vat_code: Mapped[str | None] = mapped_column(String(10))
    rgs_code: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Contact(TenantScoped, Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default=ContactKind.CUSTOMER)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    address_line: Mapped[str | None] = mapped_column(String(200))
    postcode: Mapped[str | None] = mapped_column(String(12))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(2), default="NL")
    vat_number: Mapped[str | None] = mapped_column(String(20))
    kvk_number: Mapped[str | None] = mapped_column(String(20))
    iban: Mapped[str | None] = mapped_column(String(34))
    # GDPR erasure: personal fields are blanked, bookkeeping rows stay (fiscal retention).
    anonymised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class JournalEntry(TenantScoped, Base):
    """Immutable once inserted; corrections are reversing entries."""

    __tablename__ = "journal_entries"
    __table_args__ = (UniqueConstraint("administration_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    number: Mapped[str] = mapped_column(String(20))
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[int | None] = mapped_column(Integer)
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()
    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", order_by="JournalLine.id", lazy="selectin"
    )


class JournalLine(TenantScoped, Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit_cents >= 0 AND credit_cents >= 0", name="ck_line_non_negative"),
        CheckConstraint("(debit_cents = 0) <> (credit_cents = 0)", name="ck_line_one_side"),
        CheckConstraint("vat_role IS NULL OR vat_role IN ('base', 'vat')", name="ck_line_vat_role"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    debit_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    description: Mapped[str | None] = mapped_column(String(300))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    # VAT tagging drives the btw-aangifte: 'base' lines carry the net amount,
    # 'vat' lines the tax, both tagged with the VAT code they belong to.
    vat_code: Mapped[str | None] = mapped_column(String(10))
    vat_role: Mapped[str | None] = mapped_column(String(4))
    entry: Mapped[JournalEntry] = relationship(back_populates="lines")


class NumberSequence(TenantScoped, Base):
    """Gap-free counters (invoice numbers, journal numbers) per administration and year."""

    __tablename__ = "number_sequences"
    administration_id: Mapped[int] = mapped_column(
        ForeignKey("administrations.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class DailyTakings(TenantScoped, Base):
    """One day of till sales, gross per VAT rate and by how it was received.

    Never edited: a correction reverses the journal entry (reversed_at is set) and posts anew.
    """

    __tablename__ = "daily_takings"
    __table_args__ = (
        CheckConstraint(
            "gross_high_cents >= 0 AND gross_low_cents >= 0 AND gross_zero_cents >= 0 "
            "AND cash_cents >= 0 AND card_cents >= 0 AND other_cents >= 0",
            name="ck_takings_non_negative"),
        CheckConstraint(
            "gross_high_cents + gross_low_cents + gross_zero_cents "
            "= cash_cents + card_cents + other_cents",
            name="ck_takings_balanced"),
        Index("uq_daily_takings_active_day", "administration_id", "takings_date", unique=True,
              postgresql_where=text("reversed_at IS NULL")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    takings_date: Mapped[date] = mapped_column(Date, index=True)
    gross_high_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    gross_low_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    gross_zero_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    cash_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    card_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    other_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    note: Mapped[str | None] = mapped_column(String(200))
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    reversal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()

    @property
    def gross_total_cents(self) -> int:
        return self.gross_high_cents + self.gross_low_cents + self.gross_zero_cents


class Attachment(TenantScoped, Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Stored in the database so a single pg_dump backs up everything.
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = _now()


class SalesInvoice(TenantScoped, Base):
    __tablename__ = "sales_invoices"
    __table_args__ = (UniqueConstraint("administration_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    # Assigned when the invoice is issued; drafts have no number.
    number: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.DRAFT)
    issue_date: Mapped[date] = mapped_column(Date)
    supply_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    total_excl_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_vat_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_incl_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    paid_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_note_of_id: Mapped[int | None] = mapped_column(ForeignKey("sales_invoices.id"))
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    lines: Mapped[list["SalesInvoiceLine"]] = relationship(
        back_populates="invoice", order_by="SalesInvoiceLine.position", lazy="selectin",
        cascade="all, delete-orphan",
    )
    contact: Mapped[Contact] = relationship(lazy="joined")


class SalesInvoiceLine(TenantScoped, Base):
    __tablename__ = "sales_invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoices.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)
    vat_code: Mapped[str] = mapped_column(String(10))
    revenue_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    line_excl_cents: Mapped[int] = mapped_column(BigInteger)
    invoice: Mapped[SalesInvoice] = relationship(back_populates="lines")


class PurchaseInvoice(TenantScoped, Base):
    __tablename__ = "purchase_invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    supplier_reference: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.DRAFT)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(300))
    total_excl_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_vat_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_incl_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    paid_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"))
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = _now()
    lines: Mapped[list["PurchaseInvoiceLine"]] = relationship(
        back_populates="invoice", order_by="PurchaseInvoiceLine.position", lazy="selectin",
        cascade="all, delete-orphan",
    )
    contact: Mapped[Contact] = relationship(lazy="joined")


class PurchaseInvoiceLine(TenantScoped, Base):
    __tablename__ = "purchase_invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("purchase_invoices.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500))
    expense_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    amount_excl_cents: Mapped[int] = mapped_column(BigInteger)
    vat_code: Mapped[str] = mapped_column(String(10))
    # As stated on the supplier's invoice (may differ by a cent from our own calculation).
    vat_cents: Mapped[int] = mapped_column(BigInteger)
    invoice: Mapped[PurchaseInvoice] = relationship(back_populates="lines")


class BankAccount(TenantScoped, Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("administration_id", "iban"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    iban: Mapped[str] = mapped_column(String(34))
    name: Mapped[str] = mapped_column(String(100))
    ledger_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))


class BankStatement(TenantScoped, Base):
    """One imported file. The hash stops the same file being imported twice."""

    __tablename__ = "bank_statements"
    __table_args__ = (UniqueConstraint("bank_account_id", "file_sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    file_format: Mapped[str] = mapped_column(String(20))  # camt053 | mt940 | csv
    opening_balance_cents: Mapped[int | None] = mapped_column(BigInteger)
    closing_balance_cents: Mapped[int | None] = mapped_column(BigInteger)
    imported_at: Mapped[datetime] = _now()


class BankTransaction(TenantScoped, Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("bank_account_id", "dedupe_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("bank_statements.id"))
    booking_date: Mapped[date] = mapped_column(Date, index=True)
    value_date: Mapped[date | None] = mapped_column(Date)
    # Signed: positive = money in, negative = money out.
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    counterparty_name: Mapped[str | None] = mapped_column(String(200))
    counterparty_iban: Mapped[str | None] = mapped_column(String(34))
    description: Mapped[str | None] = mapped_column(Text)
    end_to_end_id: Mapped[str | None] = mapped_column(String(100))
    bank_reference: Mapped[str | None] = mapped_column(String(100))
    # Overlapping statement files must not create duplicate transactions.
    dedupe_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default=TxStatus.UNMATCHED)
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))


class PaymentAllocation(TenantScoped, Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint(
            "(sales_invoice_id IS NULL) <> (purchase_invoice_id IS NULL)",
            name="ck_allocation_one_invoice",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    bank_transaction_id: Mapped[int] = mapped_column(ForeignKey("bank_transactions.id"), index=True)
    sales_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("sales_invoices.id"))
    purchase_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_invoices.id"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)


class AuditEvent(TenantScoped, Base):
    """Append-only (enforced by a database trigger)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    administration_id: Mapped[int | None] = mapped_column(ForeignKey("administrations.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class InboxStatus(StrEnum):
    NEW = "new"  # waiting for extraction
    REVIEW = "review"  # extracted (or failed), waiting for the owner
    BOOKED = "booked"  # turned into a purchase invoice
    REJECTED = "rejected"


class InboxDocument(TenantScoped, Base):
    """A received document (email attachment, upload, phone) on its way to becoming a purchase."""

    __tablename__ = "inbox_documents"
    __table_args__ = (UniqueConstraint("administration_id", "content_sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    source: Mapped[str] = mapped_column(String(10))  # email | upload | phone
    status: Mapped[str] = mapped_column(String(10), default=InboxStatus.NEW, index=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("attachments.id"))
    content_sha256: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str | None] = mapped_column(String(254))
    subject: Mapped[str | None] = mapped_column(String(300))
    email_message_id: Mapped[str | None] = mapped_column(String(300))
    received_at: Mapped[datetime] = _now()
    # ubl | pdf_text | vision | none
    extraction_method: Mapped[str | None] = mapped_column(String(10))
    extraction: Mapped[dict | None] = mapped_column(JSONB)
    extraction_error: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    purchase_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_invoices.id"))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class FeedbackEntry(TenantScoped, Base):
    """Pilot feedback, stored verbatim. Personal data: exported with the administration and
    deleted with the tenant."""

    __tablename__ = "feedback_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class ForwardingNotice(TenantScoped, Base):
    """A mail-forwarding confirmation link waiting for the owner. Holds the link, not the mail."""

    __tablename__ = "forwarding_notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # gmail
    link: Mapped[str] = mapped_column(String(600))
    received_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiToken(TenantScoped, Base):
    """Upload-only token for the phone shortcut. Only the SHA-256 of the token is stored."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    administration_id: Mapped[int] = mapped_column(ForeignKey("administrations.id"))
    name: Mapped[str] = mapped_column(String(60))
    token_prefix: Mapped[str] = mapped_column(String(12))
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
