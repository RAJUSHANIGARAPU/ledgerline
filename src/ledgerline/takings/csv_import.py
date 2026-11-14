"""Quarter of Z-reports at once: one row per day, all rows valid or nothing is posted."""
import csv
import io
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import Administration, DailyTakings
from ledgerline.money import MoneyError, to_cents
from ledgerline.takings import service
from ledgerline.takings.service import TakingsInput

MAX_ROWS = 1000
# Template column -> TakingsInput field. The 21/9/0 headers name the VAT rate of the sales.
COLUMNS = {
    "date": "takings_date",
    "gross_21": "gross_high_cents",
    "gross_9": "gross_low_cents",
    "gross_0": "gross_zero_cents",
    "cash": "cash_cents",
    "card": "card_cents",
    "other": "other_cents",
    "note": "note",
}
AMOUNT_FIELDS = [f for f in COLUMNS.values() if f.endswith("_cents")]
TEMPLATE_HEADER = list(COLUMNS)
TEMPLATE_ROWS = [
    ["2026-10-01", "1210,00", "109,00", "0,00", "320,00", "899,00", "100,00", "Z-report 1041"],
    ["2026-10-02", "605,00", "0,00", "0,00", "205,00", "400,00", "0,00", ""],
]


class CsvImportError(ValueError):
    """The file was refused; nothing was posted. `errors` holds (line number, message) pairs."""

    def __init__(self, errors: list[tuple[int, str]]):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) found; nothing was imported")


@dataclass
class ImportResult:
    rows: list[DailyTakings] = field(default_factory=list)

    @property
    def gross_total_cents(self) -> int:
        return sum(r.gross_total_cents for r in self.rows)


def _parse_date(text: str) -> date:
    text = text.strip()
    try:
        if len(text) == 10 and text[4] == "-":
            return date.fromisoformat(text)
        day, month, year = text.split("-")
        return date(int(year), int(month), int(day))
    except ValueError:
        raise ValueError(f"not a valid date: {text!r} (use YYYY-MM-DD or DD-MM-YYYY)") from None


def _parse_row(row: dict[str, str]) -> TakingsInput:
    inp = TakingsInput(takings_date=_parse_date(row.get("date") or ""))
    for column, name in COLUMNS.items():
        if name == "takings_date":
            continue
        raw = (row.get(column) or "").strip()
        if name == "note":
            inp.note = raw or None
        elif raw:
            try:
                setattr(inp, name, to_cents(raw))
            except MoneyError:
                raise ValueError(f"{column}: not a valid amount: {raw!r}") from None
    return inp


def read_rows(content: bytes) -> list[tuple[int, dict[str, str]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvImportError([(0, "the file is not UTF-8 text; save it as CSV (UTF-8)")]) from None
    delimiter = ";" if text.split("\n", 1)[0].count(";") >= 1 else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        raise CsvImportError([(0, "the file is empty")]) from None
    missing = [c for c in COLUMNS if c != "note" and c not in header]
    unknown = [h for h in header if h and h not in COLUMNS]
    if missing or unknown:
        parts = []
        if missing:
            parts.append(f"missing column(s): {', '.join(missing)}")
        if unknown:
            parts.append(f"unknown column(s): {', '.join(unknown)}")
        raise CsvImportError([(1, "; ".join(parts) + "; download the template")])
    rows = []
    for number, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        rows.append((number, dict(zip(header, values, strict=False))))
    if not rows:
        raise CsvImportError([(0, "the file has no rows")])
    if len(rows) > MAX_ROWS:
        raise CsvImportError([(0, f"at most {MAX_ROWS} rows per file")])
    return rows


def import_takings(session: Session, admin: Administration, content: bytes,
                   user_id: int | None = None) -> ImportResult:
    """Validate every row first; post them all only when none has a problem."""
    errors: list[tuple[int, str]] = []
    parsed: list[tuple[int, TakingsInput]] = []
    for number, row in read_rows(content):
        try:
            parsed.append((number, _parse_row(row)))
        except ValueError as exc:
            errors.append((number, str(exc)))
    known = set(session.scalars(select(DailyTakings.takings_date).where(
        DailyTakings.administration_id == admin.id, DailyTakings.reversed_at.is_(None))))
    seen: dict[date, int] = {}
    for number, inp in parsed:
        for message in service.problems_for(inp, admin.locked_until):
            errors.append((number, message))
        if inp.takings_date in known:
            errors.append((number, f"{inp.takings_date.isoformat()} is already recorded"))
        elif inp.takings_date in seen:
            errors.append((number, f"{inp.takings_date.isoformat()} also appears on line "
                                   f"{seen[inp.takings_date]}"))
        seen.setdefault(inp.takings_date, number)
    if errors:
        raise CsvImportError(sorted(errors))
    result = ImportResult()
    for _, inp in sorted(parsed, key=lambda item: item[1].takings_date):
        try:
            result.rows.append(service.post_takings(session, admin, inp, user_id))
        except service.TakingsError as exc:
            raise CsvImportError([(0, str(exc))]) from exc
    return result


def template_csv() -> str:
    """Semicolon-separated with decimal commas, the way Dutch spreadsheets open it."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(TEMPLATE_HEADER)
    writer.writerows(TEMPLATE_ROWS)
    return "\ufeff" + buffer.getvalue()
