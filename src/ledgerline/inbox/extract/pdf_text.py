"""Read a PDF's text layer with pypdf and pull out invoice fields with Dutch-aware patterns.

Everything here is a heuristic: whatever was guessed or does not reconcile gets a warning,
and a document without a usable text layer raises so the next extractor can try.
"""
import logging
import re
import time
from collections.abc import Iterable
from datetime import date, timedelta
from difflib import SequenceMatcher
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from schwifty import IBAN
from schwifty.exceptions import SchwiftyException

from ledgerline.inbox.extract import ExtractedInvoice, ExtractedLine, ExtractionError
from ledgerline.inbox.extract.platform import parse_platform_text
from ledgerline.money import MoneyError, percent_of, to_cents

log = logging.getLogger(__name__)
MAX_PAGES = 20
MAX_LINE_CHARS = 600
MAX_TEXT_CHARS = 400_000
MAX_READ_SECONDS = 20.0
TOLERANCE_CENTS = 2  # suppliers round per line, so net + VAT can differ from the total
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
# A token that contains a digit, so a label such as "Klantnummer" is never taken for a number
_TOKEN = r"((?=[A-Za-z0-9\-/._]*\d)[A-Za-z0-9][A-Za-z0-9\-/._]{1,29})"  # noqa: S105
NUMBER_RES = (
    re.compile(
        r"(?:factuur|invoice|creditnota|credit\s*nota)\s*(?:nummer|nr\.?|no\.?|number|#)\s*[:#]?\s*"
        + _TOKEN, re.IGNORECASE),
    re.compile(r"(?:factuur|invoice)\s*[-:\u2013]?\s*n[\u00b0o]\.?\s*[:#]?\s*" + _TOKEN,
               re.IGNORECASE),
    re.compile(r"^[ \t]*(?:factuur|invoice)[ \t]*[-:\u2013]?[ \t]+" + _TOKEN,
               re.IGNORECASE | re.MULTILINE),
)
INVOICE_DATE_LABEL = re.compile(r"factuurdatum|invoice\s*date|datum\s+factuur", re.IGNORECASE)
DUE_DATE_LABEL = re.compile(
    r"vervaldatum|uiterlijk\s+te\s+betalen|uiterste\s+betaaldatum|betaaldatum|"
    r"te\s+voldoen\s+voor|due\s*date|pay\s+by", re.IGNORECASE)
TERMS_RE = re.compile(r"(?:binnen|within|betaaltermijn[:\s]*)\s*(\d{1,3})\s*(?:dagen|days)",
                      re.IGNORECASE)
INCL_LABEL = re.compile(
    r"totaal\s*(?:te\s*betalen|incl\.?(?:usief)?\.?\s*(?:btw|b\.t\.w\.))|"
    r"(?<!nog )(?<!nog  )te\s*betalen|"
    r"bedrag\s*incl\.?(?:usief)?\s*btw|totaalbedrag|eindtotaal|amount\s*due|grand\s*total|"
    r"total\s*(?:due|incl\w*\.?\s*vat)", re.IGNORECASE)
EXCL_LABEL = re.compile(
    r"subtotaal(?!\s*(?:btw|b\.t\.w\.|vat))|totaal\s*excl\.?(?:usief)?\.?(?:\s*(?:btw|b\.t\.w\.))?|"
    r"netto\s*bedrag|bedrag\s*excl\.?(?:usief)?\s*btw|subtotal|total\s*excl\w*\.?\s*(?:vat)?",
    re.IGNORECASE)
PLAIN_TOTAL = re.compile(r"^\s*(?:totaal|total)\b", re.IGNORECASE)
# Labels that can mean either the net or the gross amount; only the sum check can tell.
AMBIGUOUS_LABEL = re.compile(
    r"(?:^|(?<=\s\s))\s*(?:totaalbedrag|factuurbedrag|factuurtotaal|totaal|total|bedrag)\b",
    re.IGNORECASE)
