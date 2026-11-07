from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ledgerline.reports import aging as aging_mod
from ledgerline.reports import financial
from ledgerline.reports.dashboard import build_dashboard
from ledgerline.reports.export import Cents, csv_response
from ledgerline.reports.params import parse_date
from ledgerline.web import AdminDep, SessionDep, UserDep, render

router = APIRouter()

Opt = Annotated[str | None, Query()]


def _wants_csv(request: Request) -> bool:
    return request.query_params.get("export") == "csv"


def _comma(request: Request) -> bool:
    return request.query_params.get("comma") == "1"


def _year_start(today: date) -> date:
    return date(today.year, 1, 1)


@router.get("/")
def dashboard(request: Request, session: SessionDep, user: UserDep, admin: AdminDep):
    today = date.today()
    data = build_dashboard(session, admin, today)
    chart = {
        "labels": [m["label"] for m in data.months],
        "revenue": [m["revenue"] for m in data.months],
        "expenses": [m["expenses"] for m in data.months],
    }
    return render(request, "dashboard/index.html", {"d": data, "chart": chart, "admin": admin})


@router.get("/reports")
def reports_index(request: Request, user: UserDep, admin: AdminDep):
    today = date.today()
    return render(request, "reports/index.html", {"start": _year_start(today), "end": today})


@router.get("/reports/pnl")
def pnl(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
        start: Opt = None, end: Opt = None):
    today = date.today()
    s, e = parse_date(start, _year_start(today)), parse_date(end, today)
    report = financial.profit_and_loss(session, admin.id, s, e)
    if _wants_csv(request):
        rows = [("Revenue", r.account.code, r.account.name, Cents(r.balance_cents))
                for r in report.revenue.rows]
        rows += [("Expenses", r.account.code, r.account.name, Cents(r.balance_cents))
                 for r in report.expenses.rows]
        rows.append(("Result", "", "", Cents(report.result_cents)))
        header = ["Section", "Code", "Account", "Amount"]
        return csv_response(f"profit-and-loss-{s}-{e}.csv", header, rows, comma=_comma(request))
    return render(request, "reports/pnl.html", {"r": report, "start": s, "end": e})


@router.get("/reports/balance")
def balance(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
            end: Opt = None):
    as_of = parse_date(end, date.today())
    sheet = financial.balance_sheet(session, admin.id, as_of)
    if _wants_csv(request):
        rows = []
        for section in (sheet.assets, sheet.liabilities, sheet.equity):
            rows += [(section.title, r.account.code, r.account.name, Cents(r.balance_cents))
                     for r in section.rows]
            rows += [(section.title, "", label, Cents(v)) for label, v in section.extra]
            rows.append((section.title, "", "Total", Cents(section.total_cents)))
        return csv_response(f"balance-sheet-{as_of}.csv", ["Section", "Code", "Account", "Amount"],
                            rows, comma=_comma(request))
    return render(request, "reports/balance.html", {"r": sheet, "end": as_of})


@router.get("/reports/trial")
def trial(request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
          start: Opt = None, end: Opt = None):
    s, e = parse_date(start), parse_date(end, date.today())
    rows = financial.trial_balance(session, admin.id, e, s)
    if _wants_csv(request):
        data = [(r.account.code, r.account.name, Cents(r.debit_cents), Cents(r.credit_cents))
                for r in rows]
        data.append(("", "Total", Cents(sum(r.debit_cents for r in rows)),
                     Cents(sum(r.credit_cents for r in rows))))
        return csv_response(f"trial-balance-{e}.csv", ["Code", "Account", "Debit", "Credit"],
                            data, comma=_comma(request))
    return render(request, "reports/trial.html", {
        "rows": rows, "start": s, "end": e,
        "debit": sum(r.debit_cents for r in rows), "credit": sum(r.credit_cents for r in rows),
    })


@router.get("/reports/aging/{kind}")
def aging(kind: str, request: Request, session: SessionDep, user: UserDep, admin: AdminDep,
          end: Opt = None):
    if kind not in ("receivables", "payables"):
        raise HTTPException(404)
    as_of = parse_date(end, date.today())
    rows = aging_mod.aging(session, admin.id, kind, as_of)
    totals = aging_mod.totals(rows)
    if _wants_csv(request):
        header = ["Contact", *aging_mod.BUCKETS, "Total"]
        data = [(r.name, *[Cents(b) for b in r.buckets], Cents(r.total_cents)) for r in rows]
        data.append(("Total", *[Cents(t) for t in totals], Cents(sum(totals))))
        return csv_response(f"aged-{kind}-{as_of}.csv", header, data, comma=_comma(request))
    return render(request, "reports/aging.html", {
        "rows": rows, "totals": totals, "grand": sum(totals), "buckets": aging_mod.BUCKETS,
        "kind": kind, "end": as_of,
    })
