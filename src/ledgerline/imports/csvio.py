"""Reading CSV files exported from Dutch spreadsheets and accounting packages."""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ledgerline.money import MoneyError, to_cents

MAX_BYTES = 1024 * 1024
MAX_ROWS = 5000
DELIMITERS = (";", ",", "\t")


class CsvError(ValueError):
    """The file as a whole cannot be read; the message is safe to show."""


@dataclass
class Table:
    rows: list[tuple[int, dict[str, str]]]
    delimiter: str
    warnings: list[str] = field(default_factory=list)
    row_errors: dict[int, str] = field(default_factory=dict)


def decode(content: bytes) -> tuple[str, list[str]]:
    if len(content) > MAX_BYTES:
        raise CsvError(f"The file is larger than {MAX_BYTES // 1024} KB.")
    if not content.strip():
        raise CsvError("The file is empty.")
    try:
        return content.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        text = content.decode("cp1252", errors="replace")
        return text, ["The file is not UTF-8; it was read as Windows-1252. "
                      "Check that accents look right."]


def detect_delimiter(text: str) -> str:
    best, width = ",", 0
    for candidate in DELIMITERS:
        try:
            first = next(csv.reader(io.StringIO(text), delimiter=candidate), [])
        except csv.Error:
            continue
        if len(first) > width:
            best, width = candidate, len(first)
    return best


def _key(header: str) -> str:
    return re.sub(r"[\s\-./]+", "_", header.strip().lower()).strip("_")


def read_table(text: str, aliases: dict[str, tuple[str, ...]],
               required: tuple[str, ...]) -> Table:
    """Rows as {canonical column: text}; `aliases` maps each canonical column to accepted names."""
    delimiter = detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise CsvError("The file has no header row.") from exc
    lookup = {_key(name): canonical for canonical, names in aliases.items()
              for name in (canonical, *names)}
    columns = [lookup.get(_key(h)) for h in header]
    present = {c for c in columns if c}
    missing = [c for c in required if c not in present]
    if missing:
        raise CsvError("Missing column: " + ", ".join(missing) + ". "
                       "Check the header row and the separator.")
    table = Table([], delimiter)
    ignored = [h.strip() for h, c in zip(header, columns, strict=True) if c is None and h.strip()]
    if ignored:
        table.warnings.append("Ignored columns: " + ", ".join(ignored) + ".")
    repeated = sorted({c for c in present if columns.count(c) > 1})
    if repeated:
        raise CsvError("Column appears twice: " + ", ".join(repeated) + ".")
    number = 1
    try:
        for record in reader:
            number += 1
            if not any(cell.strip() for cell in record):
                continue
            if len(table.rows) >= MAX_ROWS:
                raise CsvError(f"More than {MAX_ROWS} rows; split the file.")
            if len(record) > len(columns) and any(c.strip() for c in record[len(columns):]):
                table.row_errors[number] = (
                    "More cells than the header has columns; check separators and quotes.")
            values = {c: record[i].strip() if i < len(record) else ""
                      for i, c in enumerate(columns) if c}
            table.rows.append((number, values))
    except csv.Error as exc:
        raise CsvError(f"The file could not be read near row {number + 1}: {exc}") from exc
    if not table.rows:
        raise CsvError("The file has a header but no rows.")
    return table


_THOUSANDS = re.compile(r"\d{1,3}(\.\d{3})+")


def parse_amount(raw: str) -> int:
    """Euros to cents. Accepts 1234.56, 1.234,56, 1,234.56 and 1234,56; refuses guesses."""
    text = re.sub(r"[\s€]|EUR", "", raw.strip(), flags=re.IGNORECASE)
    if not text:
        raise MoneyError("amount is empty")
    sign = ""
    if text[0] in "+-":
        sign, text = text[0] if text[0] == "-" else "", text[1:]
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        if text.count(",") > 1:
            raise MoneyError(f"not a valid amount: {raw!r}")
        text = text.replace(",", ".")
    elif _THOUSANDS.fullmatch(text):
        raise MoneyError(f"ambiguous amount {raw!r}: write 1234,00 or 1.234,00")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise MoneyError(f"not a valid amount: {raw!r} (at most two decimals)")
    try:
        Decimal(text)
    except InvalidOperation as exc:
        raise MoneyError(f"not a valid amount: {raw!r}") from exc
    return to_cents(sign + text)


_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DAY_FIRST = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")


def parse_date(raw: str) -> date:
    """yyyy-mm-dd or dd-mm-yyyy (also with / or .); never month-first."""
    text = raw.strip()
    try:
        if m := _ISO.fullmatch(text):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _DAY_FIRST.fullmatch(text):
            return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        pass
    raise ValueError(f"not a valid date: {raw!r} (use yyyy-mm-dd or dd-mm-yyyy)")
