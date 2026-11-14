"""Read a PDF's text layer with pypdf and pull out invoice fields with Dutch-aware patterns.

Everything here is a heuristic: whatever was guessed or does not reconcile gets a warning,
and a document without a usable text layer raises so the next extractor can try.
"""
import re
from collections.abc import Iterable
from datetime import date, timedelta
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from schwifty import IBAN
from schwifty.exceptions import SchwiftyException

from ledgerline.inbox.extract import ExtractedInvoice, ExtractedLine, ExtractionError
from ledgerline.inbox.extract.platform import parse_platform_text
from ledgerline.money import MoneyError, percent_of, to_cents

MAX_PAGES = 20
MONTHS = {
    "januari": 1, "jan": 1, "january": 1, "februari": 2, "feb": 2, "february": 2,
    "maart": 3, "mrt": 3, "march": 3, "mar": 3, "april": 4, "apr": 4, "mei": 5, "may": 5,
    "juni": 6, "jun": 6, "june": 6, "juli": 7, "jul": 7, "july": 7, "augustus": 8, "aug": 8,
    "august": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
AMOUNT = r"-?\s?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?!\d)|-?\d+\.\d{2}(?!\d)"
AMOUNT_RE = re.compile(AMOUNT)
DATE_RE = re.compile(
    r"(?P<num>\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b)|(?P<iso>\b(\d{4})-(\d{2})-(\d{2})\b)"
    r"|(?P<word>\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b)"
)
VAT_NL_RE = re.compile(r"\bNL\s?(\d{9})\s?B\s?(\d{2})\b", re.IGNORECASE)
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")
KVK_RE = re.compile(
    r"(?:kvk|k\.v\.k\.?|kamer\s+van\s+koophandel|chamber\s+of\s+commerce)"
    r"(?:[\s\-]*(?:nummer|nr\.?|number|no\.?))?[\s:.#-]*(\d{8})\b", re.IGNORECASE)
NUMBER_RE = re.compile(
    r"(?:factuurnummer|factuurnr\.?|factuur\s*nr\.?|factuur\s*no\.?|invoice\s*(?:number|no\.?|nr\.?|#)"
    r"|creditnota\s*(?:nummer|nr\.?))\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-/._]{1,29})", re.IGNORECASE)
INVOICE_DATE_LABEL = re.compile(r"factuurdatum|invoice\s*date|datum\s+factuur", re.IGNORECASE)
DUE_DATE_LABEL = re.compile(
    r"vervaldatum|uiterlijk\s+te\s+betalen|uiterste\s+betaaldatum|betaaldatum|"
    r"te\s+voldoen\s+voor|due\s*date|pay\s+by", re.IGNORECASE)
TERMS_RE = re.compile(r"(?:binnen|within|betaaltermijn[:\s]*)\s*(\d{1,3})\s*(?:dagen|days)",
                      re.IGNORECASE)
INCL_LABEL = re.compile(
    r"totaal\s*(?:te\s*betalen|incl\.?(?:usief)?\.?\s*(?:btw|b\.t\.w\.))|te\s*betalen|"
    r"bedrag\s*incl\.?(?:usief)?\s*btw|totaalbedrag|eindtotaal|amount\s*due|grand\s*total|"
    r"total\s*(?:due|incl\w*\.?\s*vat)", re.IGNORECASE)
EXCL_LABEL = re.compile(
    r"subtotaal|totaal\s*excl\.?(?:usief)?\.?(?:\s*(?:btw|b\.t\.w\.))?|netto\s*bedrag|"
    r"bedrag\s*excl\.?(?:usief)?\s*btw|subtotal|total\s*excl\w*\.?\s*(?:vat)?", re.IGNORECASE)
PLAIN_TOTAL = re.compile(r"^\s*(?:totaal|total)\b", re.IGNORECASE)
VAT_RATE_RE = re.compile(
    r"(?:btw|b\.t\.w\.|vat)\s*(?:hoog|laag)?\s*(\d{1,2})(?:[,.]0+)?\s*%", re.IGNORECASE)
VAT_TOTAL_LABEL = re.compile(
    r"^\s*(?:totaal\s+)?(?:btw|b\.t\.w\.|vat)\s*(?:bedrag)?\s*[:€]?\s*(?=[-\d€ ])", re.IGNORECASE)
REVERSE_RE = re.compile(
    r"btw[\s\-]*verlegd|verlegde\s+btw|reverse[\s\-]*charge|btw\s+(?:is\s+)?verlegd", re.IGNORECASE)
SKIP_NAME = re.compile(
    r"factuur|invoice|creditnota|pagina|page|datum|date|www\.|@|^\W*\d", re.IGNORECASE)


def parse_amount(raw: str) -> int | None:
    text = raw.replace(" ", "")
    try:
        return to_cents(text)
    except MoneyError:
        return None


def parse_date(raw: str) -> date | None:
    match = DATE_RE.search(raw)
    if not match:
        return None
    try:
        if match.group("num"):
            day, month, year = (int(g) for g in match.group(2, 3, 4))
        elif match.group("iso"):
            year, month, day = (int(g) for g in match.group(6, 7, 8))
        else:
            month = MONTHS.get(match.group(11).lower())
            if month is None:
                return None
            day, year = int(match.group(10)), int(match.group(12))
        return date(year, month, day)
    except ValueError:
        return None


def read_text(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise ExtractionError("the PDF is password protected")
        pages = [(p.extract_text() or "") for p in reader.pages[:MAX_PAGES]]
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError) as exc:
        raise ExtractionError("the PDF could not be read") from exc
    text = "\n".join(pages)
    if len(re.sub(r"\s+", "", text)) < 20:
        raise ExtractionError("no text layer")
    return text


def _after_label(lines: list[str], label: re.Pattern[str], parse, window: int = 1):
    for i, line in enumerate(lines):
        match = label.search(line)
        if not match:
            continue
        rest = line[match.end():]
        for chunk in [rest, *lines[i + 1:i + 1 + window]]:
            value = parse(chunk)
            if value is not None:
                return value
    return None


def _first_amount(chunk: str) -> int | None:
    match = AMOUNT_RE.search(chunk)
    return parse_amount(match.group()) if match else None


def _valid_ibans(text: str, ignore: set[str]) -> list[str]:
    found = []
    for match in IBAN_RE.finditer(text):
        compact = match.group().replace(" ", "")
        try:
            iban = str(IBAN(compact))
        except SchwiftyException:
            continue
        if iban not in ignore and iban not in found:
            found.append(iban)
    return found


def _supplier_name(lines: list[str]) -> str | None:
    for line in lines[:12]:
        clean = line.strip()
        if len(clean) >= 3 and re.search(r"[A-Za-z]{3}", clean) and not SKIP_NAME.search(clean):
            return clean[:200]
    return None


def _vat_groups(lines: list[str]) -> dict[int, tuple[int | None, int | None]]:
    """rate -> (base, vat) from lines like 'BTW 21% over 100,00 21,00' or 'BTW 21% 21,00'."""
    groups: dict[int, tuple[int | None, int | None]] = {}
    for i, line in enumerate(lines):
        match = VAT_RATE_RE.search(line)
        if not match:
            continue
        amounts = [parse_amount(m.group()) for m in AMOUNT_RE.finditer(line[match.end():])]
        if not amounts and i + 1 < len(lines):
            amounts = [parse_amount(m.group()) for m in AMOUNT_RE.finditer(lines[i + 1])]
        amounts = [a for a in amounts if a is not None]
        rate = int(match.group(1))
        if len(amounts) >= 2:
            groups[rate] = (amounts[-2], amounts[-1])
        elif len(amounts) == 1:
            groups[rate] = (None, amounts[0])
    return groups


def _infer_rate(excl: int, vat: int) -> int | None:
    if excl == 0:
        return None
    for rate in (21, 9, 0):
        if abs(percent_of(excl, rate) - vat) <= 1:
            return rate
    return None


def parse_text(
    text: str, ignore_vat: Iterable[str] = (), ignore_iban: Iterable[str] = (),
    ignore_kvk: Iterable[str] = (),
) -> ExtractedInvoice:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    warnings: list[str] = []
    own_vat = {v.replace(" ", "").upper() for v in ignore_vat if v}
    own_iban = {v.replace(" ", "").upper() for v in ignore_iban if v}
    own_kvk = {v for v in ignore_kvk if v}

    number_match = NUMBER_RE.search(text)
    number = number_match.group(1).rstrip(".-/_") if number_match else None
    if number and number.lower() in {"datum", "date", "nummer", "number"}:
        number = None
    invoice_date = _after_label(lines, INVOICE_DATE_LABEL, parse_date)
    if invoice_date is None:
        invoice_date = next((d for d in map(parse_date, lines) if d), None)
        if invoice_date:
            warnings.append("The invoice date was guessed from the first date in the document.")
    due_date = _after_label(lines, DUE_DATE_LABEL, parse_date)
    if due_date is None and invoice_date:
        terms = TERMS_RE.search(text)
        if terms:
            due_date = invoice_date + timedelta(days=int(terms.group(1)))
            warnings.append("The due date was worked out from the payment term in the text.")

    vats = []
    for m in VAT_NL_RE.finditer(text):
        vat = f"NL{m.group(1)}B{m.group(2)}"
        if vat not in own_vat and vat not in vats:
            vats.append(vat)
    if len(vats) > 1:
        warnings.append("More than one VAT number found; the first was used.")
    kvk = next((k for k in KVK_RE.findall(text) if k not in own_kvk), None)
    ibans = _valid_ibans(text, own_iban)
    if len(ibans) > 1:
        warnings.append("More than one IBAN found; the first was used.")
    name = _supplier_name(lines)
    if name:
        warnings.append("The supplier name was taken from the top of the page; check it.")

    groups = _vat_groups(lines)
    excl = _after_label(lines, EXCL_LABEL, _first_amount)
    incl = _after_label(lines, INCL_LABEL, _first_amount)
    if incl is None:
        plain = [_first_amount(ln) for ln in lines if PLAIN_TOTAL.search(ln)]
        plain = [a for a in plain if a is not None]
        if plain:
            incl = plain[-1]
            warnings.append("The total payable was read from a line called 'Totaal'; check it.")
    vat_total = sum(v for _, v in groups.values() if v is not None) if groups else None
    if vat_total is None:
        vat_total = _after_label(lines, VAT_TOTAL_LABEL, _first_amount)
    reverse = bool(REVERSE_RE.search(text))
    if reverse and vat_total is None:
        vat_total = 0

    if excl is None and incl is not None and vat_total is not None:
        excl = incl - vat_total
        warnings.append("The total excl. VAT was worked out from the total and the VAT.")
    if incl is None and excl is not None and vat_total is not None:
        incl = excl + vat_total
        warnings.append("The total payable was worked out from the totals excl. VAT and VAT.")
    if excl is None and incl is None and not number:
        raise ExtractionError("no invoice data found in the text")
    if reverse:
        warnings.append("The text says the VAT is reverse-charged (btw verlegd); check it.")
    if None in (excl, vat_total, incl):
        warnings.append("Not all totals could be read; fill in what is missing.")
    elif abs(excl + vat_total - incl) > 1:
        warnings.append("Totals do not add up: excl. VAT + VAT is not the total payable.")

    description = f"Invoice {number}" if number else "Purchase"
    extracted_lines: list[ExtractedLine] = []
    if reverse and excl is not None:
        extracted_lines.append(ExtractedLine(description, excl, None, 0))
    elif groups:
        for rate, (base, vat) in sorted(groups.items(), reverse=True):
            if base is None and rate:
                base = round(vat * 100 / rate) if vat else None
                warnings.append(f"The base for {rate}% VAT was worked out from the VAT amount.")
            if base is not None:
                extracted_lines.append(
                    ExtractedLine(f"{description} ({rate}% VAT)", base, rate, vat))
        base_sum = sum(ln.amount_excl_cents for ln in extracted_lines)
        if excl is not None and abs(base_sum - excl) > 1:
            warnings.append("The VAT bases do not add up to the total excl. VAT.")
    elif excl is not None:
        rate = _infer_rate(excl, vat_total) if vat_total is not None else None
        extracted_lines.append(ExtractedLine(description, excl, rate, vat_total))
    return ExtractedInvoice(
        supplier_name=name,
        supplier_vat_number=vats[0] if vats else None,
        supplier_kvk=kvk,
        supplier_iban=ibans[0] if ibans else None,
        invoice_number=number,
        invoice_date=invoice_date,
        due_date=due_date,
        total_excl_cents=excl,
        total_vat_cents=vat_total,
        total_incl_cents=incl,
        reverse_charge=reverse,
        lines=extracted_lines,
        warnings=warnings,
    )


class PdfTextExtractor:
    method = "pdf_text"

    def __init__(self, ignore_vat=(), ignore_iban=(), ignore_kvk=()):
        self.ignore = (tuple(ignore_vat), tuple(ignore_iban), tuple(ignore_kvk))

    def can_handle(self, content_type: str, content: bytes) -> bool:
        return content_type == "application/pdf"

    def extract(self, content_type: str, content: bytes) -> ExtractedInvoice:
        text = read_text(content)
        statement = parse_platform_text(text)
        if statement is not None:
            return ExtractedInvoice(
                supplier_name=statement.name, invoice_number=statement.reference,
                invoice_date=statement.period_end, warnings=statement.warnings,
                platform_statement=statement.to_json(),
            )
        return parse_text(text, *self.ignore)
