import pytest
from sqlalchemy import func, select

from ledgerline import cli
from ledgerline.imports import contacts as imp
from ledgerline.imports import csvio
from ledgerline.models import AuditEvent, Contact, ContactKind
from tests.conftest import tenant_db
from tests.helpers import contact

HEADER = "name;kind;email;address;postcode;city;country;vat_number;kvk;iban"
GOOD = (HEADER + "\n"
        "Voorbeeld Klant BV;customer;a@klant.example;Straat 1;1011 AB;Amsterdam;NL;"
        "NL123456782B01;12345678;NL91ABNA0417164300\n"
        "Voorbeeld Leverancier;supplier;;;;;;;;\n")


def upload(client, url, content: bytes, name="contacts.csv"):
    return client.post(url, data={"csrf_token": client.csrf},
                       files={"file": (name, content, "text/csv")}, follow_redirects=False)


def count(session):
    session.expire_all()
    return session.scalar(select(func.count()).select_from(Contact))


def test_plan_creates_valid_rows_and_maps_columns(session, admin):
    plan = imp.plan_contacts(session, admin, GOOD.encode())
    assert [r.action for r in plan.rows] == ["create", "create"]
    first = plan.rows[0].data
    assert first["address_line"] == "Straat 1" and first["kvk_number"] == "12345678"
    assert first["vat_number"] == "NL123456782B01" and first["kind"] == "customer"
    assert plan.rows[1].data["country"] == "NL"
    assert count(session) == 0


def test_validation_errors_use_the_shared_checks(session, admin):
    bad = (HEADER + "\n;customer;;;;;;;;\n"
           "X;wholesaler;nope;;;;;NL1;12;NL00BAD\n"
           "Y;both;ok@x.example;;;;N;;;\n")
    rows = imp.plan_contacts(session, admin, bad.encode()).rows
    assert [r.action for r in rows] == ["error", "error", "error"]
    assert "name: Name is required." in rows[0].reason
    for fragment in ("Unknown contact kind", "Email address is not valid", "VAT number does not",
                     "KvK number must be 8 digits", "IBAN is not valid"):
        assert fragment in rows[1].reason
    assert "country: Country must be a two-letter" in rows[2].reason


def test_kind_words_in_dutch_and_empty_kind_default(session, admin):
    text = "naam;soort\nA;klant\nB;leverancier\nC;beide\nD;\n"
    rows = imp.plan_contacts(session, admin, text.encode()).rows
    assert [r.data["kind"] for r in rows] == ["customer", "supplier", "both", "customer"]
    assert "kind is empty" in rows[3].warnings[0]


def test_customer_without_address_gets_a_warning_supplier_does_not(session, admin):
    rows = imp.plan_contacts(session, admin, b"name;kind\nA;customer\nB;supplier\n").rows
    assert any("cannot be issued" in w for w in rows[0].warnings) and not rows[1].warnings


@pytest.mark.parametrize("existing, row, why", [
    ({"vat_number": "NL123456782B01"}, "Anders;customer;;;;;;NL12 3456 782 b01;;", "VAT number"),
    ({"kvk_number": "12345678"}, "Anders;customer;;;;;;;1234 5678;", "KvK number"),
    ({"name": "Klant BV", "postcode": "1011 AB"}, "klant  bv;customer;;;1011ab;;;;;",
     "name and postcode"),
])
def test_duplicates_of_existing_contacts_are_skipped(session, admin, existing, row, why):
    fields = {"name": "Bestaand", **existing}
    contact(session, admin, **fields)
    session.commit()
    plan = imp.plan_contacts(session, admin, (HEADER + "\n" + row + "\n").encode())
    assert plan.rows[0].action == "skip" and why in plan.rows[0].reason
    assert "existing contact" in plan.rows[0].reason


def test_same_name_in_a_different_postcode_is_not_a_duplicate(session, admin):
    contact(session, admin, name="Klant BV", postcode="1011 AB")
    session.commit()
    plan = imp.plan_contacts(session, admin, b"name;postcode\nKlant BV;3511 CD\n")
    assert plan.rows[0].action == "create"


