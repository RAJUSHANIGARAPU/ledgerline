"""CSV bank exports: reading, column mapping and presets, ending in the shared structures."""
import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from schwifty import IBAN

from ledgerline.banking.parsed import ParsedStatement, ParsedTx, StatementError

DELIMITERS = (";", ",", "\t", "|")
MAX_ERRORS = 5

_DEBIT_WORDS = {"af", "d", "db", "dbit", "debit", "debet", "afschrijving", "-"}
_CREDIT_WORDS = {"bij", "c", "cr", "crdt", "credit", "bijschrijving", "+"}
_AUTO_DATE_FORMATS = (
    "%Y-%m-%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y",
)
_CURRENCY_TOKENS = re.compile(r"€|EUR", re.IGNORECASE)
_GROUPED = {",": re.compile(r"^\d{1,3}(,\d{3})+$"), ".": re.compile(r"^\d{1,3}(\.\d{3})+$")}


class MissingColumns(StatementError):
    """The mapping names columns the file does not have."""


@dataclass
class CsvMapping:
    """Which header of the file holds what. Headers are matched case-insensitively."""

    date: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    direction: str | None = None
    value_date: str | None = None
    counterparty: str | None = None
    iban: str | None = None
    description: list[str] = field(default_factory=list)
    reference: str | None = None
    end_to_end: str | None = None
    currency: str | None = None
    own_iban: str | None = None
    date_format: str = "auto"  # "auto" or a strptime pattern such as %d-%m-%Y
    decimal: str = "auto"  # "auto", "," or "."

    def to_json(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}

    @classmethod
    def from_json(cls, data: dict) -> "CsvMapping":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def columns(self) -> list[str]:
        singles = (self.date, self.amount, self.debit, self.credit, self.direction,
                   self.value_date, self.counterparty, self.iban, self.reference,
                   self.end_to_end, self.currency, self.own_iban)
        return [c for c in (*singles, *self.description) if c]

    def validate(self) -> None:
        if not self.date:
            raise StatementError("choose the date column")
        has_amount = bool(self.amount)
        has_split = bool(self.debit or self.credit)
        if has_amount == has_split:
            raise StatementError(
                "choose either one amount column, or separate debit and credit columns"
            )
        if self.direction and not has_amount:
            raise StatementError("a debit/credit indicator needs the amount column")
        if self.decimal not in ("auto", ",", "."):
            raise StatementError("the decimal separator must be auto, a comma or a point")
        if self.date_format != "auto":
            probe = date(2026, 1, 31)
            try:
                ok = datetime.strptime(datetime(2026, 1, 31).strftime(self.date_format),
                                       self.date_format).date() == probe
            except ValueError:
                ok = False
            if not ok:
                raise StatementError(f"invalid date format {self.date_format!r}")


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    source_url: str
    accessed: str
    detect: tuple[str, ...]
    mapping: CsvMapping


# Only banks whose own published documentation states the column layout get a preset. The others
# (ING, ABN AMRO, bunq, Knab) use the generic mapper; see docs/USER_GUIDE.md.
PRESETS: dict[str, Preset] = {
    "rabobank": Preset(
        key="rabobank",
        label="Rabobank CSV (26-column export)",
        source_url="https://media.rabobank.com/m/4a7130f6f0042a57/original/"
                   "pdf_formaatbeschrijving_csv-pdf.pdf",
        accessed="2026-11-14",
        detect=("IBAN/BBAN", "Volgnr", "Rentedatum", "Saldo na trn", "Naam tegenpartij"),
        mapping=CsvMapping(
            date="Datum", value_date="Rentedatum", amount="Bedrag",
            counterparty="Naam tegenpartij", iban="Tegenrekening IBAN/BBAN",
            description=["Omschrijving-1", "Omschrijving-2", "Omschrijving-3"],
            reference="Volgnr", end_to_end="Transactiereferentie", currency="Munt",
            own_iban="IBAN/BBAN", date_format="%Y-%m-%d", decimal=",",
        ),
    ),
}


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]
    delimiter: str
    first_row_number: int


