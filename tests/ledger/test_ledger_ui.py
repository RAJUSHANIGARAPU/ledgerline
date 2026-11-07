from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline.ledger.chart import create_administration
from ledgerline.ledger.service import LineSpec, post_entry
from ledgerline.models import Account, AuditEvent, JournalEntry
from ledgerline.money import format_eur
from tests.helpers import account


def _form(client, session, admin, *, debit="100,00", credit="100,00", when="2026-02-10",
          description="Owner contribution", codes=("1100", "0520")):
    ids = [str(account(session, admin, c).id) for c in codes]
    return {
        "csrf_token": client.csrf, "date": when, "description": description,
        "account_id": ids, "debit": [debit, ""], "credit": ["", credit],
        "line_description": ["", ""], "vat_code": ["", ""], "vat_role": ["base", "base"],
    }


def _entries(session):
    session.expire_all()
    return session.scalars(select(JournalEntry).order_by(JournalEntry.id)).all()


def _other_admin(session):
    other = create_administration(session, name="Ander BV")
    cash = session.scalar(select(Account).where(Account.administration_id == other.id,
                                                Account.code == "1000"))
    capital = session.scalar(select(Account).where(Account.administration_id == other.id,
                                                   Account.code == "0520"))
    entry = post_entry(session, other.id, date(2026, 1, 5), "Their entry",
                       [LineSpec(cash.id, debit_cents=500), LineSpec(capital.id, credit_cents=500)])
    session.commit()
    return other, entry, cash


# ---- manual entries

def test_manual_entry_is_posted_and_audited(client, session, admin):
    response = client.post("/ledger/new", data=_form(client, session, admin),
                           follow_redirects=False)
    assert response.status_code == 303
    (entry,) = _entries(session)
    assert response.headers["location"] == f"/ledger/{entry.id}"
    assert (entry.number, entry.source_type, entry.description) == (
        "2026-00001", "manual", "Owner contribution")
    assert sorted((line.debit_cents, line.credit_cents) for line in entry.lines) == [
        (0, 10000), (10000, 0)]
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "journal.post"))
    assert event.entity_id == entry.id


def test_unbalanced_entry_is_refused_with_message(client, session, admin):
    response = client.post("/ledger/new",
                           data=_form(client, session, admin, debit="100,00", credit="99,99"))
    assert response.status_code == 422
    assert "does not balance" in response.text
    assert 'value="Owner contribution"' in response.text  # the form keeps what was typed
    assert _entries(session) == []


@pytest.mark.parametrize(("overrides", "message"), [
    ({"debit": "abc"}, "not a valid amount"),
    ({"description": "  "}, "enter a description"),
    ({"when": "2026-13-45"}, "invalid date"),
])
def test_invalid_input_shows_error_and_posts_nothing(client, session, admin, overrides, message):
    response = client.post("/ledger/new", data=_form(client, session, admin, **overrides))
    assert response.status_code == 422 and message in response.text
    assert _entries(session) == []


def test_line_with_amount_but_no_account_is_refused(client, session, admin):
    data = _form(client, session, admin)
    data["account_id"] = [data["account_id"][0], ""]
    response = client.post("/ledger/new", data=data)
    assert response.status_code == 422 and "line 2: choose an account" in response.text


def test_vat_tags_are_stored_on_lines(client, session, admin):
    data = _form(client, session, admin, debit="121,00", credit="100,00",
                 codes=("1300", "8000"))
    vat_account = str(account(session, admin, "1700").id)
    data["account_id"].append(vat_account)
    data["debit"].append("")
    data["credit"].append("21,00")
    data["line_description"].append("")
    data["vat_code"] = ["", "V21", "V21"]
    data["vat_role"] = ["base", "base", "vat"]
    assert client.post("/ledger/new", data=data, follow_redirects=False).status_code == 303
    (entry,) = _entries(session)
    tags = {(line.account_id, line.vat_code, line.vat_role) for line in entry.lines}
    assert (account(session, admin, "8000").id, "V21", "base") in tags
    assert (int(vat_account), "V21", "vat") in tags


def test_vat_code_without_a_valid_value_is_refused(client, session, admin):
    data = _form(client, session, admin)
    data["vat_code"] = ["ZZZ", ""]
    response = client.post("/ledger/new", data=data)
    assert response.status_code == 422 and "unknown VAT code ZZZ" in response.text


def test_foreign_account_in_a_line_is_refused(client, session, admin):
    _other, _entry, their_cash = _other_admin(session)
    data = _form(client, session, admin)
    data["account_id"] = [str(their_cash.id), data["account_id"][1]]
    response = client.post("/ledger/new", data=data)
    assert response.status_code == 422 and "belongs elsewhere" in response.text
    assert [e.administration_id for e in _entries(session)] == [_other.id]