LABEL_WORDS = re.compile(r"btw|totaal|subtotal|emballage|total", re.IGNORECASE)
DEPOSIT_LABEL = re.compile(
    r"(?:sub)?totaal\s+(?:emballage|statiegeld)|emballage\s+totaal", re.IGNORECASE)
# "BTW 21%", "BTW (21% over x)", "21,00% BTW over x", "21% btw", "21% over x is y"
VAT_RATE_RES = (
    re.compile(r"(?:btw|b\.t\.w\.|vat)[^\d%\n]{0,12}(\d{1,2})(?:[,.]\d+)?\s*%", re.IGNORECASE),
    re.compile(r"(\d{1,2})(?:[,.]\d+)?\s*%\s*(?:btw|b\.t\.w\.|vat)\b", re.IGNORECASE),
    re.compile(r"(\d{1,2})(?:[,.]\d+)?\s*%\s+(?:over|of)\b", re.IGNORECASE),
)
VAT_TOTAL_LABEL = re.compile(
    r"^\s*(?:(?:sub)?totaal\s+)?(?:btw|b\.t\.w\.|vat)\s*(?:bedrag)?\s*[:€]?\s*(?=-?\d|€)",
    re.IGNORECASE)
# "U betaalt bij (waarvan EUR 210,00 btw) EUR 1.210,00": the VAT inside a stated payable amount
WAARVAN_VAT_RE = re.compile(
    r"\(waarvan\s*(?:€|eur)?\s*(" + AMOUNT + r")\s*(?:aan\s+)?(?:btw|b\.t\.w\.|vat)\)"
    r"\s*(?:€|eur)?\s*(" + AMOUNT + ")", re.IGNORECASE)
INCLUDING_VAT_RE = re.compile(
    r"incl\w*\.?\s*(?:€|eur)?\s*(" + AMOUNT + r")\s*(?:aan\s+)?(?:btw|b\.t\.w\.|vat)\b",
    re.IGNORECASE)
REVERSE_RE = re.compile(
    r"btw[\s\-]*verlegd|verlegde\s+btw|reverse[\s\-]*charge|btw\s+(?:is\s+)?verlegd", re.IGNORECASE)
# Documents that are not purchase invoices, told apart by wording (only without an invoice number)
STATEMENT_RE = re.compile(
    r"betaaloverzicht|uitbetalingsoverzicht|afrekeningsoverzicht|rekeningoverzicht|"
    r"payout\s+(?:statement|summary)|payment\s+statement|settlement\s+statement", re.IGNORECASE)
REMINDER_RE = re.compile(
    r"aanmaning|betalingsherinnering|herinnering|ingebrekestelling|vraag\s+om\s+te\s+betalen|"
    r"bij\s+niet\s+betalen|incassokosten|wettelijke\s+rente|payment\s+reminder|final\s+notice|"
    r"niet\s+betaalde\s+rekening", re.IGNORECASE)
LEDGER_RE = re.compile(
    r"grootboek(?:mutatie)?kaart|grootboekmutaties|mutatiekaart|general\s+ledger|proef-?\s?balans|"
    r"trial\s+balance|journaalpost|dagboekoverzicht", re.IGNORECASE)
PAYSLIP_RE = re.compile(
    r"salarisspecificatie|loonstrook|loonspecificatie|salaris(?:\s|-)?strook|payslip|salary\s+slip",
    re.IGNORECASE)
CONTRACT_TITLE_RE = re.compile(
    r"orderformulier|bestelformulier|overeenkomst|offerte|contract|pro\s*forma|order\s+form|quotation",
    re.IGNORECASE)
