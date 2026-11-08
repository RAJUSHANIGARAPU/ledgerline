"""Phone token, database role/privilege and worker/mail attacks."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from ledgerline.config import get_settings
from ledgerline.inbox import email_intake
from tests.conftest import tenant_db
from tests.inbox.factories import eml, ubl
from tests.inbox.test_email_intake import FakeMailbox

XML = ubl([("Hosting", 1000, "S", 21)])


def _post(client, token, number="1"):
    return client.post(
        "/api/v1/inbox?filename=x.xml",
        content=ubl([("H", 1000, "S", 21)], number=number),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/xml"},
    )


# ------------------------------------------------------------------ phone token


def test_token_failures_are_indistinguishable(client, seeded_a, owner_engine):
    from ledgerline.mobile.tokens import PREFIX

    unknown = _post(client, PREFIX + "x" * 43)
    malformed = _post(client, "nonsense")
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE api_tokens SET revoked_at = now()"))
    revoked = _post(client, seeded_a["token"])
    shapes = {
        (r.status_code, r.text, r.headers.get("www-authenticate"))
        for r in (unknown, malformed, revoked)
    }
    assert len(shapes) == 1, shapes


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE memberships SET role = 'accountant'",
        "DELETE FROM memberships",
        "UPDATE users SET is_active = false",
    ],
)
def test_token_dies_with_the_users_rights(client, seeded_a, owner_engine, sql):
    assert _post(client, seeded_a["token"], "ok").status_code == 201
    with owner_engine.begin() as conn:
        conn.execute(text(sql))
    assert _post(client, seeded_a["token"], "after").status_code == 401


def test_token_cannot_write_into_another_tenant(client, seeded_a, seeded_b, q):
    for n in range(3):
        assert _post(client, seeded_a["token"], f"n{n}").status_code == 201
    assert (
        q(
            "SELECT count(*) FROM inbox_documents WHERE administration_id = :b",
            b=seeded_b["administration_id"],
        )[0][0]
        == 1
    )  # only B's seeded document
    # query param tricks must not choose the administration
    r = client.post(
        f"/api/v1/inbox?filename=x.xml&administration_id={seeded_b['administration_id']}"
        f"&tenant_id=999",
        content=ubl([("H", 1, "S", 21)], number="z"),
        headers={
            "Authorization": f"Bearer {seeded_a['token']}",
            "Content-Type": "application/xml",
            "X-Tenant-Id": "2",
            "X-Administration-Id": str(seeded_b["administration_id"]),
        },
    )
    assert r.status_code == 201
    assert (
        q(
            "SELECT count(*) FROM inbox_documents WHERE administration_id = :b",
            b=seeded_b["administration_id"],
        )[0][0]
        == 1
    )


def test_unauthenticated_token_guessing_is_rate_limited(client, seeded_a):
    codes = [_post(client, f"ll_guess{i}").status_code for i in range(25)]
    assert codes[:20] == [401] * 20 and set(codes[20:]) == {429}
    # the block applies before the token lookup, so even a valid token waits for the window
    assert _post(client, seeded_a["token"], "late").status_code == 429


def test_successful_requests_do_not_count_as_failures(client, seeded_a):
    for n in range(25):
        assert _post(client, seeded_a["token"], f"ok{n}").status_code in (201, 429)
    from ledgerline.mobile import tokens

    assert not tokens.too_many_failures("testclient")


# ------------------------------------------------------------------ database


def _app(database_url):
    return create_engine(database_url)


def test_definer_functions_are_hardened_and_not_public(owner_engine):
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT proname, proconfig, has_function_privilege('public', oid, 'EXECUTE') "
                "FROM pg_proc WHERE prosecdef AND pronamespace = 'public'::regnamespace"
            )
        ).all()
    assert {r[0] for r in rows} == {
        "ledgerline_route_inbox_token",
        "ledgerline_tenant_of_api_token",
    }
    for name, config, public in rows:
        assert not public, name
        assert config == ["search_path=pg_catalog, public, pg_temp"], name


def test_definer_functions_cannot_be_shadowed_by_temp_tables(database_url, seeded_a, seeded_b):
    """pg_temp comes last and tables are schema-qualified, so a temp table named like the
    real one cannot make the functions answer with an invented tenant."""
    engine = _app(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TEMP TABLE administrations (id int, tenant_id int, inbox_token text)")
            )
            conn.execute(
                text("INSERT INTO administrations VALUES (424242, 7, 'faketoken0123456789abcd')")
            )
            conn.execute(text("CREATE TEMP TABLE api_tokens (tenant_id int, token_sha256 text)"))
            conn.execute(text("INSERT INTO api_tokens VALUES (7, 'fake')"))
            routed = conn.execute(
                text("SELECT * FROM ledgerline_route_inbox_token('faketoken0123456789abcd')")
            ).all()
            api = conn.execute(text("SELECT ledgerline_tenant_of_api_token('fake')")).scalar()
        assert routed == [] and api is None
    finally:
        engine.dispose()


def test_app_role_has_no_dangerous_privileges(owner_engine, database_url):
    with owner_engine.connect() as conn:
        role = conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
                "WHERE rolname = 'ledgerline_app'"
            )
        ).one()
        assert tuple(role) == (False, False, False, False)
        bad = conn.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'ledgerline_app' AND privilege_type NOT IN "
                "('SELECT','INSERT','UPDATE','DELETE')"
            )
        ).all()
        assert not bad, bad
        assert not conn.execute(
            text("SELECT has_schema_privilege('ledgerline_app','public','CREATE')")
        ).scalar()
    engine = _app(database_url)
    try:
        with engine.connect() as conn:
            for stmt in (
                "CREATE TABLE evil (id int)",
                "ALTER TABLE contacts DISABLE ROW LEVEL SECURITY",
                "DROP POLICY tenant_isolation ON contacts",
                "ALTER TABLE contacts NO FORCE ROW LEVEL SECURITY",
                "SET ROLE ledgerline",
                "SELECT * FROM alembic_version",
            ):
                with pytest.raises(DBAPIError):
                    conn.execute(text(stmt))
                conn.rollback()
    finally:
        engine.dispose()


def test_tenant_cannot_be_changed_or_inserted_for_another_tenant(
    database_url, tenant, other_tenant, seeded_a
):
    engine = _app(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)}
            )
            with pytest.raises(DBAPIError):
                conn.execute(text("UPDATE contacts SET tenant_id = :o"), {"o": other_tenant.id})
            conn.rollback()
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)}
            )
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO contacts (administration_id, name, kind, country, "
                        "tenant_id) VALUES (:a, 'x', 'customer', 'NL', :o)"
                    ),
                    {"a": seeded_a["administration_id"], "o": other_tenant.id},
                )
    finally:
        engine.dispose()


def test_a_future_table_without_policy_is_not_readable_by_the_app_role(owner_engine, database_url):
    """The only guard for a forgotten policy is a test. A table created later inherits the
    default privileges granted to ledgerline_app and is readable across tenants."""
    with owner_engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE zz_future (id serial primary key, tenant_id int, note text)")
        )
        conn.execute(
            text(
                "INSERT INTO zz_future (tenant_id, note) VALUES (1, 'secret-of-1'), "
                "(2, 'secret-of-2')"
            )
        )
    engine = _app(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', '1', true)"))
            try:
                rows = conn.execute(text("SELECT note FROM zz_future")).all()
            except DBAPIError:
                rows = []
        assert rows == [], (
            f"app role read rows of every tenant from an unprotected new table: {rows}"
        )
    finally:
        engine.dispose()
        with owner_engine.begin() as conn:
            conn.execute(text("DROP TABLE zz_future"))


# ------------------------------------------------------------------ worker / email


def _settings(default_admin):
    return get_settings().model_copy(
        update={
            "imap_host": "m.example",
            "imap_user": "invoices@jouwbedrijf.example",
            "imap_default_administration_id": default_admin,
            "imap_allowed_senders": "",
        }
    )


def test_stranger_cannot_deliver_into_a_tenant_by_guessing_its_administration_id(
    session, tenant, other_tenant, admin, other_admin
):
    """The numeric +<id> address no longer routes; with several tenants it is rejected."""
    msg = eml(
        to=f"invoices+{other_admin.id}@jouwbedrijf.example",
        sender="attacker@evil.example",
        attachments=[("a.xml", XML, "application", "xml")],
        message_id="<evil@x>",
    )
    box = FakeMailbox({"1": msg})
    result = email_intake.poll_once(session, lambda _s: box, _settings(admin.id))
    assert result.rejected == 1 and list(box.folders) == ["Ledgerline/Rejected"]
    with tenant_db(other_tenant.id) as s:
        n = s.execute(text("SELECT count(*) FROM inbox_documents")).scalar()
    assert n == 0, "unauthenticated mail reached another tenant's inbox"


def test_unrouted_mail_does_not_fall_into_the_first_tenant(
    session, tenant, other_tenant, admin, other_admin
):
    """Without a valid token, mail is set aside when several tenants exist."""
    msg = eml(
        to="invoices@jouwbedrijf.example",
        sender="x@y.example",
        attachments=[("a.xml", XML, "application", "xml")],
        message_id="<m@x>",
    )
    email_intake.poll_once(session, lambda _s: FakeMailbox({"1": msg}), _settings(admin.id))
    with tenant_db(tenant.id) as s:
        n = s.execute(text("SELECT count(*) FROM inbox_documents")).scalar()
    assert n == 0, "misaddressed mail was stored in the default tenant"


def test_inbox_token_function_answers_only_for_an_exact_token(database_url, seeded_a, seeded_b, q):
    token_b = q(
        "SELECT inbox_token FROM administrations WHERE id = :a", a=seeded_b["administration_id"]
    )[0][0]
    engine = _app(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', '1', true)"))
            call = text("SELECT administration_id, tenant_id FROM ledgerline_route_inbox_token(:t)")
            assert conn.execute(call, {"t": "0" * 32}).all() == []
            assert conn.execute(call, {"t": str(seeded_b["administration_id"])}).all() == []
            assert conn.execute(call, {"t": token_b[:-1]}).all() == []
            assert conn.execute(call, {"t": token_b.upper()}).all() == []
            assert conn.execute(call, {"t": ""}).all() == []
            got = conn.execute(call, {"t": token_b}).all()
        assert [tuple(r) for r in got][0][0] == seeded_b["administration_id"]
    finally:
        engine.dispose()
