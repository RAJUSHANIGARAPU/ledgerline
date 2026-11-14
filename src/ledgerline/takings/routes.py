"""Daily takings: entry, correction, CSV import and weekly/monthly totals."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from ledgerline.config import get_settings
from ledgerline.models import DailyTakings
from ledgerline.money import MoneyError, from_cents, to_cents
from ledgerline.reports.params import parse_date, parse_int
from ledgerline.takings import csv_import, service
from ledgerline.takings.service import TakingsError, TakingsInput
from ledgerline.takings.totals import totals
from ledgerline.web import AdminDep, OwnerDep, SessionDep, UserDep, flash, render

router = APIRouter(prefix="/takings")

Opt = Annotated[str | None, Query()]
LIST_LIMIT = 200
AMOUNT_FORM_FIELDS = ("gross_high", "gross_low", "gross_zero", "cash", "card", "other")


def _redirect(url: str = "/takings") -> RedirectResponse:
    return RedirectResponse(url, status.HTTP_303_SEE_OTHER)


def _row_or_404(session, admin, takings_id: int) -> DailyTakings:
    row = session.get(DailyTakings, takings_id)
    if row is None or row.administration_id != admin.id:
        raise HTTPException(404)
    return row


def _parse_form(values: dict[str, str]) -> TakingsInput:
    """Form text to input; every problem is reported, not just the first."""
    problems = []
    try:
        day = date.fromisoformat((values.get("date") or "").strip())
    except ValueError:
        problems.append("enter a valid date")
        day = date.today()
    inp = TakingsInput(takings_date=day, note=values.get("note"))
    for name in AMOUNT_FORM_FIELDS:
        raw = (values.get(name) or "").strip()
        if not raw:
            continue
        try:
            setattr(inp, f"{name}_cents", to_cents(raw))
        except MoneyError:
            problems.append(f"{name.replace('_', ' ')}: not a valid amount: {raw!r}")
    if problems:
        raise TakingsError(*problems)
    return inp


def _values_of(row: DailyTakings) -> dict[str, str]:
    values = {"date": row.takings_date.isoformat(), "note": row.note or ""}
    for name in AMOUNT_FORM_FIELDS:
        cents = getattr(row, f"{name}_cents")
        values[name] = f"{from_cents(cents):.2f}".replace(".", ",") if cents else ""
    return values


def _form_page(request, admin, values, errors=None, row=None, status_code=200):
    return render(request, "takings/form.html", {
        "admin": admin, "values": values, "errors": errors or [], "row": row,
        "today": date.today().isoformat(),
    }, status_code)


# ---------------------------------------------------------------- list

@router.get("")
def index(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
          start: Opt = None, end: Opt = None, show: Opt = None):
    s, e = parse_date(start), parse_date(end)
    stmt = select(DailyTakings).where(DailyTakings.administration_id == admin.id)
    if show != "reversed":
        stmt = stmt.where(DailyTakings.reversed_at.is_(None))
    if s:
        stmt = stmt.where(DailyTakings.takings_date >= s)
    if e:
        stmt = stmt.where(DailyTakings.takings_date <= e)
    rows = session.scalars(stmt.order_by(DailyTakings.takings_date.desc(), DailyTakings.id.desc())
                           .limit(LIST_LIMIT)).all()
    return render(request, "takings/index.html", {
        "admin": admin, "rows": rows, "start": start or "", "end": end or "",
        "show_reversed": show == "reversed", "limit": LIST_LIMIT,
    })


# ---------------------------------------------------------------- entry and correction

@router.get("/new")
def new_form(request: Request, user: OwnerDep, admin: AdminDep):
    return _form_page(request, admin, {"date": date.today().isoformat()})


@router.post("/new")
async def create(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep):
    values = {k: str(v) for k, v in (await request.form()).items()}
    try:
        inp = _parse_form(values)
        row = service.post_takings(session, admin, inp, user.id)
        session.commit()
    except TakingsError as exc:
        session.rollback()
        return _form_page(request, admin, values, exc.problems, status_code=422)
    flash(request, f"Takings of {row.takings_date.isoformat()} recorded.")
    return _redirect()


@router.get("/{takings_id}/edit")
def edit_form(takings_id: int, request: Request, session: SessionDep, user: OwnerDep,
              admin: AdminDep):
    row = _row_or_404(session, admin, takings_id)
    if row.reversed_at is not None:
        raise HTTPException(404)
    return _form_page(request, admin, _values_of(row), row=row)


@router.post("/{takings_id}/edit")
async def edit(takings_id: int, request: Request, session: SessionDep, user: OwnerDep,
               admin: AdminDep):
    row = _row_or_404(session, admin, takings_id)
    values = {k: str(v) for k, v in (await request.form()).items()}
    values["date"] = row.takings_date.isoformat()
    try:
        inp = _parse_form(values)
        service.replace_takings(session, admin, row, inp, user.id)
        session.commit()
    except TakingsError as exc:
        session.rollback()
        return _form_page(request, admin, values, exc.problems, row=row, status_code=422)
    flash(request, f"Takings of {inp.takings_date.isoformat()} corrected (reversed and reposted).")
    return _redirect()


@router.post("/{takings_id}/reverse")
def reverse(takings_id: int, request: Request, session: SessionDep, user: OwnerDep,
            admin: AdminDep):
    row = _row_or_404(session, admin, takings_id)
    try:
        service.reverse_takings(session, row, user.id, "takings reversed")
        session.commit()
        flash(request, f"Takings of {row.takings_date.isoformat()} reversed.")
    except TakingsError as exc:
        session.rollback()
        flash(request, str(exc), "error")
    return _redirect()


# ---------------------------------------------------------------- CSV import

@router.get("/import")
def import_form(request: Request, user: OwnerDep, admin: AdminDep):
    return render(request, "takings/import.html", {
        "admin": admin, "errors": [], "result": None,
        "max_mb": get_settings().max_upload_bytes // (1024 * 1024),
    })


@router.get("/template.csv")
def template(user: UserDep):
    return Response(
        csv_import.template_csv(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dagomzet-template.csv"'})


@router.post("/import")
def import_file(request: Request, session: SessionDep, user: OwnerDep, admin: AdminDep,
                file: Annotated[UploadFile, File()]):
    limit = get_settings().max_upload_bytes
    content = file.file.read(limit + 1)
    errors: list[tuple[int, str]] = []
    result = None
    if len(content) > limit:
        errors = [(0, "the file is too large")]
    else:
        try:
            result = csv_import.import_takings(session, admin, content, user.id)
            session.commit()
        except csv_import.CsvImportError as exc:
            session.rollback()
            errors = exc.errors
    if result is not None:
        flash(request, f"Imported {len(result.rows)} days of takings.")
        return _redirect()
    return render(request, "takings/import.html", {
        "admin": admin, "errors": errors, "result": None,
        "max_mb": limit // (1024 * 1024)}, 422)


# ---------------------------------------------------------------- totals

@router.get("/totals")
def totals_page(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
                kind: Opt = None, year: Opt = None):
    kind = kind or "month"
    if kind not in ("week", "month"):
        raise HTTPException(422, "invalid period type")
    y = parse_int(year) or date.today().year
    if not 2000 <= y <= 2100:
        raise HTTPException(422, "invalid year")
    buckets = totals(session, admin.id, kind, date(y, 1, 1), date(y, 12, 31))
    return render(request, "takings/totals.html", {
        "admin": admin, "kind": kind, "year": y, "buckets": buckets,
        "years": range(date.today().year - 5, date.today().year + 2),
        "sums": {n: sum(getattr(b, n) for b in buckets) for n in (
            "days", "gross_high_cents", "gross_low_cents", "gross_zero_cents", "cash_cents",
            "card_cents", "other_cents", "gross_total_cents")},
    })
