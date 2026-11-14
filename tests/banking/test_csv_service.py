from datetime import date

import pytest
from sqlalchemy import func, select

from ledgerline.banking import service
from ledgerline.banking.csv_import import CsvMapping
from ledgerline.banking.service import BankingError, MappingNeeded
from ledgerline.models import AuditEvent, BankStatement, BankTransaction, TxStatus
from tests.conftest import _login

from .conftest import IBAN, fixture_bytes
from .csv_samples import RABO_IBAN, generic_csv, rabo_csv, rabo_row, rabo_standard

GENERIC_HEADER = ["Datum", "Bedrag", "Naam", "Omschrijving"]
MAPPING = CsvMapping(date="Datum", amount="Bedrag", counterparty="Naam",
                     description=["Omschrijving"], decimal=",")


@pytest.fixture
def rabo(session, admin):
    account = service.create_bank_account(session, admin, RABO_IBAN, "Rabo zakelijk")
    session.commit()
    return account


def camt_for(iban):
    return fixture_bytes("camt053_02.xml").replace(IBAN.encode(), iban.encode())


def count(session):
    return session.scalar(select(func.count()).select_from(BankTransaction))


def audit_actions(session):
    return [a.action for a in session.scalars(select(AuditEvent).order_by(AuditEvent.id))]


def test_rabobank_csv_imports_through_the_preset(session, rabo):
    result = service.import_statement(session, rabo, "mutaties.csv", rabo_standard())
    session.commit()
    assert (result.created, result.skipped, result.cross_format_duplicates) == (2, 0, 0)
    assert session.scalar(select(BankStatement.file_format)) == "csv"
    txs = session.scalars(select(BankTransaction).order_by(BankTransaction.id)).all()
    assert [(t.amount_cents, t.status, t.entry_source) for t in txs] == [
        (12_100, TxStatus.UNMATCHED, "import"), (-5_000, TxStatus.UNMATCHED, "import"),
    ]


def test_same_csv_twice_and_overlapping_csv(session, rabo):
    service.import_statement(session, rabo, "a.csv", rabo_standard())
    with pytest.raises(BankingError, match="already been imported"):
        service.import_statement(session, rabo, "again.csv", rabo_standard())
    more = rabo_csv([
        rabo_row(102, "2026-06-03", "-50,00", "Leverancier BV", "NL86INGB0002445588",
                 "Inkoop juni"),
        rabo_row(103, "2026-06-04", "+5,00", "Klant BV", "NL02ABNA0123456789", "Fooi"),
    ])
    result = service.import_statement(session, rabo, "b.csv", more)
    assert (result.created, result.skipped) == (1, 1)


def test_csv_for_another_account_is_refused(session, bank):
    with pytest.raises(BankingError, match="no transactions for"):
        service.import_statement(session, bank, "rabo.csv", rabo_standard())


def test_unknown_csv_asks_for_a_mapping(session, bank):
    data = generic_csv([["02-06-2026", "12,50", "Bakker", "Brood"]], GENERIC_HEADER)
    with pytest.raises(MappingNeeded) as needed:
        service.import_statement(session, bank, "bank.csv", data)
    assert needed.value.headers == GENERIC_HEADER and needed.value.rows[0][2] == "Bakker"
    assert count(session) == 0


def test_generic_mapping_is_saved_per_account_and_reused(session, bank):
    first = generic_csv([["02-06-2026", "12,50", "Bakker", "Brood"]], GENERIC_HEADER)
    service.import_statement(session, bank, "one.csv", first, mapping=MAPPING, save_mapping=True)
    session.commit()
    session.refresh(bank)
    assert CsvMapping.from_json(bank.csv_mapping) == MAPPING
    second = generic_csv([["03-06-2026", "-7,25", "Slager", "Worst"]], GENERIC_HEADER)
    result = service.import_statement(session, bank, "two.csv", second)
    assert result.created == 1
    assert "bank_account.csv_mapping_save" in audit_actions(session)


def test_mapping_is_not_saved_unless_asked(session, bank):
    data = generic_csv([["02-06-2026", "12,50", "Bakker", "Brood"]], GENERIC_HEADER)
    service.import_statement(session, bank, "one.csv", data, mapping=MAPPING)
    session.refresh(bank)
    assert bank.csv_mapping is None


def test_saved_mapping_that_no_longer_fits_asks_again(session, bank):
    first = generic_csv([["02-06-2026", "12,50", "Bakker", "Brood"]], GENERIC_HEADER)
    service.import_statement(session, bank, "one.csv", first, mapping=MAPPING, save_mapping=True)
    changed = generic_csv([["03-06-2026", "1,00"]], ["Boekdatum", "Waarde"])
    with pytest.raises(MappingNeeded):
        service.import_statement(session, bank, "two.csv", changed)