def test_period_lock_blocks_manual_posting(client, session, admin):
    admin.locked_until = date(2026, 2, 28)
    session.commit()
    response = client.post("/ledger/new", data=_form(client, session, admin, when="2026-02-28"))
    assert response.status_code == 422 and "period is closed up to 2026-02-28" in response.text
    ok = client.post("/ledger/new", data=_form(client, session, admin, when="2026-03-01"),
                     follow_redirects=False)
    assert ok.status_code == 303 and len(_entries(session)) == 1


def test_balance_indicator(client):
    text = client.get("/ledger/new/balance?debit=10,00&debit=&credit=&credit=4,00").text
    assert "difference" in text and format_eur(600) in text
    assert "balanced" in client.get("/ledger/new/balance?debit=10,00&credit=10,00").text
    assert "check the amounts" in client.get("/ledger/new/balance?debit=x").text


def test_new_form_and_line_partial_render(client):
    page = client.get("/ledger/new")
    assert page.status_code == 200 and 'hx-get="/ledger/new/line"' in page.text
    assert "<script>" not in page.text
    assert 'name="vat_role"' in client.get("/ledger/new/line").text


# ---- journal list, detail, reversal

def _post(session, admin, when, description, cents=1000, debit="1100", credit="0520"):
    entry = post_entry(session, admin.id, when, description, [
        LineSpec(account(session, admin, debit).id, debit_cents=cents),
        LineSpec(account(session, admin, credit).id, credit_cents=cents)])
    session.commit()
    return entry


def test_list_filters_by_range_account_and_text(client, session, admin):
    a = _post(session, admin, date(2026, 1, 10), "Rent January", debit="4000", credit="1100")
    b = _post(session, admin, date(2026, 2, 10), "Laptop 100% paid", debit="0200", credit="1100")
    c = _post(session, admin, date(2026, 3, 10), "Contribution", debit="1100", credit="0520")
    page = client.get("/ledger").text
    assert all(e.number in page for e in (a, b, c))
    assert a.number not in client.get("/ledger?start=2026-02-01").text
    ranged = client.get("/ledger?start=2026-02-01&end=2026-02-28").text
    assert b.number in ranged and a.number not in ranged and c.number not in ranged
    by_account = client.get(f"/ledger?account_id={account(session, admin, '0520').id}").text
    assert c.number in by_account and a.number not in by_account
    assert b.number in client.get("/ledger?q=100%25").text
    assert a.number not in client.get("/ledger?q=100%25").text  # '%' is literal, not a wildcard
    assert a.number in client.get("/ledger?q=rent").text


def test_list_paginates(client, session, admin):
    for i in range(27):
        _post(session, admin, date(2026, 1, 1), f"Entry {i}", cents=100 + i)
    first = client.get("/ledger").text
    second = client.get("/ledger?page=2").text
    assert "27 entries" in first and "page 1 of 2" in first
    assert "2026-00001" in second and "2026-00001" not in first and "2026-00027" in first


def test_detail_shows_lines_and_reverse_creates_counter_entry(client, session, admin):
    entry = _post(session, admin, date(2026, 2, 1), "Contribution", cents=2500)
    page = client.get(f"/ledger/{entry.id}")
    assert page.status_code == 200 and format_eur(2500) in page.text
    response = client.post(f"/ledger/{entry.id}/reverse", follow_redirects=False, data={
        "csrf_token": client.csrf, "date": "2026-02-15", "reason": "wrong amount"})
    assert response.status_code == 303
    first, reversal = _entries(session)
    assert reversal.reversal_of_id == first.id
    assert reversal.description == f"Reversal of {first.number}: wrong amount"
    assert sorted((line.debit_cents, line.credit_cents) for line in reversal.lines) == [
        (0, 2500), (2500, 0)]
    assert reversal.number in client.get(f"/ledger/{entry.id}").text
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "journal.reverse"))


def test_second_reversal_and_missing_reason_are_refused(client, session, admin):
    entry = _post(session, admin, date(2026, 2, 1), "X")
    url = f"/ledger/{entry.id}/reverse"
    no_reason = client.post(url, data={"csrf_token": client.csrf, "reason": " "},
                            follow_redirects=True)
    assert "give a reason" in no_reason.text
    client.post(url, data={"csrf_token": client.csrf, "reason": "dup", "date": "2026-02-02"})
    again = client.post(url, follow_redirects=True, data={
        "csrf_token": client.csrf, "reason": "dup2", "date": "2026-02-03"})
    assert "already been reversed" in again.text
    assert len(_entries(session)) == 2


def test_reversal_into_locked_period_is_refused(client, session, admin):
    entry = _post(session, admin, date(2026, 2, 1), "X")
    admin.locked_until = date(2026, 2, 28)
    session.commit()
    response = client.post(f"/ledger/{entry.id}/reverse", follow_redirects=True, data={
        "csrf_token": client.csrf, "reason": "late", "date": "2026-02-10"})
    assert "period is closed" in response.text and len(_entries(session)) == 1