NOT_INVOICE_NOTES = {
    "statement": "This looks like a payout or account statement, not an invoice.",
    "reminder": "This looks like a payment reminder, not an invoice.",
    "contract": "This looks like a contract or order form, not an invoice.",
    "own_sales": "The issuer looks like your own business: probably a sales invoice.",
    "ledger": "This looks like an accounting report (ledger card or balance), not an invoice.",
    "payslip": "This looks like a payslip, not an invoice; nothing was read from it.",
}
SKIP_NAME = re.compile(
    r"factuur|invoice|creditnota|pagina|page|datum|date|www\.|@|^\W*\d|\b\d{4}\s?[A-Z]{2}\b|"
    r"klantnummer|relatienummer|relatienr|debiteurnummer|beste |geachte |t\.a\.v\.|"
    r"omschrijving|bedrag|totaal|\bbtw\b|aantal|\bprijs\b|^kopie$|^origineel$|^copy$|"
    r"(?:straat|weg|laan|plein|kade|gracht|singel)\s+\d|^(?:tel|telefoon|iban|kvk|btw)\b|"
    r"^(?:per|t\.n\.v\.?|dhr|mevr|mw|de heer|mevrouw|nederland|holland|groep|group|europe)\b|[$%]|"
    r"derdengelden|^(?:tarief|aant|goed)$|administratie|afdeling",
    re.IGNORECASE)
LABELS = {"kenmerk", "referentie", "uw referentie", "ons kenmerk", "betreft", "salaris basic"}
NAME_LIKE = re.compile(r"[A-Z][^\W\d_]+(?:[ '&.-][^\W\d_]+){0,4}\.?")
IDENTIFIER_LINE = re.compile(r"\bNL\s?\d{9}\s?B\d{2}|\bkvk\b|\biban\b|\bbtw", re.IGNORECASE)
LEGAL_FORM_RE = re.compile(
    r"\b(?:b\.?v\.?|n\.?v\.?|v\.?o\.?f\.?|u\.?a\.?|c\.?v\.?|holding|stichting|gmbh|ltd|inc|s\.?a\.?)"
    r"(?=[\s,.;)]|$)", re.IGNORECASE)


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


def _bounded(text: str) -> str:
    """Cut absurd lines and total size: the line-level regexes are quadratic on long lines."""
    return "\n".join(line[:MAX_LINE_CHARS] for line in text.splitlines())[:MAX_TEXT_CHARS]


def _read_pages(reader: PdfReader, deadline: float, **kwargs) -> str:
    out = []
    for page in reader.pages[:MAX_PAGES]:
        if time.monotonic() > deadline:
            raise ExtractionError("reading the PDF took too long")
        out.append(page.extract_text(**kwargs) or "")
    return _bounded("\n".join(out))


def read_texts(content: bytes) -> list[str]:
    """The text layer read two ways: pypdf's default order, then layout order.

    The default order can put all labels before all values; layout mode keeps them on one
    line but can fold columns together, so the caller keeps whichever parse reconciles.
    """
    deadline = time.monotonic() + MAX_READ_SECONDS
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise ExtractionError("the PDF is password protected")
        texts = [_read_pages(reader, deadline)]
        if len(re.sub(r"\s+", "", texts[0])) < 20:
            raise ExtractionError("no text layer")
        try:
            texts.append(_read_pages(reader, deadline, extraction_mode="layout"))
        except (PyPdfError, ValueError, KeyError, RecursionError):
            log.warning("layout text extraction failed; using the default reading only")
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError) as exc:
        raise ExtractionError("the PDF could not be read") from exc
    return texts


def read_text(content: bytes) -> str:
    return read_texts(content)[0]


NUMBER_LABEL = re.compile(
    r"(?:factuur|invoice|credit\s*nota|creditnota)\s*(?:nummer|nr\.?|no\.?|number)\s*:?",
    re.IGNORECASE)
NUMBER_CELL = re.compile(r"(?=[A-Za-z0-9\-/._]*\d)[A-Za-z0-9][A-Za-z0-9\-/._]{2,29}")