def test_bad_explicit_mapping_is_a_banking_error(session, bank):
    data = generic_csv([["02-06-2026", "12,50", "Bakker", "Brood"]], GENERIC_HEADER)
    wrong = CsvMapping(date="Nope", amount="Bedrag")
    with pytest.raises(BankingError, match="not in the file"):
        service.import_statement(session, bank, "x.csv", data, mapping=wrong)


def test_one_account_cannot_reuse_another_accounts_mapping(session, admin, bank):
    other = service.create_bank_account(session, admin, RABO_IBAN, "Tweede")
    data = generic_csv([["02-06-2026", "12,50", "Bakker", "Brood"]], GENERIC_HEADER)
    service.import_statement(session, bank, "one.csv", data, mapping=MAPPING, save_mapping=True)
    with pytest.raises(MappingNeeded):
        service.import_statement(session, other, "one.csv", data)


def test_csv_after_camt_for_the_same_period_adds_nothing(session, rabo):
    service.import_statement(session, rabo, "camt.xml", camt_for(RABO_IBAN))
    result = service.import_statement(session, rabo, "mutaties.csv", rabo_standard())
    assert (result.created, result.skipped, result.cross_format_duplicates) == (0, 2, 2)
    assert any("another file format" in w for w in result.warnings)
    assert count(session) == 2


def test_camt_after_csv_adds_nothing_either(session, rabo):
    service.import_statement(session, rabo, "mutaties.csv", rabo_standard())
    result = service.import_statement(session, rabo, "camt.xml", camt_for(RABO_IBAN))
    assert (result.created, result.cross_format_duplicates) == (0, 2)


def test_generic_csv_without_iban_column_matches_camt_by_date_and_amount(session, bank):
    service.import_statement(session, bank, "camt.xml", fixture_bytes("camt053_02.xml"))
    data = generic_csv(
        [["02-06-2026", "121,00", "KLANT B.V.", "factuur"],
         ["03-06-2026", "-50,00", "Leverancier", "inkoop"],
         ["04-06-2026", "-9,99", "Nieuw", "extra"]],
        GENERIC_HEADER,
    )
    result = service.import_statement(session, bank, "x.csv", data, mapping=MAPPING)
    assert (result.created, result.cross_format_duplicates) == (1, 2)


def test_value_date_can_carry_the_match(session, rabo):
    service.import_statement(session, rabo, "camt.xml", camt_for(RABO_IBAN))
    data = rabo_csv([rabo_row(7, "2026-06-04", "+121,00", "Klant BV", "NL02ABNA0123456789",
                              "x", value="2026-06-02")])
    assert service.import_statement(session, rabo, "x.csv", data).created == 0


def test_a_different_counterparty_iban_is_not_a_duplicate(session, rabo):
    service.import_statement(session, rabo, "camt.xml", camt_for(RABO_IBAN))
    data = rabo_csv([rabo_row(7, "2026-06-02", "+121,00", "Andere Klant", "NL86INGB0002445588",
                              "x")])
    assert service.import_statement(session, rabo, "x.csv", data).created == 1


def test_equal_payments_on_one_day_need_equal_stored_lines(session, rabo):
    service.import_statement(session, rabo, "camt.xml", camt_for(RABO_IBAN))
    data = rabo_csv([
        rabo_row(7, "2026-06-02", "+121,00", "Klant BV", "NL02ABNA0123456789", "a"),
        rabo_row(8, "2026-06-02", "+121,00", "Klant BV", "NL02ABNA0123456789", "b"),
    ])
    result = service.import_statement(session, rabo, "x.csv", data)
    assert (result.created, result.cross_format_duplicates) == (1, 1)


def test_same_amount_on_another_day_is_new(session, rabo):
    service.import_statement(session, rabo, "camt.xml", camt_for(RABO_IBAN))
    data = rabo_csv([rabo_row(7, "2026-06-09", "+121,00", "Klant BV", "NL02ABNA0123456789", "a")])
    assert service.import_statement(session, rabo, "x.csv", data).created == 1


def test_a_manual_line_blocks_the_same_payment_arriving_in_a_file(session, rabo):
    service.add_manual_transaction(
        session, rabo, date(2026, 6, 2), 12_100, "Klant BV", None, "factuur", "banking app",
        today=date(2026, 6, 10))
    result = service.import_statement(session, rabo, "x.csv", rabo_standard())
    assert (result.created, result.cross_format_duplicates) == (1, 1)


def test_other_accounts_are_never_compared(session, admin, rabo, bank):
    service.import_statement(session, bank, "camt.xml", fixture_bytes("camt053_02.xml"))
    assert service.import_statement(session, rabo, "x.csv", rabo_standard()).created == 2