def decode(content: bytes) -> str:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return content.decode("cp1252")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _split(text: str, delimiter: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"'))


def _sniff(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    best, best_score = ";", 0
    for delimiter in DELIMITERS:
        sizes = [len(r) for r in _split(sample, delimiter) if any(c.strip() for c in r)]
        if not sizes:
            continue
        common = max(set(sizes), key=sizes.count)
        score = common * sizes.count(common) if common > 1 else 0
        if score > best_score:
            best, best_score = delimiter, score
    return best


def read_table(content: bytes) -> Table:
    text = decode(content).replace("\x00", "")
    delimiter = _sniff(text)
    records = _split(text, delimiter)
    for index, record in enumerate(records):
        if sum(1 for c in record if c.strip()) >= 2:
            headers = [c.strip() for c in record]
            rows = [r for r in records[index + 1:] if any(c.strip() for c in r)]
            if not rows:
                raise StatementError("the CSV file has a header but no transactions")
            return Table(headers, rows, delimiter, index + 2)
    raise StatementError("not a CSV bank export: no header row with columns was found")


def detect_preset(headers: list[str]) -> Preset | None:
    present = {h.strip().lower() for h in headers}
    for preset in PRESETS.values():
        if all(h.lower() in present for h in preset.detect):
            return preset
    return None


def parse_amount(raw: str, decimal: str = "auto") -> int:
    """Euro cents from 1.234,56 / 1,234.56 / -12,50 / 12,50- / (12,50) / +99,00 / € 5,00."""
    text = _CURRENCY_TOKENS.sub("", raw).replace(" ", "").replace("\u00a0", "").strip()
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    signs = 0
    if text and text[0] in "+-":
        negative, signs, text = text[0] == "-" or negative, signs + 1, text[1:]
    if text and text[-1] in "+-":
        negative, signs, text = text[-1] == "-" or negative, signs + 1, text[:-1]
    if signs > 1 or not text or not re.fullmatch(r"[0-9.,]+", text):
        raise StatementError(f"not a valid amount: {raw!r}")
    value = _to_decimal(text, decimal, raw)
    if value != value.quantize(Decimal("0.01")):
        raise StatementError(f"more than two decimals in amount: {raw!r}")
    cents = int(value * 100)
    return -cents if negative else cents


def _to_decimal(text: str, decimal: str, raw: str) -> Decimal:
    comma, point = text.count(","), text.count(".")
    if decimal == ",":
        normal = text.replace(".", "").replace(",", ".") if comma <= 1 else None
    elif decimal == ".":
        normal = text.replace(",", "") if point <= 1 else None
    elif comma and point:
        sep = "," if text.rfind(",") > text.rfind(".") else "."
        other = "." if sep == "," else ","
        whole, _, frac = text.rpartition(sep)
        normal = (whole.replace(other, "") + "." + frac) if text.count(sep) == 1 and (
            _GROUPED[other].match(whole)) else None
    elif comma or point:
        sep = "," if comma else "."
        if text.count(sep) > 1:
            normal = text.replace(sep, "") if _GROUPED[sep].match(text) else None
        else:
            whole, _, frac = text.partition(sep)
            if len(frac) == 3:
                raise StatementError(
                    f"ambiguous amount {raw!r}: set the decimal separator for this file"
                )
            normal = f"{whole or '0'}.{frac}" if 1 <= len(frac) <= 2 else None
    else:
        normal = text
    if normal is None or normal.endswith(".") or normal.startswith("."):
        raise StatementError(f"not a valid amount: {raw!r}")
    try:
        return Decimal(normal)
    except InvalidOperation as exc:
        raise StatementError(f"not a valid amount: {raw!r}") from exc


def parse_date(raw: str, date_format: str = "auto") -> date:
    text = raw.strip()
    if date_format != "auto":
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError as exc:
            raise StatementError(f"date {raw!r} does not match {date_format}") from exc
    text = re.split(r"[T ]", text, maxsplit=1)[0]
    for pattern in _AUTO_DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise StatementError(f"unrecognised date: {raw!r}")


def _iban(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    try:
        return IBAN(value.replace(" ", "")).compact
    except ValueError:
        return None


def _direction(value: str, raw_amount: str) -> int:
    word = value.strip().lower()
    if word in _DEBIT_WORDS:
        return -1
    if word in _CREDIT_WORDS:
        return 1
    raise StatementError(f"unknown debit/credit indicator {value!r} for amount {raw_amount!r}")


class _Row:
    def __init__(self, index: dict[str, int], cells: list[str]):
        self._index, self._cells = index, cells

    def get(self, column: str | None) -> str:
        if not column:
            return ""
        position = self._index[column.strip().lower()]
        return self._cells[position].strip() if position < len(self._cells) else ""


def _signed_amount(row: _Row, mapping: CsvMapping) -> int:
    if mapping.amount:
        raw = row.get(mapping.amount)
        cents = parse_amount(raw, mapping.decimal)
        if mapping.direction:
            sign = _direction(row.get(mapping.direction), raw)
            return sign * abs(cents)
        return cents
    debit, credit = row.get(mapping.debit), row.get(mapping.credit)
    out = abs(parse_amount(debit, mapping.decimal)) if debit else 0
    inn = abs(parse_amount(credit, mapping.decimal)) if credit else 0
    if out and inn:
        raise StatementError("both a debit and a credit amount are filled")
    return inn - out


def parse_csv(content: bytes, mapping: CsvMapping, account_iban: str) -> ParsedStatement:
    mapping.validate()
    table = read_table(content)
    index = {h.lower(): i for i, h in enumerate(table.headers) if h}
    missing = [c for c in mapping.columns() if c.strip().lower() not in index]
    if missing:
        raise MissingColumns(
            "these columns are not in the file: " + ", ".join(missing)
            + ". Map the columns again for this file."
        )
    transactions: list[ParsedTx] = []
    errors: list[str] = []
    other_accounts: set[str] = set()
    for number, cells in enumerate(table.rows, start=table.first_row_number):
        row = _Row(index, cells)
        try:
            if mapping.own_iban:
                own = _iban(row.get(mapping.own_iban))
                if own and own != account_iban:
                    other_accounts.add(own)
                    continue
            currency = row.get(mapping.currency).upper()
            if currency and currency != "EUR":
                raise StatementError(f"only EUR statements are supported (found {currency})")
            cents = _signed_amount(row, mapping)
            if cents == 0:
                raise StatementError("zero amount")
            booking = parse_date(row.get(mapping.date), mapping.date_format)
            value_raw = row.get(mapping.value_date)
            description = " ".join(
                " ".join(row.get(c) for c in mapping.description).split()
            )
            reference = row.get(mapping.reference)
            end_to_end = row.get(mapping.end_to_end)
            transactions.append(
                ParsedTx(
                    booking_date=booking,
                    amount_cents=cents,
                    value_date=parse_date(value_raw, mapping.date_format) if value_raw else None,
                    counterparty_name=row.get(mapping.counterparty) or None,
                    counterparty_iban=_iban(row.get(mapping.iban)),
                    description=description or None,
                    end_to_end_id=(
                        None if end_to_end in ("", "NOTPROVIDED") else end_to_end[:100]
                    ),
                    bank_reference=reference[:100] if reference else None,
                )
            )
        except StatementError as exc:
            errors.append(f"row {number}: {exc}")
            if len(errors) >= MAX_ERRORS:
                break
    if errors:
        raise StatementError("; ".join(errors))
    if not transactions:
        found = ", ".join(sorted(other_accounts)) or "none"
        raise StatementError(
            f"the file has no transactions for {account_iban} (accounts in the file: {found})"
        )
    notes = []
    if other_accounts:
        left_out = ", ".join(sorted(other_accounts))
        notes.append(f"Rows of other accounts in the file were left out: {left_out}.")
    return ParsedStatement(
        file_format="csv", iban=account_iban, opening_cents=None, closing_cents=None,
        transactions=transactions, notes=notes,
    )


def suggest_mapping(headers: list[str]) -> dict:
    """Guess columns from common header words, to pre-select the mapping form."""
    hints = {
        "date": ("boekdatum", "transactiedatum", "datum", "date"),
        "amount": ("bedrag", "amount", "transactiebedrag"),
        "counterparty": ("naam tegenpartij", "tegenrekeninghouder", "naam / omschrijving",
                         "counterparty", "naam"),
        "iban": ("tegenrekening", "tegenrekeningnummer", "counterparty iban"),
        "description": ("omschrijving", "mededelingen", "description"),
    }
    lowered = {h.lower(): h for h in headers}
    found: dict = {}
    for key, words in hints.items():
        for word in words:
            match = next((h for low, h in lowered.items() if low == word), None)
            if match:
                found[key] = [match] if key == "description" else match
                break
    return found
