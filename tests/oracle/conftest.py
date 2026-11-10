# ruff: noqa: E501
"""Harness that drives the application and the independent oracle side by side."""
import hashlib
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ledgerline.banking import service as bank
from ledgerline.invoicing import service as inv
from ledgerline.models import (
    Account,
    BankStatement,
    BankTransaction,
    Contact,
    ContactKind,
    JournalEntry,
    JournalLine,
    TxStatus,
)
from ledgerline.reports import aging as aging_mod
from ledgerline.reports import financial
from ledgerline.reports.dashboard import build_dashboard
from ledgerline.vat.icp import icp_overview
from ledgerline.vat.vat_return import compute_vat_return
from tests.helpers import account
from tests.oracle import oracle as model

CHECKS = {"count": 0}


def check(label, actual, expected):
    CHECKS["count"] += 1
    assert actual == expected, f"{label}: app={actual!r} oracle={expected!r}"


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"oracle: {CHECKS['count']} comparisons made")


def eur(text: str) -> int:
    """'-€ 1.234,56' -> cents."""
    if not text.strip():
        return 0
    negative = "-" in text
    digits = re.sub(r"[^0-9,]", "", text.replace(".", "")).replace(",", "")
    return -int(digits) if negative else int(digits)


def model_month(o, year, month):
    revenue = expenses = 0
    for p in o.postings:
        if (p.on.year, p.on.month) == (year, month):
            kind = model.account_type(p.account)
            if kind == model.REVENUE:
                revenue -= p.net
            elif kind == model.EXPENSE:
                expenses += p.net
    return year, month, revenue, expenses


