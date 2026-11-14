"""Pilot measurements, derived from audit events and inbox rows. No content, no amounts.

Uploads and refusals come from `inbox.receive` / `inbox.receive_failed` events, the time to
process from the receive event and the first `inbox.create_purchase` / `inbox.link_duplicate`
event of the same document. Feedback is the only table added for the pilot.
"""
import csv
import io
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.inbox.service import CHANNELS
from ledgerline.models import Administration, AuditEvent, FeedbackEntry, User

PROCESSED_ACTIONS = ("inbox.create_purchase", "inbox.link_duplicate")
CHANNEL_ORDER = ("web", "phone", "shortcut", "email")


@dataclass
class ChannelCount:
    ok: int = 0
    failed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class AdministrationReport:
    administration_id: int
    name: str
    channels: dict[str, ChannelCount] = field(default_factory=dict)
    seconds_to_processed: list[float] = field(default_factory=list)
    unprocessed: int = 0

    @property
    def median_seconds(self) -> float | None:
        return median(self.seconds_to_processed) if self.seconds_to_processed else None

    @property
    def p90_seconds(self) -> float | None:
        return percentile(self.seconds_to_processed, 90)


def percentile(values: list[float], pct: int) -> float | None:
    """Nearest-rank percentile; small pilot samples make interpolation misleading."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def window(start: date, end: date) -> tuple[datetime, datetime]:
    """UTC bounds of an inclusive date range."""
    return (datetime.combine(start, time.min, UTC),
            datetime.combine(end + timedelta(days=1), time.min, UTC))


def _channel(details: dict | None) -> str:
    details = details or {}
    return details.get("channel") or CHANNELS.get(details.get("source", ""), "unknown")


def administration_reports(session: Session, start: date, end: date) -> list[AdministrationReport]:
    """One report per administration visible to the session's tenant."""
    lo, hi = window(start, end)
    reports = {a.id: AdministrationReport(a.id, a.name)
               for a in session.scalars(select(Administration).order_by(Administration.id))}
    events = session.execute(
        select(AuditEvent.administration_id, AuditEvent.action, AuditEvent.entity_id,
               AuditEvent.details, AuditEvent.created_at)
        .where(AuditEvent.action.in_(("inbox.receive", "inbox.receive_failed")),
               AuditEvent.created_at >= lo, AuditEvent.created_at < hi)
        .order_by(AuditEvent.id)
    ).all()
    received: dict[tuple[int, int], datetime] = {}
    for admin_id, action, entity_id, details, created_at in events:
        report = reports.get(admin_id)
        if report is None:
            continue
        count = report.channels.setdefault(_channel(details), ChannelCount())
        if action == "inbox.receive":
            count.ok += 1
            received[(admin_id, entity_id)] = created_at
        else:
            count.failed += 1
            reason = (details or {}).get("reason", "unknown")
            count.reasons[reason] = count.reasons.get(reason, 0) + 1
    if received:
        done = _first_processed(session, {doc_id for _, doc_id in received})
        for (admin_id, doc_id), at in received.items():
            if (admin_id, doc_id) in done:
                reports[admin_id].seconds_to_processed.append(
                    max(0.0, (done[(admin_id, doc_id)] - at).total_seconds()))
            else:
                reports[admin_id].unprocessed += 1
    return list(reports.values())


def _first_processed(session: Session, doc_ids: set[int]) -> dict[tuple[int, int], datetime]:
    rows = session.execute(
        select(AuditEvent.administration_id, AuditEvent.entity_id, AuditEvent.created_at)
        .where(AuditEvent.action.in_(PROCESSED_ACTIONS), AuditEvent.entity_id.in_(doc_ids))
        .order_by(AuditEvent.id)
    ).all()
    first: dict[tuple[int, int], datetime] = {}
    for admin_id, doc_id, at in rows:
        first.setdefault((admin_id, doc_id), at)
    return first


def feedback_rows(session: Session, start: date, end: date) -> list[dict]:
    """Verbatim entries of the session's tenant, oldest first."""
    lo, hi = window(start, end)
    rows = session.execute(
        select(FeedbackEntry, User.email, Administration.name)
        .join(User, User.id == FeedbackEntry.user_id)
        .join(Administration, Administration.id == FeedbackEntry.administration_id)
        .where(FeedbackEntry.created_at >= lo, FeedbackEntry.created_at < hi)
        .order_by(FeedbackEntry.id)
    ).all()
    return [{"id": e.id, "created_at": e.created_at.isoformat(), "user": email,
             "administration_id": e.administration_id, "administration": name, "text": e.body}
            for e, email, name in rows]


def feedback_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, ["id", "created_at", "user", "administration_id",
                                  "administration", "text"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(_safe(r) for r in rows)
    return out.getvalue()


def _safe(row: dict) -> dict:
    """Stop spreadsheets from running a formula a user typed into the feedback form."""
    return {k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@\t\r" else v)
            for k, v in row.items()}


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def render_text(tenant_id: int, reports: list[AdministrationReport], feedback: list[dict],
                start: date, end: date) -> str:
    lines = [f"Tenant {tenant_id}, {start} to {end} (UTC)"]
    for report in reports:
        lines += ["", f"Administration {report.administration_id}: {report.name}",
                  "  channel    ok  failed  reasons"]
        names = [c for c in CHANNEL_ORDER if c in report.channels] + sorted(
            c for c in report.channels if c not in CHANNEL_ORDER)
        for name in names or []:
            c = report.channels[name]
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(c.reasons.items())) or "-"
            lines.append(f"  {name:<9} {c.ok:>3}  {c.failed:>6}  {reasons}")
        if not names:
            lines.append("  no uploads")
        n = len(report.seconds_to_processed)
        lines.append(f"  received to processed: n={n} median={_duration(report.median_seconds)} "
                     f"p90={_duration(report.p90_seconds)}, not yet processed={report.unprocessed}")
    if feedback:
        lines += ["", "Feedback (verbatim)"]
        for r in feedback:
            lines += [f"  [{r['created_at']}] {r['user']} / {r['administration']}:",
                      *(f"    {line}" for line in r["text"].splitlines() or [""])]
    return "\n".join(lines)


def report_by_tenant(sessions, start: date, end: date) -> dict[int, tuple[list, list]]:
    """sessions: iterable of (tenant_id, tenant-scoped session), e.g. db.for_each_tenant()."""
    result = defaultdict(tuple)
    for tenant_id, session in sessions:
        result[tenant_id] = (administration_reports(session, start, end),
                             feedback_rows(session, start, end))
    return dict(result)
