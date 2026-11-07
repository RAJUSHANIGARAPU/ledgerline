"""CAMT.053 (camt.053.001.02 and .08) parser. Uploads go through defusedxml only."""
from datetime import date
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from ledgerline.banking.parsed import ParsedStatement, ParsedTx, StatementError
from ledgerline.money import MoneyError, to_cents


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(el: Element, name: str) -> list[Element]:
    return [c for c in el if _local(c.tag) == name]


def _first(el: Element | None, *path: str) -> Element | None:
    for name in path:
        if el is None:
            return None
        found = _kids(el, name)
        el = found[0] if found else None
    return el


def _text(el: Element | None, *path: str) -> str | None:
    node = _first(el, *path)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _descendant(el: Element | None, name: str) -> Element | None:
    if el is None:
        return None
    return next((n for n in el.iter() if _local(n.tag) == name), None)


def _date(el: Element | None) -> date | None:
    if el is None:
        return None
    value = _text(el, "Dt") or _text(el, "DtTm")
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise StatementError(f"invalid date {value!r}") from exc


def _amount(el: Element | None) -> int:
    if el is None or not (el.text or "").strip():
        raise StatementError("amount missing")
    currency = el.get("Ccy")
    if currency != "EUR":
        raise StatementError(f"only EUR statements are supported (found {currency or 'none'})")
    try:
        cents = to_cents(el.text.strip())
    except MoneyError as exc:
        raise StatementError(str(exc)) from exc
    if cents < 0:
        raise StatementError("negative amount in statement")
    return cents


def _signed(el: Element, indicator: str | None) -> int:
    cents = _amount(el)
    return -cents if indicator == "DBIT" else cents


def _party(details: Element, role: str) -> tuple[str | None, str | None]:
    parties = _first(details, "RltdPties")
    party = _first(parties, role)
    name = _text(party, "Nm") or _text(party, "Pty", "Nm")
    iban = _text(parties, f"{role}Acct", "Id", "IBAN")
    return name, iban


def _description(details: Element | None, entry: Element) -> tuple[str | None, str | None]:
    parts: list[str] = []
    refs = _first(details, "Refs")
    remittance = _first(details, "RmtInf")
    if remittance is not None:
        for node in _kids(remittance, "Ustrd"):
            if node.text and node.text.strip():
                parts.append(node.text.strip())
        for strd in _kids(remittance, "Strd"):
            ref = _text(strd, "CdtrRefInf", "Ref")
            if ref:
                parts.append(ref)
    if not parts:
        extra = _text(details, "AddtlTxInf") or _text(entry, "AddtlNtryInf")
        if extra:
            parts.append(extra)
    e2e = _text(refs, "EndToEndId")
    if e2e == "NOTPROVIDED":
        e2e = None
    return (" ".join(parts) or None), e2e


def _balance(stmt: Element, codes: tuple[str, ...]) -> int | None:
    for bal in _kids(stmt, "Bal"):
        if _text(bal, "Tp", "CdOrPrtry", "Cd") in codes:
            return _signed(_first(bal, "Amt"), _text(bal, "CdtDbtInd"))
    return None


def _entries(stmt: Element) -> list[ParsedTx]:
    result: list[ParsedTx] = []
    for entry in _kids(stmt, "Ntry"):
        indicator = _text(entry, "CdtDbtInd")
        if indicator not in ("CRDT", "DBIT"):
            raise StatementError("entry without credit/debit indicator")
        total = _signed(_first(entry, "Amt"), indicator)
        if (_text(entry, "RvslInd") or "").lower() == "true":
            total = -total
        booking = _date(_first(entry, "BookgDt")) or _date(_first(entry, "ValDt"))
        if booking is None:
            raise StatementError("entry without booking date")
        value = _date(_first(entry, "ValDt"))
        entry_ref = _text(entry, "AcctSvcrRef") or _text(entry, "NtryRef")
        batch = [d for node in _kids(entry, "NtryDtls") for d in _kids(node, "TxDtls")]
        sides = batch or [None]
        if len(sides) > 1 and any(_first(d, "AmtDtls", "TxAmt", "Amt") is None for d in sides):
            raise StatementError("batch entry without per-transaction amounts")
        built: list[ParsedTx] = []
        for index, details in enumerate(sides):
            amount_el = _first(details, "AmtDtls", "TxAmt", "Amt")
            cents = total if amount_el is None else _signed(amount_el, indicator)
            if amount_el is not None and (_text(entry, "RvslInd") or "").lower() == "true":
                cents = -cents
            role = "Dbtr" if total > 0 else "Cdtr"
            name, iban = _party(details, role) if details is not None else (None, None)
            description, e2e = _description(details, entry)
            reference = _text(_first(details, "Refs"), "AcctSvcrRef") or entry_ref
            if reference and len(sides) > 1:
                reference = f"{reference}#{index + 1}"
            built.append(
                ParsedTx(
                    booking_date=booking,
                    value_date=value,
                    amount_cents=cents,
                    counterparty_name=name,
                    counterparty_iban=iban,
                    description=description,
                    end_to_end_id=e2e,
                    bank_reference=reference[:100] if reference else None,
                )
            )
        if sum(t.amount_cents for t in built) != total:
            raise StatementError("transaction amounts do not add up to the entry amount")
        result.extend(built)
    return result


def parse(content: bytes) -> ParsedStatement:
    try:
        root = fromstring(content, forbid_dtd=True)
    except (DefusedXmlException, ParseError) as exc:
        raise StatementError(f"not a valid or safe XML file: {exc}") from exc
    if _local(root.tag) != "Document" or "camt.053" not in root.tag:
        raise StatementError("not a CAMT.053 document")
    container = _first(root, "BkToCstmrStmt")
    statements = _kids(container, "Stmt") if container is not None else []
    if not statements:
        raise StatementError("no statement found in the file")
    ibans = {_text(s, "Acct", "Id", "IBAN") for s in statements}
    if len(ibans) != 1 or None in ibans:
        raise StatementError("statement has no single account IBAN")
    transactions: list[ParsedTx] = []
    for stmt in statements:
        transactions.extend(_entries(stmt))
    return ParsedStatement(
        file_format="camt053",
        iban=ibans.pop().replace(" ", "").upper(),
        opening_cents=_balance(statements[0], ("OPBD", "PRCD")),
        closing_cents=_balance(statements[-1], ("CLBD",)),
        transactions=transactions,
    )
