"""Session, membership and role attacks."""

import pytest
from sqlalchemy import text

from ledgerline.models import Contact
from tests.adversarial.conftest import forge_session, use_admin


def test_forged_tenant_in_cookie_is_ignored(client, owner, other_tenant, other_admin, seeded_b):
    forge_session(
        client, user_id=owner.id, tenant_id=other_tenant.id, administration_id=other_admin.id
    )
    r = client.get("/sales", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tenants"


def test_other_users_id_with_own_tenant_needs_membership(client, owner, other_owner, tenant):
    """B's owner id, A's tenant id: B's owner has no membership in A."""
    forge_session(client, user_id=other_owner.id, tenant_id=tenant.id)
    r = client.get("/sales", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tenants"


def test_foreign_administration_in_session_falls_back_to_own_tenant(
    client, admin, other_admin, seeded_b
):
    forge_session(client, user_id=1, tenant_id=admin.tenant_id, administration_id=other_admin.id)
    # user id 1 = the owner fixture's user in tenant A
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Andere Zaak" not in r.text and "Noordlicht" not in r.text


def test_deactivated_user_loses_access_immediately(client, owner, owner_engine):
    assert client.get("/sales").status_code == 200
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_active = false WHERE id = :i"), {"i": owner.id})
    r = client.get("/sales", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_downgrade_applies_to_every_write_route_immediately(client, owner, owner_engine, admin):
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET role = 'accountant' WHERE user_id = :i"), {"i": owner.id}
        )
    for path, data in (
        ("/contacts/new", {"name": "x", "kind": "customer"}),
        (
            "/settings/users",
            {
                "email": "z@example.com",
                "role": "owner",
                "password": "a long enough password",
                "confirm": "a long enough password",
            },
        ),
        ("/settings/devices", {"name": "p"}),
    ):
        r = client.post(path, data={**data, "csrf_token": client.csrf}, follow_redirects=False)
        assert r.status_code == 403, path
    assert client.get("/settings/devices").status_code == 403
    assert client.get("/settings/privacy/export.zip").status_code == 403


def test_accountant_cannot_read_user_list_or_device_tokens(readonly_client, owner, seeded_a):
    page = readonly_client.get("/settings").text
    assert owner.email not in page
    assert readonly_client.get("/settings/devices").status_code == 403


def test_bookkeeper_cannot_export_or_anonymise_or_manage(bookkeeper_client, seeded_a):
    for url in (
        "/settings/privacy/export.zip",
        f"/settings/privacy/contacts/{seeded_a['ids'][Contact]}/export.json",
        "/settings/devices",
    ):
        assert bookkeeper_client.get(url).status_code == 403, url


def test_bookkeeper_cannot_add_user_even_as_owner(bookkeeper_client, q):
    r = bookkeeper_client.post(
        "/settings/users",
        data={
            "email": "pwn@example.com",
            "role": "owner",
            "password": "a long enough password",
            "confirm": "a long enough password",
            "csrf_token": bookkeeper_client.csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert q("SELECT count(*) FROM users WHERE email = 'pwn@example.com'")[0][0] == 0


def test_bookkeeper_cannot_change_payment_details_or_create_administrations(
    bookkeeper_client, admin, q
):
    """Business settings, the IBAN printed on invoices and new administrations are owner-only."""
    r = bookkeeper_client.post(
        "/settings",
        data={
            "name": "Testbedrijf",
            "legal_form": "bv",
            "iban": "NL02ABNA0123456789",
            "csrf_token": bookkeeper_client.csrf,
        },
        follow_redirects=False,
    )
    iban = q("SELECT iban FROM administrations WHERE id = :a", a=admin.id)[0][0]
    assert r.status_code == 403
    assert iban == "NL91ABNA0417164300"
    for path, data in (
        ("/settings/administrations", {"new_name": "Extra"}),
        ("/setup/business", {"name": "X", "iban": "NL02ABNA0123456789"}),
        ("/setup/vat", {"scheme": "kor"}),
        ("/settings/inbox-address", {}),
    ):
        r = bookkeeper_client.post(
            path, data={**data, "csrf_token": bookkeeper_client.csrf}, follow_redirects=False
        )
        assert r.status_code == 403, path
    assert q("SELECT count(*) FROM administrations")[0][0] == 1


def test_switch_with_forged_tenant_post_is_refused(client, other_tenant, other_admin):
    r = client.post(
        "/tenants",
        data={"tenant_id": other_tenant.id, "csrf_token": client.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert client.get("/settings").status_code == 200  # still in own tenant


def test_nonexistent_and_foreign_tenant_ids_look_the_same(client, other_tenant):
    a = client.post("/tenants", data={"tenant_id": other_tenant.id, "csrf_token": client.csrf})
    b = client.post("/tenants", data={"tenant_id": 987654, "csrf_token": client.csrf})
    assert (a.status_code, a.json()) == (b.status_code, b.json())


def test_foreign_and_nonexistent_ids_are_indistinguishable_on_every_id_route(
    client, seeded_a, seeded_b
):
    """Status and body must match for 'exists in tenant B' and 'exists nowhere'."""
    from tests.tenancy.test_routes import ID_ROUTES, call, fill

    use_admin(client, seeded_a["administration_id"])
    missing = {k: 99999999 for k in seeded_b["ids"]}
    diffs = []
    for path, method in ID_ROUTES:
        if method != "GET" and path.startswith("/settings/users"):
            continue
        r1 = call(client, method, fill(path, seeded_b["ids"]), path)
        r2 = call(client, method, fill(path, missing), path)
        if (r1.status_code, r1.text[:200]) != (r2.status_code, r2.text[:200]):
            diffs.append((method, path, r1.status_code, r2.status_code))
    assert not diffs, diffs


@pytest.mark.xfail(
    strict=True,
    reason="known limitation: user creation answers 'already exists'"
    " across tenants; invitations are future work",
)
def test_add_user_does_not_reveal_that_an_email_exists_in_another_tenant(client, other_owner, q):
    """Account-existence oracle across tenants: 'already exists' vs success."""

    def attempt(email):
        r = client.post(
            "/settings/users",
            data={
                "email": email,
                "role": "accountant",
                "password": "a long enough password",
                "confirm": "a long enough password",
                "csrf_token": client.csrf,
            },
            follow_redirects=True,
        )
        return "already exists" in r.text

    assert attempt(other_owner.email) == attempt("nobody-here@example.com")


@pytest.mark.xfail(
    strict=True,
    reason="known limitation: row ids come from global sequences; public ids are future work",
)
def test_number_sequences_do_not_leak_other_tenants_volume(client, seeded_a, seeded_b, q):
    """Invoice numbers are per administration; check the global serial ids are not exposed
    as the visible counter. Row ids are global, so they do reveal cross-tenant activity."""
    use_admin(client, seeded_a["administration_id"])

    def new_invoice_id():
        r = client.post(
            "/sales/new",
            data={
                "contact_id": seeded_a["ids"][Contact],
                "issue_date": "2026-11-01",
                "due_date": "2026-12-01",
                "line_description": "w",
                "line_qty": "1",
                "line_price": "1,00",
                "line_vat_code": "V21",
                "line_account": q(
                    "SELECT id FROM accounts WHERE administration_id = :a AND code='8000'",
                    a=seeded_a["administration_id"],
                )[0][0],
                "csrf_token": client.csrf,
            },
            follow_redirects=False,
        )
        assert r.status_code == 303, r.status_code
        return int(r.headers["location"].rstrip("/").split("/")[-1])

    first = new_invoice_id()
    from datetime import date

    from tests.conftest import tenant_db

    # B creates 5 invoices between A's two
    with tenant_db(
        q("SELECT tenant_id FROM administrations WHERE id = :a", a=seeded_b["administration_id"])[
            0
        ][0]
    ) as s:
        from ledgerline.models import SalesInvoice as SI

        for _ in range(5):
            s.add(
                SI(
                    administration_id=seeded_b["administration_id"],
                    contact_id=seeded_b["ids"][Contact],
                    status="draft",
                    issue_date=date(2026, 1, 1),
                    due_date=date(2026, 2, 1),
                    total_excl_cents=0,
                    total_vat_cents=0,
                    total_incl_cents=0,
                    paid_cents=0,
                )
            )
        s.commit()
    second = new_invoice_id()
    assert second - first == 1, f"A sees ids {first} -> {second}: B created 5 invoices in between"
