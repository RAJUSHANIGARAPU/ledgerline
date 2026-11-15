"""Pilot measurements, derived from audit events and inbox rows. No content, no amounts.

Uploads and refusals come from `inbox.receive` / `inbox.receive_failed` events, the time to
process from the receive event and the first `inbox.create_purchase` / `inbox.link_duplicate` /
`inbox.book_platform_statement` event of the same document. Review edits come from the field
names stored on `inbox.create_purchase`. Feedback is the only table added for the pilot.
"""
import csv
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.inbox.extract.platform import PLATFORM_NAMES, THUISBEZORGD, UBER
from ledgerline.inbox.service import CHANNELS
from ledgerline.models import (
    Administration,
    AuditEvent,
    FeedbackEntry,
    InvoiceStatus,
    Membership,
    PurchaseInvoice,
    User,
)

CREATE_ACTION = "inbox.create_purchase"
STATEMENT_ACTION = "inbox.book_platform_statement"
PROCESSED_ACTIONS = (CREATE_ACTION, "inbox.link_duplicate", STATEMENT_ACTION)
REJECT_ACTION = "inbox.reject"
CHANNEL_ORDER = ("web", "phone", "shortcut", "email")
ROLE_ORDER = ("owner", "bookkeeper", "uploader", "device", "email")
METHOD_ORDER = ("ubl", "pdf_text", "vision", "none", "limit")
BY_HAND = ("none", "limit")
FIELD_ORDER = ("supplier", "invoice_number", "date", "net", "vat_21", "vat_9", "vat_0", "total")


@dataclass
class ChannelCount:
    ok: int = 0
    failed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class MethodStats:
    """Documents saved as a purchase, by how they were read."""

    documents: int = 0
    untouched: int = 0
    fields: dict[str, int] = field(default_factory=dict)

    @property
    def untouched_share(self) -> float | None:
        return self.untouched / self.documents if self.documents else None


@dataclass
class AdministrationReport:
    administration_id: int
    name: str
    channels: dict[str, ChannelCount] = field(default_factory=dict)
    seconds_to_processed: list[float] = field(default_factory=list)
    unprocessed: int = 0
    # Accepted uploads by "owner #1", "uploader #2", "device #1" or "email"; never a name.
    uploaders: dict[str, int] = field(default_factory=dict)
    statements: dict[str, int] = field(default_factory=dict)
    methods: dict[str, MethodStats] = field(default_factory=dict)
    edit_untracked: int = 0
    purchases_created: int = 0
    purchases_booked: int = 0
    tenant_id: int | None = None

    @property
    def uploader_count(self) -> int:
        return sum(1 for label in self.uploaders if label not in ("email", "unknown"))

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
    reports = {a.id: AdministrationReport(a.id, a.name, tenant_id=a.tenant_id)
               for a in session.scalars(select(Administration).order_by(Administration.id))}
    events = session.execute(
        select(AuditEvent.administration_id, AuditEvent.action, AuditEvent.entity_id,
               AuditEvent.details, AuditEvent.created_at, AuditEvent.user_id)
        .where(AuditEvent.action.in_(("inbox.receive", "inbox.receive_failed")),
               AuditEvent.created_at >= lo, AuditEvent.created_at < hi)
        .order_by(AuditEvent.id)
    ).all()
    received: dict[tuple[int, int], datetime] = {}
    uploads: list[tuple[int, str, int | None]] = []
    for admin_id, action, entity_id, details, created_at, user_id in events:
        report = reports.get(admin_id)
        if report is None:
            continue
        channel = _channel(details)
        count = report.channels.setdefault(channel, ChannelCount())
        if action == "inbox.receive":
            count.ok += 1
            received[(admin_id, entity_id)] = created_at
            uploads.append((admin_id, channel, user_id))
        else:
            count.failed += 1
            reason = (details or {}).get("reason", "unknown")
            count.reasons[reason] = count.reasons.get(reason, 0) + 1
    _count_uploaders(session, reports, uploads)
    if received:
        outcomes = _outcomes(session, {doc_id for _, doc_id in received})
        for (admin_id, doc_id), at in received.items():
            report, key = reports[admin_id], (admin_id, doc_id)
            if key in outcomes.first:
                report.seconds_to_processed.append(
                    max(0.0, (outcomes.first[key] - at).total_seconds()))
            elif key not in outcomes.rejected:
                report.unprocessed += 1
            if key in outcomes.statements:
                platform = outcomes.statements[key]
                platform = platform if platform in PLATFORM_NAMES else "other"
                report.statements[platform] = report.statements.get(platform, 0) + 1
            if key in outcomes.created:
                _count_review(report, outcomes.created[key])
        _count_booked(session, reports, outcomes, received)
    return list(reports.values())


