"""The VAT return must be approved, with its figures, before it can be settled."""
from datetime import date

from sqlalchemy import select, text

from ledgerline.models import AuditEvent, JournalEntry, VatApproval
from tests.conftest import _login, tenant_db
from tests.vat.booking import approve_form, approve_period, book_purchase, book_sale

D = date(2026, 2, 10)
PERIOD = {"kind": "quarter", "year": "2026", "number": "1"}


def _settle(client):
    return client.post("/vat/settle", data={**PERIOD, "csrf_token": client.csrf},
                       follow_redirects=True)


def _settlements(session):
    session.expire_all()
    return session.scalars(select(JournalEntry).where(
        JournalEntry.source_type == "vat_settlement:2026-Q1")).all()


def test_approve_then_settle(client, session, admin, owner):
    book_sale(session, admin, "V21", 10000, D)
    session.commit()
    assert approve_period(client).status_code == 303
    session.expire_all()
    approval = session.scalar(select(VatApproval))
    assert approval.approved_by_id == owner.id
    assert approval.figures["1a"] == [10000, 2100] and approval.figures["balance"] == [0, 2100]
    page = client.get("/vat?kind=quarter&year=2026&number=1").text
    assert f"Approved by {owner.email}" in page
    assert "Post VAT settlement" in page
    assert "posted for" in _settle(client).text
    assert len(_settlements(session)) == 1
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "vat.approve"))
    assert event.details["figures"]["1a"] == [10000, 2100]


def test_settle_without_approval_is_refused(client, session, admin):
    book_sale(session, admin, "V21", 10000, D)
    session.commit()
    page = client.get("/vat?kind=quarter&year=2026&number=1").text
    assert "Post VAT settlement" not in page
    assert "has not been approved" in _settle(client).text
    assert _settlements(session) == []


def test_figures_changed_after_approval_refuse_settlement(client, session, admin):
    book_sale(session, admin, "V21", 10000, D)
    session.commit()
    approve_period(client)
    book_purchase(session, admin, "I21", 5000, D)
    session.commit()
    page = client.get("/vat?kind=quarter&year=2026&number=1").text
    assert "figures changed since" in page and "Post VAT settlement" not in page
    assert "changed after they were approved" in _settle(client).text
    assert _settlements(session) == []
    # approving the new figures replaces the old approval and settlement works again
    approve_period(client)
    session.expire_all()
    assert len(session.scalars(select(VatApproval)).all()) == 1
    _settle(client)
    assert len(_settlements(session)) == 1


def test_every_rubriek_and_the_documents_statement_are_required(client, session, admin):
    book_sale(session, admin, "V21", 10000, D)
    session.commit()
    form = approve_form(client)
    missing_one = {**form, "confirm": [c for c in form["confirm"] if c != "4b"]}
    no_statement = {k: v for k, v in form.items() if k != "documents_checked"}
    for data in (missing_one, no_statement):
        client.post("/vat/approve", data=data, follow_redirects=False)
    assert session.scalar(select(VatApproval.id)) is None
    assert {"1a", "4b", "5a", "5b"} <= set(form["confirm"])


def test_figures_that_moved_while_reviewing_are_not_approved(client, session, admin):
    book_sale(session, admin, "V21", 10000, D)
    session.commit()
    form = approve_form(client)
    book_sale(session, admin, "V21", 1000, D)
    session.commit()
    client.post("/vat/approve", data=form, follow_redirects=False)
    assert session.scalar(select(VatApproval.id)) is None


def test_bookkeeper_may_approve(bookkeeper_client, session, admin, bookkeeper):
    book_sale(session, admin, "V21", 10000, D)
    session.commit()
    assert approve_period(bookkeeper_client).status_code == 303
    assert session.scalar(select(VatApproval.approved_by_id)) == bookkeeper.id


def test_uploader_and_accountant_cannot_approve(uploader_client, readonly_client, session, admin):
    for who in (uploader_client, readonly_client):
        response = who.post("/vat/approve", data={
            **PERIOD, "figures": "x", "documents_checked": "yes", "confirm": ["1a"],
            "csrf_token": who.csrf})
        assert response.status_code == 403
    assert session.scalar(select(VatApproval.id)) is None


def test_other_tenant_neither_sees_nor_reuses_the_approval(client, session, admin, owner,
                                                           other_admin, other_owner,
                                                           other_tenant, owner_engine):
    for a in (admin, other_admin):
        with tenant_db(a.tenant_id) as s:
            book_sale(s, a, "V21", 10000, D)
            s.commit()
    approve_period(client)
    other = _login(other_owner.email)
    page = other.get("/vat?kind=quarter&year=2026&number=1").text
    assert "Not approved yet" in page and owner.email not in page
    assert "has not been approved" in _settle(other).text
    with tenant_db(other_tenant.id) as s:
        assert s.scalar(select(VatApproval.id)) is None
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM vat_approvals")).scalar() == 1
    assert approve_period(other).status_code == 303
    with owner_engine.connect() as conn:
        tenants = conn.execute(text("SELECT count(DISTINCT tenant_id) FROM vat_approvals")).scalar()
    assert tenants == 2
