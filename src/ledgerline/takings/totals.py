"""Weekly and monthly sums of recorded days, to compare with the till's own Z-report totals."""
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.models import DailyTakings

FIELDS = ("gross_high_cents", "gross_low_cents", "gross_zero_cents", "cash_cents", "card_cents",
          "other_cents")


@dataclass
class Bucket:
    label: str
    start: date
    end: date
    days: int = 0
    gross_high_cents: int = 0
    gross_low_cents: int = 0
    gross_zero_cents: int = 0
    cash_cents: int = 0
    card_cents: int = 0
    other_cents: int = 0

    @property
    def gross_total_cents(self) -> int:
        return self.gross_high_cents + self.gross_low_cents + self.gross_zero_cents


def _bounds(kind: str, day: date) -> tuple[str, date, date]:
    if kind == "week":
        start = day - timedelta(days=day.weekday())
        iso = start.isocalendar()
        return f"{iso.year}-W{iso.week:02d}", start, start + timedelta(days=6)
    start = day.replace(day=1)
    end = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1)) - timedelta(days=1)
    return start.strftime("%Y-%m"), start, end


def totals(session: Session, admin_id: int, kind: str, start: date, end: date) -> list[Bucket]:
    """Active (not reversed) days in [start, end], grouped by ISO week or calendar month."""
    rows = session.scalars(select(DailyTakings).where(
        DailyTakings.administration_id == admin_id, DailyTakings.reversed_at.is_(None),
        DailyTakings.takings_date >= start, DailyTakings.takings_date <= end,
    ).order_by(DailyTakings.takings_date))
    buckets: dict[str, Bucket] = {}
    for row in rows:
        label, first, last = _bounds(kind, row.takings_date)
        bucket = buckets.setdefault(label, Bucket(label, first, last))
        bucket.days += 1
        for name in FIELDS:
            setattr(bucket, name, getattr(bucket, name) + getattr(row, name))
    return list(buckets.values())
