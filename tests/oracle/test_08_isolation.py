# ruff: noqa: E501
"""Scenario 9: two administrations in one tenant never mix."""
import csv
import io
from datetime import date

import pytest

from ledgerline.banking import service as bank
from ledgerline.banking.service import BankingError
from ledgerline.invoicing import service as inv
from ledgerline.invoicing.calc import InvoiceError
from ledgerline.ledger.chart import create_administration
from ledgerline.models import ContactKind
from tests.adversarial.conftest import use_admin
from tests.oracle.conftest import World, check

Q = (date(2026, 1, 1), date(2026, 3, 31))
D = date(2026, 2, 10)


@pytest.fixture
def two(world, session, client):
    second = create_administration(
        session, name="Tweede Zaak", kvk_number="11112222", vat_number="NL222222222B01",
        iban="NL91ABNA0417164300", address_line="Laan 2", postcode="2000 AA", city="Haarlem",
        email="x@example.test",
    )
    session.commit()
    other = World(session, second, client)
    other.contact("Alfa BV")           # same names on purpose
    other.contact("Leverancier NL", ContactKind.SUPPLIER)
    other.contact("EU Klant GmbH", vat="DE123456789", country="DE")
    return world, other


def build(w, scale):
    w.sale("S1", "Alfa BV", D, [(1, 10000 * scale, "V21", "8000"), (2, 3333, "V9", "8010")])
    w.sale("S2", "EU Klant GmbH", D, [(1, 7000 * scale, "VEU", "8030")])
    w.purchase("P1", "Leverancier NL", D, [("4000", 5000 * scale, "I21", None)])
    w.receive(date(2026, 2, 20), [("S1", 5000)])
    w.book_bank(date(2026, 2, 21), -1250, "4800", "I0")


def test_both_administrations_match_their_own_oracle(two):
    a, b = two
    build(a, 1)
    build(b, 7)
    for w in (a, b):
        w.verify_documents()
        w.verify_ledger(Q[1])
        w.verify_subledger(Q[1])
        w.verify_vat(*Q)
        w.verify_icp(*Q)
        w.verify_aging(Q[1])
        w.verify_dashboard(Q[1])
    check("separate numbering", (a.docs["S1"].number, b.docs["S1"].number), ("2026-0001", "2026-0001"))


def test_http_views_follow_the_selected_administration(two):
    a, b = two
    build(a, 1)
    build(b, 7)
    client = a.client
    for w in (a, b, a):
        use_admin(client, w.admin.id)
        body = client.get("/reports/trial?end=2026-03-31&export=csv").text.lstrip("﻿")
        rows = list(csv.reader(io.StringIO(body)))[1:]
        got = {r[0]: (round(float(r[2]) * 100), round(float(r[3]) * 100)) for r in rows if r[0]}
        want = w.o.trial(Q[1])
        check("trial csv", {k: v for k, v in got.items() if v != (0, 0) and k}, want)
        icp = client.get("/vat/icp?kind=quarter&year=2026&number=1&export=csv").text
        check("icp csv amount", icp.lstrip("﻿").strip().splitlines()[1].split(",")[-1],
              f"{w.o.icp(*Q)['EU Klant GmbH'] / 100:.2f}")


def test_foreign_contacts_accounts_and_invoices_are_refused(two):
    a, b = two
    build(a, 1)
    build(b, 1)
    with pytest.raises(InvoiceError):
        inv.create_sales_invoice(a.session, a.admin, b.contacts["Alfa BV"],
                                 [inv.SalesLineInput("x", 1, 100, "V21", a.acct("8000"))],
                                 issue_date=D)
    a.session.rollback()
    with pytest.raises(InvoiceError):
        inv.create_sales_invoice(a.session, a.admin, a.contacts["Alfa BV"],
                                 [inv.SalesLineInput("x", 1, 100, "V21", b.acct("8000"))],
                                 issue_date=D)
    a.session.rollback()
    with pytest.raises(InvoiceError):
        inv.issue_sales_invoice(a.session, a.admin, b.docs["S1"], None)
    a.session.rollback()
    t = a.tx(date(2026, 3, 1), 12100)
    with pytest.raises(BankingError):
        bank.match(a.session, t, [bank.Allocation("sales", b.docs["S1"].id, 12100)], None)
    a.session.rollback()
    with pytest.raises(BankingError):
        bank.book_to_account(a.session, t, b.acct("8000"), None, None)
    a.session.rollback()
    for w in (a, b):
        w.verify_ledger(Q[1])


def test_lock_and_settlement_of_one_administration_do_not_touch_the_other(two):
    a, b = two
    build(a, 1)
    build(b, 1)
    a.admin.locked_until = date(2026, 3, 31)
    a.session.commit()
    draft = b.sale("S9", "Alfa BV", date(2026, 3, 15), [(1, 1000, "V21", "8000")])  # b is open
    check("b issues inside a's locked dates", draft.number, "2026-0003")
    use_admin(a.client, b.admin.id)
    r = a.client.post("/vat/settle", data={"csrf_token": a.client.csrf, "kind": "quarter",
                                           "year": "2026", "number": "1"}, follow_redirects=False)
    check("b settles", r.status_code, 303)
    b.o.settle_vat(*Q)
    a.session.expire_all()
    b.verify_ledger(Q[1])
    a.verify_ledger(Q[1])
    a.verify_vat(*Q)
    check("a has no settlement", a.raw_balances().get("1790", 0), 0)