def test_manual_line_is_marked_and_audited(session, rabo, owner):
    tx = service.add_manual_transaction(
        session, rabo, date(2026, 6, 2), -2_500, " Slager ", "nl86 ingb 0002 4455 88",
        "Vlees  juni", "no export yet, read from the app", owner.id, today=date(2026, 6, 10))
    session.commit()
    assert (tx.entry_source, tx.statement_id, tx.entered_by_user_id) == ("manual", None, owner.id)
    assert (tx.counterparty_name, tx.counterparty_iban, tx.description) == (
        "Slager", "NL86INGB0002445588", "Vlees juni")
    event = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "bank_transaction.manual_entry")).one()
    assert event.user_id == owner.id and event.entity_id == tx.id
    assert event.details == {"source": "manual", "date": "2026-06-02", "amount_cents": -2_500,
                             "reason": "no export yet, read from the app"}


def test_manual_line_flows_through_booking_and_stays_marked(session, admin, rabo):
    from tests.helpers import account

    tx = service.add_manual_transaction(
        session, rabo, date(2026, 6, 2), -2_500, "Slager", None, None, "app", None,
        today=date(2026, 6, 10))
    service.book_to_account(session, tx, account(session, admin, "4000").id)
    session.commit()
    assert (tx.status, tx.entry_source) == (TxStatus.MATCHED, "manual")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"amount_cents": 0}, "cannot be zero"),
        ({"booking_date": date(2026, 6, 11)}, "future"),
        ({"counterparty_name": " ", "description": ""}, "counterparty or a description"),
        ({"reason": " a "}, "say why"),
        ({"counterparty_iban": "NL00BAD"}, "invalid IBAN"),
    ],
)
def test_manual_line_validation(session, rabo, kwargs, message):
    args = {"booking_date": date(2026, 6, 2), "amount_cents": 100, "counterparty_name": "X",
            "counterparty_iban": None, "description": None, "reason": "app"}
    args.update(kwargs)
    with pytest.raises(BankingError, match=message):
        service.add_manual_transaction(session, rabo, today=date(2026, 6, 10), **args)
    assert count(session) == 0


def test_database_refuses_a_manual_line_with_a_statement_and_the_reverse(session, rabo):
    from sqlalchemy.exc import IntegrityError

    service.import_statement(session, rabo, "x.csv", rabo_standard())
    tx = session.scalar(select(BankTransaction))
    tx.entry_source = "manual"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_routes_csv_flow_end_to_end(client, session, bank):
    data = generic_csv([["02-06-2026", "1.234,56", "Bakker", "Brood"]], GENERIC_HEADER)
    asked = client.post(
        f"/bank/accounts/{bank.id}/import",
        files={"file": ("bank.csv", data, "text/csv")},
        data={"csrf_token": client.csrf}, follow_redirects=False,
    )
    assert asked.status_code == 200 and "Which column is what?" in asked.text
    import base64
    form = {
        "csrf_token": client.csrf, "filename": "bank.csv",
        "content_b64": base64.b64encode(data).decode(), "date_column": "Datum",
        "amount_mode": "single", "amount": "Bedrag", "counterparty": "Naam",
        "description": ["Omschrijving"], "date_format": "auto", "decimal": ",",
        "remember": "true",
    }
    done = client.post(f"/bank/accounts/{bank.id}/import/csv", data=form, follow_redirects=False)
    assert done.status_code == 303
    session.refresh(bank)
    assert bank.csv_mapping["amount"] == "Bedrag"
    assert session.scalar(select(BankTransaction.amount_cents)) == 123_456
    again = generic_csv([["03-06-2026", "1,00", "X", "y"]], GENERIC_HEADER)
    redo = client.post(
        f"/bank/accounts/{bank.id}/import",
        files={"file": ("next.csv", again, "text/csv")},
        data={"csrf_token": client.csrf}, follow_redirects=False,
    )
    assert redo.status_code == 303


def test_routes_mapping_error_shows_the_form_again(client, bank):
    import base64
    data = generic_csv([["02-06-2026", "abc", "Bakker", "Brood"]], GENERIC_HEADER)
    form = {"csrf_token": client.csrf, "filename": "bank.csv", "date_column": "Datum",
            "content_b64": base64.b64encode(data).decode(), "amount": "Bedrag"}
    page = client.post(f"/bank/accounts/{bank.id}/import/csv", data=form, follow_redirects=False)
    assert page.status_code == 200 and "not a valid amount" in page.text