def _count_uploaders(session: Session, reports: dict[int, AdministrationReport],
                     uploads: list[tuple[int, str, int | None]]) -> None:
    """Split accepted uploads by role and an anonymous number per user, in user-id order."""
    user_ids = {u for _, _, u in uploads if u is not None}
    roles: dict[tuple[int, int], str] = {}
    if user_ids:
        roles = {(m.user_id, m.tenant_id): m.role for m in session.scalars(
            select(Membership).where(Membership.user_id.in_(user_ids)))}
    people: dict[int, dict[tuple[str, int], int]] = defaultdict(lambda: defaultdict(int))
    for admin_id, channel, user_id in uploads:
        report = reports[admin_id]
        if channel == "email" or user_id is None:
            kind = "email" if channel == "email" else "unknown"
            report.uploaders[kind] = report.uploaders.get(kind, 0) + 1
            continue
        role = "device" if channel == "shortcut" else roles.get(
            (user_id, report.tenant_id), "unknown")
        people[admin_id][(role, user_id)] += 1
    for admin_id, counts in people.items():
        index: dict[str, int] = defaultdict(int)
        for (role, _), n in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1])):
            index[role] += 1
            reports[admin_id].uploaders[f"{role} #{index[role]}"] = n


def _count_review(report: AdministrationReport, details: dict) -> None:
    changed = details.get("changed")
    if changed is None:
        report.edit_untracked += 1
        return
    method = details.get("method") or "none"
    stats = report.methods.setdefault(method, MethodStats())
    stats.documents += 1
    if method in BY_HAND:
        return
    if not changed:
        stats.untouched += 1
    for name in changed:
        stats.fields[name] = stats.fields.get(name, 0) + 1


def _count_booked(session: Session, reports, outcomes, received) -> None:
    """Purchases created from the period's documents, and how many left draft since."""
    invoice_ids = {}
    for key, details in outcomes.created.items():
        if key in received and details.get("purchase_invoice_id") is not None:
            invoice_ids[key] = details["purchase_invoice_id"]
    if not invoice_ids:
        return
    status = dict(session.execute(select(PurchaseInvoice.id, PurchaseInvoice.status).where(
        PurchaseInvoice.id.in_(set(invoice_ids.values())))).all())
    for (admin_id, _), invoice_id in invoice_ids.items():
        if invoice_id in status:
            reports[admin_id].purchases_created += 1
            if status[invoice_id] != InvoiceStatus.DRAFT:
                reports[admin_id].purchases_booked += 1


@dataclass
class Outcomes:
    first: dict[tuple[int, int], datetime] = field(default_factory=dict)
    rejected: set[tuple[int, int]] = field(default_factory=set)
    statements: dict[tuple[int, int], str] = field(default_factory=dict)
    created: dict[tuple[int, int], dict] = field(default_factory=dict)


def _outcomes(session: Session, doc_ids: set[int]) -> Outcomes:
    """First processing time per document, the documents a reviewer rejected, the platform of
    booked statements and the details of the first purchase created."""
    rows = session.execute(
        select(AuditEvent.administration_id, AuditEvent.entity_id, AuditEvent.action,
               AuditEvent.created_at, AuditEvent.details)
        .where(AuditEvent.action.in_((*PROCESSED_ACTIONS, REJECT_ACTION)),
               AuditEvent.entity_id.in_(doc_ids))
        .order_by(AuditEvent.id)
    ).all()
    out = Outcomes()
    for admin_id, doc_id, action, at, details in rows:
        key = (admin_id, doc_id)
        if action == REJECT_ACTION:
            out.rejected.add(key)
            continue
        out.first.setdefault(key, at)
        if action == STATEMENT_ACTION:
            out.statements.setdefault(key, (details or {}).get("platform", "other"))
        elif action == CREATE_ACTION:
            out.created.setdefault(key, details or {})
    return out


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
    return [{"id": e.id, "tenant_id": e.tenant_id, "created_at": e.created_at.isoformat(),
             "user": email,
             "administration_id": e.administration_id, "administration": name, "text": e.body}
            for e, email, name in rows]