def test_duplicates_inside_the_file_keep_the_first_row(session, admin):
    text = (HEADER + "\nA;customer;;;1000 AA;;;NL123456782B01;;\n"
            "B;customer;;;;;;NL123456782B01;;\nA;customer;;;1000AA;;;;;\nC;customer;;;;;;;;\n")
    rows = imp.plan_contacts(session, admin, text.encode()).rows
    assert [r.action for r in rows] == ["create", "skip", "skip", "create"]
    assert "same VAT number as row 2 (A)" in rows[1].reason
    assert "same name and postcode as row 2" in rows[2].reason


def test_error_rows_do_not_hide_later_duplicates(session, admin):
    text = "name;vat_number\nA;NL1\nB;NL123456782B01\nC;NL123456782B01\n"
    rows = imp.plan_contacts(session, admin, text.encode()).rows
    assert [r.action for r in rows] == ["error", "create", "skip"]


def test_apply_creates_only_planned_rows_and_audits(session, admin, owner):
    contact(session, admin, name="Voorbeeld Klant BV", postcode="1011 AB")
    session.commit()
    plan = imp.plan_contacts(session, admin, GOOD.encode())
    created = imp.apply_plan(session, admin, plan, owner.id, "contacts.csv")
    session.commit()
    assert created == 1 and count(session) == 2
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.action.in_(["contact.create", "contact.import"]))).all()
    summary = next(e for e in events if e.action == "contact.import")
    assert summary.details == {"file": "contacts.csv", "created": 1, "skipped": 1, "errors": 0}
    assert sum(e.action == "contact.create" for e in events) == 1


def test_import_page_preview_and_confirm_through_the_ui(client, session, admin):
    assert client.get("/contacts/import").status_code == 200
    r = upload(client, "/contacts/import/preview", b"\xef\xbb\xbf" + GOOD.encode())
    assert r.status_code == 200 and 'data-action="create"' in r.text
    assert "Create 2 contacts" in r.text and count(session) == 0
    payload = csvio.decode(b"\xef\xbb\xbf" + GOOD.encode())[0]
    done = client.post("/contacts/import/confirm", follow_redirects=False, data={
        "csrf_token": client.csrf, "payload": payload, "filename": "contacts.csv"})
    assert done.status_code == 303 and done.headers["location"] == "/contacts"
    assert count(session) == 2
    again = upload(client, "/contacts/import/preview", GOOD.encode())
    assert again.text.count('data-action="skip"') == 2 and "Nothing to create" in again.text


def test_confirm_revalidates_and_ignores_tampered_rows(client, session, admin):
    forged = HEADER + "\n<script>x</script>;customer;;;;;;NL1;;\nOK BV;customer;;;;;;;;\n"
    client.post("/contacts/import/confirm", data={
        "csrf_token": client.csrf, "payload": forged, "filename": "x.csv"})
    session.expire_all()
    assert [c.name for c in session.scalars(select(Contact))] == ["OK BV"]


def test_preview_shows_row_errors_and_unreadable_file_message(client, session, admin):
    bad_row = (HEADER + "\nA;customer;;;;;;NL1;;\n").encode()
    bad = upload(client, "/contacts/import/preview", bad_row)
    assert 'data-action="error"' in bad.text and "VAT number does not match" in bad.text
    nothing = upload(client, "/contacts/import/preview", b"colour;size\nred;4\n")
    assert nothing.status_code == 422 and "Missing column: name" in nothing.text
    assert upload(client, "/contacts/import/preview", b"").status_code == 422
    assert client.post("/contacts/import/preview", data={"csrf_token": client.csrf}
                       ).status_code == 422
    assert count(session) == 0


