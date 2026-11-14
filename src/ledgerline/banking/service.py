"""Banking use cases. Callers own the transaction; these functions flush but do not commit."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256

from schwifty import IBAN
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ledgerline import audit
from ledgerline.banking import matching, sepa
from ledgerline.banking.importer import file_sha256, parse_statement
from ledgerline.banking.parsed import ParsedStatement, ParsedTx, StatementError
from ledgerline.config import get_settings
from ledgerline.ledger.service import (
    LedgerError,
    LineSpec,
    post_entry,
    reverse_entry,
    system_account,
)
from ledgerline.models import (
    Account,
    AccountType,
    Administration,
    BankAccount,
    BankStatement,
    BankTransaction,
    InvoiceStatus,
    JournalEntry,
    PaymentAllocation,
    PurchaseInvoice,
    SalesInvoice,
    SystemRole,
    TxStatus,
)
from ledgerline.money import vat_in_gross
from ledgerline.vat import codes as vat_codes
from ledgerline.vat.codes import Direction


class BankingError(ValueError):
    """A rule was broken; the message is safe to show to the user."""


@dataclass
class ImportResult:
    statement: BankStatement
    created: int
    skipped: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Allocation:
    kind: str  # matching.SALES | matching.PURCHASE
    invoice_id: int
    amount_cents: int


def normalise_iban(value: str) -> str:
    try:
        return IBAN(value.replace(" ", "")).compact
    except ValueError as exc:
        raise BankingError(f"invalid IBAN: {value!r}") from exc


def create_bank_account(
    session: Session,
    administration: Administration,
    iban: str,
    name: str,
    ledger_account_id: int | None = None,
    user_id: int | None = None,
) -> BankAccount:
    iban = normalise_iban(iban)
    name = name.strip()
    if not name:
        raise BankingError("a name is required")
    if ledger_account_id is None:
        ledger = system_account(session, administration.id, SystemRole.BANK)
    else:
        ledger = session.get(Account, ledger_account_id)
        if ledger is None or ledger.administration_id != administration.id:
            raise BankingError("unknown ledger account")
    if ledger.type != AccountType.ASSET:
        raise BankingError("the ledger account must be an asset account")
    if session.scalar(
        select(BankAccount.id).where(
            BankAccount.administration_id == administration.id, BankAccount.iban == iban
        )
    ):
        raise BankingError("this IBAN is already registered")
    account = BankAccount(
        administration_id=administration.id, iban=iban, name=name, ledger_account_id=ledger.id
    )
    session.add(account)
    session.flush()
    audit.record(
        session, "bank_account.create", user_id=user_id, administration_id=administration.id,
        entity_type="bank_account", entity_id=account.id, details={"iban": iban},
    )
    return account


def get_bank_account(
    session: Session, administration_id: int, account_id: int
) -> BankAccount | None:
    account = session.get(BankAccount, account_id)
    if account is None or account.administration_id != administration_id:
        return None
    return account


def get_transaction(session: Session, administration_id: int, tx_id: int) -> BankTransaction | None:
    return session.scalar(
        select(BankTransaction)
        .join(BankAccount, BankAccount.id == BankTransaction.bank_account_id)
        .where(BankTransaction.id == tx_id, BankAccount.administration_id == administration_id)
    )


def _dedupe_keys(parsed: list[ParsedTx]) -> list[str]:
    seen: dict[str, int] = defaultdict(int)
    keys = []
    for tx in parsed:
        if tx.bank_reference:
            base = f"ref|{tx.bank_reference}|{tx.booking_date}|{tx.amount_cents}"
        else:
            base = "|".join(
                [
                    "nref", tx.booking_date.isoformat(), str(tx.amount_cents),
                    tx.counterparty_iban or "", tx.description or "", tx.end_to_end_id or "",
                ]
            )
        seen[base] += 1
        keys.append(sha256(f"{base}|{seen[base]}".encode()).hexdigest())
    return keys


def import_statement(
    session: Session,
    bank_account: BankAccount,
    filename: str,
    content: bytes,
    user_id: int | None = None,
) -> ImportResult:
    if len(content) > get_settings().max_upload_bytes:
        raise BankingError("the file is too large")
    if not content.strip():
        raise BankingError("the file is empty")
    digest = file_sha256(content)
    if session.scalar(
        select(BankStatement.id).where(
            BankStatement.bank_account_id == bank_account.id, BankStatement.file_sha256 == digest
        )
    ):
        raise BankingError("this file has already been imported")
    try:
        parsed: ParsedStatement = parse_statement(content)
    except StatementError as exc:
        raise BankingError(str(exc)) from exc
    if parsed.iban != bank_account.iban:
        raise BankingError(
            f"the statement is for {parsed.iban}, not for {bank_account.iban}"
        )

    statement = BankStatement(
        bank_account_id=bank_account.id,
        file_name=filename[:255],
        file_sha256=digest,
        file_format=parsed.file_format,
        opening_balance_cents=parsed.opening_cents,
        closing_balance_cents=parsed.closing_cents,
    )
    session.add(statement)
    session.flush()

    keys = _dedupe_keys(parsed.transactions)
    existing = set(
        session.scalars(
            select(BankTransaction.dedupe_key).where(
                BankTransaction.bank_account_id == bank_account.id,
                BankTransaction.dedupe_key.in_(keys),
            )
        )
    )
    created = 0
    for tx, key in zip(parsed.transactions, keys, strict=True):
        if key in existing:
            continue
        session.add(
            BankTransaction(
                bank_account_id=bank_account.id,
                statement_id=statement.id,
                booking_date=tx.booking_date,
                value_date=tx.value_date,
                amount_cents=tx.amount_cents,
                counterparty_name=(tx.counterparty_name or None) and tx.counterparty_name[:200],
                counterparty_iban=tx.counterparty_iban,
                description=tx.description,
                end_to_end_id=tx.end_to_end_id,
                bank_reference=tx.bank_reference,
                dedupe_key=key,
                status=TxStatus.UNMATCHED,
            )
        )
        created += 1
    session.flush()
    skipped = len(parsed.transactions) - created

    warnings = []
    if parsed.opening_cents is not None and parsed.closing_cents is not None:
        total = sum(t.amount_cents for t in parsed.transactions)
        if parsed.opening_cents + total != parsed.closing_cents:
            warnings.append(
                "Opening balance plus transactions does not equal the closing balance "
                f"({parsed.opening_cents} + {total} != {parsed.closing_cents} cents)."
            )
    audit.record(
        session, "bank_statement.import", user_id=user_id,
        administration_id=bank_account.administration_id, entity_type="bank_statement",
        entity_id=statement.id,
        details={"file": filename[:255], "created": created, "skipped": skipped},
    )
    return ImportResult(statement, created, skipped, warnings)


def suggest(
    session: Session, tx: BankTransaction, candidates: list[matching.Candidate] | None = None
) -> list[matching.Suggestion]:
    return matching.suggest(session, tx, candidates)


def _admin_id(session: Session, tx: BankTransaction) -> int:
    return session.get(BankAccount, tx.bank_account_id).administration_id


def _require_unmatched(tx: BankTransaction) -> None:
    if tx.status != TxStatus.UNMATCHED:
        raise BankingError("this transaction is already processed")


def _recompute(invoice: SalesInvoice | PurchaseInvoice, delta_cents: int) -> None:
    invoice.paid_cents += delta_cents
    invoice.status = (
        InvoiceStatus.PAID if invoice.paid_cents >= invoice.total_incl_cents else InvoiceStatus.OPEN
    )


def match(
    session: Session,
    tx: BankTransaction,
    allocations: list[Allocation],
    user_id: int | None = None,
) -> JournalEntry:
    _require_unmatched(tx)
    if not allocations:
        raise BankingError("select at least one invoice")
    admin_id = _admin_id(session, tx)
    kind = matching.SALES if tx.amount_cents > 0 else matching.PURCHASE
    model = SalesInvoice if kind == matching.SALES else PurchaseInvoice
    if len({a.invoice_id for a in allocations}) != len(allocations):
        raise BankingError("an invoice can only be selected once")
    if sum(a.amount_cents for a in allocations) != abs(tx.amount_cents):
        raise BankingError("the allocated amounts must add up to the transaction amount")

    bank = session.get(BankAccount, tx.bank_account_id)
    control = system_account(
        session, admin_id, SystemRole.DEBTORS if kind == matching.SALES else SystemRole.CREDITORS
    )
    per_contact: dict[int, int] = defaultdict(int)
    numbers = []
    invoices = []
    for alloc in allocations:
        if alloc.kind != kind:
            raise BankingError("money in matches sales invoices, money out purchase invoices")
        if alloc.amount_cents <= 0:
            raise BankingError("allocated amounts must be positive")
        invoice = session.scalar(
            select(model)
            .where(model.id == alloc.invoice_id, model.administration_id == admin_id)
            .with_for_update(of=model)
        )
        if invoice is None or invoice.status != InvoiceStatus.OPEN:
            raise BankingError("an invoice is unknown or not open")
        if alloc.amount_cents > invoice.total_incl_cents - invoice.paid_cents:
            raise BankingError("an allocation exceeds the remaining invoice amount")
        per_contact[invoice.contact_id] += alloc.amount_cents
        numbers.append(getattr(invoice, "number", None) or invoice.supplier_reference)
        invoices.append((invoice, alloc))

    contact_lines = [
        LineSpec(
            control.id,
            credit_cents=amount if kind == matching.SALES else 0,
            debit_cents=amount if kind == matching.PURCHASE else 0,
            contact_id=contact_id,
        )
        for contact_id, amount in per_contact.items()
    ]
    total = abs(tx.amount_cents)
    bank_line = LineSpec(
        bank.ledger_account_id,
        debit_cents=total if kind == matching.SALES else 0,
        credit_cents=total if kind == matching.PURCHASE else 0,
    )
    label = "Receipt" if kind == matching.SALES else "Payment"
    entry = post_entry(
        session, admin_id, tx.booking_date, f"{label} {', '.join(numbers)}"[:300],
        [bank_line, *contact_lines], source_type="bank_transaction", source_id=tx.id,
        user_id=user_id,
    )
    for invoice, alloc in invoices:
        session.add(
            PaymentAllocation(
                bank_transaction_id=tx.id,
                sales_invoice_id=invoice.id if kind == matching.SALES else None,
                purchase_invoice_id=invoice.id if kind == matching.PURCHASE else None,
                amount_cents=alloc.amount_cents,
            )
        )
        _recompute(invoice, alloc.amount_cents)
    tx.status = TxStatus.MATCHED
    tx.journal_entry_id = entry.id
    session.flush()
    audit.record(
        session, "bank_transaction.match", user_id=user_id, administration_id=admin_id,
        entity_type="bank_transaction", entity_id=tx.id,
        details={"entry": entry.number, "invoices": numbers},
    )
    return entry


def _split_vat(gross_cents: int, rate: Decimal) -> int:
    return vat_in_gross(gross_cents, rate)


def book_to_account(
    session: Session,
    tx: BankTransaction,
    account_id: int,
    vat_code: str | None = None,
    user_id: int | None = None,
    description: str | None = None,
) -> JournalEntry:
    _require_unmatched(tx)
    admin_id = _admin_id(session, tx)
    bank = session.get(BankAccount, tx.bank_account_id)
    target = session.get(Account, account_id)
    if target is None or target.administration_id != admin_id or not target.is_active:
        raise BankingError("unknown account")
    if target.id == bank.ledger_account_id:
        raise BankingError("choose an account other than the bank account")

    money_in = tx.amount_cents > 0
    gross = abs(tx.amount_cents)
    base, vat, vat_account = gross, 0, None
    tag_code = None
    if vat_code:
        try:
            code = vat_codes.get(vat_code)
        except ValueError as exc:
            raise BankingError(str(exc)) from exc
        wanted = Direction.SALES if money_in else Direction.PURCHASE
        if code.direction != wanted:
            raise BankingError("this VAT code does not fit the direction of the transaction")
        if code.reverse_charge:
            raise BankingError("reverse-charge codes cannot be booked from the bank")
        tag_code = code.code
        if code.rate > 0:
            vat = _split_vat(gross, code.rate)
            base = gross - vat
            if money_in:
                high = code.rate >= 15
                role = SystemRole.VAT_PAYABLE_HIGH if high else SystemRole.VAT_PAYABLE_LOW
            else:
                role = SystemRole.VAT_RECEIVABLE
            vat_account = system_account(session, admin_id, role)

    side = "credit_cents" if money_in else "debit_cents"
    lines = [
        LineSpec(
            bank.ledger_account_id,
            **{"debit_cents" if money_in else "credit_cents": gross},
        ),
        LineSpec(
            target.id, **{side: base}, vat_code=tag_code, vat_role="base" if tag_code else None
        ),
    ]
    if vat and vat_account:
        lines.append(LineSpec(vat_account.id, **{side: vat}, vat_code=tag_code, vat_role="vat"))
    text = (description or tx.counterparty_name or tx.description or target.name).strip()
    entry = post_entry(
        session, admin_id, tx.booking_date, text, lines,
        source_type="bank_transaction", source_id=tx.id, user_id=user_id,
    )
    tx.status = TxStatus.MATCHED
    tx.journal_entry_id = entry.id
    session.flush()
    audit.record(
        session, "bank_transaction.book", user_id=user_id, administration_id=admin_id,
        entity_type="bank_transaction", entity_id=tx.id,
        details={"entry": entry.number, "account": target.code, "vat_code": tag_code},
    )
    return entry


def ignore(session: Session, tx: BankTransaction, user_id: int | None = None) -> None:
    _require_unmatched(tx)
    tx.status = TxStatus.IGNORED
    audit.record(
        session, "bank_transaction.ignore", user_id=user_id,
        administration_id=_admin_id(session, tx), entity_type="bank_transaction", entity_id=tx.id,
    )


def undo(
    session: Session, tx: BankTransaction, user_id: int | None = None, on_date: date | None = None
) -> None:
    admin_id = _admin_id(session, tx)
    if tx.status == TxStatus.UNMATCHED:
        raise BankingError("nothing to undo")
    if tx.status == TxStatus.MATCHED and tx.journal_entry_id:
        entry = session.get(JournalEntry, tx.journal_entry_id)
        reverse_entry(session, entry, on_date or date.today(), "bank match undone", user_id)
        for alloc in session.scalars(
            select(PaymentAllocation).where(PaymentAllocation.bank_transaction_id == tx.id)
        ):
            if alloc.sales_invoice_id:
                invoice = session.get(SalesInvoice, alloc.sales_invoice_id)
            else:
                invoice = session.get(PurchaseInvoice, alloc.purchase_invoice_id)
            _recompute(invoice, -alloc.amount_cents)
        session.execute(
            delete(PaymentAllocation).where(PaymentAllocation.bank_transaction_id == tx.id)
        )
    tx.status = TxStatus.UNMATCHED
    tx.journal_entry_id = None
    session.flush()
    audit.record(
        session, "bank_transaction.undo", user_id=user_id, administration_id=admin_id,
        entity_type="bank_transaction", entity_id=tx.id,
    )


def accept_exact_matches(
    session: Session, bank_account: BankAccount, user_id: int | None = None
) -> int:
    candidates = matching.load_candidates(session, bank_account.administration_id)
    txs = session.scalars(
        select(BankTransaction)
        .where(
            BankTransaction.bank_account_id == bank_account.id,
            BankTransaction.status == TxStatus.UNMATCHED,
        )
        .order_by(BankTransaction.booking_date, BankTransaction.id)
    ).all()
    accepted = 0
    for tx in txs:
        hits = [
            s for s in matching.suggest(session, tx, candidates)
            if s.exact_amount and s.number_hit
        ]
        if len(hits) != 1:
            continue
        cand = hits[0].candidate
        try:
            with session.begin_nested():
                allocation = Allocation(cand.kind, cand.invoice_id, abs(tx.amount_cents))
                match(session, tx, [allocation], user_id)
        except (BankingError, LedgerError):
            continue
        candidates = [
            c for c in candidates
            if (c.kind, c.invoice_id) != (cand.kind, cand.invoice_id)
        ]
        accepted += 1
    return accepted


def sepa_export(
    session: Session,
    administration: Administration,
    bank_account: BankAccount,
    invoice_ids: list[int],
    execution_date: date,
    now: datetime | None = None,
) -> tuple[str, bytes]:
    if not invoice_ids:
        raise BankingError("select at least one purchase invoice")
    invoices = session.scalars(
        select(PurchaseInvoice)
        .where(
            PurchaseInvoice.administration_id == administration.id,
            PurchaseInvoice.id.in_(set(invoice_ids)),
        )
        .order_by(PurchaseInvoice.due_date, PurchaseInvoice.id)
    ).all()
    if len(invoices) != len(set(invoice_ids)):
        raise BankingError("an invoice was not found")
    now = now or datetime.now(UTC)
    payments = []
    for inv in invoices:
        remaining = inv.total_incl_cents - inv.paid_cents
        if inv.status != InvoiceStatus.OPEN or remaining <= 0:
            raise BankingError(f"{inv.supplier_reference} is not open")
        if not inv.contact.iban:
            raise BankingError(f"{inv.contact.name} has no IBAN")
        payments.append(
            sepa.SepaPayment(
                creditor_name=inv.contact.name,
                creditor_iban=inv.contact.iban,
                amount_cents=remaining,
                end_to_end_id=f"INV{inv.id}",
                remittance=inv.supplier_reference,
            )
        )
    try:
        xml = sepa.build_pain001(
            administration.name, bank_account.iban, payments, execution_date,
            debtor_bic=administration.bic if administration.iban == bank_account.iban else None,
            message_id=f"LL{now:%Y%m%d%H%M%S}", created_at=now,
        )
    except sepa.SepaError as exc:
        raise BankingError(str(exc)) from exc
    return f"sepa-{now:%Y%m%d-%H%M%S}.xml", xml