# ---- period lock

def _lock(client, value, confirm=False):
    data = {"csrf_token": client.csrf, "locked_until": value}
    if confirm:
        data["confirm"] = "1"
    return client.post("/ledger/lock", data=data, follow_redirects=False)


def test_lock_moves_forward_and_is_audited(client, session, admin):
    assert _lock(client, "2026-01-31").status_code == 303
    assert _lock(client, "2026-03-31").status_code == 303
    session.refresh(admin)
    assert admin.locked_until == date(2026, 3, 31)
    events = session.scalars(select(AuditEvent).where(AuditEvent.action == "period.lock")
                             .order_by(AuditEvent.id)).all()
    assert [(e.details["from"], e.details["to"], e.details["backwards"]) for e in events] == [
        (None, "2026-01-31", False), ("2026-01-31", "2026-03-31", False)]


def test_lock_backwards_needs_confirmation(client, session, admin):
    _lock(client, "2026-03-31")
    refused = _lock(client, "2026-01-31")
    assert refused.status_code == 422 and "reopens closed periods" in refused.text
    session.refresh(admin)
    assert admin.locked_until == date(2026, 3, 31)
    assert _lock(client, "2026-01-31", confirm=True).status_code == 303
    session.refresh(admin)
    assert admin.locked_until == date(2026, 1, 31)
    assert _lock(client, "", confirm=True).status_code == 303
    session.refresh(admin)
    assert admin.locked_until is None
    last = session.scalars(select(AuditEvent).where(AuditEvent.action == "period.lock")
                           .order_by(AuditEvent.id.desc())).first()
    assert last.details == {"from": "2026-01-31", "to": None, "backwards": True}


# ---- accounts

def _account_data(client, **fields):
    data = {"csrf_token": client.csrf, "code": "4990", "name": "Test cost", "type": "expense",
            "default_vat_code": "I21", "rgs_code": "WBedAlg"}
    return data | fields


def test_chart_lists_groups_with_balances(client, session, admin):
    _post(session, admin, date(2026, 2, 1), "Contribution", cents=7500)
    page = client.get("/accounts").text
    for heading in ("Asset", "Liability", "Equity", "Revenue", "Expense"):
        assert f">{heading}</h2>" in page
    assert format_eur(7500) in page


def test_create_account(client, session, admin):
    response = client.post("/accounts/new", data=_account_data(client), follow_redirects=False)
    assert response.status_code == 303
    created = account(session, admin, "4990")
    assert (created.name, created.type, created.default_vat_code, created.rgs_code,
            created.is_active) == ("Test cost", "expense", "I21", "WBedAlg", True)
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "account.create"))


def test_duplicate_code_is_refused_but_allowed_in_other_administration(client, session, admin):
    response = client.post("/accounts/new", data=_account_data(client, code="4000"))
    assert response.status_code == 422 and "already used" in response.text
    other, _entry, _cash = _other_admin(session)
    assert session.scalar(select(func.count()).select_from(Account).where(
        Account.administration_id == other.id, Account.code == "4000")) == 1
    assert client.post("/accounts/new", data=_account_data(client, code="4991"),
                       follow_redirects=False).status_code == 303


@pytest.mark.parametrize("fields", [{"type": "bogus"}, {"default_vat_code": "NOPE"},
                                    {"name": " "}, {"code": "12345678901"}])
def test_account_validation(client, fields):
    assert client.post("/accounts/new", data=_account_data(client, **fields)).status_code == 422


def test_type_change_refused_when_account_has_lines(client, session, admin):
    _post(session, admin, date(2026, 2, 1), "Rent", debit="4000", credit="1100")
    rent = account(session, admin, "4000")
    response = client.post(f"/accounts/{rent.id}/edit",
                           data=_account_data(client, code="4000", name="Rent", type="asset"))
    assert response.status_code == 422 and "already has journal lines" in response.text
    session.refresh(rent)
    assert rent.type == "expense"
    renamed = client.post(f"/accounts/{rent.id}/edit", follow_redirects=False,
                          data=_account_data(client, code="4001", name="Premises", type="expense"))
    assert renamed.status_code == 303
    session.refresh(rent)
    assert (rent.code, rent.name) == ("4001", "Premises")


def test_type_change_allowed_without_lines_but_not_for_system_accounts(client, session, admin):
    unused = account(session, admin, "4900")
    ok = client.post(f"/accounts/{unused.id}/edit", follow_redirects=False,
                     data=_account_data(client, code="4900", name="Other", type="asset"))
    assert ok.status_code == 303
    session.refresh(unused)
    assert unused.type == "asset"
    bank = account(session, admin, "1100")
    refused = client.post(f"/accounts/{bank.id}/edit",
                          data=_account_data(client, code="1100", name="Bank", type="expense"))
    assert refused.status_code == 422 and "system account" in refused.text


