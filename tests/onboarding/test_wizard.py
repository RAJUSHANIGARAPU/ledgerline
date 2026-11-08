from datetime import date

import pytest
from sqlalchemy import select

from ledgerline import audit  # noqa: F401
from ledgerline.ledger.service import reverse_entry
from ledgerline.models import Administration, AuditEvent, BankAccount, JournalEntry
from ledgerline.onboarding import service
from tests.helpers import account

BUSINESS = {"name": "Jansen Advies", "legal_form": "eenmanszaak", "kvk_number": "12345678",
            "vat_number": "NL123456789B01", "address_line": "Kade 1", "postcode": "1000 AA",
            "city": "Utrecht", "iban": "NL91ABNA0417164300", "email": "a@b.nl",
            "invoice_prefix": "JA-", "payment_terms_days": "14"}


def post(client, url, **data):
    return client.post(url, data={**data, "csrf_token": client.csrf}, follow_redirects=False)


def by_key(session, admin):
    session.expire_all()
    admin = session.get(Administration, admin.id)
    return {s.key: s for s in service.steps(session, admin)}


def test_progress_is_derived_from_data(session, admin):
    admin.kvk_number = admin.vat_number = admin.iban = None
    session.commit()
    s = by_key(session, admin)
    assert s["business"].done is False and s["bank"].done is False
    assert s["review"].done is None and not service.setup_complete(session, admin)
    admin.kvk_number, admin.vat_number = "12345678", "NL123456789B01"
    admin.iban = "NL91ABNA0417164300"
    session.commit()
    assert by_key(session, admin)["business"].done is True


def test_setup_pages_render_and_resume(client, session, admin):
    r = client.get("/setup")
    assert r.status_code == 200 and "Finish setting up" in r.text
    assert "2 of 6 steps done" in r.text
    for key in service.STEP_KEYS:
        assert client.get(f"/setup/{key}").status_code == 200
    assert client.get("/setup/nope").status_code == 404


def test_business_validation_errors_are_shown_and_nothing_saved(client, session, admin):
    r = post(client, "/setup/business", **{**BUSINESS, "vat_number": "NL1", "kvk_number": "12",
                                           "iban": "NL00BAD", "email": "x"})
    assert r.status_code == 422
    assert "VAT number does not match" in r.text and "KvK number must be 8 digits" in r.text
    assert "IBAN is not valid" in r.text and "Email address is not valid" in r.text
    session.expire_all()
    assert session.get(Administration, admin.id).name == "Testbedrijf"


def test_business_saved_and_audited(client, session, admin):
    r = post(client, "/setup/business", **BUSINESS)
    assert r.status_code == 303
    session.expire_all()
    a = session.get(Administration, admin.id)
    assert (a.name, a.invoice_prefix, a.payment_terms_days, a.city) == (
        "Jansen Advies", "JA-", 14, "Utrecht")
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "administration.update"))


def test_kor_toggle_persists_and_rejects_nonsense(client, session, admin):
    assert post(client, "/setup/vat", scheme="kor").status_code == 303
    session.expire_all()
    assert session.get(Administration, admin.id).kor is True
    assert post(client, "/setup/vat", scheme="regular").status_code == 303
    session.expire_all()
    assert session.get(Administration, admin.id).kor is False
    assert post(client, "/setup/vat", scheme="x").status_code == 422


def test_bank_step_creates_account(client, session, admin):
    r = post(client, "/setup/bank", name="Zakelijk", iban="NL91 ABNA 0417 1643 00")
    assert r.status_code == 303
    bank = session.scalar(select(BankAccount))
    assert bank.iban == "NL91ABNA0417164300"
    assert bank.ledger_account_id == account(session, admin, "1100").id
    assert by_key(session, admin)["bank"].done is True
    dup = post(client, "/setup/bank", name="Again", iban="NL91ABNA0417164300")
    assert dup.status_code == 422 and "already registered" in dup.text
    assert post(client, "/setup/bank", name="", iban="bad").status_code == 422


def _bank(client, session):
    post(client, "/setup/bank", name="Zakelijk", iban="NL91ABNA0417164300")
    return session.scalar(select(BankAccount))


def test_opening_balance_posts_balanced_entry_and_refuses_duplicate(client, session, admin):
    bank = _bank(client, session)
    r = post(client, "/setup/opening", entry_date="2026-01-01",
             **{f"balance_{bank.id}": "1.234,56"})
    assert r.status_code == 303
    session.expire_all()
    entry = session.scalar(
        select(JournalEntry).where(JournalEntry.source_type == "opening_balance"))
    got = {line.account_id: (line.debit_cents, line.credit_cents) for line in entry.lines}
    assert got == {account(session, admin, "1100").id: (123456, 0),
                   account(session, admin, "0500").id: (0, 123456)}
    assert by_key(session, admin)["opening"].done is True

    dup = post(client, "/setup/opening", entry_date="2026-01-01", **{f"balance_{bank.id}": "5"})
    assert dup.status_code == 422 and "already posted" in dup.text
    assert len(session.scalars(select(JournalEntry)).all()) == 1

    reverse_entry(session, entry, date(2026, 1, 2), "redo")
    session.commit()
    assert by_key(session, admin)["opening"].done is False
    again = post(client, "/setup/opening", entry_date="2026-01-01",
                 **{f"balance_{bank.id}": "-10,00"})
    assert again.status_code == 303
    last = session.scalars(select(JournalEntry).where(
        JournalEntry.source_type == "opening_balance").order_by(JournalEntry.id)).all()[-1]
    assert sum(line.debit_cents for line in last.lines) == sum(
        line.credit_cents for line in last.lines) == 1000


def test_opening_balance_input_errors(client, session, admin):
    bank = _bank(client, session)
    assert post(client, "/setup/opening", entry_date="", **{f"balance_{bank.id}": "1"}
                ).status_code == 422
    r = post(client, "/setup/opening", entry_date="2026-01-01", **{f"balance_{bank.id}": "abc"})
    assert r.status_code == 422 and "Enter an amount" in r.text
    r = post(client, "/setup/opening", entry_date="2026-01-01", **{f"balance_{bank.id}": "0"})
    assert r.status_code == 422 and "at least one" in r.text
    session.expire_all()
    assert admin.locked_until is None
    admin.locked_until = date(2026, 3, 31)
    session.commit()
    r = post(client, "/setup/opening", entry_date="2026-01-01", **{f"balance_{bank.id}": "5"})
    assert r.status_code == 422 and "closed" in r.text


@pytest.mark.parametrize("path", ["/setup/business", "/setup/vat", "/setup/bank", "/setup/opening"])
def test_readonly_user_cannot_post(readonly_client, path):
    r = readonly_client.post(path, data={"csrf_token": readonly_client.csrf})
    assert r.status_code == 403


def test_readonly_user_cannot_open_wizard_but_can_read_help(readonly_client):
    assert readonly_client.get("/setup").status_code == 403
    assert readonly_client.get("/setup/business").status_code == 403
    assert readonly_client.get("/help").status_code == 200


def test_post_without_csrf_is_refused(client):
    assert client.post("/setup/vat", data={"scheme": "kor"}).status_code == 403