def test_template_downloads_in_both_separators_and_imports_cleanly(client, session, admin):
    semi = client.get("/contacts/import/template.csv")
    comma = client.get("/contacts/import/template.csv?sep=comma")
    assert semi.content.startswith(b"\xef\xbb\xbf" + HEADER.encode().replace(b"\n", b""))
    assert comma.content.startswith(b"\xef\xbb\xbfname,kind,")
    assert "attachment" in semi.headers["content-disposition"]
    for response in (semi, comma):
        plan = upload(client, "/contacts/import/preview", response.content)
        assert plan.text.count('data-action="create"') == 2


def test_import_needs_a_write_role_and_csrf(readonly_client, client, session, admin):
    assert readonly_client.get("/contacts/import").status_code == 403
    r = readonly_client.post("/contacts/import/preview", data={"csrf_token": readonly_client.csrf},
                             files={"file": ("c.csv", GOOD.encode(), "text/csv")})
    assert r.status_code == 403
    r = readonly_client.post("/contacts/import/confirm", data={
        "csrf_token": readonly_client.csrf, "payload": GOOD})
    assert r.status_code == 403
    no_csrf = client.post("/contacts/import/confirm", data={"payload": GOOD})
    assert no_csrf.status_code == 403 and count(session) == 0
    assert readonly_client.get("/contacts/import/template.csv").status_code == 200


def test_bookkeeper_may_import(bookkeeper_client, session, admin):
    r = upload(bookkeeper_client, "/contacts/import/preview", GOOD.encode())
    assert r.status_code == 200


def test_import_never_touches_or_sees_another_tenant(client, session, admin, other_tenant,
                                                     other_admin):
    with tenant_db(other_tenant.id) as other:
        other.add(Contact(administration_id=other_admin.id, name="Voorbeeld Klant BV",
                          kind=ContactKind.CUSTOMER, vat_number="NL123456782B01",
                          kvk_number="12345678", postcode="1011 AB"))
        other.commit()
    r = upload(client, "/contacts/import/preview", GOOD.encode())
    assert r.text.count('data-action="create"') == 2, "another tenant's contacts are no duplicates"
    client.post("/contacts/import/confirm", data={
        "csrf_token": client.csrf, "payload": GOOD, "filename": "c.csv"})
    assert count(session) == 2
    with tenant_db(other_tenant.id) as other:
        assert other.scalar(select(func.count()).select_from(Contact)) == 1
        assert other.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "contact.import")) == 0


def test_fixed_route_wins_over_the_contact_id_route(client, admin):
    assert client.get("/contacts/import").status_code == 200
    assert client.get("/contacts/99999").status_code == 404


def test_cli_is_a_dry_run_until_apply(session, tenant, admin, tmp_path, capsys):
    path = tmp_path / "contacts.csv"
    path.write_bytes(GOOD.encode())
    assert cli.main(["import-contacts", "--file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "2 to create" in out and "Dry run" in out and count(session) == 0
    assert cli.main(["import-contacts", "--file", str(path), "--apply"]) == 0
    assert count(session) == 2
    assert cli.main(["import-contacts", "--file", str(path), "--apply"]) == 0
    assert "2 duplicates skipped" in capsys.readouterr().out and count(session) == 2


def test_cli_reports_unreadable_files_and_unknown_users(session, tenant, admin, tmp_path, capsys):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"colour\nred\n")
    assert cli.main(["import-contacts", "--file", str(path)]) == 1
    assert "Missing column" in capsys.readouterr().err
    path.write_bytes(GOOD.encode())
    assert cli.main(["import-contacts", "--file", str(path), "--user", "nobody@example.com"]) == 1
    assert "no user" in capsys.readouterr().err


def test_cli_needs_an_administration_when_there_are_several(session, tenant, admin, tmp_path,
                                                            capsys):
    from ledgerline.ledger.chart import create_administration

    create_administration(session, name="Tweede")
    session.commit()
    path = tmp_path / "c.csv"
    path.write_bytes(GOOD.encode())
    assert cli.main(["import-contacts", "--file", str(path)]) == 1
    assert "--administration" in capsys.readouterr().err
    assert cli.main(["import-contacts", "--file", str(path), "--administration", str(admin.id),
                     "--apply"]) == 0