def test_deactivate_keeps_history_and_blocks_posting(client, session, admin):
    rent = account(session, admin, "4000")
    entry = _post(session, admin, date(2026, 2, 1), "Rent", debit="4000", credit="1100")
    response = client.post(f"/accounts/{rent.id}/deactivate", follow_redirects=False,
                           data={"csrf_token": client.csrf})
    assert response.status_code == 303
    session.refresh(rent)
    assert rent.is_active is False
    assert session.get(JournalEntry, entry.id) is not None
    posted = client.post("/ledger/new", data=_form(client, session, admin, codes=("4000", "1100")))
    assert posted.status_code == 422 and "inactive" in posted.text
    client.post(f"/accounts/{rent.id}/activate", data={"csrf_token": client.csrf})
    session.refresh(rent)
    assert rent.is_active is True


def test_system_account_cannot_be_deactivated(client, session, admin):
    bank = account(session, admin, "1100")
    response = client.post(f"/accounts/{bank.id}/deactivate", data={"csrf_token": client.csrf},
                           follow_redirects=True)
    assert "cannot be deactivated" in response.text
    session.refresh(bank)
    assert bank.is_active is True


def test_account_ledger_has_running_balance(client, session, admin):
    _post(session, admin, date(2026, 1, 5), "In", cents=10000)
    _post(session, admin, date(2026, 1, 9), "More", cents=2500)
    post_entry(session, admin.id, date(2026, 2, 1), "Out", [
        LineSpec(account(session, admin, "4000").id, debit_cents=4000),
        LineSpec(account(session, admin, "1100").id, credit_cents=4000)])
    session.commit()
    bank = account(session, admin, "1100")
    page = client.get(f"/accounts/{bank.id}").text
    for cents in (10000, 12500, 8500):
        assert format_eur(cents) in page
    later = client.get(f"/accounts/{bank.id}?start=2026-02-01").text
    assert "Opening balance" in later and format_eur(12500) in later and format_eur(8500) in later
    assert format_eur(10000) not in later


# ---- access control

def test_readonly_user_can_read_but_not_write(readonly_client, session, admin):
    entry = _post(session, admin, date(2026, 2, 1), "X")
    bank = account(session, admin, "1100")
    token = readonly_client.csrf
    for url in ("/ledger", "/ledger/new", "/ledger/lock", "/accounts", f"/accounts/{bank.id}",
                f"/ledger/{entry.id}"):
        assert readonly_client.get(url).status_code == 200
    writes = [
        ("/ledger/new", {"date": "2026-02-01", "description": "x"}),
        (f"/ledger/{entry.id}/reverse", {"reason": "x"}),
        ("/ledger/lock", {"locked_until": "2026-01-31"}),
        ("/accounts/new", {"code": "9", "name": "n", "type": "asset"}),
        (f"/accounts/{bank.id}/edit", {"code": "1", "name": "n", "type": "asset"}),
        (f"/accounts/{bank.id}/deactivate", {}),
        (f"/accounts/{bank.id}/activate", {}),
    ]
    for url, data in writes:
        assert readonly_client.post(url, data=data | {"csrf_token": token}).status_code == 403, url
    assert len(_entries(session)) == 1
    session.refresh(admin)
    assert admin.locked_until is None


def test_post_without_csrf_token_is_refused(client, session, admin):
    data = _form(client, session, admin) | {"csrf_token": "wrong"}
    assert client.post("/ledger/new", data=data).status_code == 403
    assert _entries(session) == []


def test_other_administrations_rows_are_404(client, session, admin):
    other, entry, their_cash = _other_admin(session)
    token = {"csrf_token": client.csrf}
    assert client.get(f"/ledger/{entry.id}").status_code == 404
    assert client.post(f"/ledger/{entry.id}/reverse",
                       data=token | {"reason": "x"}).status_code == 404
    assert client.get(f"/accounts/{their_cash.id}").status_code == 404
    assert client.get(f"/accounts/{their_cash.id}/edit").status_code == 404
    edit = _account_data(client, code="1000", name="Hijack", type="asset")
    assert client.post(f"/accounts/{their_cash.id}/edit", data=edit).status_code == 404
    assert client.post(f"/accounts/{their_cash.id}/deactivate", data=token).status_code == 404
    assert client.get(f"/ledger?account_id={their_cash.id}").status_code == 404
    session.refresh(their_cash)
    assert (their_cash.name, their_cash.is_active) == ("Cash", True)
    assert len(session.scalars(select(JournalEntry).where(
        JournalEntry.administration_id == other.id)).all()) == 1
    assert "Their entry" not in client.get("/ledger").text
