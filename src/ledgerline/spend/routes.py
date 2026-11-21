from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from ledgerline.models import Contact, ContactKind
from ledgerline.reports.export import Cents, csv_response
from ledgerline.reports.params import parse_date, parse_int
from ledgerline.spend.service import SpendFilter, overview
from ledgerline.web import AdminDep, SessionDep, UserDep, render

router = APIRouter()

Opt = Annotated[str | None, Query()]

PRESETS = ("month", "quarter", "year", "last_year", "all")
PRESET_LABELS = {"month": "This month", "quarter": "This quarter", "year": "This year",
                 "last_year": "Last year", "all": "All time"}
CSV_HEADER = ["Date", "Supplier", "Invoice number", "Source", "Status", "Excl. VAT", "VAT",
              "Incl. VAT", "Open"]


def preset_range(preset: str, today: date) -> tuple[date | None, date | None]:
    if preset == "month":
        return date(today.year, today.month, 1), today
    if preset == "quarter":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today
    if preset == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if preset == "all":
        return None, None
    return date(today.year, 1, 1), today


def _resolve(date_from: str | None, date_to: str | None, contact_id: str | None,
             drafts: str | None, preset: str | None):
    """Explicit dates override the preset; the default is the current calendar year."""
    start, end = parse_date(date_from), parse_date(date_to)
    explicit = start is not None or end is not None
    chosen = preset if preset in PRESETS else "year"
    if not explicit:
        start, end = preset_range(chosen, date.today())
    flt = SpendFilter(date_from=start, date_to=end, contact_id=parse_int(contact_id),
                      include_drafts=(drafts or "").lower() in ("1", "on", "true"))
    return flt, (None if explicit else chosen)


def _query(flt: SpendFilter) -> dict[str, str]:
    params = {}
    if flt.date_from:
        params["date_from"] = flt.date_from.isoformat()
    if flt.date_to:
        params["date_to"] = flt.date_to.isoformat()
    if not params:
        params["preset"] = "all"  # no dates would otherwise fall back to the current year
    if flt.contact_id is not None:
        params["contact_id"] = str(flt.contact_id)
    if flt.include_drafts:
        params["drafts"] = "1"
    return params


def _suppliers(session, admin_id: int) -> list[Contact]:
    return list(session.scalars(
        select(Contact)
        .where(Contact.administration_id == admin_id,
               Contact.kind.in_((ContactKind.SUPPLIER, ContactKind.BOTH)),
               Contact.anonymised_at.is_(None))
        .order_by(Contact.name)))


@router.get("/spend")
def spend_overview(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
                   date_from: Opt = None, date_to: Opt = None, contact_id: Opt = None,
                   drafts: Opt = None, preset: Opt = None):
    flt, active = _resolve(date_from, date_to, contact_id, drafts, preset)
    data = overview(session, admin.id, flt)
    keep = {k: v for k, v in _query(flt).items() if k in ("contact_id", "drafts")}
    return render(request, "spend/overview.html", {
        "data": data, "flt": flt, "active_preset": active,
        "presets": [(p, PRESET_LABELS[p], urlencode({**keep, "preset": p})) for p in PRESETS],
        "suppliers": _suppliers(session, admin.id),
        "export_query": urlencode(_query(flt)),
        "has_drafts": any(r.status == "draft" for r in data.rows),
    })


@router.get("/spend.csv")
def spend_csv(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
              date_from: Opt = None, date_to: Opt = None, contact_id: Opt = None,
              drafts: Opt = None, preset: Opt = None, comma: Opt = None):
    flt, _ = _resolve(date_from, date_to, contact_id, drafts, preset)
    data = overview(session, admin.id, flt)
    rows = [(r.invoice_date.isoformat(), r.supplier, r.supplier_reference, r.source, r.status,
             Cents(r.excl_cents), Cents(r.vat_cents), Cents(r.incl_cents), Cents(r.open_cents))
            for r in data.rows]
    start = flt.date_from.isoformat() if flt.date_from else "start"
    end = flt.date_to.isoformat() if flt.date_to else "today"
    return csv_response(f"spend-{start}_{end}.csv", CSV_HEADER, rows, comma=comma == "1")