class World:
    def __init__(self, session, admin, client):
        self.session, self.admin, self.client = session, admin, client
        self.o = model.Oracle()
        self.docs: dict = {}
        self.contacts: dict[str, Contact] = {}
        self.txs: dict[str, BankTransaction] = {}
        self.bank_account = bank.create_bank_account(session, admin, "NL91ABNA0417164300", "Main")
        self.statement = BankStatement(
            bank_account_id=self.bank_account.id, file_name="x.csv", file_format="csv",
            file_sha256=hashlib.sha256(b"x").hexdigest(),
        )
        session.add(self.statement)
        session.commit()
        self.n_tx = 0

    # --------------------------------------------------------- contacts
    def contact(self, name, kind=ContactKind.CUSTOMER, vat="NL999999999B01", country="NL"):
        c = Contact(
            administration_id=self.admin.id, name=name, kind=kind, address_line="Straat 1",
            postcode="1234 AB", city="Plaats", vat_number=vat, country=country,
        )
        self.session.add(c)
        self.session.commit()
        self.contacts[name] = c
        return c

    def acct(self, code):
        return account(self.session, self.admin, code).id

    # ------------------------------------------------------------ sales
    def sale(self, key, customer, on, lines, due=None, issue=True, expect_lines=None):
        """lines: (quantity, price_cents, vat_code, revenue_account_code)."""
        c = self.contacts[customer]
        due = due or on + timedelta(days=self.admin.payment_terms_days)
        inputs = [
            inv.SalesLineInput(f"line {i}", Decimal(str(q)), p, code, self.acct(a))
            for i, (q, p, code, a) in enumerate(lines)
        ]
        invoice = inv.create_sales_invoice(
            self.session, self.admin, c, inputs, issue_date=on, due_date=due
        )
        if issue:
            inv.issue_sales_invoice(self.session, self.admin, invoice, None)
            self.o.sale(key, customer, on, due, expect_lines or lines)
        self.session.commit()
        self.docs[key] = invoice
        return invoice

    def credit(self, key, original, on):
        note = inv.create_credit_note(self.session, self.admin, self.docs[original], None, on)
        self.session.commit()
        self.o.credit_note(key, original, on)
        self.docs[key] = note
        return note

    # -------------------------------------------------------- purchases
    def purchase(self, key, supplier, on, lines, due=None, ref=None):
        """lines: (expense_account_code, net_cents, vat_code, stated_vat_cents or None)."""
        c = self.contacts[supplier]
        due = due or on + timedelta(days=self.admin.payment_terms_days)
        inputs = [
            inv.PurchaseLineInput(f"line {i}", self.acct(a), net, code, stated)
            for i, (a, net, code, stated) in enumerate(lines)
        ]
        invoice = inv.create_purchase_invoice(
            self.session, self.admin, c, inputs, supplier_reference=ref or key,
            invoice_date=on, due_date=due,
        )
        inv.book_purchase_invoice(self.session, self.admin, invoice, None)
        self.session.commit()
        self.o.purchase(key, supplier, on, due, lines)
        self.docs[key] = invoice
        return invoice

    # ------------------------------------------------------------- bank
    def tx(self, on, amount_cents) -> BankTransaction:
        self.n_tx += 1
        t = BankTransaction(
            bank_account_id=self.bank_account.id, statement_id=self.statement.id,
            booking_date=on, amount_cents=amount_cents, dedupe_key=f"k{self.n_tx}",
            status=TxStatus.UNMATCHED,
        )
        self.session.add(t)
        self.session.commit()
        return t

    def receive(self, on, allocations):
        t = self.tx(on, sum(a for _, a in allocations))
        allocs = [bank.Allocation("sales", self.docs[k].id, a) for k, a in allocations]
        bank.match(self.session, t, allocs, None)
        self.session.commit()
        self.o.receipt(on, allocations)
        return t

    def pay(self, on, allocations):
        t = self.tx(on, -sum(a for _, a in allocations))
        allocs = [bank.Allocation("purchase", self.docs[k].id, a) for k, a in allocations]
        bank.match(self.session, t, allocs, None)
        self.session.commit()
        self.o.payment(on, allocations)
        return t

    def book_bank(self, on, amount_cents, account_code, vat_code=None):
        t = self.tx(on, amount_cents)
        bank.book_to_account(self.session, t, self.acct(account_code), vat_code, None)
        self.session.commit()
        self.o.bank_booking(on, abs(amount_cents), account_code, vat_code, amount_cents > 0)
        return t

    # ----------------------------------------------------------- verify
    def raw_balances(self, end=None, start=None):
        stmt = (
            select(Account.code, func.sum(JournalLine.debit_cents - JournalLine.credit_cents))
            .join(JournalLine, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalEntry.administration_id == self.admin.id)
            .group_by(Account.code)
        )
        if end:
            stmt = stmt.where(JournalEntry.entry_date <= end)
        if start:
            stmt = stmt.where(JournalEntry.entry_date >= start)
        return {code: int(v) for code, v in self.session.execute(stmt) if v}

    def verify_subledger(self, end: date | None = None):
        for code in (model.DEBTORS, model.CREDITORS):
            stmt = (
                select(Contact.name, func.sum(JournalLine.debit_cents - JournalLine.credit_cents))
                .join(JournalLine, JournalLine.contact_id == Contact.id)
                .join(Account, Account.id == JournalLine.account_id)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalEntry.administration_id == self.admin.id, Account.code == code)
                .group_by(Contact.name)
            )
            if end:
                stmt = stmt.where(JournalEntry.entry_date <= end)
            got = {n: int(v) for n, v in self.session.execute(stmt) if v}
            check(f"sub-ledger {code}", got, self.o.contact_balances(code, end))

    def verify_documents(self):
        self.session.expire_all()
        for key, doc in self.o.docs.items():
            app = self.docs[key]
            check(f"{key} excl", app.total_excl_cents, doc.excl)
            check(f"{key} vat", app.total_vat_cents, doc.vat)
            check(f"{key} incl", app.total_incl_cents, doc.incl)
            check(f"{key} paid", app.paid_cents, doc.paid)
            check(f"{key} status", str(app.status), doc.status)

    def verify_ledger(self, end: date, start: date | None = None):
        self.session.expire_all()
        o = self.o
        check("raw balances", self.raw_balances(end, start), o.balances(end, start))
        check("journal sums to zero", sum(self.raw_balances().values()), 0)
        year_start = date(end.year, 1, 1)
        trial = {
            r.account.code: (r.debit_cents, r.credit_cents)
            for r in financial.trial_balance(self.session, self.admin.id, end)
            if r.debit_cents or r.credit_cents
        }
        check("trial balance", trial, o.trial(end))
        check("trial debit = credit", sum(d for d, _ in trial.values()),
              sum(c for _, c in trial.values()))
        pnl = financial.profit_and_loss(self.session, self.admin.id, year_start, end)
        exp = o.pnl(year_start, end)
        check("pnl revenue", pnl.revenue.total_cents, exp["revenue"])
        check("pnl expenses", pnl.expenses.total_cents, exp["expenses"])
        check("pnl result", pnl.result_cents, exp["result"])
        sheet = financial.balance_sheet(self.session, self.admin.id, end)
        bs = o.balance_sheet(end)
        check("bs assets", sheet.assets.total_cents, bs["assets"])
        check("bs liabilities", sheet.liabilities.total_cents, bs["liabilities"])
        check("bs equity incl result", sheet.equity.total_cents, bs["equity"])
        check("bs previous result", sheet.equity.extra[0][1], bs["previous"])
        check("bs current result", sheet.equity.extra[1][1], bs["current"])
        check("bs balances", sheet.balanced, True)
        check("bs identity", bs["assets"], bs["liabilities"] + bs["equity"])

    def verify_vat(self, start: date, end: date):
        self.session.expire_all()
        want = self.o.vat_return(start, end)
        got = compute_vat_return(self.session, self.admin.id, start, end)
        for r in model.RUBRIEKEN:
            line = got.line(r)
            check(f"{r} base", line.base_cents, want["base"][r])
            check(f"{r} vat", line.vat_cents, want["vat"][r])
            check(f"{r} form base", line.form_base_euros, want["form_base"][r])
            check(f"{r} form vat", line.form_vat_euros, want["form_vat"][r])
        check("5a", got.owed_cents, want["owed"])
        check("5b", got.deductible_cents, want["deductible"])
        check("balance", got.balance_cents, want["balance"])
        check("form 5a", got.form_owed_euros, want["form_owed"])
        check("form 5b", got.form_deductible_euros, want["form_deductible"])
        check("form balance", got.form_balance_euros, want["form_owed"] - want["form_deductible"])

    def verify_icp(self, start: date, end: date):
        got = {r.name: r.amount_cents for r in icp_overview(self.session, self.admin.id, start, end)
               if r.amount_cents}
        check("icp", got, self.o.icp(start, end))

    def verify_aging(self, as_of: date):
        self.session.expire_all()
        for kind, okind in (("receivables", "sales"), ("payables", "purchase")):
            got = {r.name: r.buckets for r in aging_mod.aging(self.session, self.admin.id, kind, as_of)
                   if r.total_cents}
            check(f"aging {kind}", got, self.o.aging(okind, as_of))

    def verify_dashboard(self, today: date):
        self.session.expire_all()
        d = build_dashboard(self.session, self.admin, today)
        o = self.o
        check("dash bank", d.bank_cents, o.balances(today).get(model.BANK, 0))
        check("dash receivable", d.receivable_cents, sum(o.outstanding("sales").values()))
        check("dash payable", d.payable_cents, sum(o.outstanding("purchase").values()))
        quarter = (today.month - 1) // 3
        qs = date(today.year, quarter * 3 + 1, 1)
        qe = date(today.year + (quarter == 3), (quarter * 3 + 3) % 12 + 1, 1) - timedelta(days=1)
        check("dash vat", d.vat_cents, o.vat_return(qs, qe)["balance"])
        check("dash overdue count", d.receivable_overdue,
              sum(1 for k, v in o.outstanding("sales").items() if v > 0 and o.docs[k].due < today))
        index = today.year * 12 + today.month - 1
        months = [divmod(index - n, 12) for n in range(11, -1, -1)]
        want = [model_month(o, y, m + 1) for y, m in months]
        check("dash months", [(m["label"], m["revenue"], m["expenses"]) for m in d.months],
              [(f"{y}-{m:02d}", r, e) for (y, m, r, e) in want if True])
        check("dash result ytd", d.result_ytd_cents, o.pnl(date(today.year, 1, 1), today)["result"])

    def verify_all(self, start: date, end: date):
        self.verify_documents()
        self.verify_ledger(end)
        self.verify_vat(start, end)
        self.verify_icp(start, end)
        self.verify_aging(end)
        self.verify_dashboard(end)


@pytest.fixture
def world(session, admin, client):
    w = World(session, admin, client)
    w.contact("Alfa BV")
    w.contact("Beta BV")
    w.contact("Gamma BV")
    w.contact("Delta VOF")
    w.contact("Epsilon NV")
    w.contact("EU Klant GmbH", vat="DE123456789", country="DE")
    w.contact("Leverancier NL", ContactKind.SUPPLIER)
    w.contact("Leverancier EU", ContactKind.SUPPLIER, vat="DE987654321", country="DE")
    w.contact("Leverancier US", ContactKind.SUPPLIER, vat=None, country="US")
    w.contact("Onderaannemer NL", ContactKind.SUPPLIER)
    return w
