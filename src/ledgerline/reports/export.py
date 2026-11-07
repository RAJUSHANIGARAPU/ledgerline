"""CSV output. Dutch spreadsheets expect ';' as separator and ',' as decimal mark."""
import csv
import io
from collections.abc import Iterable, Sequence

from fastapi.responses import Response

from ledgerline.money import from_cents

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def cell(value: object, *, comma: bool) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Cents):
        text = f"{from_cents(value.cents):.2f}"
        return text.replace(".", ",") if comma else text
    text = "" if value is None else str(value)
    # Text that a spreadsheet would run as a formula is defused.
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


class Cents:
    __slots__ = ("cents",)

    def __init__(self, cents: int) -> None:
        self.cents = cents


def to_csv(header: Sequence[str], rows: Iterable[Sequence[object]], *, comma: bool) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";" if comma else ",", lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([cell(v, comma=comma) for v in row])
    return buffer.getvalue()


def csv_response(
    filename: str, header: Sequence[str], rows: Iterable[Sequence[object]], *, comma: bool = False
) -> Response:
    body = "\ufeff" + to_csv(header, rows, comma=comma)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