def _aligned_number(text: str) -> str | None:
    """A number printed under its label, in the same column, when other labels share the row."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        label = NUMBER_LABEL.search(line)
        if not label or len(re.findall(r"[A-Za-z]:(?:\s|$)", line)) < 2:
            continue  # only a row of several labels; one lone label is not read across lines
        for below in lines[i + 1:i + 3]:
            for cell in NUMBER_CELL.finditer(below):
                token = cell.group().rstrip(".-/_")
                if abs(cell.start() - label.start()) <= 12 and not DATE_RE.fullmatch(token):
                    return token
    return None


def _invoice_number(text: str) -> str | None:
    """The first token after an invoice-number label that has a digit and is not a date."""
    for rx in NUMBER_RES:
        for match in rx.finditer(text):
            token = match.group(1).rstrip(".-/_")
            if len(token) >= 3 and not DATE_RE.fullmatch(token):
                return token
    aligned = _aligned_number(text)
    if aligned:
        return aligned
    # A header row "<date>  <long number>" near the top, as on till-style invoices
    header = re.search(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\s+(\d{9,14})\b",
                       "\n".join(text.splitlines()[:15]))
    return header.group(1) if header else None


def _document_type(lines: list[str], text: str, number: str | None, foreign_ids: bool,
                   own_ids_seen: bool, has_amounts: bool) -> str:
    if own_ids_seen and not foreign_ids and (number or has_amounts):
        return "own_sales"
    if number:
        return "invoice"
    if STATEMENT_RE.search(text):
        return "statement"
    if REMINDER_RE.search(text):
        return "reminder"
    if lines and CONTRACT_TITLE_RE.search(lines[0]):
        return "contract"
    title = "\n".join(lines[:8])
    if LEDGER_RE.search(title):
        return "ledger"
    if PAYSLIP_RE.search(title):
        return "payslip"
    return "invoice"


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


def _all_after_label(lines: list[str], label: re.Pattern[str], parse, window: int = 1) -> list:
    found = []
    for i, line in enumerate(lines):
        match = label.search(line)
        if not match:
            continue
        for chunk in [line[match.end():], *lines[i + 1:i + 1 + window]]:
            value = parse(chunk)
            if value is not None:
                found.append(value)
                break
    return found


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


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _key(name: str) -> str:
    return _norm("".join(re.findall(r"[A-Za-z0-9]+", name)[:2]))


def _is_own(candidate: str, own_keys: set[str]) -> bool:
    head = _key(candidate)
    return any(k in _norm(candidate) or SequenceMatcher(None, head, k).ratio() >= 0.85
               for k in own_keys)


def _name_candidates(lines: list[str]) -> list[tuple[str, int]]:
    """(text, line index) for each column segment near the top and the foot of the document."""
    out, seen = [], set()
    for i in [*range(min(40, len(lines))), *range(max(40, len(lines) - 15), len(lines))]:
        for segment in re.split(r"\s{3,}|\s\|\s", lines[i]):
            clean = re.sub(r"^.*\bt\.n\.v\.?\s+", "", segment.strip(), flags=re.IGNORECASE)
            if clean not in seen:
                seen.add(clean)
                out.append((clean, i))
    return out


def _supplier_name(lines: list[str], text: str, own_names: set[str]) -> tuple[str | None, bool]:
    """(name, trusted). Trusted means printed twice, or a company form next to its VAT/KvK/IBAN."""
    own_keys = {k for k in map(_key, own_names) if k}
    usable = [(c, i) for c, i in _name_candidates(lines)
              if len(c) >= 3 and re.search(r"[A-Za-z]{3}", c) and not SKIP_NAME.search(c)
              and len(c.split()) <= (10 if LEGAL_FORM_RE.search(c) else 6)
              and not _is_own(c, own_keys)]
    id_lines = [i for i, ln in enumerate(lines) if IDENTIFIER_LINE.search(ln)]
    legal = [(c, i) for c, i in usable if LEGAL_FORM_RE.search(c) and len(c) <= 80]
    near = next(((c, i) for c, i in legal if any(abs(i - j) <= 2 for j in id_lines)), None)
    def printed_twice(candidate: str) -> bool:
        return len(_norm(candidate)) >= 5 and _norm(text).count(_norm(candidate)) >= 2

    pick = near or (legal[0] if legal else None) or next(
        ((c, i) for c, i in usable
         if NAME_LIKE.fullmatch(c) and c.lower() not in LABELS and (printed_twice(c) or i < 3)),
        None)
    if pick is None:
        return None, False
    name = pick[0]
    repeated = printed_twice(name)
    return name[:200], bool(near) or repeated


def _amounts(chunk: str) -> list[int]:
    parsed = (parse_amount(m.group()) for m in AMOUNT_RE.finditer(chunk))
    return [a for a in parsed if a is not None]


def _vat_groups(lines: list[str]) -> dict[int, tuple[int | None, int | None]]:
    """rate -> (base, vat); the last amount on a rate line is the VAT, the one before it the base.

    Several lines for one rate (one per supplier on a combined invoice) are added up; an identical
    line repeated in a second table is not.
    """
    parts: dict[int, list[tuple[int | None, int | None]]] = {}
    for i, line in enumerate(lines):
        match = next((m for rx in VAT_RATE_RES if (m := rx.search(line))), None)
        if not match:
            continue
        amounts = _amounts(line[match.end():])
        if not amounts and i + 1 < len(lines):
            amounts = _amounts(lines[i + 1])
        pair = ((amounts[-2], amounts[-1]) if len(amounts) >= 2
                else (None, amounts[0]) if amounts else None)
        if pair and pair not in parts.setdefault(int(match.group(1)), []):
            parts[int(match.group(1))].append(pair)
    groups: dict[int, tuple[int | None, int | None]] = {}
    for rate, pairs in parts.items():
        bases = [b for b, _ in pairs]
        groups[rate] = (None if None in bases else sum(bases), sum(v for _, v in pairs))
    return groups


RECAP_ROW = re.compile(r"^\s*\d{2,4}\s+\D.*?\s\d+\s+(" + AMOUNT + r")\s+(" + AMOUNT + r")\s+("
                       + AMOUNT + r")\s*$")


def _recap_groups(lines: list[str]) -> dict[int, tuple[int | None, int | None]]:
    """rate -> (base, vat) from an article-group recap whose rows carry base, VAT and total but
    no rate (cash-and-carry receipts); the rate of each row is worked out from its amounts."""
    out: dict[int, tuple[int, int]] = {}
    for line in lines:
        match = RECAP_ROW.match(line)
        if not match:
            continue
        base, vat, total = (parse_amount(g) for g in match.groups())
        if None in (base, vat, total) or abs(base + vat - total) > 1:
            continue
        rate = _infer_rate(base, vat, 2 + base // 5000) if base else None
        if rate is None:
            return {}
        prev = out.get(rate, (0, 0))
        out[rate] = (prev[0] + base, prev[1] + vat)
    return dict(out)


def _block_sum(lines: list[str], index: int) -> int | None:
    """Sum of the 'name  amount' rows under a label that has no amount of its own."""
    rows = []
    for line in lines[index + 1:index + 8]:
        found = _amounts(line)
        if len(found) != 1 or LABEL_WORDS.search(line):
            break
        rows.append(found[0])
    return sum(rows) if len(rows) >= 2 else None


def _excl_candidates(lines: list[str]) -> list[int]:
    found = []
    for i, line in enumerate(lines):
        match = EXCL_LABEL.search(line)
        if not match:
            continue
        value = _first_amount(line[match.end():])
        if value is None:
            value = _block_sum(lines, i)
        if value is None and i + 1 < len(lines):
            value = _first_amount(lines[i + 1])
        if value is not None:
            found.append(value)
    return found


def _total_rows(lines: list[str]) -> list[tuple[int, int, int]]:
    """(excl, vat, incl) from a 'Totaal  qty  net  vat  gross' row whose last three add up."""
    rows = []
    for line in lines:
        if PLAIN_TOTAL.search(line):
            found = _amounts(line)
            if len(found) >= 3 and abs(found[-3] + found[-2] - found[-1]) <= TOLERANCE_CENTS:
                rows.append((found[-3], found[-2], found[-1]))
    return rows


def _choose_totals(excl, incl, ambiguous, vats, deposit):
    """The (excl, vat, incl, deposit_used) whose figures add up; explicit labels are tried first."""
    excls = list(dict.fromkeys(excl + ambiguous))
    incls = list(dict.fromkeys(incl + ambiguous))
    for vat in dict.fromkeys(vats):
        for e in excls:
            for i in incls:
                for extra in (0, *deposit):
                    if (e != i or vat == 0) and abs(e + vat + extra - i) <= TOLERANCE_CENTS:
                        return e, vat, i, extra
    return None


def _infer_rate(excl: int, vat: int, tolerance: int | None = None) -> int | None:
    if excl == 0:
        return None
    allowed = 1 + excl // 100_000 if tolerance is None else tolerance  # rounding grows with size
    for rate in (21, 9, 0):
        if abs(percent_of(excl, rate) - vat) <= allowed:
            return rate
    return None


def parse_text(
    text: str, ignore_vat: Iterable[str] = (), ignore_iban: Iterable[str] = (),
    ignore_kvk: Iterable[str] = (), ignore_name: Iterable[str] = (),
) -> ExtractedInvoice:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    warnings: list[str] = []
    own_vat = {v.replace(" ", "").upper() for v in ignore_vat if v}
    own_iban = {v.replace(" ", "").upper() for v in ignore_iban if v}
    own_kvk = {v for v in ignore_kvk if v}

    number = _invoice_number(text)
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
    compact = re.sub(r"[\s.]", "", text).upper()
    own_ids_seen = any(v in compact for v in own_vat) or any(
        k in KVK_RE.findall(text) for k in own_kvk)
    doc_type = _document_type(lines, text, number, bool(vats or kvk or ibans), own_ids_seen,
                              bool(AMOUNT_RE.search(text)))
    if doc_type != "invoice":
        return ExtractedInvoice(document_type=doc_type, warnings=[NOT_INVOICE_NOTES[doc_type]])
    name, name_trusted = _supplier_name(lines, text, {n for n in ignore_name if n})
    if name and not name_trusted:
        warnings.append("The supplier name was taken from the top of the page; check it.")
    elif not name:
        warnings.append("The supplier name could not be read; enter it.")

    groups = _vat_groups(lines)
    recap = _recap_groups(lines) if not groups else {}
    excl_c = _excl_candidates(lines)
    incl_c = _all_after_label(lines, INCL_LABEL, _first_amount)
    ambiguous = _all_after_label(
        [ln for ln in lines if AMBIGUOUS_LABEL.search(ln)], AMBIGUOUS_LABEL, _first_amount, 0)
    vat_labelled = _all_after_label(lines, VAT_TOTAL_LABEL, _first_amount)
    vat_c = ([sum(v for _, v in groups.values() if v is not None)] if groups else [])
    vat_c += vat_labelled + [parse_amount(m.group(1)) for m in INCLUDING_VAT_RE.finditer(text)]
    linked = None  # a payable amount with its VAT stated in the same breath
    for waarvan in WAARVAN_VAT_RE.finditer(text):
        linked = linked or (parse_amount(waarvan.group(2)), parse_amount(waarvan.group(1)))
        vat_c.append(linked[1])
        incl_c.append(linked[0])
    if REVERSE_RE.search(text):
        vat_c.append(0)
    deposit = _all_after_label(lines, DEPOSIT_LABEL, _first_amount)
    chosen = _choose_totals(excl_c, incl_c, ambiguous, [v for v in vat_c if v is not None], deposit)
    rows = _total_rows(lines)
    if chosen is None and rows:
        chosen = (*rows[-1], 0)
    deposit_used = 0
    if chosen:
        excl, vat_total, incl, deposit_used = chosen
        if deposit_used:
            warnings.append("The total includes a deposit (emballage) outside excl. VAT and VAT.")
    elif linked and None not in linked:
        excl, (incl, vat_total) = None, linked
    else:
        excl = excl_c[0] if excl_c else None
        incl = incl_c[0] if incl_c else None
        if incl is None:
            plain = [_first_amount(ln) for ln in lines if PLAIN_TOTAL.search(ln)]
            plain = [a for a in plain if a is not None]
            if plain:
                incl = plain[-1]
                warnings.append("The total payable was read from a line called 'Totaal'; check it.")
        vat_total = next((v for v in vat_c if v is not None), None)
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
    elif abs(excl + vat_total + deposit_used - incl) > TOLERANCE_CENTS:
        warnings.append("Totals do not add up: excl. VAT + VAT is not the total payable.")

    description = f"Invoice {number}" if number else "Purchase"
    extracted_lines: list[ExtractedLine] = []
    if reverse and excl is not None:
        extracted_lines.append(ExtractedLine(description, excl, None, 0))
    elif groups or (recap and excl is not None
                    and abs(sum(b for b, _ in recap.values()) - excl) <= TOLERANCE_CENTS):
        if not groups:
            groups = recap
            warnings.append("The VAT rates were worked out from the article-group recap.")
        missing = [r for r, (b, _) in groups.items() if b is None and r]
        for rate, (base, vat) in sorted(groups.items(), reverse=True):
            if base is None and rate and len(missing) == 1 and excl is not None:
                base = excl - sum(b for b, _ in groups.values() if b is not None)
                warnings.append(
                    f"The base for {rate}% VAT was worked out from the total excl. VAT.")
            elif base is None and rate:
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
    if deposit_used:
        extracted_lines.append(
            ExtractedLine("Emballage (deposit)", deposit_used, 0, 0, is_deposit=True))
    if not extracted_lines and (incl or excl):
        warnings.append("No lines could be read from this document; enter them by hand.")
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


def _reconciles(parsed: ExtractedInvoice) -> bool:
    totals = (parsed.total_excl_cents, parsed.total_vat_cents, parsed.total_incl_cents)
    return None not in totals and abs(totals[0] + totals[1] - totals[2]) <= TOLERANCE_CENTS


def parse_texts(texts: list[str], *args) -> ExtractedInvoice:
    """Parse each reading and keep the one whose totals reconcile; warn when none does."""
    parsed, error = [], None
    for text in texts:
        try:
            parsed.append(parse_text(text, *args))
        except ExtractionError as exc:
            error = error or exc
    if not parsed:
        raise error
    best = max(reversed(parsed), key=lambda p: (
        _reconciles(p), p.document_type != "invoice", p.invoice_number is not None,
        -len(p.warnings)))
    if len(parsed) > 1 and best.document_type == "invoice" and not _reconciles(best):
        best.warnings.append("The amounts could not be reconciled in either reading of the PDF.")
    return best


class PdfTextExtractor:
    method = "pdf_text"

    def __init__(self, ignore_vat=(), ignore_iban=(), ignore_kvk=(), ignore_name=()):
        self.ignore = (tuple(ignore_vat), tuple(ignore_iban), tuple(ignore_kvk), tuple(ignore_name))

    def can_handle(self, content_type: str, content: bytes) -> bool:
        return content_type == "application/pdf"

    def extract(self, content_type: str, content: bytes) -> ExtractedInvoice:
        texts = read_texts(content)
        # A platform statement is recognised before the invoice/credit-note/reminder
        # classification: it is a settlement report with its own booking flow.
        for text in texts:
            statement = parse_platform_text(text)
            if statement is not None:
                return ExtractedInvoice(
                    supplier_name=statement.name, invoice_number=statement.reference,
                    invoice_date=statement.invoice_date or statement.period_end,
                    warnings=statement.warnings,
                    platform_statement=statement.to_json(),
                )
        return parse_texts(texts, *self.ignore)