def feedback_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    columns = ["id", "tenant_id", "created_at", "user", "administration_id", "administration",
               "text"]
    writer = csv.DictWriter(out, columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(_safe(r) for r in rows)
    return out.getvalue()


def _safe(row: dict) -> dict:
    """Stop spreadsheets from running a formula a user typed into the feedback form."""
    return {k: ("'" + v if isinstance(v, str) and v and v[0] in "=+-@\t\r" else v)
            for k, v in row.items()}


_UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def printable(value: object) -> str:
    """Make stored text inert in a terminal: control characters (so ANSI and OSC sequences)
    and bidi overrides become visible escapes. Newline and tab stay."""
    return _UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", str(value))


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def _who_lines(report: AdministrationReport) -> list[str]:
    if not report.uploaders:
        return []
    names = sorted(report.uploaders, key=lambda n: (
        ROLE_ORDER.index(n.split(" ")[0]) if n.split(" ")[0] in ROLE_ORDER else len(ROLE_ORDER),
        n))
    who = ", ".join(f"{printable(n)}={report.uploaders[n]}" for n in names)
    return [f"  accepted uploads by who: {who} "
            f"({report.uploader_count} signed-in users or devices)"]


def _statement_lines(report: AdministrationReport) -> list[str]:
    counts = {PLATFORM_NAMES[key]: report.statements.get(key, 0)
              for key in (THUISBEZORGD, UBER)} | {"other": report.statements.get("other", 0)}
    return [f"  platform statements booked: {sum(counts.values())} ("
            + ", ".join(f"{k}={v}" for k, v in counts.items()) + ")"]


def _percent(share: float | None) -> str:
    return "-" if share is None else f"{share:.0%}"


def _review_lines(report: AdministrationReport) -> list[str]:
    lines = [f"  purchases created from inbox documents: {report.purchases_created}, "
             f"booked since: {report.purchases_booked}"]
    if not report.methods and not report.edit_untracked:
        return lines
    lines.append("  review of documents saved as a purchase, by how they were read")
    lines.append("  method     docs  read without typing  fields changed")
    for method in [m for m in METHOD_ORDER if m in report.methods] + sorted(
            m for m in report.methods if m not in METHOD_ORDER):
        stats = report.methods[method]
        if method in BY_HAND:
            detail = "typed by hand"
        else:
            detail = ", ".join(f"{f}={stats.fields[f]}" for f in FIELD_ORDER
                               if f in stats.fields) or "-"
        lines.append(f"  {printable(method):<9} {stats.documents:>5}  "
                     f"{_percent(stats.untouched_share):>19}  {detail}")
    if report.edit_untracked:
        lines.append(f"  not tracked (saved before edit tracking): {report.edit_untracked}")
    return lines


def render_text(tenant_id: int, reports: list[AdministrationReport], feedback: list[dict],
                start: date, end: date) -> str:
    lines = [f"Tenant {tenant_id}, {start} to {end} (UTC)"]
    for report in reports:
        lines += ["", f"Administration {report.administration_id}: {printable(report.name)}",
                  "  channel    ok  failed  reasons"]
        names = [c for c in CHANNEL_ORDER if c in report.channels] + sorted(
            c for c in report.channels if c not in CHANNEL_ORDER)
        for name in names or []:
            c = report.channels[name]
            reasons = ", ".join(f"{printable(k)}={v}" for k, v in sorted(c.reasons.items())) or "-"
            lines.append(f"  {printable(name):<9} {c.ok:>3}  {c.failed:>6}  {reasons}")
        if not names:
            lines.append("  no uploads")
        n = len(report.seconds_to_processed)
        lines.append(f"  received to processed: n={n} median={_duration(report.median_seconds)} "
                     f"p90={_duration(report.p90_seconds)}, not yet processed={report.unprocessed}")
        lines += _who_lines(report) + _statement_lines(report) + _review_lines(report)
    if feedback:
        lines += ["", "Feedback (verbatim)"]
        for r in feedback:
            lines += [f"  [{r['created_at']}] {printable(r['user'])} / "
                      f"{printable(r['administration'])}:",
                      *(f"    {line}" for line in printable(r["text"]).split("\n"))]
    return "\n".join(lines)


def report_by_tenant(sessions, start: date, end: date) -> dict[int, tuple[list, list]]:
    """sessions: iterable of (tenant_id, tenant-scoped session), e.g. db.for_each_tenant()."""
    result = defaultdict(tuple)
    for tenant_id, session in sessions:
        result[tenant_id] = (administration_reports(session, start, end),
                             feedback_rows(session, start, end))
    return dict(result)


def report_dict(report: AdministrationReport) -> dict:
    """The machine-readable form of one administration's numbers (no names, no feedback)."""
    return {
        "administration_id": report.administration_id,
        "channels": {n: {"ok": c.ok, "failed": c.failed, "reasons": c.reasons}
                     for n, c in report.channels.items()},
        "uploads_by": report.uploaders,
        "uploader_count": report.uploader_count,
        "processed": {"n": len(report.seconds_to_processed),
                      "median_seconds": report.median_seconds,
                      "p90_seconds": report.p90_seconds, "not_yet_processed": report.unprocessed},
        "platform_statements_booked": report.statements,
        "purchases_created": report.purchases_created,
        "purchases_booked": report.purchases_booked,
        "review": {m: {"documents": st.documents, "untouched": st.untouched,
                       "untouched_share": st.untouched_share, "fields_changed": st.fields,
                       "typed_by_hand": m in BY_HAND}
                   for m, st in report.methods.items()},
        "review_not_tracked": report.edit_untracked,
    }