def test_routes_manual_line_shows_as_manually_entered(client, session, bank):
    response = client.post(
        f"/bank/accounts/{bank.id}/manual",
        data={"csrf_token": client.csrf, "booking_date": "2026-01-05", "amount": "12,50",
              "direction": "out", "counterparty_name": "Bakker", "reason": "app"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(f"/bank/reconcile?account_id={bank.id}")
    assert "Manually entered" in page.text and "Bakker" in page.text
    assert session.scalar(select(BankTransaction.amount_cents)) == -1_250
    assert "Manually entered" not in client.get("/bank").text


def test_routes_manual_line_error_is_flashed(client, bank):
    client.post(
        f"/bank/accounts/{bank.id}/manual",
        data={"csrf_token": client.csrf, "booking_date": "2026-01-05", "amount": "0",
              "direction": "in", "counterparty_name": "X", "reason": "app"},
        follow_redirects=False,
    )
    assert "cannot be zero" in client.get("/bank").text


def test_another_tenant_cannot_touch_the_account(client, session, bank, other_owner):
    other = _login(other_owner.email)
    data = generic_csv([["02-06-2026", "1,00", "X", "y"]], GENERIC_HEADER)
    attempts = [
        other.post(f"/bank/accounts/{bank.id}/import",
                   files={"file": ("a.csv", data, "text/csv")}, data={"csrf_token": other.csrf}),
        other.post(f"/bank/accounts/{bank.id}/manual",
                   data={"csrf_token": other.csrf, "booking_date": "2026-01-05",
                         "amount": "1", "direction": "in", "reason": "app"}),
        other.post(f"/bank/accounts/{bank.id}/import/csv",
                   data={"csrf_token": other.csrf, "filename": "a.csv", "content_b64": "x",
                         "date_column": "Datum"}),
    ]
    assert [r.status_code for r in attempts] == [404, 404, 404]
    assert count(session) == 0 and bank.csv_mapping is None


def test_another_tenant_cannot_see_the_mapping_or_manual_lines(owner_engine, session, bank,
                                                               other_tenant):
    from sqlalchemy import text

    from tests.conftest import tenant_db

    service.add_manual_transaction(session, bank, date(2026, 1, 5), 100, "X", None, None, "app",
                                   today=date(2026, 6, 10))
    bank.csv_mapping = MAPPING.to_json()
    session.commit()
    with tenant_db(other_tenant.id) as other:
        assert other.scalar(select(func.count()).select_from(BankTransaction)) == 0
        assert other.execute(text("SELECT csv_mapping FROM bank_accounts")).all() == []


def test_left_out_lines_are_returned_and_can_be_imported_anyway(session, bank, admin):
    service.import_statement(session, bank, "camt.xml", fixture_bytes("camt053_02.xml"))
    data = generic_csv(
        [["02-06-2026", "121,00", "KLANT B.V.", "genuine second payment"],
         ["04-06-2026", "-9,99", "Nieuw", "extra"]],
        GENERIC_HEADER,
    )
    result = service.import_statement(session, bank, "x.csv", data, mapping=MAPPING)
    assert result.created == 1 and len(result.left_out) == 1
    line = result.left_out[0]
    assert (line["amount_cents"], line["booking_date"]) == (12_100, "2026-06-02")
    before = count(session)
    assert service.import_left_out_lines(session, bank, result.statement.id, [line]) == 1
    assert count(session) == before + 1
    # importing the same line twice does nothing, and the override is audited
    assert service.import_left_out_lines(session, bank, result.statement.id, [line]) == 0
    assert session.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "bank_transaction.import_despite_duplicate")) == 1


def test_left_out_import_rejects_a_foreign_statement_and_bad_data(session, admin, bank, rabo):
    result = service.import_statement(session, rabo, "camt.xml", camt_for(RABO_IBAN))
    with pytest.raises(BankingError):
        service.import_left_out_lines(session, bank, result.statement.id, [])
    with pytest.raises(BankingError):
        service.import_left_out_lines(session, rabo, result.statement.id, [{"x": 1}])


def test_import_page_lists_skipped_lines_and_owner_imports_selected(client, session, admin):
    import json
    import re

    account = service.create_bank_account(session, admin, RABO_IBAN, "Rabo zakelijk")
    service.import_statement(session, account, "camt.xml", camt_for(RABO_IBAN))
    session.commit()
    response = client.post(
        f"/bank/accounts/{account.id}/import",
        data={"csrf_token": client.csrf},
        files={"file": ("mutaties.csv", rabo_standard(), "text/csv")},
    )
    assert response.status_code == 200
    assert "Lines left out" in response.text and "Import selected lines" in response.text
    from html import unescape
    raw = [unescape(v) for v in re.findall(r'name="line" value="([^"]+)"', response.text)]
    assert len(raw) == 2
    statement_id = int(re.search(r'name="statement_id" value="(\d+)"', response.text).group(1))
    done = client.post(
        f"/bank/accounts/{account.id}/import/left-out",
        data={"csrf_token": client.csrf, "statement_id": str(statement_id), "line": raw[:1]},
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert json.loads(raw[0])["dedupe_key"] in set(session.scalars(
        select(BankTransaction.dedupe_key)))
